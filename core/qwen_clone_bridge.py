from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.utils import load_key, rprint

APP_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = APP_ROOT / "output"
AUDIO_DIR = OUTPUT_DIR / "audio"
DEFAULT_DUB_ROOT = OUTPUT_DIR / "dub"
WORKER_SCRIPT = APP_ROOT / "qwen_clone_audio_worker.py"
FINAL_VIDEO_FILENAME = "final_video.mp4"
AUDIO_ONLY_SUFFIXES = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".oga", ".opus"}

RAW_AUDIO_PATH = AUDIO_DIR / "raw.mp3"
VOCAL_AUDIO_PATH = AUDIO_DIR / "vocal.mp3"
BACKGROUND_AUDIO_PATH = AUDIO_DIR / "background.mp3"
SOURCE_SUBTITLE_PATH = AUDIO_DIR / "src_subs_for_audio.srt"
TARGET_SUBTITLE_PATH = AUDIO_DIR / "trans_subs_for_audio.srt"

TARGET_LANGUAGE_NAMES = {
    "zh": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "de": "German",
    "fr": "French",
    "ru": "Russian",
    "pt": "Portuguese",
    "es": "Spanish",
    "it": "Italian",
}

LANGUAGE_ALIASES = {
    "chinese": "zh",
    "english": "en",
    "japanese": "ja",
    "korean": "ko",
    "german": "de",
    "french": "fr",
    "russian": "ru",
    "portuguese": "pt",
    "spanish": "es",
    "italian": "it",
    "zh-cn": "zh",
    "jp": "ja",
    "kr": "ko",
    "pt-br": "pt",
}


@dataclass(slots=True)
class QwenCloneSettings:
    enabled: bool
    python_exe: Path | None
    project_dir: Path
    config_path: Path
    output_root: Path
    use_vocal_stem: bool
    align_mode: str
    align_snap_window_ms: int
    align_fade_ms: int
    align_fallback: str
    mix_background: bool
    background_gain_db: float
    keep_dry_track: bool
    mix_fail_policy: str


@dataclass(slots=True)
class DubbingResult:
    status: str
    audio_url: str | None = None
    dry_audio_url: str | None = None
    manifest_url: str | None = None
    log_url: str | None = None
    error: str | None = None
    mix_status: str | None = None
    mix_error: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "dub_status": self.status,
            "dub_audio_url": self.audio_url,
            "dub_dry_audio_url": self.dry_audio_url,
            "dub_manifest_url": self.manifest_url,
            "dub_log_url": self.log_url,
            "dub_error": self.error,
            "dub_mix_status": self.mix_status,
            "dub_mix_error": self.mix_error,
        }


@dataclass(slots=True)
class PythonLaunchResolution:
    path: Path | None
    notes: list[str]


@dataclass(slots=True)
class StemPreparationResult:
    input_audio_path: Path
    background_audio_path: Path | None
    status: str
    error: str | None = None


def supported_dubbing_language_codes() -> list[str]:
    return sorted(TARGET_LANGUAGE_NAMES)


def supports_dubbing_language(target_language: str | None) -> bool:
    return _resolve_language_code(target_language) in TARGET_LANGUAGE_NAMES


