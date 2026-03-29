from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from qwen_comfy_clone.domain import Segment


TIMESTAMP_RE = re.compile(r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{3})")
NUMERIC_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
DIRECTORY_SRT_PAIRS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("audio", "trans_subs_for_audio.srt"), ("audio", "src_subs_for_audio.srt")),
    (("trans.srt",), ("src.srt",)),
)
TARGET_COMPANIONS: dict[str, tuple[tuple[str, ...], ...]] = {
    "trans_subs_for_audio.srt": (("src_subs_for_audio.srt",), ("..", "src_subs_for_audio.srt")),
    "trans.srt": (("src.srt",), ("..", "src.srt")),
}


def load_segments(path: Path) -> list[Segment]:
    if path.is_dir():
        return _load_paired_directory(path)

    suffix = path.suffix.lower()
    if suffix == ".srt":
        return _load_srt_with_optional_source(path)
    if suffix == ".vtt":
        return _load_vtt(path)
    if suffix == ".json":
        return _load_json(path)
    raise ValueError(f"Unsupported subtitle format: {suffix}")


def prepare_paired_srt_input(source_srt: Path, target_srt: Path, workspace_dir: Path) -> Path:
    if source_srt.suffix.lower() != ".srt" or target_srt.suffix.lower() != ".srt":
        raise ValueError("Source and target subtitle inputs must both be .srt files")

    bundle_dir = workspace_dir / "paired_srt_input"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_srt, bundle_dir / "src.srt")
    shutil.copy2(target_srt, bundle_dir / "trans.srt")
    return bundle_dir


def merge_overlapping_segments(segments: list[Segment]) -> list[Segment]:
    if not segments:
        return []

    merged: list[Segment] = [segments[0]]
    for segment in segments[1:]:
        current = merged[-1]
        if segment.start_ms < current.end_ms:
            merged[-1] = Segment(
                index=current.index,
                start_ms=min(current.start_ms, segment.start_ms),
                end_ms=max(current.end_ms, segment.end_ms),
                text=_join_text(current.text, segment.text),
                source_text=_join_optional_text(current.source_text, segment.source_text),
            )
            continue
        merged.append(segment)
    return merged


def linearize_overlapping_segments(segments: list[Segment], total_duration_ms: int) -> list[Segment]:
    if not segments:
        return []

    normalized = [_clamp_segment(segment, total_duration_ms) for segment in segments]
    resolved: list[Segment] = []
    cluster: list[Segment] = [normalized[0]]
    cluster_end = normalized[0].end_ms
    cursor = 0

    for segment in normalized[1:]:
        if segment.start_ms < cluster_end:
            cluster.append(segment)
            cluster_end = max(cluster_end, segment.end_ms)
            continue
        linearized = _linearize_cluster(cluster, cursor, total_duration_ms)
        resolved.extend(linearized)
        cursor = linearized[-1].end_ms if linearized else cursor
        cluster = [segment]
        cluster_end = segment.end_ms

    linearized = _linearize_cluster(cluster, cursor, total_duration_ms)
    resolved.extend(linearized)
    return resolved


