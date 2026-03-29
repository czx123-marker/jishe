from __future__ import annotations

from pathlib import Path
from typing import Protocol

from qwen_comfy_clone.config import ASRConfig
from qwen_comfy_clone.domain import TimedToken, TimedTranscript, TimedTranscriptSegment
from qwen_comfy_clone.local_models import resolve_faster_whisper_source
from qwen_comfy_clone.logging_utils import get_logger


class ASRBackend(Protocol):
    def transcribe(self, audio_path: Path, language: str | None) -> str: ...

    def transcribe_timed(self, audio_path: Path, language: str | None) -> TimedTranscript: ...


logger = get_logger("asr")


class FasterWhisperASR:
    def __init__(self, config: ASRConfig) -> None:
        self._config = config
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return self._model

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed. Install it with: python -m pip install faster-whisper"
            ) from exc

        model_source = resolve_faster_whisper_source(
            self._config.model_name,
            self._config.model_path,
            local_files_only=self._config.local_files_only,
        )
        logger.info(
            "Loading faster-whisper model from %s on %s (%s).",
            model_source,
            self._config.device,
            self._config.compute_type,
        )
        self._model = WhisperModel(
            model_source,
            device=self._config.device,
            compute_type=self._config.compute_type,
        )
        return self._model

    def transcribe(self, audio_path: Path, language: str | None) -> str:
        return self.transcribe_timed(audio_path, language).text

    def transcribe_timed(self, audio_path: Path, language: str | None) -> TimedTranscript:
        model = self._load_model()
        normalized_language = None if language in {None, "", "auto", "Auto"} else language
        logger.info("Running timed ASR for %s (language=%s).", audio_path, normalized_language or "auto")
        segments, _ = model.transcribe(
            str(audio_path),
            language=normalized_language,
            vad_filter=True,
            word_timestamps=True,
        )

        transcript_segments: list[TimedTranscriptSegment] = []
        transcript_text_parts: list[str] = []
        for segment in segments:
            segment_text = " ".join(str(segment.text or "").split()).strip()
            if not segment_text:
                continue

            words: list[TimedToken] = []
            for word in list(getattr(segment, "words", []) or []):
                word_text = " ".join(str(getattr(word, "word", "") or "").split()).strip()
                if not word_text:
                    continue
                start_ms = _seconds_to_ms(getattr(word, "start", None), fallback=getattr(segment, "start", 0.0))
                end_ms = _seconds_to_ms(getattr(word, "end", None), fallback=getattr(segment, "end", 0.0))
                if end_ms <= start_ms:
                    end_ms = start_ms + 1
                words.append(TimedToken(text=word_text, start_ms=start_ms, end_ms=end_ms))

            start_ms = _seconds_to_ms(getattr(segment, "start", 0.0), fallback=0.0)
            end_ms = _seconds_to_ms(getattr(segment, "end", 0.0), fallback=getattr(segment, "start", 0.0))
            if end_ms <= start_ms:
                end_ms = start_ms + 1
            transcript_segments.append(
                TimedTranscriptSegment(
                    text=segment_text,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    words=words,
                )
            )
            transcript_text_parts.append(segment_text)

        transcript_text = " ".join(transcript_text_parts).strip()
        if not transcript_text:
            raise RuntimeError(f"ASR returned no text for {audio_path}")

        logger.info(
            "Timed ASR complete for %s with %d segments.",
            audio_path.name,
            len(transcript_segments),
        )
        return TimedTranscript(text=transcript_text, segments=transcript_segments)


def create_asr_backend(config: ASRConfig) -> ASRBackend:
    if config.backend == "faster_whisper":
        return FasterWhisperASR(config)
    raise ValueError(f"Unsupported ASR backend: {config.backend}")


def _seconds_to_ms(value, *, fallback: float) -> int:
    raw_value = fallback if value is None else value
    return int(round(float(raw_value) * 1000))