def run_dubbing_for_current_output(
    file_uuid: str,
    *,
    target_language: str | None,
    source_language: str | None,
) -> DubbingResult:
    if not supports_dubbing_language(target_language):
        return DubbingResult(
            status="unsupported",
            error=f"Qwen3-TTS does not support target language '{target_language or ''}'.",
            mix_status="not_run",
        )

    settings = _load_settings()
    if not settings.enabled:
        return DubbingResult(status="disabled", mix_status="not_run")

    output_dir = settings.output_root / file_uuid
    output_dir.mkdir(parents=True, exist_ok=True)

    missing_inputs = [
        str(path.relative_to(APP_ROOT))
        for path in (RAW_AUDIO_PATH, SOURCE_SUBTITLE_PATH, TARGET_SUBTITLE_PATH)
        if not path.exists()
    ]
    if missing_inputs:
        _write_backend_log(
            output_dir / "backend.log",
            [
                "ChineseClick Qwen clone integration failed before launch.",
                "Missing input files:",
                *missing_inputs,
            ],
        )
        return build_dubbing_result(file_uuid)

    if not settings.project_dir.exists():
        _write_backend_log(
            output_dir / "backend.log",
            [
                "Configured qwen_clone.project_dir was not found.",
                str(settings.project_dir),
            ],
        )
        return build_dubbing_result(file_uuid)

    if not settings.config_path.exists():
        _write_backend_log(
            output_dir / "backend.log",
            [
                "Configured qwen_clone.config_path was not found.",
                str(settings.config_path),
            ],
        )
        return build_dubbing_result(file_uuid)

    python_resolution = _resolve_python_executable(settings)
    if python_resolution.notes:
        _write_backend_log(output_dir / "backend.log", python_resolution.notes)
    if python_resolution.path is None:
        return build_dubbing_result(file_uuid)

    stem_result = _prepare_input_audio(
        settings=settings,
        python_exe=python_resolution.path,
        output_dir=output_dir,
    )

    target_language_name = TARGET_LANGUAGE_NAMES[_resolve_language_code(target_language)]
    source_language_name, asr_language_code = _resolve_source_runtime(source_language)
    command = [
        str(python_resolution.path),
        "-m",
        "qwen_comfy_clone.cli",
        "run",
        "--audio",
        str(stem_result.input_audio_path),
        "--source-srt",
        str(SOURCE_SUBTITLE_PATH),
        "--target-srt",
        str(TARGET_SUBTITLE_PATH),
        "--out-dir",
        str(output_dir),
        "--target-language",
        target_language_name,
        "--source-language",
        source_language_name,
        "--asr-language",
        asr_language_code,
        "--config",
        str(settings.config_path),
        "--align-mode",
        settings.align_mode,
        "--align-snap-window-ms",
        str(settings.align_snap_window_ms),
        "--align-fade-ms",
        str(settings.align_fade_ms),
        "--align-fallback",
        settings.align_fallback,
    ]

    env = _build_project_env(settings.project_dir)
    rprint(f"[cyan]Running Qwen clone pipeline for {file_uuid}...[/cyan]")
    completed = subprocess.run(
        command,
        cwd=str(settings.project_dir),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if completed.returncode != 0:
        _write_backend_log(
            output_dir / "backend.log",
            [
                "ChineseClick Qwen clone command failed.",
                f"Command: {' '.join(command)}",
                f"Exit code: {completed.returncode}",
                "STDOUT:",
                completed.stdout.strip() or "(empty)",
                "STDERR:",
                completed.stderr.strip() or "(empty)",
            ],
            append=True,
        )
        return build_dubbing_result(file_uuid)

    final_audio_path = output_dir / "final.wav"
    if not final_audio_path.exists():
        _write_backend_log(
            output_dir / "backend.log",
            [
                "Qwen clone command completed without producing final.wav.",
                "STDOUT:",
                completed.stdout.strip() or "(empty)",
                "STDERR:",
                completed.stderr.strip() or "(empty)",
            ],
            append=True,
        )
        return build_dubbing_result(file_uuid)

    dry_audio_path = output_dir / "dry.wav"
    shutil.copy2(final_audio_path, dry_audio_path)

    mix_status, mix_error = _finalize_mix(
        settings=settings,
        python_exe=python_resolution.path,
        dry_audio_path=dry_audio_path,
        final_audio_path=final_audio_path,
        background_audio_path=stem_result.background_audio_path,
        output_dir=output_dir,
        stem_error=stem_result.error,
    )

    _augment_manifest(
        manifest_path=output_dir / "manifest.json",
        source_audio_path=stem_result.input_audio_path,
        dry_audio_path=dry_audio_path,
        final_audio_path=final_audio_path,
        background_audio_path=stem_result.background_audio_path,
        stem_status=stem_result.status,
        stem_error=stem_result.error,
        mix_status=mix_status,
        mix_error=mix_error,
    )

    return build_dubbing_result(file_uuid, default_status="completed")


def build_dubbing_result_for_history(subtitles_path: str | os.PathLike[str]) -> DubbingResult:
    file_uuid = Path(subtitles_path).stem
    return build_dubbing_result(file_uuid)


def ensure_dubbed_playback_url(file_uuid: str, source_media_path: str | os.PathLike[str] | None) -> str | None:
    output_dir = _resolve_output_dir(file_uuid)
    final_audio_path = output_dir / "final.wav"
    if not final_audio_path.exists():
        return None

    if not source_media_path:
        return _to_output_url(final_audio_path)

    source_path = Path(source_media_path)
    if not source_path.exists():
        _write_backend_log(
            output_dir / "backend.log",
            [
                "Source media file for final playback was not found. Falling back to final.wav.",
                str(source_path),
            ],
            append=True,
        )
        return _to_output_url(final_audio_path)

    if source_path.suffix.lower() in AUDIO_ONLY_SUFFIXES:
        return _to_output_url(final_audio_path)

    muxed_video_path = output_dir / FINAL_VIDEO_FILENAME
    if _playback_asset_is_current(muxed_video_path, source_path, final_audio_path):
        return _to_output_url(muxed_video_path)

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        _write_backend_log(
            output_dir / "backend.log",
            [
                "ffmpeg was not found while building the final dubbed video. Falling back to final.wav.",
            ],
            append=True,
        )
        return _to_output_url(final_audio_path)

    mux_error = _mux_dubbed_video(
        ffmpeg=ffmpeg,
        source_media_path=source_path,
        final_audio_path=final_audio_path,
        output_video_path=muxed_video_path,
    )
    if mux_error:
        _write_backend_log(
            output_dir / "backend.log",
            [
                "Final dubbed video mux failed. Falling back to final.wav.",
                mux_error,
            ],
            append=True,
        )
        return _to_output_url(final_audio_path)

    return _to_output_url(muxed_video_path)


def build_dubbing_result(file_uuid: str, *, default_status: str = "disabled") -> DubbingResult:
    output_dir = _resolve_output_dir(file_uuid)
    log_path = output_dir / "backend.log"
    manifest_path = output_dir / "manifest.json"
    final_audio_path = output_dir / "final.wav"
    dry_audio_path = output_dir / "dry.wav"
    bridge_meta = _read_bridge_metadata(manifest_path)

    if final_audio_path.exists():
        status = "completed"
    elif log_path.exists() or manifest_path.exists() or output_dir.exists():
        status = "failed"
    else:
        status = default_status

    mix_status = bridge_meta.get("mix_status") if bridge_meta else None
    mix_error = bridge_meta.get("mix_error") if bridge_meta else None
    if status in {"disabled", "unsupported"} and mix_status is None:
        mix_status = "not_run"

    return DubbingResult(
        status=status,
        audio_url=_to_output_url(final_audio_path),
        dry_audio_url=_to_output_url(dry_audio_path),
        manifest_url=_to_output_url(manifest_path),
        log_url=_to_output_url(log_path),
        error=_summarize_log_error(log_path) if status == "failed" else None,
        mix_status=mix_status,
        mix_error=mix_error,
    )


def _load_settings() -> QwenCloneSettings:
    return QwenCloneSettings(
        enabled=bool(_safe_load_key("qwen_clone.enabled", False)),
        python_exe=_resolve_optional_config_path(_safe_load_key("qwen_clone.python_exe", "")),
        project_dir=_resolve_config_path(_safe_load_key("qwen_clone.project_dir", "subprojects/qwen-comfy-local-clone")),
        config_path=_resolve_config_path(_safe_load_key("qwen_clone.config_path", "subprojects/qwen-comfy-local-clone/config.chineseclick.json")),
        output_root=_resolve_config_path(_safe_load_key("qwen_clone.output_root", "output/dub")),
        use_vocal_stem=bool(_safe_load_key("qwen_clone.use_vocal_stem", True)),
        align_mode=str(_safe_load_key("qwen_clone.align.mode", "target_asr")),
        align_snap_window_ms=int(_safe_load_key("qwen_clone.align.snap_window_ms", 250) or 250),
        align_fade_ms=int(_safe_load_key("qwen_clone.align.fade_ms", 25) or 25),
        align_fallback=str(_safe_load_key("qwen_clone.align.fallback", "heuristic")),
        mix_background=bool(_safe_load_key("qwen_clone.mix_background", True)),
        background_gain_db=float(_safe_load_key("qwen_clone.background_gain_db", 0) or 0),
        keep_dry_track=bool(_safe_load_key("qwen_clone.keep_dry_track", True)),
        mix_fail_policy=str(_safe_load_key("qwen_clone.mix_fail_policy", "dry_only")),
    )


def _prepare_input_audio(
    *,
    settings: QwenCloneSettings,
    python_exe: Path,
    output_dir: Path,
) -> StemPreparationResult:
    if not settings.use_vocal_stem:
        return StemPreparationResult(
            input_audio_path=RAW_AUDIO_PATH,
            background_audio_path=None,
            status="skipped",
        )

    if not WORKER_SCRIPT.exists():
        error = f"Stem worker script was not found: {WORKER_SCRIPT}"
        _write_backend_log(output_dir / "backend.log", [error], append=True)
        return StemPreparationResult(
            input_audio_path=RAW_AUDIO_PATH,
            background_audio_path=None,
            status="failed",
            error=error,
        )

    command = [
        str(python_exe),
        str(WORKER_SCRIPT),
        "separate",
        "--input",
        str(RAW_AUDIO_PATH),
        "--vocal-output",
        str(VOCAL_AUDIO_PATH),
        "--background-output",
        str(BACKGROUND_AUDIO_PATH),
    ]
    completed = subprocess.run(
        command,
        cwd=str(APP_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0 or not VOCAL_AUDIO_PATH.exists() or not BACKGROUND_AUDIO_PATH.exists():
        error = completed.stderr.strip() or completed.stdout.strip() or "Stem separation failed."
        _write_backend_log(
            output_dir / "backend.log",
            [
                "Stem separation failed. Falling back to raw audio.",
                f"Command: {' '.join(command)}",
                "STDOUT:",
                completed.stdout.strip() or "(empty)",
                "STDERR:",
                completed.stderr.strip() or "(empty)",
            ],
            append=True,
        )
        return StemPreparationResult(
            input_audio_path=RAW_AUDIO_PATH,
            background_audio_path=None,
            status="failed",
            error=error[:400],
        )

    return StemPreparationResult(
        input_audio_path=VOCAL_AUDIO_PATH,
        background_audio_path=BACKGROUND_AUDIO_PATH,
        status="completed",
    )


def _finalize_mix(
    *,
    settings: QwenCloneSettings,
    python_exe: Path,
    dry_audio_path: Path,
    final_audio_path: Path,
    background_audio_path: Path | None,
    output_dir: Path,
    stem_error: str | None,
) -> tuple[str, str | None]:
    if not settings.mix_background:
        return "skipped", None

    if background_audio_path is None or not background_audio_path.exists():
        reason = stem_error or "Background stem is not available. Final output kept as dry dubbing."
        _write_backend_log(output_dir / "backend.log", [reason], append=True)
        return "dry_only", reason[:400]

    command = [
        str(python_exe),
        str(WORKER_SCRIPT),
        "mix",
        "--voice-input",
        str(dry_audio_path),
        "--background-input",
        str(background_audio_path),
        "--output",
        str(final_audio_path),
        "--background-gain-db",
        str(settings.background_gain_db),
    ]
    completed = subprocess.run(
        command,
        cwd=str(APP_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0 or not final_audio_path.exists():
        shutil.copy2(dry_audio_path, final_audio_path)
        error = completed.stderr.strip() or completed.stdout.strip() or "Background mix failed."
        _write_backend_log(
            output_dir / "backend.log",
            [
                "Background mix failed. Falling back to the dry dubbing track.",
                f"Command: {' '.join(command)}",
                "STDOUT:",
                completed.stdout.strip() or "(empty)",
                "STDERR:",
                completed.stderr.strip() or "(empty)",
            ],
            append=True,
        )
        return "dry_only", error[:400]
    return "completed", None


def _augment_manifest(
    *,
    manifest_path: Path,
    source_audio_path: Path,
    dry_audio_path: Path,
    final_audio_path: Path,
    background_audio_path: Path | None,
    stem_status: str,
    stem_error: str | None,
    mix_status: str,
    mix_error: str | None,
) -> None:
    if not manifest_path.exists():
        return
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return

    payload["chineseclick_bridge"] = {
        "source_audio_path": str(source_audio_path),
        "dry_audio_path": str(dry_audio_path) if dry_audio_path.exists() else None,
        "final_audio_path": str(final_audio_path) if final_audio_path.exists() else None,
        "background_audio_path": str(background_audio_path) if background_audio_path and background_audio_path.exists() else None,
        "stem_status": stem_status,
        "stem_error": stem_error,
        "mix_status": mix_status,
        "mix_error": mix_error,
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_bridge_metadata(manifest_path: Path) -> dict[str, Any] | None:
    if not manifest_path.exists():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    bridge_meta = payload.get("chineseclick_bridge")
    if isinstance(bridge_meta, dict):
        return bridge_meta
    return None


def _resolve_output_dir(file_uuid: str) -> Path:
    try:
        output_root = _load_settings().output_root
    except Exception:
        output_root = DEFAULT_DUB_ROOT
    return output_root / file_uuid


def _resolve_source_runtime(source_language: str | None) -> tuple[str, str]:
    requested_code = _resolve_language_code(source_language)
    if requested_code and requested_code != "auto":
        return TARGET_LANGUAGE_NAMES.get(requested_code, "Chinese"), requested_code

    detected_code = _resolve_language_code(_safe_load_key("whisper.detected_language"))
    if detected_code and detected_code in TARGET_LANGUAGE_NAMES:
        return TARGET_LANGUAGE_NAMES[detected_code], detected_code

    configured_code = _resolve_language_code(_safe_load_key("whisper.language"))
    if configured_code and configured_code in TARGET_LANGUAGE_NAMES:
        return TARGET_LANGUAGE_NAMES[configured_code], configured_code

    return "Chinese", "auto"


def _resolve_language_code(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower().replace("_", "-")
    if not normalized:
        return None
    if normalized in TARGET_LANGUAGE_NAMES:
        return normalized
    return LANGUAGE_ALIASES.get(normalized)


def _resolve_config_path(raw_value: str | None) -> Path:
    raw_text = str(raw_value or "").strip()
    if not raw_text:
        return APP_ROOT / "__missing__"
    path = Path(raw_text)
    if path.is_absolute():
        return path
    return APP_ROOT / path


def _resolve_optional_config_path(raw_value: str | None) -> Path | None:
    raw_text = str(raw_value or "").strip()
    if not raw_text:
        return None
    return _resolve_config_path(raw_text)


def _safe_load_key(key: str, default=None):
    try:
        return load_key(key)
    except Exception:
        return default


def _resolve_python_executable(settings: QwenCloneSettings) -> PythonLaunchResolution:
    notes: list[str] = []
    checked: set[Path] = set()

    if settings.python_exe is not None:
        checked.add(settings.python_exe)
        if settings.python_exe.exists():
            version = _read_python_version(settings.python_exe)
            if version and version >= (3, 12):
                return PythonLaunchResolution(settings.python_exe, notes)
            notes.extend(
                [
                    "Configured qwen_clone.python_exe is not a usable Python 3.12+ interpreter.",
                    str(settings.python_exe),
                ]
            )
        else:
            notes.extend(
                [
                    "Configured qwen_clone.python_exe was not found.",
                    str(settings.python_exe),
                ]
            )

    for candidate in _iter_fallback_python_candidates():
        if candidate in checked:
            continue
        checked.add(candidate)
        version = _read_python_version(candidate)
        if version and version >= (3, 12):
            notes.extend(
                [
                    "Falling back to an auto-detected Python interpreter for qwen clone.",
                    str(candidate),
                ]
            )
            return PythonLaunchResolution(candidate, notes)

    notes.append("No usable Python 3.12+ interpreter was found for qwen clone.")
    notes.append("Install qwen clone dependencies into a local Python 3.12+ environment or set qwen_clone.python_exe explicitly.")
    return PythonLaunchResolution(None, notes)


def _iter_fallback_python_candidates() -> list[Path]:
    candidates: list[Path] = []

    current_python = Path(sys.executable).resolve() if sys.executable else None
    if current_python and current_python.exists():
        candidates.append(current_python)

    discovered_python = shutil.which("python")
    if discovered_python:
        candidates.append(Path(discovered_python).resolve())

    try:
        completed = subprocess.run(
            ["where.exe", "python"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        completed = None

    if completed and completed.stdout:
        for raw_line in completed.stdout.splitlines():
            candidate = Path(raw_line.strip())
            if candidate.exists():
                candidates.append(candidate.resolve())

    unique_candidates: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        unique_candidates.append(candidate)
    return unique_candidates


def _read_python_version(python_exe: Path) -> tuple[int, int] | None:
    try:
        completed = subprocess.run(
            [
                str(python_exe),
                "-c",
                "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if completed.returncode != 0:
        return None

    version_text = completed.stdout.strip()
    if not version_text:
        return None
    parts = version_text.split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _build_project_env(project_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    project_src = project_dir / "src"
    python_path_parts = [str(project_src)]
    existing_python_path = env.get("PYTHONPATH")
    if existing_python_path:
        python_path_parts.append(existing_python_path)
    env["PYTHONPATH"] = os.pathsep.join(python_path_parts)
    return env


def _playback_asset_is_current(output_path: Path, source_media_path: Path, final_audio_path: Path) -> bool:
    if not output_path.exists():
        return False
    output_mtime = output_path.stat().st_mtime
    return output_mtime >= source_media_path.stat().st_mtime and output_mtime >= final_audio_path.stat().st_mtime


def _mux_dubbed_video(
    *,
    ffmpeg: str,
    source_media_path: Path,
    final_audio_path: Path,
    output_video_path: Path,
) -> str | None:
    output_video_path.parent.mkdir(parents=True, exist_ok=True)
    output_video_path.unlink(missing_ok=True)

    commands = [
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_media_path),
            "-i",
            str(final_audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(output_video_path),
        ],
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_media_path),
            "-i",
            str(final_audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(output_video_path),
        ],
    ]

    errors: list[str] = []
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=str(APP_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode == 0 and output_video_path.exists():
            return None
        errors.append(
            "\n".join(
                [
                    f"Command: {' '.join(command)}",
                    f"Exit code: {completed.returncode}",
                    "STDOUT:",
                    completed.stdout.strip() or "(empty)",
                    "STDERR:",
                    completed.stderr.strip() or "(empty)",
                ]
            )
        )
        output_video_path.unlink(missing_ok=True)

    return "\n\n".join(errors)


def _to_output_url(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        relative = path.relative_to(OUTPUT_DIR)
    except ValueError:
        return None
    return f"/output/{relative.as_posix()}"


def _write_backend_log(log_path: Path, lines: list[str], *, append: bool = False) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append and log_path.exists() else "w"
    content = "\n".join(line for line in lines if line is not None).strip()
    if not content:
        return
    with open(log_path, mode, encoding="utf-8") as handle:
        if append and log_path.stat().st_size > 0:
            handle.write("\n\n")
        handle.write(content)
        handle.write("\n")


def _summarize_log_error(log_path: Path) -> str | None:
    if not log_path.exists():
        return None
    try:
        lines = [line.strip() for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()]
    except OSError:
        return None
    for line in reversed(lines):
        if line:
            return line[:300]
    return None