def timestamp_to_ms(value: str) -> int:
    match = TIMESTAMP_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Invalid timestamp '{value}'")
    hours = int(match.group("h"))
    minutes = int(match.group("m"))
    seconds = int(match.group("s"))
    millis = int(match.group("ms"))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def _load_srt(path: Path) -> list[Segment]:
    content = path.read_text(encoding="utf-8-sig")
    blocks = re.split(r"\r?\n\r?\n+", content.strip())
    segments: list[Segment] = []
    for block in blocks:
        lines = [line.strip("\ufeff") for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        if "-->" in lines[0]:
            time_line = lines[0]
            text_lines = lines[1:]
        else:
            time_line = lines[1]
            text_lines = lines[2:]
        if "-->" not in time_line:
            continue
        start_text, end_text = [part.strip() for part in time_line.split("-->", maxsplit=1)]
        text = " ".join(text_lines).strip()
        if not text:
            continue
        segments.append(
            Segment(
                index=len(segments) + 1,
                start_ms=timestamp_to_ms(start_text),
                end_ms=timestamp_to_ms(end_text),
                text=text,
            )
        )
    return segments


def _load_paired_directory(path: Path) -> list[Segment]:
    for target_parts, source_parts in DIRECTORY_SRT_PAIRS:
        target_path = path.joinpath(*target_parts)
        source_path = path.joinpath(*source_parts)
        if target_path.exists() and source_path.exists():
            return _merge_parallel_srt(_load_srt(source_path), _load_srt(target_path))
    raise ValueError(
        "Subtitle directory must contain audio/src_subs_for_audio.srt + audio/trans_subs_for_audio.srt "
        "or src.srt + trans.srt"
    )


def _load_srt_with_optional_source(path: Path) -> list[Segment]:
    target_segments = _load_srt(path)
    companion_paths = TARGET_COMPANIONS.get(path.name.lower())
    if not companion_paths:
        return target_segments
    for parts in companion_paths:
        source_path = path.parent.joinpath(*parts).resolve()
        if source_path.exists():
            return _merge_parallel_srt(_load_srt(source_path), target_segments)
    return target_segments


def _load_vtt(path: Path) -> list[Segment]:
    lines = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip().upper() == "WEBVTT":
            continue
        lines.append(line.replace(".", ",") if "-->" in line else line)
    temp_path = path.with_suffix(".srt.tmp")
    temp_path.write_text("\n".join(lines), encoding="utf-8")
    try:
        return _load_srt(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)


def _load_json(path: Path) -> list[Segment]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("segments")
    if not isinstance(raw, list):
        raise ValueError("JSON subtitle input must be a list or an object with a 'segments' list")

    segments: list[Segment] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError("Each subtitle item must be an object")
        start_ms, end_ms = _resolve_segment_times(item)
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        segments.append(
            Segment(
                index=index,
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
                source_text=_extract_source_text(item),
            )
        )
    return segments


def _merge_parallel_srt(source_segments: list[Segment], target_segments: list[Segment]) -> list[Segment]:
    if len(source_segments) != len(target_segments):
        raise ValueError(
            "Source and target subtitle counts do not match: "
            f"{len(source_segments)} vs {len(target_segments)}"
        )
    merged: list[Segment] = []
    for source_segment, target_segment in zip(source_segments, target_segments):
        merged.append(
            Segment(
                index=target_segment.index,
                start_ms=target_segment.start_ms,
                end_ms=target_segment.end_ms,
                text=target_segment.text,
                source_text=source_segment.text,
            )
        )
    return merged


def repair_timings(segments: list[Segment], total_duration_ms: int) -> list[Segment]:
    if not segments or total_duration_ms <= 0:
        return segments

    repaired: list[Segment | None] = [None] * len(segments)
    valid_indexes: list[int] = []
    cursor = 0
    for index, segment in enumerate(segments):
        start_ms = max(0, min(segment.start_ms, total_duration_ms))
        end_ms = max(0, min(segment.end_ms, total_duration_ms))
        if start_ms < end_ms and start_ms >= cursor:
            repaired[index] = Segment(
                index=segment.index,
                start_ms=start_ms,
                end_ms=end_ms,
                text=segment.text,
                source_text=segment.source_text,
            )
            valid_indexes.append(index)
            cursor = end_ms

    boundaries = [-1, *valid_indexes, len(segments)]
    for left_boundary, right_boundary in zip(boundaries, boundaries[1:]):
        run = [idx for idx in range(left_boundary + 1, right_boundary) if repaired[idx] is None]
        if not run:
            continue
        left_ms = repaired[left_boundary].end_ms if left_boundary >= 0 and repaired[left_boundary] is not None else 0
        right_ms = repaired[right_boundary].start_ms if right_boundary < len(segments) and repaired[right_boundary] is not None else total_duration_ms
        durations = _allocate([_weight(segments[idx].text) for idx in run], max(right_ms - left_ms, len(run)))
        cursor = left_ms
        for offset, segment_index in enumerate(run):
            duration_ms = max(1, durations[offset])
            end_ms = cursor + duration_ms
            if offset == len(run) - 1 and right_ms > cursor:
                end_ms = right_ms
            repaired[segment_index] = Segment(
                index=segments[segment_index].index,
                start_ms=cursor,
                end_ms=max(cursor + 1, end_ms),
                text=segments[segment_index].text,
                source_text=segments[segment_index].source_text,
            )
            cursor = repaired[segment_index].end_ms

    return [segment for segment in repaired if segment is not None]


def _resolve_segment_times(item: dict[str, object]) -> tuple[int, int]:
    if "start_ms" in item and "end_ms" in item:
        return _coerce_time_value(item["start_ms"], unit="ms"), _coerce_time_value(item["end_ms"], unit="ms")
    return _coerce_time_value(item["start"], unit="s"), _coerce_time_value(item["end"], unit="s")


def _coerce_time_value(value: object, *, unit: str) -> int:
    if isinstance(value, (int, float)):
        multiplier = 1 if unit == "ms" else 1000
        return int(round(float(value) * multiplier))
    text = str(value).strip()
    if NUMERIC_RE.fullmatch(text):
        multiplier = 1 if unit == "ms" else 1000
        return int(round(float(text) * multiplier))
    return timestamp_to_ms(text)


def _extract_source_text(item: dict[str, object]) -> str | None:
    for key in ("source", "source_text", "ref_text"):
        value = item.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _join_optional_text(left: str | None, right: str | None) -> str | None:
    if left and right:
        return _join_text(left, right)
    return left or right


def _clamp_segment(segment: Segment, total_duration_ms: int) -> Segment:
    if total_duration_ms > 0:
        start_ms = max(0, min(segment.start_ms, total_duration_ms))
        end_ms = max(0, min(segment.end_ms, total_duration_ms))
    else:
        start_ms = max(0, segment.start_ms)
        end_ms = max(0, segment.end_ms)
    if end_ms <= start_ms:
        end_ms = start_ms + 1
    return Segment(
        index=segment.index,
        start_ms=start_ms,
        end_ms=end_ms,
        text=segment.text,
        source_text=segment.source_text,
    )


def _linearize_cluster(cluster: list[Segment], cursor_ms: int, total_duration_ms: int) -> list[Segment]:
    if not cluster:
        return []

    cluster_start = max(cursor_ms, cluster[0].start_ms)
    cluster_end = max(cluster_start + len(cluster), max(segment.end_ms for segment in cluster))
    if total_duration_ms > 0:
        cluster_start = min(cluster_start, total_duration_ms)
        cluster_end = min(cluster_end, total_duration_ms)
        if cluster_end <= cluster_start:
            cluster_end = min(total_duration_ms, cluster_start + len(cluster))
    if len(cluster) == 1:
        only = cluster[0]
        start_ms = max(cursor_ms, only.start_ms)
        end_ms = max(start_ms + 1, min(cluster_end, only.end_ms))
        return [
            Segment(
                index=only.index,
                start_ms=start_ms,
                end_ms=end_ms,
                text=only.text,
                source_text=only.source_text,
            )
        ]

    total_span = max(len(cluster), cluster_end - cluster_start)
    durations = _allocate([_weight(segment.text) for segment in cluster], total_span)
    resolved: list[Segment] = []
    cursor = cluster_start
    for offset, segment in enumerate(cluster):
        duration_ms = max(1, durations[offset])
        end_ms = cursor + duration_ms
        if offset == len(cluster) - 1:
            end_ms = cluster_end
        resolved.append(
            Segment(
                index=segment.index,
                start_ms=cursor,
                end_ms=max(cursor + 1, end_ms),
                text=segment.text,
                source_text=segment.source_text,
            )
        )
        cursor = resolved[-1].end_ms
    return resolved


def _join_text(left: str, right: str) -> str:
    left_clean = " ".join(left.split()).strip()
    right_clean = " ".join(right.split()).strip()
    if not left_clean:
        return right_clean
    if not right_clean:
        return left_clean
    if left_clean == right_clean:
        return left_clean
    if left_clean.endswith(right_clean):
        return left_clean
    if right_clean.startswith(left_clean):
        return right_clean
    return f"{left_clean} {right_clean}"


def _weight(text: str) -> int:
    return max(1, len(re.sub(r"\s+", "", text)))


def _allocate(weights: list[int], total_ms: int) -> list[int]:
    if not weights:
        return []
    if total_ms <= 0:
        return [1] * len(weights)

    remaining = total_ms
    remaining_weight = sum(max(1, value) for value in weights)
    allocated: list[int] = []
    for index, weight in enumerate(weights):
        slots_left = len(weights) - index
        if index == len(weights) - 1:
            duration = remaining
        else:
            duration = int(round(remaining * max(1, weight) / remaining_weight))
            duration = max(1, min(duration, remaining - (slots_left - 1)))
        allocated.append(duration)
        remaining -= duration
        remaining_weight -= max(1, weight)
    return allocated
