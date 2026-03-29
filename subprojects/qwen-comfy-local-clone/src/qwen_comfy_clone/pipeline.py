from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from qwen_comfy_clone.asr import ASRBackend, create_asr_backend
from qwen_comfy_clone.audio import (
    ReferenceMetrics,
    analyze_reference_audio,
    concat_audio,
    cut_audio,
    cut_audio_with_fade,
    find_low_energy_boundary,
    make_silence,
    probe_duration_ms,
    stretch_down_to_duration,
)
from qwen_comfy_clone.config import ProjectConfig
from qwen_comfy_clone.domain import (
    PipelineResult,
    Segment,
    SentenceAlignment,
    SourceClip,
    SynthClip,
    TimedToken,
    TimedTranscript,
)
from qwen_comfy_clone.logging_utils import get_logger
from qwen_comfy_clone.subtitles import linearize_overlapping_segments, load_segments, repair_timings
from qwen_comfy_clone.textnorm import looks_like_repetitive_or_broken_text, normalize_tts_text
from qwen_comfy_clone.tts import LocalQwenTTS, VoicePrompt


@dataclass(slots=True)
class ReferenceCandidate:
    clip: SourceClip
    metrics: ReferenceMetrics
    score: float


@dataclass(slots=True)
class ChunkAlignmentResult:
    alignments: list[SentenceAlignment]
    source: str
    score: float | None = None
    fallback_reason: str | None = None


logger = get_logger("pipeline")
MIN_SYNTH_CHUNK_MS = 10_000
SOFT_MIN_SYNTH_CHUNK_MS = 6_000
MAX_SYNTH_CHUNK_MS = 30_000
MAX_SYNTH_TEXT_CHARS = 260
MAX_SYNTH_TEXT_DENSITY = 20.0
PREFERRED_BREAK_GAP_MS = 800
MEDIUM_PAUSE_GAP_MS = 350
LONG_PAUSE_GAP_MS = 900
TERMINAL_PUNCTUATION = (".", "!", "?", "。", "！", "？")
ALIGNMENT_SCORE_THRESHOLD = 0.35
ALIGNMENT_SEARCH_EXTRA_TOKENS = 4
MIN_BOUNDARY_GAP_MS = 20
NON_LATIN_TOKEN_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7a3]")
LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")
TARGET_LANGUAGE_TO_ASR_CODE = {
    "Chinese": "zh",
    "English": "en",
    "Japanese": "ja",
    "Korean": "ko",
    "German": "de",
    "French": "fr",
    "Russian": "ru",
    "Portuguese": "pt",
    "Spanish": "es",
    "Italian": "it",
}


