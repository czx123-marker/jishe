from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import shutil
import subprocess

import librosa
import numpy as np
import soundfile as sf

from qwen_comfy_clone.logging_utils import get_logger


@dataclass(slots=True)
class ReferenceMetrics:
    duration_ms: int
    active_ratio: float
    clipping_ratio: float
    rms_dbfs: float
    peak_abs: float


logger = get_logger("audio")


def load_audio(path: Path, sample_rate: int | None = None, *, mono: bool = True) -> tuple[np.ndarray, int]:
    waveform, sr = librosa.load(str(path), sr=sample_rate, mono=mono)
    return np.asarray(waveform, dtype=np.float32), int(sr)


def save_audio(path: Path, waveform: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.asarray(waveform, dtype=np.float32), sample_rate, subtype="PCM_16")


def probe_duration_ms(path: Path) -> int:
    try:
        info = sf.info(str(path))
        return int(info.frames / info.samplerate * 1000)
    except Exception:
        waveform, sr = load_audio(path)
        return waveform_duration_ms(waveform, sr)


def waveform_duration_ms(waveform: np.ndarray, sample_rate: int) -> int:
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    return int(len(waveform) / sample_rate * 1000)


def cut_audio(input_audio: Path, output_audio: Path, *, start_ms: int, end_ms: int) -> None:
    waveform, sr = load_audio(input_audio)
    start_frame = max(0, int(start_ms / 1000 * sr))
    end_frame = max(start_frame + 1, int(end_ms / 1000 * sr))
    save_audio(output_audio, waveform[start_frame:end_frame], sr)


