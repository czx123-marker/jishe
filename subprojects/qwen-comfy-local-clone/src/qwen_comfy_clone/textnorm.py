from __future__ import annotations

import re
import unicodedata

from qwen_comfy_clone.languages import resolve_language


SPACELESS_LANGUAGES = {"Chinese", "Japanese"}
SPACE_RE = re.compile(r"\s+")
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?])")
SPACE_AFTER_PUNCT_RE = re.compile(r"([,.;:!?])(?![\s\"')\]}]|$)")
CJK_INNER_SPACE_RE = re.compile(r"(?<=[\u4e00-\u9fff\u3040-\u30ff])\s+(?=[\u4e00-\u9fff\u3040-\u30ff])")
CJK_PUNCT_SPACE_RE = re.compile(r"\s*([，。！？；：])\s*")
REPEATED_CJK_CHAR_RE = re.compile(r"([\u4e00-\u9fff\u3040-\u30ff])\1{5,}")
REPEATED_LATIN_TOKEN_RE = re.compile(r"\b([A-Za-z]{2,})(?:\s+\1){3,}\b", re.IGNORECASE)


def normalize_tts_text(text: str, language: str | None, *, purpose: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""

    resolved = resolve_language(language or "English", allow_auto=False)
    normalized = unicodedata.normalize("NFKC", raw)
    normalized = (
        normalized.replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
        .replace("\ufeff", "")
    )
    normalized = SPACE_RE.sub(" ", normalized).strip()

    if resolved in SPACELESS_LANGUAGES:
        if purpose == "reference":
            normalized = normalized.replace(" ", "")
        normalized = CJK_INNER_SPACE_RE.sub("", normalized)
        normalized = CJK_PUNCT_SPACE_RE.sub(r"\1", normalized)
        return normalized.strip()

    normalized = SPACE_BEFORE_PUNCT_RE.sub(r"\1", normalized)
    normalized = SPACE_AFTER_PUNCT_RE.sub(r"\1 ", normalized)
    normalized = SPACE_RE.sub(" ", normalized).strip()
    return normalized


def looks_like_repetitive_or_broken_text(text: str, language: str | None) -> bool:
    normalized = normalize_tts_text(text, language, purpose="target")
    if not normalized:
        return False
    if REPEATED_CJK_CHAR_RE.search(normalized):
        return True
    if REPEATED_LATIN_TOKEN_RE.search(normalized):
        return True
    return False