class SubtitleVoiceClonePipeline:
    def __init__(
        self,
        config: ProjectConfig,
        *,
        asr_backend: ASRBackend | None = None,
        tts_backend: LocalQwenTTS | None = None,
    ) -> None:
        self._config = config
        self._asr = asr_backend or create_asr_backend(config.asr)
        self._tts = tts_backend or LocalQwenTTS(config.tts, ref_audio_max_seconds=config.reference.ref_audio_max_seconds)
        self._global_reference_manifest: dict[str, Any] | None = None

    def run(
        self,
        *,
        audio_path: Path,
        subtitle_path: Path,
        output_dir: Path,
        target_language: str | None = None,
    ) -> PipelineResult:
        logger.info("Starting clone pipeline. audio=%s subtitles=%s out=%s", audio_path, subtitle_path, output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        total_duration_ms = probe_duration_ms(audio_path)
        raw_segments = load_segments(subtitle_path)
        original_count = len(raw_segments)
        segments = linearize_overlapping_segments(raw_segments, total_duration_ms)
        segments = repair_timings(segments, total_duration_ms)
        if not segments:
            raise RuntimeError("No subtitle segments were found.")
        if any(segment.start_ms < previous.end_ms for previous, segment in zip(raw_segments, raw_segments[1:])):
            logger.warning("Input subtitles overlap. The timeline was linearized for safe synthesis.")
        logger.info("Loaded %d repaired subtitle segments from %d raw entries.", len(segments), original_count)

        resolved_target_language = self._tts.resolve_language(
            target_language or self._config.subtitle.target_language,
            allow_auto=False,
        )
        logger.info(
            "Using serial chunk synthesis with chunk window %d-%d ms. target_language=%s",
            MIN_SYNTH_CHUNK_MS,
            MAX_SYNTH_CHUNK_MS,
            resolved_target_language,
        )

        source_dir = output_dir / "source_segments"
        chunk_source_dir = output_dir / "chunk_source_segments"
        reference_dir = output_dir / "reference"
        synth_dir = output_dir / "synth_segments"
        aligned_dir = output_dir / "aligned_segments"
        source_dir.mkdir(exist_ok=True)
        chunk_source_dir.mkdir(exist_ok=True)
        reference_dir.mkdir(exist_ok=True)
        synth_dir.mkdir(exist_ok=True)
        aligned_dir.mkdir(exist_ok=True)

        source_clips = self._cut_source_clips(audio_path, segments, source_dir)
        self._populate_reference_texts(source_clips)
        self._normalize_reference_texts(source_clips)

        generation_segments, generation_members = self._build_generation_segments(segments)
        generation_clips = self._cut_source_clips(
            audio_path,
            generation_segments,
            chunk_source_dir,
            member_segment_indexes=generation_members,
        )
        self._populate_reference_texts(generation_clips)
        self._normalize_reference_texts(generation_clips)
        logger.info("Planned %d synthesis chunks.", len(generation_clips))

        self._global_reference_manifest = None
        global_prompt: VoicePrompt | None = None
        if self._config.reference.mode == "global":
            logger.info("Building a global voice prompt from source segments.")
            global_prompt = self._build_global_prompt(source_clips, reference_dir)
        else:
            logger.info("Using per-chunk reference prompts.")

        synth_clips: list[SynthClip] = []
        for clip in generation_clips:
            clip_label = self._clip_label(clip.member_segment_indexes)
            tts_text = normalize_tts_text(
                clip.segment.text,
                resolved_target_language,
                purpose="target",
            )
            if looks_like_repetitive_or_broken_text(tts_text, resolved_target_language):
                logger.warning("Chunk %s target text looks repetitive or broken: %.80s", clip_label, tts_text)
            logger.info(
                "Synthesizing chunk %s covering %d-%d ms with %d subtitle lines.",
                clip_label,
                clip.segment.start_ms,
                clip.segment.end_ms,
                len(clip.member_segment_indexes),
            )
            prompt = global_prompt or self._tts.build_voice_prompt(clip.audio_path, clip.normalized_ref_text or clip.ref_text)
            output_audio = synth_dir / f"{clip_label}.wav"
            generated_duration_ms = self._tts.synthesize(
                text=tts_text,
                language=resolved_target_language,
                output_audio=output_audio,
                prompt=prompt,
                target_duration_ms=clip.segment.duration_ms,
            )
            synth_clips.append(
                SynthClip(
                    segment=clip.segment,
                    source_audio_path=clip.audio_path,
                    output_audio_path=output_audio,
                    ref_text=clip.ref_text,
                    normalized_ref_text=clip.normalized_ref_text,
                    tts_text=tts_text,
                    ref_text_source=clip.ref_text_source,
                    generated_duration_ms=generated_duration_ms,
                    member_segment_indexes=clip.member_segment_indexes,
                )
            )
            logger.info("Chunk %s synthesized with duration %d ms.", clip_label, generated_duration_ms)

        sentence_alignments = self._build_sentence_alignments(
            synth_clips=synth_clips,
            original_segments=segments,
            target_language=resolved_target_language,
            output_dir=aligned_dir,
        )
        final_audio_path = output_dir / "final.wav"
        self._merge_sentence_alignments_to_timeline(sentence_alignments, final_audio_path, aligned_dir)
        manifest_path = output_dir / "manifest.json"
        self._write_manifest(manifest_path, source_clips, synth_clips, sentence_alignments)
        logger.info("Pipeline completed. final=%s manifest=%s", final_audio_path, manifest_path)
        return PipelineResult(final_audio_path=final_audio_path, manifest_path=manifest_path)

    def _cut_source_clips(
        self,
        input_audio: Path,
        segments: list[Segment],
        output_dir: Path,
        *,
        member_segment_indexes: list[tuple[int, ...]] | None = None,
    ) -> list[SourceClip]:
        clips: list[SourceClip] = []
        for idx, segment in enumerate(segments):
            members = member_segment_indexes[idx] if member_segment_indexes else (segment.index,)
            clip_label = self._clip_label(members)
            start_ms = max(0, segment.start_ms - self._config.subtitle.pre_roll_ms)
            end_ms = max(start_ms + 1, segment.end_ms + self._config.subtitle.post_roll_ms)
            output_audio = output_dir / f"{clip_label}.wav"
            cut_audio(input_audio, output_audio, start_ms=start_ms, end_ms=end_ms)
            logger.info("Cut source clip %s from %d to %d ms.", clip_label, start_ms, end_ms)
            clips.append(
                SourceClip(
                    segment=segment,
                    audio_path=output_audio,
                    ref_text=segment.source_text or "",
                    ref_text_source="subtitle_source" if segment.source_text else "",
                    member_segment_indexes=members,
                )
            )
        return clips

    def _populate_reference_texts(self, clips: list[SourceClip]) -> None:
        for clip in clips:
            clip_label = self._clip_label(clip.member_segment_indexes)
            if clip.ref_text.strip():
                logger.info("Reference text for clip %s comes from the source subtitle.", clip_label)
                continue
            clip.ref_text = self._asr.transcribe(clip.audio_path, self._config.asr.language)
            clip.ref_text_source = "asr"
            logger.info("Reference text for clip %s came from source-side ASR.", clip_label)

    def _normalize_reference_texts(self, clips: list[SourceClip]) -> None:
        for clip in clips:
            clip.normalized_ref_text = normalize_tts_text(
                clip.ref_text,
                self._config.subtitle.source_language,
                purpose="reference",
            ) or clip.ref_text
            logger.info(
                "Normalized reference text for clip %s to %d chars.",
                self._clip_label(clip.member_segment_indexes),
                len(clip.normalized_ref_text),
            )

    def _build_generation_segments(self, segments: list[Segment]) -> tuple[list[Segment], list[tuple[int, ...]]]:
        if not segments:
            return [], []

        groups: list[list[Segment]] = []
        current: list[Segment] = [segments[0]]
        for segment in segments[1:]:
            current_duration = current[-1].end_ms - current[0].start_ms
            candidate_duration = segment.end_ms - current[0].start_ms
            gap_ms = max(0, segment.start_ms - current[-1].end_ms)
            candidate_text_length = self._group_text_length([*current, segment])
            candidate_text_density = self._group_text_density([*current, segment])
            if current_duration >= MIN_SYNTH_CHUNK_MS and gap_ms >= PREFERRED_BREAK_GAP_MS:
                groups.append(current)
                current = [segment]
                continue
            if candidate_duration > MAX_SYNTH_CHUNK_MS:
                groups.append(current)
                current = [segment]
                continue
            if candidate_duration >= SOFT_MIN_SYNTH_CHUNK_MS and (
                candidate_text_length > MAX_SYNTH_TEXT_CHARS
                or candidate_text_density > MAX_SYNTH_TEXT_DENSITY
            ):
                groups.append(current)
                current = [segment]
                continue
            current.append(segment)
        groups.append(current)
        groups = self._rebalance_generation_groups(groups)

        merged_segments: list[Segment] = []
        member_indexes: list[tuple[int, ...]] = []
        for group in groups:
            members = tuple(segment.index for segment in group)
            merged_segments.append(self._merge_generation_group(group))
            member_indexes.append(members)
            duration_ms = group[-1].end_ms - group[0].start_ms
            text_length = self._group_text_length(group)
            text_density = self._group_text_density(group)
            logger.info(
                "Chunk %s planned: duration=%dms text_len=%d text_density=%.2f",
                self._clip_label(members),
                duration_ms,
                text_length,
                text_density,
            )
        return merged_segments, member_indexes

    def _rebalance_generation_groups(self, groups: list[list[Segment]]) -> list[list[Segment]]:
        if len(groups) <= 1:
            return groups

        index = 0
        while index < len(groups):
            duration_ms = self._group_duration_ms(groups[index])
            if duration_ms >= MIN_SYNTH_CHUNK_MS:
                index += 1
                continue
            if index + 1 < len(groups):
                combined = groups[index] + groups[index + 1]
                if (
                    self._group_duration_ms(combined) <= MAX_SYNTH_CHUNK_MS
                    and self._group_text_length(combined) <= MAX_SYNTH_TEXT_CHARS
                    and self._group_text_density(combined) <= MAX_SYNTH_TEXT_DENSITY
                ):
                    groups[index] = combined
                    del groups[index + 1]
                    continue
            if index > 0:
                combined = groups[index - 1] + groups[index]
                if (
                    self._group_duration_ms(combined) <= MAX_SYNTH_CHUNK_MS
                    and self._group_text_length(combined) <= MAX_SYNTH_TEXT_CHARS
                    and self._group_text_density(combined) <= MAX_SYNTH_TEXT_DENSITY
                ):
                    groups[index - 1] = combined
                    del groups[index]
                    index = max(0, index - 1)
                    continue
            index += 1
        return groups

    def _merge_generation_group(self, segments: list[Segment]) -> Segment:
        text = self._join_group_texts([segment.text for segment in segments], segments)
        source_text = None
        if all(segment.source_text and segment.source_text.strip() for segment in segments):
            source_text = self._join_group_texts([segment.source_text or "" for segment in segments], segments)
        return Segment(
            index=segments[0].index,
            start_ms=segments[0].start_ms,
            end_ms=segments[-1].end_ms,
            text=text,
            source_text=source_text,
        )

    def _group_duration_ms(self, segments: list[Segment]) -> int:
        return max(0, segments[-1].end_ms - segments[0].start_ms)

    def _group_text_length(self, segments: list[Segment]) -> int:
        return len(self._join_group_texts([segment.text for segment in segments], segments))

    def _group_text_density(self, segments: list[Segment]) -> float:
        duration_ms = max(1, self._group_duration_ms(segments))
        return self._group_text_length(segments) / (duration_ms / 1000.0)

    def _join_group_texts(self, texts: list[str], segments: list[Segment]) -> str:
        cleaned = [text.strip() for text in texts]
        result = cleaned[0] if cleaned else ""
        for prev_segment, next_segment, next_text in zip(segments, segments[1:], cleaned[1:]):
            gap_ms = max(0, next_segment.start_ms - prev_segment.end_ms)
            result = f"{result}{self._separator_for_gap(gap_ms, result)}{next_text}"
        return result.strip()

    def _separator_for_gap(self, gap_ms: int, current_text: str) -> str:
        if not current_text:
            return ""
        if gap_ms >= LONG_PAUSE_GAP_MS:
            base = current_text.rstrip()
            if not base.endswith(TERMINAL_PUNCTUATION):
                return ". ... "
            return " ... "
        if gap_ms >= MEDIUM_PAUSE_GAP_MS:
            base = current_text.rstrip()
            if not base.endswith(TERMINAL_PUNCTUATION):
                return ". "
            return " "
        base = current_text.rstrip()
        if base.endswith(TERMINAL_PUNCTUATION):
            return " "
        return ", "

    def _build_global_prompt(self, clips: list[SourceClip], output_dir: Path) -> VoicePrompt:
        candidates = self._score_reference_candidates(clips)
        chosen = self._choose_reference_candidates(candidates)
        if not chosen:
            raise RuntimeError("No usable reference clip could be selected.")

        reference_audio = output_dir / "global_reference.wav"
        concat_audio([item.clip.audio_path for item in chosen], reference_audio, gap_ms=self._config.reference.join_gap_ms)
        reference_text = " ".join(item.clip.normalized_ref_text for item in chosen).strip()
        if not reference_text:
            raise RuntimeError("Global reference text is empty.")
        logger.info("Built a global prompt from source segments %s.", [item.clip.segment.index for item in chosen])

        self._global_reference_manifest = {
            "audio_path": str(reference_audio),
            "text": reference_text,
            "target_ms": self._config.reference.target_ms,
            "join_gap_ms": self._config.reference.join_gap_ms,
            "chosen_segments": [
                {
                    "index": item.clip.segment.index,
                    "source_audio_path": str(item.clip.audio_path),
                    "duration_ms": item.metrics.duration_ms,
                    "active_ratio": round(item.metrics.active_ratio, 4),
                    "clipping_ratio": round(item.metrics.clipping_ratio, 6),
                    "rms_dbfs": round(item.metrics.rms_dbfs, 2),
                    "peak_abs": round(item.metrics.peak_abs, 4),
                    "score": round(item.score, 4),
                    "ref_text_source": item.clip.ref_text_source,
                }
                for item in chosen
            ],
        }
        return self._tts.build_voice_prompt(reference_audio, reference_text)

    def _score_reference_candidates(self, clips: list[SourceClip]) -> list[ReferenceCandidate]:
        in_range: list[ReferenceCandidate] = []
        fallback: list[ReferenceCandidate] = []
        for clip in clips:
            if not clip.normalized_ref_text.strip():
                continue
            metrics = analyze_reference_audio(clip.audio_path)
            candidate = ReferenceCandidate(
                clip=clip,
                metrics=metrics,
                score=self._reference_score(clip, metrics),
            )
            fallback.append(candidate)
            if self._config.reference.min_ms <= metrics.duration_ms <= self._config.reference.max_ms:
                in_range.append(candidate)
        candidates = in_range or fallback
        return sorted(
            candidates,
            key=lambda item: (
                -item.score,
                -item.metrics.duration_ms,
                -item.metrics.active_ratio,
                item.metrics.clipping_ratio,
                item.clip.segment.index,
            ),
        )

    def _reference_score(self, clip: SourceClip, metrics: ReferenceMetrics) -> float:
        duration_score = min(metrics.duration_ms / 1000.0, self._config.reference.target_ms / 1000.0)
        silence_penalty = max(0.0, 0.65 - metrics.active_ratio) * 6.0
        quiet_penalty = max(0.0, -24.0 - metrics.rms_dbfs) / 3.0
        clipping_penalty = metrics.clipping_ratio * 200.0
        score = duration_score - silence_penalty - quiet_penalty - clipping_penalty
        if clip.ref_text_source != "subtitle_source":
            score -= 0.35
        return score

    def _choose_reference_candidates(self, candidates: list[ReferenceCandidate]) -> list[ReferenceCandidate]:
        chosen: list[ReferenceCandidate] = []
        total_ms = 0
        target_ms = max(self._config.reference.min_ms, self._config.reference.target_ms)
        max_count = max(1, self._config.reference.clip_count)
        for candidate in candidates:
            if len(chosen) >= max_count:
                break
            chosen.append(candidate)
            total_ms += candidate.metrics.duration_ms
            if total_ms >= target_ms:
                break
        logger.info(
            "Selected %d global reference clips with total duration %d ms.",
            len(chosen or candidates[:1]),
            total_ms if chosen else (candidates[0].metrics.duration_ms if candidates else 0),
        )
        return chosen or candidates[:1]

    def _build_sentence_alignments(
        self,
        *,
        synth_clips: list[SynthClip],
        original_segments: list[Segment],
        target_language: str,
        output_dir: Path,
    ) -> list[SentenceAlignment]:
        segment_by_index = {segment.index: segment for segment in original_segments}
        sentence_alignments: list[SentenceAlignment] = []
        for synth_clip in synth_clips:
            member_segments = [
                segment_by_index[index]
                for index in synth_clip.member_segment_indexes
                if index in segment_by_index
            ]
            if not member_segments:
                continue
            alignment_result = self._align_chunk_members(
                synth_clip=synth_clip,
                member_segments=member_segments,
                target_language=target_language,
                output_dir=output_dir,
            )
            synth_clip.alignment_source = alignment_result.source
            synth_clip.alignment_score = alignment_result.score
            synth_clip.alignment_fallback_reason = alignment_result.fallback_reason
            synth_clip.member_alignments = [self._serialize_alignment_item(item) for item in alignment_result.alignments]
            sentence_alignments.extend(alignment_result.alignments)
        return sentence_alignments

    def _align_chunk_members(
        self,
        *,
        synth_clip: SynthClip,
        member_segments: list[Segment],
        target_language: str,
        output_dir: Path,
    ) -> ChunkAlignmentResult:
        clip_label = self._clip_label(synth_clip.member_segment_indexes)
        if len(member_segments) == 1:
            alignment = self._materialize_sentence_alignment(
                synth_clip=synth_clip,
                segment=member_segments[0],
                local_start_ms=0,
                local_end_ms=probe_duration_ms(synth_clip.output_audio_path),
                alignment_source="single_sentence",
                output_dir=output_dir,
            )
            return ChunkAlignmentResult(alignments=[alignment], source="single_sentence", score=1.0)

        fallback_reason: str | None = None
        if self._config.alignment.mode == "target_asr":
            try:
                transcript = self._asr.transcribe_timed(
                    synth_clip.output_audio_path,
                    self._resolve_target_asr_language(target_language),
                )
                asr_result = self._align_chunk_with_timed_transcript(
                    synth_clip=synth_clip,
                    member_segments=member_segments,
                    transcript=transcript,
                    target_language=target_language,
                    output_dir=output_dir,
                )
                if asr_result.score is not None and asr_result.score >= ALIGNMENT_SCORE_THRESHOLD:
                    logger.info(
                        "Chunk %s aligned with target-side ASR (avg_score=%.3f).",
                        clip_label,
                        asr_result.score,
                    )
                    return asr_result
                fallback_reason = (
                    f"target-side ASR confidence below threshold ({asr_result.score:.3f})"
                    if asr_result.score is not None
                    else "target-side ASR confidence unavailable"
                )
                logger.warning("Chunk %s fell back to heuristic alignment: %s", clip_label, fallback_reason)
            except Exception as exc:
                fallback_reason = f"target-side ASR failed: {exc}"
                logger.warning("Chunk %s target-side alignment failed: %s", clip_label, exc)

        fallback_result = self._align_chunk_with_heuristic(
            synth_clip=synth_clip,
            member_segments=member_segments,
            target_language=target_language,
            output_dir=output_dir,
            fallback_reason=fallback_reason or "target-side alignment unavailable",
        )
        logger.info("Chunk %s aligned with heuristic fallback.", clip_label)
        return fallback_result

    def _align_chunk_with_timed_transcript(
        self,
        *,
        synth_clip: SynthClip,
        member_segments: list[Segment],
        transcript: TimedTranscript,
        target_language: str,
        output_dir: Path,
    ) -> ChunkAlignmentResult:
        timed_tokens = self._collect_alignment_tokens(transcript, target_language)
        if len(timed_tokens) < len(member_segments):
            raise RuntimeError("Timed ASR returned too few alignment tokens.")

        line_tokens = [self._tokenize_alignment_text(segment.text, target_language) for segment in member_segments]
        if any(not tokens for tokens in line_tokens):
            raise RuntimeError("One or more subtitle lines could not be tokenized for alignment.")

        total_asr_tokens = len(timed_tokens)
        total_line_tokens = sum(len(tokens) for tokens in line_tokens)
        boundary_indexes: list[tuple[int, int, float]] = []
        start_index = 0
        consumed_line_tokens = 0

        for line_index, tokens in enumerate(line_tokens):
            consumed_line_tokens += len(tokens)
            remaining_lines = len(line_tokens) - line_index - 1
            min_end = start_index + 1
            max_end = total_asr_tokens - remaining_lines
            if max_end < min_end:
                raise RuntimeError("Timed ASR tokens are insufficient for monotonic alignment.")
            predicted_end = int(round(total_asr_tokens * consumed_line_tokens / max(total_line_tokens, 1)))
            search_radius = max(ALIGNMENT_SEARCH_EXTRA_TOKENS, len(tokens) + ALIGNMENT_SEARCH_EXTRA_TOKENS)
            candidate_start = max(min_end, predicted_end - search_radius)
            candidate_end = min(max_end, predicted_end + search_radius)
            if candidate_start > candidate_end:
                candidate_start = min_end
                candidate_end = max_end

            best_end = None
            best_score = -1.0
            for end_index in range(candidate_start, candidate_end + 1):
                if end_index <= start_index:
                    continue
                candidate_tokens = [item.text for item in timed_tokens[start_index:end_index]]
                score = self._alignment_similarity(tokens, candidate_tokens, target_language)
                score -= abs(len(candidate_tokens) - len(tokens)) * 0.03
                if score > best_score:
                    best_score = score
                    best_end = end_index

            if best_end is None:
                raise RuntimeError("No monotonic ASR alignment boundary could be selected.")
            boundary_indexes.append((start_index, best_end, best_score))
            start_index = best_end

        if boundary_indexes and boundary_indexes[-1][1] < total_asr_tokens:
            last_start, _, last_score = boundary_indexes[-1]
            boundary_indexes[-1] = (last_start, total_asr_tokens, last_score)

        raw_starts: list[int] = []
        raw_ends: list[int] = []
        scores: list[float] = []
        for start_index, end_index, score in boundary_indexes:
            if end_index <= start_index:
                raise RuntimeError("ASR alignment produced an empty token slice.")
            raw_starts.append(timed_tokens[start_index].start_ms)
            raw_ends.append(timed_tokens[end_index - 1].end_ms)
            scores.append(score)

        boundaries = self._snap_chunk_boundaries(
            synth_clip.output_audio_path,
            raw_starts=raw_starts,
            raw_ends=raw_ends,
        )
        alignments = [
            self._materialize_sentence_alignment(
                synth_clip=synth_clip,
                segment=segment,
                local_start_ms=start_ms,
                local_end_ms=end_ms,
                alignment_source="target_asr",
                alignment_score=scores[index],
                output_dir=output_dir,
            )
            for index, (segment, start_ms, end_ms) in enumerate(zip(member_segments, boundaries[:-1], boundaries[1:]))
        ]
        average_score = sum(scores) / max(len(scores), 1)
        return ChunkAlignmentResult(alignments=alignments, source="target_asr", score=average_score)

    def _align_chunk_with_heuristic(
        self,
        *,
        synth_clip: SynthClip,
        member_segments: list[Segment],
        target_language: str,
        output_dir: Path,
        fallback_reason: str,
    ) -> ChunkAlignmentResult:
        chunk_duration_ms = probe_duration_ms(synth_clip.output_audio_path)
        weights = [max(1, len(self._tokenize_alignment_text(segment.text, target_language))) for segment in member_segments]
        durations = self._allocate(weights, max(chunk_duration_ms, len(member_segments)))
        raw_starts: list[int] = []
        raw_ends: list[int] = []
        cursor_ms = 0
        for duration_ms in durations:
            raw_starts.append(cursor_ms)
            cursor_ms = min(chunk_duration_ms, cursor_ms + duration_ms)
            raw_ends.append(cursor_ms)
        if raw_ends:
            raw_ends[-1] = chunk_duration_ms

        boundaries = self._snap_chunk_boundaries(
            synth_clip.output_audio_path,
            raw_starts=raw_starts,
            raw_ends=raw_ends,
            keep_first_at_zero=True,
            keep_last_at_duration=True,
        )
        alignments = [
            self._materialize_sentence_alignment(
                synth_clip=synth_clip,
                segment=segment,
                local_start_ms=start_ms,
                local_end_ms=end_ms,
                alignment_source="heuristic",
                fallback_reason=fallback_reason,
                output_dir=output_dir,
            )
            for segment, start_ms, end_ms in zip(member_segments, boundaries[:-1], boundaries[1:])
        ]
        return ChunkAlignmentResult(alignments=alignments, source="heuristic", fallback_reason=fallback_reason)

    def _collect_alignment_tokens(self, transcript: TimedTranscript, target_language: str) -> list[TimedToken]:
        collected: list[TimedToken] = []
        for segment in transcript.segments:
            source_tokens = segment.words or [TimedToken(text=segment.text, start_ms=segment.start_ms, end_ms=segment.end_ms)]
            for token in source_tokens:
                collected.extend(self._expand_timed_token(token, target_language))
        return [token for token in collected if token.text]

    def _expand_timed_token(self, token: TimedToken, target_language: str) -> list[TimedToken]:
        pieces = self._tokenize_alignment_text(token.text, target_language)
        if not pieces:
            return []
        durations = self._allocate(
            [max(1, len(piece)) for piece in pieces],
            max(token.duration_ms, len(pieces)),
        )
        expanded: list[TimedToken] = []
        cursor_ms = token.start_ms
        for index, (piece, duration_ms) in enumerate(zip(pieces, durations)):
            end_ms = token.end_ms if index == len(pieces) - 1 else min(token.end_ms, cursor_ms + duration_ms)
            if end_ms <= cursor_ms:
                end_ms = cursor_ms + 1
            expanded.append(TimedToken(text=piece, start_ms=cursor_ms, end_ms=end_ms))
            cursor_ms = end_ms
        return expanded

    def _tokenize_alignment_text(self, text: str, target_language: str) -> list[str]:
        normalized = normalize_tts_text(text, target_language, purpose="target")
        compact = " ".join(str(normalized or "").split()).strip()
        if not compact:
            return []
        if target_language in {"Chinese", "Japanese", "Korean"} or NON_LATIN_TOKEN_RE.search(compact):
            return [char for char in compact if char.isalnum() or NON_LATIN_TOKEN_RE.match(char)]
        return [token.lower() for token in LATIN_TOKEN_RE.findall(compact.lower())]

    def _alignment_similarity(self, expected_tokens: list[str], actual_tokens: list[str], target_language: str) -> float:
        if not expected_tokens or not actual_tokens:
            return 0.0
        separator = "" if target_language in {"Chinese", "Japanese", "Korean"} else " "
        expected = separator.join(expected_tokens)
        actual = separator.join(actual_tokens)
        return SequenceMatcher(None, expected, actual).ratio()

    def _snap_chunk_boundaries(
        self,
        audio_path: Path,
        *,
        raw_starts: list[int],
        raw_ends: list[int],
        keep_first_at_zero: bool = False,
        keep_last_at_duration: bool = False,
    ) -> list[int]:
        if not raw_starts or not raw_ends or len(raw_starts) != len(raw_ends):
            raise RuntimeError("Invalid raw alignment boundaries.")

        chunk_duration_ms = probe_duration_ms(audio_path)
        boundaries: list[int] = []

        first_boundary = 0 if keep_first_at_zero else max(0, min(chunk_duration_ms - 1, raw_starts[0]))
        if not keep_first_at_zero:
            first_boundary = find_low_energy_boundary(
                audio_path,
                first_boundary,
                window_ms=self._config.alignment.snap_window_ms,
                min_ms=0,
                max_ms=max(MIN_BOUNDARY_GAP_MS, raw_ends[0]),
            )
        boundaries.append(first_boundary)

        for index in range(len(raw_starts) - 1):
            target_boundary = max(boundaries[-1] + MIN_BOUNDARY_GAP_MS, (raw_ends[index] + raw_starts[index + 1]) // 2)
            max_boundary = max(target_boundary, raw_ends[index + 1] - MIN_BOUNDARY_GAP_MS)
            max_boundary = min(max_boundary, chunk_duration_ms - MIN_BOUNDARY_GAP_MS)
            snapped = find_low_energy_boundary(
                audio_path,
                target_boundary,
                window_ms=self._config.alignment.snap_window_ms,
                min_ms=boundaries[-1] + MIN_BOUNDARY_GAP_MS,
                max_ms=max_boundary,
            )
            boundaries.append(snapped)

        last_boundary = chunk_duration_ms if keep_last_at_duration else max(boundaries[-1] + MIN_BOUNDARY_GAP_MS, min(chunk_duration_ms, raw_ends[-1]))
        if not keep_last_at_duration:
            last_boundary = find_low_energy_boundary(
                audio_path,
                last_boundary,
                window_ms=self._config.alignment.snap_window_ms,
                min_ms=boundaries[-1] + MIN_BOUNDARY_GAP_MS,
                max_ms=chunk_duration_ms,
            )
        boundaries.append(max(boundaries[-1] + MIN_BOUNDARY_GAP_MS, min(chunk_duration_ms, last_boundary)))

        for index in range(1, len(boundaries)):
            if boundaries[index] <= boundaries[index - 1]:
                boundaries[index] = min(chunk_duration_ms, boundaries[index - 1] + MIN_BOUNDARY_GAP_MS)
        return boundaries

    def _materialize_sentence_alignment(
        self,
        *,
        synth_clip: SynthClip,
        segment: Segment,
        local_start_ms: int,
        local_end_ms: int,
        alignment_source: str,
        output_dir: Path,
        alignment_score: float | None = None,
        fallback_reason: str | None = None,
    ) -> SentenceAlignment:
        clip_label = self._clip_label(synth_clip.member_segment_indexes)
        local_start_ms = max(0, local_start_ms)
        local_end_ms = max(local_start_ms + 1, local_end_ms)
        output_audio_path = output_dir / f"{segment.index:04d}.wav"
        cut_audio_with_fade(
            synth_clip.output_audio_path,
            output_audio_path,
            start_ms=local_start_ms,
            end_ms=local_end_ms,
            fade_ms=self._config.alignment.fade_ms,
        )
        return SentenceAlignment(
            segment=segment,
            source_chunk_label=clip_label,
            output_audio_path=output_audio_path,
            local_start_ms=local_start_ms,
            local_end_ms=local_end_ms,
            alignment_source=alignment_source,
            alignment_score=alignment_score,
            fallback_reason=fallback_reason,
        )

    def _merge_sentence_alignments_to_timeline(
        self,
        sentence_alignments: list[SentenceAlignment],
        output_audio: Path,
        work_dir: Path,
    ) -> None:
        if not sentence_alignments:
            raise RuntimeError("No synthesized sentence clips are available for merge.")

        parts: list[Path] = []
        cursor_ms = 0
        for item in sentence_alignments:
            clip_path = item.output_audio_path
            original_duration_ms = probe_duration_ms(clip_path)
            fitted_path = work_dir / f"{item.segment.index:04d}.fitted.wav"
            ratio = stretch_down_to_duration(
                clip_path,
                fitted_path,
                target_ms=item.segment.duration_ms,
                min_ratio=self._config.output.stretch_min_ratio,
                max_ratio=self._config.output.stretch_max_ratio,
            )
            if ratio is not None:
                clip_path = fitted_path
                item.fitted_ratio = ratio
            elif original_duration_ms > item.segment.duration_ms:
                overrun_ms = original_duration_ms - item.segment.duration_ms
                item.drift_reason = f"sentence clip overruns target duration by {overrun_ms} ms"
                logger.warning("Sentence %04d drifted by %d ms because no safe stretch was available.", item.segment.index, overrun_ms)

            if item.segment.start_ms > cursor_ms:
                silence_path = work_dir / f"{item.segment.index:04d}.gap.wav"
                make_silence(silence_path, item.segment.start_ms - cursor_ms)
                parts.append(silence_path)
                cursor_ms = item.segment.start_ms
            elif item.segment.start_ms < cursor_ms and item.drift_reason is None:
                item.drift_reason = f"timeline cursor already exceeded the target start by {cursor_ms - item.segment.start_ms} ms"

            parts.append(clip_path)
            cursor_ms += probe_duration_ms(clip_path)

        final_target_ms = sentence_alignments[-1].segment.end_ms
        if final_target_ms > cursor_ms:
            tail_gap = work_dir / "tail_gap.wav"
            make_silence(tail_gap, final_target_ms - cursor_ms)
            parts.append(tail_gap)

        concat_audio(parts, output_audio)
        logger.info("Merged %d sentence-level parts into %s.", len(parts), output_audio)

    def _write_manifest(
        self,
        manifest_path: Path,
        source_clips: list[SourceClip],
        synth_clips: list[SynthClip],
        sentence_alignments: list[SentenceAlignment],
    ) -> None:
        payload = {
            "config": self._config.to_dict(),
            "global_reference": self._global_reference_manifest,
            "source_segments": [
                {
                    "index": item.segment.index,
                    "start_ms": item.segment.start_ms,
                    "end_ms": item.segment.end_ms,
                    "text": item.segment.text,
                    "source_text": item.segment.source_text,
                    "source_audio_path": str(item.audio_path),
                    "ref_text": item.ref_text,
                    "normalized_ref_text": item.normalized_ref_text,
                    "ref_text_source": item.ref_text_source,
                    "member_segment_indexes": list(item.member_segment_indexes),
                }
                for item in source_clips
            ],
            "synth_segments": [
                {
                    "index": item.segment.index,
                    "start_ms": item.segment.start_ms,
                    "end_ms": item.segment.end_ms,
                    "text": item.segment.text,
                    "source_text": item.segment.source_text,
                    "source_audio_path": str(item.source_audio_path),
                    "output_audio_path": str(item.output_audio_path),
                    "ref_text": item.ref_text,
                    "normalized_ref_text": item.normalized_ref_text,
                    "tts_text": item.tts_text,
                    "ref_text_source": item.ref_text_source,
                    "generated_duration_ms": item.generated_duration_ms,
                    "fitted_ratio": item.fitted_ratio,
                    "member_segment_indexes": list(item.member_segment_indexes),
                    "alignment_source": item.alignment_source,
                    "alignment_score": item.alignment_score,
                    "alignment_fallback_reason": item.alignment_fallback_reason,
                    "member_alignments": item.member_alignments,
                }
                for item in synth_clips
            ],
            "aligned_segments": [
                {
                    "index": item.segment.index,
                    "start_ms": item.segment.start_ms,
                    "end_ms": item.segment.end_ms,
                    "text": item.segment.text,
                    "source_text": item.segment.source_text,
                    "source_chunk_label": item.source_chunk_label,
                    "output_audio_path": str(item.output_audio_path),
                    "local_start_ms": item.local_start_ms,
                    "local_end_ms": item.local_end_ms,
                    "alignment_source": item.alignment_source,
                    "alignment_score": item.alignment_score,
                    "fitted_ratio": item.fitted_ratio,
                    "fallback_reason": item.fallback_reason,
                    "drift_reason": item.drift_reason,
                }
                for item in sentence_alignments
            ],
            "generation_strategy": {
                "mode": "serial_chunked",
                "min_chunk_ms": MIN_SYNTH_CHUNK_MS,
                "max_chunk_ms": MAX_SYNTH_CHUNK_MS,
                "max_text_chars": MAX_SYNTH_TEXT_CHARS,
                "preferred_break_gap_ms": PREFERRED_BREAK_GAP_MS,
            },
        }
        manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Wrote manifest to %s.", manifest_path)

    def _serialize_alignment_item(self, item: SentenceAlignment) -> dict[str, object]:
        return {
            "index": item.segment.index,
            "start_ms": item.segment.start_ms,
            "end_ms": item.segment.end_ms,
            "local_start_ms": item.local_start_ms,
            "local_end_ms": item.local_end_ms,
            "alignment_source": item.alignment_source,
            "alignment_score": item.alignment_score,
            "fallback_reason": item.fallback_reason,
            "drift_reason": item.drift_reason,
            "output_audio_path": str(item.output_audio_path),
        }

    def _resolve_target_asr_language(self, target_language: str) -> str | None:
        return TARGET_LANGUAGE_TO_ASR_CODE.get(target_language)

    def _allocate(self, weights: list[int], total_ms: int) -> list[int]:
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
                duration_ms = remaining
            else:
                duration_ms = int(round(remaining * max(1, weight) / remaining_weight))
                duration_ms = max(1, min(duration_ms, remaining - (slots_left - 1)))
            allocated.append(duration_ms)
            remaining -= duration_ms
            remaining_weight -= max(1, weight)
        return allocated

    def _clip_label(self, member_segment_indexes: tuple[int, ...]) -> str:
        if not member_segment_indexes:
            return "0000"
        if len(member_segment_indexes) == 1:
            return f"{member_segment_indexes[0]:04d}"
        return f"{member_segment_indexes[0]:04d}-{member_segment_indexes[-1]:04d}"
