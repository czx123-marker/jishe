from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
import torch
import torch.nn.functional as F
import torchaudio


DEFAULT_CHUNK_SECONDS = 12.0
DEFAULT_OVERLAP_SECONDS = 1.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ChineseClick audio worker for Qwen clone integration.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    separate = subparsers.add_parser("separate", help="Split raw audio into vocal/background stems.")
    separate.add_argument("--input", type=Path, required=True)
    separate.add_argument("--vocal-output", type=Path, required=True)
    separate.add_argument("--background-output", type=Path, required=True)
    separate.add_argument("--device", type=str, default=None)

    mix = subparsers.add_parser("mix", help="Mix dry dubbing with background stem.")
    mix.add_argument("--voice-input", type=Path, required=True)
    mix.add_argument("--background-input", type=Path, required=True)
    mix.add_argument("--output", type=Path, required=True)
    mix.add_argument("--background-gain-db", type=float, default=0.0)

    return parser


def separate_stems(input_path: Path, vocal_output: Path, background_output: Path, *, device: str | None) -> None:
    bundle = torchaudio.pipelines.HDEMUCS_HIGH_MUSDB_PLUS
    model = bundle.get_model()
    runtime_device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(runtime_device)
    model.eval()

    waveform, sample_rate = torchaudio.load(str(input_path))
    if waveform.shape[0] == 1:
        waveform = waveform.repeat(2, 1)
    elif waveform.shape[0] > 2:
        waveform = waveform[:2]

    target_sample_rate = bundle.sample_rate
    if sample_rate != target_sample_rate:
        waveform = torchaudio.functional.resample(waveform, orig_freq=sample_rate, new_freq=target_sample_rate)
        sample_rate = target_sample_rate

    separated = _separate_in_chunks(
        model=model,
        waveform=waveform,
        sample_rate=sample_rate,
        device=runtime_device,
    )
    sources = list(model.sources)
    vocals = separated[sources.index("vocals")]
    background = sum(separated[index] for index, source in enumerate(sources) if source != "vocals")

    _save_mp3(vocal_output, vocals.cpu().numpy(), sample_rate)
    _save_mp3(background_output, background.cpu().numpy(), sample_rate)


def mix_audio(voice_input: Path, background_input: Path, output: Path, *, background_gain_db: float) -> None:
    voice, voice_sr = librosa.load(str(voice_input), sr=None, mono=False)
    background, background_sr = librosa.load(str(background_input), sr=None, mono=False)
    voice = _ensure_2d(np.asarray(voice, dtype=np.float32))
    background = _ensure_2d(np.asarray(background, dtype=np.float32))

    target_sr = max(int(voice_sr), int(background_sr))
    if int(voice_sr) != target_sr:
        voice = np.vstack([librosa.resample(channel, orig_sr=int(voice_sr), target_sr=target_sr) for channel in voice])
    if int(background_sr) != target_sr:
        background = np.vstack([librosa.resample(channel, orig_sr=int(background_sr), target_sr=target_sr) for channel in background])

    channel_count = max(voice.shape[0], background.shape[0])
    voice = _match_channel_count(voice, channel_count)
    background = _match_channel_count(background, channel_count)

    frame_count = max(voice.shape[1], background.shape[1])
    voice = _pad_to_frames(voice, frame_count)
    background = _pad_to_frames(background, frame_count)

    background_scale = float(10 ** (background_gain_db / 20.0))
    mixed = voice + background * background_scale
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 0.99:
        mixed = mixed / peak * 0.99

    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output), mixed.T, target_sr, subtype="PCM_16")


def _separate_in_chunks(
    *,
    model,
    waveform: torch.Tensor,
    sample_rate: int,
    device: str,
    chunk_seconds: float = DEFAULT_CHUNK_SECONDS,
    overlap_seconds: float = DEFAULT_OVERLAP_SECONDS,
) -> torch.Tensor:
    chunk_frames = max(1, int(chunk_seconds * sample_rate))
    overlap_frames = max(1, int(overlap_seconds * sample_rate))
    step_frames = max(1, chunk_frames - overlap_frames)
    total_frames = waveform.shape[-1]
    source_count = len(model.sources)
    channel_count = waveform.shape[0]

    merged = torch.zeros((source_count, channel_count, total_frames), dtype=torch.float32)
    weights = torch.zeros(total_frames, dtype=torch.float32)

    with torch.inference_mode():
        start_frame = 0
        while start_frame < total_frames:
            end_frame = min(total_frames, start_frame + chunk_frames)
            chunk = waveform[:, start_frame:end_frame]
            original_frames = chunk.shape[-1]
            if original_frames < chunk_frames:
                chunk = F.pad(chunk, (0, chunk_frames - original_frames))

            separated = model(chunk.unsqueeze(0).to(device))[0].cpu()[..., :original_frames]
            window = _build_window(
                original_frames=original_frames,
                overlap_frames=min(overlap_frames, max(1, original_frames // 2)),
                is_first=start_frame == 0,
                is_last=end_frame >= total_frames,
            )
            merged[..., start_frame:end_frame] += separated * window.view(1, 1, -1)
            weights[start_frame:end_frame] += window
            start_frame += step_frames

    return merged / weights.clamp_min(1e-6).view(1, 1, -1)


def _build_window(*, original_frames: int, overlap_frames: int, is_first: bool, is_last: bool) -> torch.Tensor:
    window = torch.ones(original_frames, dtype=torch.float32)
    if overlap_frames <= 1 or original_frames <= 2:
        return window
    if not is_first:
        window[:overlap_frames] = torch.linspace(0.0, 1.0, overlap_frames, dtype=torch.float32)
    if not is_last:
        window[-overlap_frames:] = torch.linspace(1.0, 0.0, overlap_frames, dtype=torch.float32)
    return window


def _save_mp3(path: Path, waveform: np.ndarray, sample_rate: int) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to export MP3 stems.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        temp_wav = Path(handle.name)
    try:
        sf.write(str(temp_wav), waveform.T, sample_rate, subtype="PCM_16")
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(temp_wav),
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "2",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        temp_wav.unlink(missing_ok=True)


def _ensure_2d(waveform: np.ndarray) -> np.ndarray:
    if waveform.ndim == 1:
        return waveform[np.newaxis, :]
    return waveform


def _match_channel_count(waveform: np.ndarray, channel_count: int) -> np.ndarray:
    if waveform.shape[0] == channel_count:
        return waveform
    if waveform.shape[0] == 1 and channel_count == 2:
        return np.repeat(waveform, 2, axis=0)
    if waveform.shape[0] > channel_count:
        return waveform[:channel_count]
    repeats = int(np.ceil(channel_count / waveform.shape[0]))
    tiled = np.tile(waveform, (repeats, 1))
    return tiled[:channel_count]


def _pad_to_frames(waveform: np.ndarray, frame_count: int) -> np.ndarray:
    if waveform.shape[1] >= frame_count:
        return waveform[:, :frame_count]
    padding = np.zeros((waveform.shape[0], frame_count - waveform.shape[1]), dtype=np.float32)
    return np.concatenate([waveform, padding], axis=1)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "separate":
        separate_stems(
            input_path=args.input,
            vocal_output=args.vocal_output,
            background_output=args.background_output,
            device=args.device,
        )
        return
    if args.command == "mix":
        mix_audio(
            voice_input=args.voice_input,
            background_input=args.background_input,
            output=args.output,
            background_gain_db=args.background_gain_db,
        )
        return
    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
