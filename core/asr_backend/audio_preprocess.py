import os, subprocess, re
import pandas as pd
from typing import Dict, List, Tuple
from pydub import AudioSegment
from core.utils import *
from core.utils.models import *
from pydub import AudioSegment
from pydub.silence import detect_silence
from pydub.utils import mediainfo
from rich import print as rprint

SENTENCE_ENDINGS = set('。！？!?;；')
CLAUSE_SEPARATORS = set('，,、；;')
WORD_CHAR_RE = re.compile(r'[A-Za-z0-9]')
MIN_SEGMENT_PAUSE = 0.8
MIN_SEGMENT_DURATION = 0.35
MIN_SEGMENT_PAUSE = 0.8

def normalize_audio_volume(audio_path, output_path, target_db = -20.0, format = "wav"):
    audio = AudioSegment.from_file(audio_path)
    change_in_dBFS = target_db - audio.dBFS
    normalized_audio = audio.apply_gain(change_in_dBFS)
    normalized_audio.export(output_path, format=format)
    rprint(f"[green]✅ Audio normalized from {audio.dBFS:.1f}dB to {target_db:.1f}dB[/green]")
    return output_path

def convert_video_to_audio(video_file: str):
    os.makedirs(_AUDIO_DIR, exist_ok=True)
    if not os.path.exists(_RAW_AUDIO_FILE):
        rprint(f"[blue]🎬➡️🎵 Converting to high quality audio with FFmpeg ......[/blue]")
        subprocess.run([
            'ffmpeg', '-y', '-i', video_file, '-vn',
            '-c:a', 'libmp3lame', '-q:a', '2',
            '-ar', '44100',
            '-ac', '1', 
            '-metadata', 'encoding=UTF-8', _RAW_AUDIO_FILE
        ], check=True, stderr=subprocess.PIPE)
        rprint(f"[green]🎬➡️🎵 Converted <{video_file}> to <{_RAW_AUDIO_FILE}> with FFmpeg\n[/green]")

def get_audio_duration(audio_file: str) -> float:
    """Get the duration of an audio file using ffmpeg."""
    cmd = ['ffmpeg', '-i', audio_file]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _, stderr = process.communicate()
    output = stderr.decode('utf-8', errors='ignore')
    
    try:
        duration_str = [line for line in output.split('\n') if 'Duration' in line][0]
        duration_parts = duration_str.split('Duration: ')[1].split(',')[0].split(':')
        duration = float(duration_parts[0])*3600 + float(duration_parts[1])*60 + float(duration_parts[2])
    except Exception as e:
        print(f"[red]❌ Error: Failed to get audio duration: {e}[/red]")
        duration = 0
    return duration

def split_audio(audio_file: str, target_len: float = 30*60, win: float = 60) -> List[Tuple[float, float]]:
    ## 在 [target_len-win, target_len+win] 区间内用 pydub 检测静默，切分音频
    rprint(f"[blue]🎙️ Starting audio segmentation {audio_file} {target_len} {win}[/blue]")
    audio = AudioSegment.from_file(audio_file)
    duration = float(mediainfo(audio_file)["duration"])
    if duration <= target_len + win:
        return [(0, duration)]
    segments, pos = [], 0.0
    safe_margin = 0.5  # 静默点前后安全边界，单位秒

    while pos < duration:
        if duration - pos <= target_len:
            segments.append((pos, duration)); break

        threshold = pos + target_len
        ws, we = int((threshold - win) * 1000), int((threshold + win) * 1000)
        
        # 获取完整的静默区域
        silence_regions = detect_silence(audio[ws:we], min_silence_len=int(safe_margin*1000), silence_thresh=-30)
        silence_regions = [(s/1000 + (threshold - win), e/1000 + (threshold - win)) for s, e in silence_regions]
        # 筛选长度足够（至少1秒）且位置适合的静默区域
        valid_regions = [
            (start, end) for start, end in silence_regions 
            if (end - start) >= (safe_margin * 2) and threshold <= start + safe_margin <= threshold + win
        ]
        
        if valid_regions:
            start, end = valid_regions[0]
            split_at = start + safe_margin  # 在静默区域起始点后0.5秒处切分
        else:
            rprint(f"[yellow]⚠️ No valid silence regions found for {audio_file} at {threshold}s, using threshold[/yellow]")
            split_at = threshold
            
        segments.append((pos, split_at)); pos = split_at

    rprint(f"[green]🎙️ Audio split completed {len(segments)} segments[/green]")
    return segments

def _append_token(current: str, token: str) -> str:
    token = token.strip()
    if not token:
        return current
    if not current:
        return token
    if WORD_CHAR_RE.match(current[-1]) and WORD_CHAR_RE.match(token[0]):
        return f"{current} {token}"
    return current + token


