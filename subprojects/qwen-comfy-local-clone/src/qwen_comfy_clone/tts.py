from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Any
import random

import numpy as np
import torch

from qwen_comfy_clone.audio import load_audio, save_audio
from qwen_comfy_clone.config import TTSConfig
from qwen_comfy_clone.languages import known_languages, resolve_language
from qwen_comfy_clone.logging_utils import get_logger
from qwen_comfy_clone.local_models import resolve_local_model_source


@dataclass(slots=True)
class VoicePrompt:
    payload: Any
    ref_audio: Path
    ref_text: str


logger = get_logger("tts")
MIN_OUTPUT_CHUNK_MS = 10_000
MIN_DURATION_GUARD_TEXT_CHARS = 48
ESTIMATED_TEXT_CHARS_PER_SECOND = 15.0


def _torch_dtype(value: str) -> torch.dtype:
    normalized = value.strip().lower()
    if normalized in {"bfloat16", "bf16"}:
        return torch.bfloat16
    if normalized in {"float16", "fp16", "half"}:
        return torch.float16
    if normalized in {"float32", "fp32"}:
        return torch.float32
    raise ValueError(f"不支持的 dtype：{value}")


class LocalQwenTTS:
    def __init__(self, config: TTSConfig, *, ref_audio_max_seconds: float) -> None:
        self._config = config
        self._ref_audio_max_seconds = ref_audio_max_seconds
        self._engine = None

    def resolve_model_source(self) -> str:
        return resolve_local_model_source(
            self._config.model_id,
            self._config.model_path,
            local_files_only=self._config.local_files_only,
            comfyui_models_dir=self._config.comfyui_models_dir,
        )

    def resolve_language(self, language: str | None, *, allow_auto: bool) -> str:
        return resolve_language(language, self.get_supported_languages(), allow_auto=allow_auto)

    def get_supported_languages(self) -> list[str]:
        try:
            engine = self._load_engine()
        except RuntimeError as exc:
            if "CUDA device requested" in str(exc):
                return known_languages()
            raise
        getter = getattr(engine, "get_supported_languages", None)
        if not callable(getter):
            return known_languages()
        values = getter() or []
        resolved = []
        for value in values:
            try:
                resolved.append(resolve_language(str(value), known_languages(), allow_auto=False))
            except Exception:
                continue
        return resolved or known_languages()

    def build_voice_prompt(self, ref_audio: Path, ref_text: str) -> VoicePrompt:
        engine = self._load_engine()
        trimmed_audio = self._load_trimmed_reference(ref_audio)
        logger.info(
            "正在构建音色克隆提示：音频=%s，参考文本长度=%d",
            ref_audio,
            len(ref_text),
        )
        payload = engine.create_voice_clone_prompt(ref_audio=trimmed_audio, ref_text=ref_text)
        return VoicePrompt(payload=payload, ref_audio=ref_audio, ref_text=ref_text)

    def synthesize(
        self,
        *,
        text: str,
        language: str,
        output_audio: Path,
        prompt: VoicePrompt,
        target_duration_ms: int | None,
    ) -> int:
        engine = self._load_engine()
        self._apply_seed()
        estimated_duration_ms = self._estimate_text_duration_ms(text)
        kwargs = self._generation_kwargs(text=text, target_duration_ms=target_duration_ms)
        resolved_language = self.resolve_language(language, allow_auto=False)
        logger.info(
            "正在合成目标文本：长度=%d，语言=%s，目标槽位=%sms，文本估算时长=%sms",
            len(text),
            resolved_language,
            target_duration_ms if target_duration_ms is not None else "auto",
            estimated_duration_ms if estimated_duration_ms > 0 else "auto",
        )
        wavs, sample_rate = engine.generate_voice_clone(
            text=text,
            language=None if resolved_language == "Auto" else resolved_language,
            voice_clone_prompt=prompt.payload,
            **kwargs,
        )
        if not wavs:
            raise RuntimeError("Qwen3-TTS 未返回音频")
        waveform = wavs[0]
        save_audio(output_audio, waveform, sample_rate)
        logger.info("已保存合成音频：%s", output_audio)
        return int(len(waveform) / sample_rate * 1000)

    def _load_engine(self):
        if self._engine is not None:
            return self._engine
        self._require_cuda_if_needed()
        try:
            from qwen_tts import Qwen3TTSModel
        except ImportError as exc:
            raise RuntimeError("未安装 qwen-tts，请执行：python -m pip install qwen-tts") from exc

        model_source = self.resolve_model_source()
        attention = self._resolve_attention()
        self._engine = Qwen3TTSModel.from_pretrained(
            model_source,
            device_map=self._config.device,
            dtype=_torch_dtype(self._config.dtype),
            attn_implementation=attention,
        )
        logger.info(
            "Qwen3-TTS 模型加载完成：%s，设备=%s，dtype=%s，attention=%s",
            model_source,
            self._config.device,
            self._config.dtype,
            attention,
        )
        return self._engine

    def _resolve_attention(self) -> str:
        configured = self._config.attn_implementation.strip().lower()
        if configured != "auto":
            return configured
        try:
            import flash_attn  # noqa: F401
        except Exception:
            return "sdpa"
        return "flash_attention_2"

    def _generation_kwargs(self, *, text: str, target_duration_ms: int | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "do_sample": self._config.do_sample,
        }
        optional = {
            "temperature": self._config.temperature,
            "top_k": self._config.top_k,
            "top_p": self._config.top_p,
            "repetition_penalty": self._config.repetition_penalty,
        }
        kwargs.update({key: value for key, value in optional.items() if value is not None})

        duration_limit = None
        min_duration_floor = None
        duration_budget_ms = self._estimate_text_duration_ms(text)
        if target_duration_ms is not None and target_duration_ms > 0:
            duration_budget_ms = max(duration_budget_ms, target_duration_ms)
        if duration_budget_ms > 0:
            duration_limit = max(48, ceil(duration_budget_ms / 1000 * 12 * 1.2))
            min_duration_floor = max(24, ceil(min(duration_budget_ms, MIN_OUTPUT_CHUNK_MS) / 1000 * 12))
        if self._config.max_new_tokens is None:
            if duration_limit is not None:
                kwargs["max_new_tokens"] = duration_limit
        else:
            kwargs["max_new_tokens"] = min(self._config.max_new_tokens, duration_limit) if duration_limit else self._config.max_new_tokens
        if (
            min_duration_floor is not None
            and "max_new_tokens" in kwargs
            and len("".join(str(text or "").split())) <= MIN_DURATION_GUARD_TEXT_CHARS
        ):
            kwargs["min_new_tokens"] = min(int(kwargs["max_new_tokens"]), min_duration_floor)
        return kwargs

    def _estimate_text_duration_ms(self, text: str) -> int:
        compact_text = "".join(str(text or "").split())
        if not compact_text:
            return 0
        return max(1500, int(len(compact_text) / ESTIMATED_TEXT_CHARS_PER_SECOND * 1000))

    def _load_trimmed_reference(self, ref_audio: Path) -> tuple[np.ndarray, int]:
        waveform, sample_rate = load_audio(ref_audio)
        if self._ref_audio_max_seconds > 0:
            max_samples = int(self._ref_audio_max_seconds * sample_rate)
            if len(waveform) > max_samples:
                logger.info(
                    "参考音频过长，已裁剪：%s，%.2fs -> %.2fs",
                    ref_audio,
                    len(waveform) / sample_rate,
                    self._ref_audio_max_seconds,
                )
                waveform = waveform[:max_samples]
        return waveform, sample_rate

    def _apply_seed(self) -> None:
        if self._config.seed is None:
            return
        random.seed(self._config.seed)
        np.random.seed(self._config.seed)
        torch.manual_seed(self._config.seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(self._config.seed)

    def _require_cuda_if_needed(self) -> None:
        if self._config.device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError(
                "当前指定了 CUDA 设备，但当前 PyTorch 不支持 CUDA。"
            )
