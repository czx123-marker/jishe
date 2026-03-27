import os
from core.utils import *
from core.asr_backend.audio_preprocess import process_transcription, convert_video_to_audio, split_audio, save_results, normalize_audio_volume
from core.utils.models import *

@check_file_exists(_2_CLEANED_CHUNKS)
def transcribe():
    # 查找 output 目录中的视频文件
    video_extensions = {'.mp4', '.m4a', '.mp3', '.wav', '.mpeg', '.webm', '.mov'}
    video_file = None
    for file in os.listdir('output'):
        if os.path.splitext(file)[1].lower() in video_extensions:
            video_file = os.path.join('output', file)
            break
    
    if not video_file:
        raise FileNotFoundError("在 'output' 目录中未找到视频文件。")

    # 1. video to audio
    convert_video_to_audio(video_file)

    # 2. Demucs vocal separation:
    if load_key("demucs"):
        demucs_audio()
        vocal_audio = normalize_audio_volume(_VOCAL_AUDIO_FILE, _VOCAL_AUDIO_FILE, format="mp3")
    else:
        vocal_audio = _RAW_AUDIO_FILE # 在新项目中，我们直接使用原始音频

    # 3. Extract audio
    segments = split_audio(_RAW_AUDIO_FILE)
    
    # 4. Transcribe audio by clips
    runtime = load_key("whisper.runtime")
    if runtime == "local":
        from core.asr_backend.whisperX_local import transcribe_audio as ts
        rprint("[cyan]🎤 Transcribing audio with local model...[/cyan]")
    elif runtime == "cloud":
        from core.asr_backend.whisperX_302 import transcribe_audio_302 as ts
        rprint("[cyan]🎤 Transcribing audio with 302 API...[/cyan]")
    elif runtime == "elevenlabs":
        from core.asr_backend.elevenlabs_asr import transcribe_audio_elevenlabs as ts
        rprint("[cyan]🎤 Transcribing audio with ElevenLabs API...[/cyan]")

    MIN_SEGMENT_DURATION = 0.25  # seconds, guard against zero-length slices
    valid_results = []

    for start, end in segments:
        duration = max(0.0, (end or 0) - (start or 0))
        if duration < MIN_SEGMENT_DURATION:
            rprint(f"[yellow]⚠️ Skipping short segment {start:.2f}-{end:.2f}s (duration {duration:.2f}s).[/yellow]")
            continue

        try:
            result = ts(_RAW_AUDIO_FILE, vocal_audio, start, end)
        except Exception as exc:
            raise RuntimeError(f"ASR failed for segment {start:.2f}-{end:.2f}s: {exc}") from exc

        if not isinstance(result, dict) or 'segments' not in result:
            raise ValueError(f"ASR result missing 'segments' field for segment {start:.2f}-{end:.2f}s")

        if not result.get('segments'):
            rprint(f"[yellow]⚠️ ASR returned no content for segment {start:.2f}-{end:.2f}s.[/yellow]")
            continue

        valid_results.append(result)

    if not valid_results:
        rprint("[yellow]⚠️ No valid segments transcribed, falling back to whole audio.[/yellow]")
        fallback_result = ts(_RAW_AUDIO_FILE, vocal_audio)
        if not isinstance(fallback_result, dict) or 'segments' not in fallback_result:
            raise ValueError("Fallback ASR call failed to return segments.")
        valid_results = [fallback_result]

    # 5. Combine results
    combined_result = {'segments': []}
    for result in valid_results:
        combined_result['segments'].extend(result.get('segments', []))

    if not combined_result['segments']:
        raise ValueError("ASR transcription returned no segments across all chunks.")
    
    # 6. Process df
    df = process_transcription(combined_result)
    save_results(df)
        
if __name__ == "__main__":
    transcribe()
