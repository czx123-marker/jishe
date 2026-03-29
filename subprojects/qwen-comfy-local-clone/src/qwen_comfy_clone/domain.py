from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class Segment:
    index: int
    start_ms: int
    end_ms: int
    text: str
    source_text: str | None = None

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass(slots=True)
class SourceClip:
    segment: Segment
    audio_path: Path
    ref_text: str = ""
    ref_text_source: str = ""
    normalized_ref_text: str = ""
    member_segment_indexes: tuple[int, ...] = ()


@dataclass(slots=True)
class TimedToken:
    text: str
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass(slots=True)
class TimedTranscriptSegment:
    text: str
    start_ms: int
    end_ms: int
    words: list[TimedToken] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass(slots=True)
class TimedTranscript:
    text: str
    segments: list[TimedTranscriptSegment] = field(default_factory=list)


@dataclass(slots=True)
class SentenceAlignment:
    segment: Segment
    source_chunk_label: str
    output_audio_path: Path
    local_start_ms: int
    local_end_ms: int
    alignment_source: str
    alignment_score: float | None = None
    fitted_ratio: float | None = None
    fallback_reason: str | None = None
    drift_reason: str | None = None

    @property
    def local_duration_ms(self) -> int:
        return max(0, self.local_end_ms - self.local_start_ms)


@dataclass(slots=True)
class SynthClip:
    segment: Segment
    source_audio_path: Path
    output_audio_path: Path
    ref_text: str
    normalized_ref_text: str
    tts_text: str
    ref_text_source: str
    generated_duration_ms: int
    fitted_ratio: float | None = None
    member_segment_indexes: tuple[int, ...] = ()
    alignment_source: str | None = None
    alignment_score: float | None = None
    alignment_fallback_reason: str | None = None
    member_alignments: list[dict[str, object]] = field(default_factory=list)


@dataclass(slots=True)
class PipelineResult:
    final_audio_path: Path
    manifest_path: Path
