from __future__ import annotations

import os
import re
from pathlib import Path


MODEL_FOLDERS: dict[str, str] = {
    "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice": "Qwen3-TTS-12Hz-1.7B-CustomVoice",
    "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign": "Qwen3-TTS-12Hz-1.7B-VoiceDesign",
    "Qwen/Qwen3-TTS-12Hz-1.7B-Base": "Qwen3-TTS-12Hz-1.7B-Base",
    "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice": "Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "Qwen/Qwen3-TTS-12Hz-0.6B-Base": "Qwen3-TTS-12Hz-0.6B-Base",
}
MODEL_WEIGHT_FILES: tuple[str, ...] = (
    "model.safetensors",
    "model.safetensors.index.json",
    "pytorch_model.bin",
    "pytorch_model.bin.index.json",
)


def huggingface_cache_root() -> Path:
    env_value = os.getenv("HF_HUB_CACHE") or os.getenv("HUGGINGFACE_HUB_CACHE")
    if env_value:
        return Path(env_value)
    return Path.home() / ".cache" / "huggingface" / "hub"


def modelscope_cache_root() -> Path:
    env_value = os.getenv("MODELSCOPE_CACHE")
    if env_value:
        return Path(env_value)
    return Path.home() / ".cache" / "modelscope" / "hub" / "models"


def repo_id_to_hf_cache_dir(repo_id: str, cache_root: Path | None = None) -> Path:
    return (cache_root or huggingface_cache_root()) / f"models--{repo_id.replace('/', '--')}"


def resolve_hf_snapshot(repo_id: str, cache_root: Path | None = None) -> Path | None:
    repo_dir = repo_id_to_hf_cache_dir(repo_id, cache_root=cache_root)
    refs_main = repo_dir / "refs" / "main"
    if refs_main.exists():
        revision = refs_main.read_text(encoding="utf-8").strip()
        candidate = repo_dir / "snapshots" / revision
        if candidate.exists():
            return candidate

    snapshots_dir = repo_dir / "snapshots"
    if not snapshots_dir.exists():
        return None

    snapshots = [item for item in snapshots_dir.iterdir() if item.is_dir()]
    if not snapshots:
        return None
    return max(snapshots, key=lambda item: item.stat().st_mtime)


def resolve_modelscope_snapshot(repo_id: str, cache_root: Path | None = None) -> Path | None:
    if "/" not in repo_id:
        return None
    org_name, model_name = repo_id.split("/", maxsplit=1)
    org_dir = (cache_root or modelscope_cache_root()) / org_name
    if not org_dir.exists():
        return None
    target = _canonical(model_name)
    candidates = [item for item in org_dir.iterdir() if item.is_dir() and _canonical(item.name) == target]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime)


def resolve_comfyui_model_path(repo_id: str, models_dir: str | Path | None) -> Path | None:
    if not models_dir:
        return None
    root = Path(models_dir).expanduser()
    folder_name = MODEL_FOLDERS.get(repo_id, repo_id.replace("/", "_"))
    candidate = root / folder_name
    if candidate.exists() and any(candidate.iterdir()):
        return candidate
    return None


def resolve_local_model_source(
    model_id: str,
    explicit_model_path: str | Path | None,
    *,
    local_files_only: bool,
    comfyui_models_dir: str | Path | None = None,
    hf_cache_root: Path | None = None,
    modelscope_root: Path | None = None,
) -> str:
    for value in (explicit_model_path, model_id):
        if value is None:
            continue
        path = Path(value).expanduser()
        if path.exists():
            return str(path)

    comfyui_path = resolve_comfyui_model_path(model_id, comfyui_models_dir)
    if comfyui_path is not None:
        return str(comfyui_path)

    hf_snapshot = resolve_hf_snapshot(model_id, cache_root=hf_cache_root)
    if hf_snapshot is not None and _is_usable_transformers_model_dir(hf_snapshot):
        return str(hf_snapshot)

    modelscope_snapshot = resolve_modelscope_snapshot(model_id, cache_root=modelscope_root)
    if modelscope_snapshot is not None:
        return str(modelscope_snapshot)

    if local_files_only:
        raise RuntimeError(
            f"Local Qwen3-TTS model not found for '{model_id}'. "
            "Set tts.model_path or tts.comfyui_models_dir, or pre-download the model into Hugging Face/ModelScope cache."
        )
    return model_id


def resolve_faster_whisper_source(
    model_name: str,
    explicit_model_path: str | Path | None,
    *,
    local_files_only: bool,
    hf_cache_root: Path | None = None,
) -> str:
    for value in (explicit_model_path, model_name):
        if value is None:
            continue
        path = Path(value).expanduser()
        if path.exists():
            return str(path)

    repo_id = model_name if "/" in model_name else f"Systran/faster-whisper-{model_name}"
    hf_snapshot = resolve_hf_snapshot(repo_id, cache_root=hf_cache_root)
    if hf_snapshot is not None:
        return str(hf_snapshot)

    if local_files_only:
        raise RuntimeError(
            f"Local faster-whisper model not found for '{model_name}'. "
            "Set asr.model_path or pre-download the model into Hugging Face cache."
        )
    return model_name


def _canonical(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _is_usable_transformers_model_dir(path: Path) -> bool:
    return any((path / name).exists() for name in MODEL_WEIGHT_FILES)