def _split_segment_words(segment: Dict) -> List[Dict]:
    words = segment.get('words') or []
    if not isinstance(words, list) or not words:
        return []

    sentences = []
    current_text = ''
    sentence_start = None
    sentence_end = None
    last_end = None

    def flush():
        nonlocal current_text, sentence_start, sentence_end, last_end
        text = current_text.strip()
        if text:
            start_val = float(sentence_start) if sentence_start is not None else float(segment.get('start', 0.0))
            end_val = float(sentence_end) if sentence_end is not None else float(segment.get('end', 0.0))
            sentences.append({
                'start': start_val,
                'end': end_val,
                'text': text
            })
            last_end = end_val
        current_text = ''
        sentence_start = None
        sentence_end = None

    for word in words:
        if not isinstance(word, dict):
            continue
        token = (word.get('word') or '').strip()
        if not token:
            continue
        start = word.get('start')
        end = word.get('end')
        if sentence_start is None and start is not None:
            sentence_start = start
        if end is not None:
            sentence_end = end
        if last_end is not None and start is not None and (start - last_end) > MIN_SEGMENT_PAUSE and current_text:
            flush()
            sentence_start = start if start is not None else sentence_start
        current_text = _append_token(current_text, token)
        last_end = end if end is not None else last_end
        if token[-1] in SENTENCE_ENDINGS:
            flush()

    flush()
    return [seg for seg in sentences if seg['text']]




def _split_segment_text(segment: Dict) -> List[Dict]:
    text = (segment.get('text') or '').strip()
    if not text:
        return []

    start = float(segment.get('start', 0.0))
    end = float(segment.get('end', start))
    duration = max(end - start, 0.1)

    parts: List[str] = []
    buffer = ''

    for ch in text:
        buffer += ch
        if ch in SENTENCE_ENDINGS:
            parts.append(buffer.strip())
            buffer = ''
        elif ch in CLAUSE_SEPARATORS and len(buffer.strip()) >= 6:
            parts.append(buffer.strip())
            buffer = ''

    if buffer.strip():
        parts.append(buffer.strip())

    if len(parts) <= 1:
        return [{
            'start': start,
            'end': end,
            'text': text
        }]

    total_chars = sum(len(part) for part in parts)
    cursor = start
    results: List[Dict] = []

    for index, part in enumerate(parts):
        if index == len(parts) - 1:
            part_end = end
        else:
            ratio = len(part) / total_chars if total_chars else 1 / len(parts)
            part_duration = max(duration * ratio, MIN_SEGMENT_DURATION)
            part_end = min(end, cursor + part_duration)
        if part_end <= cursor:
            part_end = cursor + MIN_SEGMENT_DURATION
        if part_end > end:
            part_end = end
        results.append({
            'start': cursor,
            'end': part_end,
            'text': part
        })
        cursor = part_end

    return results


def _ensure_monotonic_timings(df: pd.DataFrame) -> None:
    if df.empty:
        return

    if df.at[0, 'end'] <= df.at[0, 'start']:
        df.at[0, 'end'] = round(df.at[0, 'start'] + MIN_SEGMENT_DURATION, 3)

    prev_end = df.at[0, 'end']
    for idx in range(1, len(df)):
        start = df.at[idx, 'start']
        if start < prev_end:
            start = prev_end
            df.at[idx, 'start'] = start
        end = df.at[idx, 'end']
        if end <= start:
            end = start + MIN_SEGMENT_DURATION
            df.at[idx, 'end'] = end
        prev_end = end
def process_transcription(result: Dict) -> pd.DataFrame:
    """Convert ASR response to DataFrame and validate content."""
    if not isinstance(result, dict):
        raise ValueError("Unexpected ASR result format; expected JSON object.")

    segments = result.get('segments', [])
    if segments is None:
        segments = []

    all_segments: List[Dict] = []
    for segment in segments:
        if not isinstance(segment, dict):
            rprint(f"[yellow]⚠️ Skipping malformed segment: {segment}[/yellow]")
            continue

        start = segment.get('start')
        end = segment.get('end')
        text = segment.get('text', '')
        if start is None or end is None:
            rprint(f"[yellow]⚠️ Segment missing timing information: {segment}[/yellow]")
            continue

        split_segments = _split_segment_words(segment)
        if not split_segments:
            split_segments = _split_segment_text({
                'start': float(start),
                'end': float(end),
                'text': (text or '').strip()
            })

        all_segments.extend(split_segments)

    df = pd.DataFrame(all_segments, columns=['start', 'end', 'text'])
    df = df.sort_values(['start', 'end'], kind='mergesort').reset_index(drop=True)
    _ensure_monotonic_timings(df)

    if df.empty:
        error_hint = result.get('error') or result.get('message') or result.get('text') or ''
        raise ValueError(f"ASR transcription returned no usable segments. {error_hint}")

    return df

def save_results(df: pd.DataFrame):
    """
    Saves the processed DataFrame to an Excel file.
    """
    os.makedirs('output/log', exist_ok=True)

    if 'text' not in df.columns:
        raise ValueError("Transcription results missing 'text' column.")

# Remove rows where 'text' is empty or just whitespace
    initial_rows = len(df)
    df = df[df['text'].fillna('').str.strip().str.len() > 0]
    removed_rows = initial_rows - len(df)
    if removed_rows > 0:
        rprint(f"[blue]ℹ️ Removed {removed_rows} row(s) with empty text.[/blue]")
    
    # Save to the raw chunks file, which is more appropriate for segment-level data
    df.to_excel(_2_CLEANED_CHUNKS, index=False)
    rprint(f"[green]📊 Segment-level results saved to {_2_CLEANED_CHUNKS}[/green]")

def save_language(language: str):
    update_key("whisper.detected_language", language)
