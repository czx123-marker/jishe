from __future__ import annotations

from typing import Iterable


OFFICIAL_LANGUAGES: list[str] = [
    "Chinese",
    "English",
    "Japanese",
    "Korean",
    "German",
    "French",
    "Russian",
    "Portuguese",
    "Spanish",
    "Italian",
]

ALIASES: dict[str, str] = {
    "auto": "Auto",
    "zh": "Chinese",
    "zh-cn": "Chinese",
    "chinese": "Chinese",
    "mandarin": "Chinese",
    "en": "English",
    "english": "English",
    "ja": "Japanese",
    "jp": "Japanese",
    "japanese": "Japanese",
    "ko": "Korean",
    "kr": "Korean",
    "korean": "Korean",
    "de": "German",
    "german": "German",
    "fr": "French",
    "french": "French",
    "ru": "Russian",
    "russian": "Russian",
    "pt": "Portuguese",
    "pt-br": "Portuguese",
    "portuguese": "Portuguese",
    "es": "Spanish",
    "spanish": "Spanish",
    "it": "Italian",
    "italian": "Italian",
}


def known_languages() -> list[str]:
    return list(OFFICIAL_LANGUAGES)


def _key(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def build_lookup(supported: Iterable[str] | None) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for language in supported or []:
        canonical = language.strip()
        for variant in {
            _key(canonical),
            _key(canonical.replace(" ", "")),
            _key(canonical.replace(" ", "-")),
        }:
            lookup[variant] = canonical
    for alias, canonical in ALIASES.items():
        if canonical == "Auto":
            lookup.setdefault(alias, canonical)
        elif canonical in lookup.values():
            lookup[alias] = canonical
    return lookup


def resolve_language(
    value: str | None,
    supported: Iterable[str] | None = None,
    *,
    allow_auto: bool,
) -> str:
    if value is None or not value.strip():
        if allow_auto:
            return "Auto"
        raise ValueError("Language is required")

    lookup = build_lookup(supported or OFFICIAL_LANGUAGES)
    normalized = _key(value)
    if normalized in lookup:
        resolved = lookup[normalized]
    else:
        resolved = ALIASES.get(normalized)
        if resolved is None:
            options = list(supported or OFFICIAL_LANGUAGES)
            raise ValueError(f"Unsupported language '{value}'. Supported languages: {options}")

    if resolved == "Auto" and not allow_auto:
        raise ValueError("Auto language selection is not allowed here")
    return resolved