def cut_audio_with_fade(
    input_audio: Path,
    output_audio: Path,
    *,
    start_ms: int,
    end_ms: int,
    fade_ms: int,
) -> None:
    waveform, sr = load_audio(input_audio)
    start_frame = max(0, int(start_ms / 1000 * sr))
    end_frame = max(start_frame + 1, int(end_ms / 1000 * sr))
    clip = np.copy(waveform[start_frame:end_frame]).astype(np.float32)
    if clip.size == 0:
        clip = np.zeros(1, dtype=np.float32)

    fade_frames = max(0, int(fade_ms / 1000 * sr))
    if fade_frames > 0:
        fade_frames = min(fade_frames, max(1, clip.size // 2))
        fade_in = np.linspace(0.0, 1.0, fade_frames, endpoint=True, dtype=np.float32)
        fade_out = np.linspace(1.0, 0.0, fade_frames, endpoint=True, dtype=np.float32)
        clip[:fade_frames] *= fade_in
        clip[-fade_frames:] *= fade_out

    save_audio(output_audio, clip, sr)


def make_silence(output_audio: Path, duration_ms: int, *, sample_rate: int = 24000) -> None:
    frames = max(1, int(duration_ms / 1000 * sample_rate))
    save_audio(output_audio, np.zeros(frames, dtype=np.float32), sample_rate)


def concat_audio(inputs: list[Path], output_audio: Path, *, gap_ms: int = 0) -> None:
    if not inputs:
        raise ValueError("No audio inputs to concatenate")

    target_sr: int | None = None
    merged: list[np.ndarray] = []
    gap_waveform: np.ndarray | None = None
    for index, path in enumerate(inputs):
        waveform, sr = load_audio(path)
        if target_sr is None:
            target_sr = sr
        elif sr != target_sr:
            waveform = librosa.resample(waveform, orig_sr=sr, target_sr=target_sr)

        if gap_ms > 0 and index > 0:
            if gap_waveform is None:
                gap_frames = max(1, int(gap_ms / 1000 * (target_sr or sr)))
                gap_waveform = np.zeros(gap_frames, dtype=np.float32)
            merged.append(gap_waveform)

        merged.append(np.asarray(waveform, dtype=np.float32))

    save_audio(output_audio, np.concatenate(merged), target_sr or 24000)


def find_low_energy_boundary(
    input_audio: Path,
    target_ms: int,
    *,
    window_ms: int,
    min_ms: int,
    max_ms: int,
    frame_ms: int = 20,
    hop_ms: int = 5,
) -> int:
    waveform, sr = load_audio(input_audio)
    if waveform.size == 0:
        return max(min_ms, min(max_ms, target_ms))

    duration_ms = waveform_duration_ms(waveform, sr)
    search_start_ms = max(0, min(duration_ms, max(min_ms, target_ms - window_ms)))
    search_end_ms = max(search_start_ms + 1, min(duration_ms, min(max_ms, target_ms + window_ms)))
    if search_end_ms <= search_start_ms:
        return max(min_ms, min(max_ms, target_ms))

    frame_length = max(1, int(frame_ms / 1000 * sr))
    hop_length = max(1, int(hop_ms / 1000 * sr))
    start_frame = int(search_start_ms / 1000 * sr)
    end_frame = max(start_frame + frame_length, int(search_end_ms / 1000 * sr))
    sample = waveform[start_frame:end_frame]
    if sample.size <= frame_length:
        return max(min_ms, min(max_ms, target_ms))

    energy = librosa.feature.rms(
        y=sample,
        frame_length=min(frame_length, sample.size),
        hop_length=hop_length,
        center=True,
    )[0]
    if energy.size == 0:
        return max(min_ms, min(max_ms, target_ms))

    best_idx = int(np.argmin(energy))
    snapped_frame = start_frame + best_idx * hop_length
    snapped_ms = int(round(snapped_frame / sr * 1000))
    return max(min_ms, min(max_ms, snapped_ms))


def analyze_reference_audio(path: Path) -> ReferenceMetrics:
    waveform, sr = load_audio(path)
    duration_ms = waveform_duration_ms(waveform, sr)
    if waveform.size == 0:
        return ReferenceMetrics(duration_ms=duration_ms, active_ratio=0.0, clipping_ratio=0.0, rms_dbfs=-120.0, peak_abs=0.0)

    peak_abs = float(np.max(np.abs(waveform)))
    clipping_ratio = float(np.mean(np.abs(waveform) >= 0.995))
    rms = float(np.sqrt(np.mean(np.square(waveform))))
    rms_dbfs = float(20 * np.log10(max(rms, 1e-6)))

    frame_length = min(len(waveform), max(256, int(sr * 0.03)))
    hop_length = max(128, frame_length // 4)
    rms_frames = librosa.feature.rms(y=waveform, frame_length=frame_length, hop_length=hop_length, center=True)[0]
    if rms_frames.size == 0:
        active_ratio = 1.0 if peak_abs > 0 else 0.0
    else:
        rms_dbfs_frames = 20 * np.log10(np.maximum(rms_frames, 1e-6))
        active_threshold = max(-45.0, rms_dbfs - 20.0)
        active_ratio = float(np.mean(rms_dbfs_frames >= active_threshold))

    return ReferenceMetrics(
        duration_ms=duration_ms,
        active_ratio=active_ratio,
        clipping_ratio=clipping_ratio,
        rms_dbfs=rms_dbfs,
        peak_abs=peak_abs,
    )


def stretch_down_to_duration(
    input_audio: Path,
    output_audio: Path,
    *,
    target_ms: int,
    min_ratio: float,
    max_ratio: float,
) -> float | None:
    waveform, sr = load_audio(input_audio)
    current_ms = waveform_duration_ms(waveform, sr)
    if current_ms <= 0 or target_ms <= 0 or current_ms <= target_ms:
        return None

    ratio = current_ms / target_ms
    if ratio < min_ratio or ratio > max_ratio:
        return None

    if _stretch_with_ffmpeg_atempo(input_audio, output_audio, tempo=ratio):
        logger.info("Used ffmpeg atempo for duration fit: %s -> %s (tempo=%.4f).", input_audio, output_audio, ratio)
        return ratio

    if _stretch_with_ffmpeg_rubberband(input_audio, output_audio, tempo=ratio):
        logger.warning(
            "ffmpeg atempo unavailable, fell back to rubberband: %s -> %s (tempo=%.4f).",
            input_audio,
            output_audio,
            ratio,
        )
        return ratio

    stretched = librosa.effects.time_stretch(waveform, rate=ratio)
    save_audio(output_audio, stretched, sr)
    logger.warning("ffmpeg stretching unavailable, fell back to librosa: %s -> %s (tempo=%.4f).", input_audio, output_audio, ratio)
    return ratio


@lru_cache(maxsize=1)
def _ffmpeg_supports_rubberband() -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
    except Exception:
        return False
    return "rubberband" in result.stdout


def _stretch_with_ffmpeg_rubberband(input_audio: Path, output_audio: Path, *, tempo: float) -> bool:
    if not _ffmpeg_supports_rubberband():
        return False
    filter_graph = (
        f"rubberband=tempo={tempo:.8f}:transients=crisp:detector=compound:"
        "phase=independent:window=short:formant=preserved:pitchq=quality"
    )
    return _run_ffmpeg_filter(input_audio, output_audio, filter_graph)


def _stretch_with_ffmpeg_atempo(input_audio: Path, output_audio: Path, *, tempo: float) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    return _run_ffmpeg_filter(input_audio, output_audio, f"atempo={tempo:.8f}")


def _run_ffmpeg_filter(input_audio: Path, output_audio: Path, filter_graph: str) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    output_audio.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(input_audio),
                "-vn",
                "-af",
                filter_graph,
                "-c:a",
                "pcm_s16le",
                str(output_audio),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
    except Exception as exc:
        logger.warning("ffmpeg stretch failed: %s", exc)
        return False
    return output_audio.exists() and output_audio.stat().st_size > 0
