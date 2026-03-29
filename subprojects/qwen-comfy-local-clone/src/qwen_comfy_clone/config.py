from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SubtitleConfig:
    source_language: str = "zh"
    target_language: str = "English"
    pre_roll_ms: int = 120
    post_roll_ms: int = 160


@dataclass(slots=True)
class ReferenceConfig:
    mode: str = "global"
    min_ms: int = 2500
    max_ms: int = 12000
    target_ms: int = 15000
    clip_count: int = 4
    join_gap_ms: int = 120
    ref_audio_max_seconds: float = 30.0


@dataclass(slots=True)
class ASRConfig:
    backend: str = "faster_whisper"
    model_name: str = "small"
    model_path: str | None = None
    local_files_only: bool = True
    device: str = "cuda"
    compute_type: str = "float16"
    language: str | None = "zh"


@dataclass(slots=True)
class TTSConfig:
    model_id: str = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
    model_path: str | None = None
    comfyui_models_dir: str | None = None
    local_files_only: bool = True
    device: str = "cuda:0"
    dtype: str = "bfloat16"
    attn_implementation: str = "auto"
    max_new_tokens: int | None = None
    do_sample: bool = False
    seed: int | None = 1234
    temperature: float | None = None
    top_k: int | None = None
    top_p: float | None = None
    repetition_penalty: float | None = None


@dataclass(slots=True)
class AlignmentConfig:
    mode: str = "target_asr"
    snap_window_ms: int = 250
    fade_ms: int = 25
    fallback: str = "heuristic"


@dataclass(slots=True)
class OutputConfig:
    lock_timeline: bool = True
    stretch_min_ratio: float = 0.85
    stretch_max_ratio: float = 1.35


@dataclass(slots=True)
class ProjectConfig:
    subtitle: SubtitleConfig = field(default_factory=SubtitleConfig)
    reference: ReferenceConfig = field(default_factory=ReferenceConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    alignment: AlignmentConfig = field(default_factory=AlignmentConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    @classmethod
    def from_file(cls, path: Path) -> "ProjectConfig":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            subtitle=SubtitleConfig(**raw.get("subtitle", {})),
            reference=ReferenceConfig(**raw.get("reference", {})),
            asr=ASRConfig(**raw.get("asr", {})),
            tts=TTSConfig(**raw.get("tts", {})),
            alignment=AlignmentConfig(**raw.get("alignment", {})),
            output=OutputConfig(**raw.get("output", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
