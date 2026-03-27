import os
import io
import json
import time
import requests
import librosa
import soundfile as sf
from rich import print as rprint
from core.utils import *
from core.utils.models import *

OUTPUT_LOG_DIR = "output/log"
def transcribe_audio_302(raw_audio_path: str, vocal_audio_path: str, start: float = None, end: float = None):
    os.makedirs(OUTPUT_LOG_DIR, exist_ok=True)
    LOG_FILE = f"{OUTPUT_LOG_DIR}/whisperx302_{start}_{end}.json"
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
        
    WHISPER_LANGUAGE = load_key("whisper.language")
    AUTO_DETECT_LANGUAGE = load_key("whisper.auto_detect_language") if load_key("whisper.auto_detect_language") is not None else False
    
    # 如果启用自动语言检测，清空语言设置
    if AUTO_DETECT_LANGUAGE:
        WHISPER_LANGUAGE = ""
        rprint(f"[cyan]🔍 Auto-detecting language...[/cyan]")
    
    update_key("whisper.language", WHISPER_LANGUAGE)
    url = "https://yunwu.ai/v1/audio/transcriptions"
    
    y, sr = librosa.load(vocal_audio_path, sr=16000)
    audio_duration = len(y) / sr
    
    if start is None or end is None:
        start = 0
        end = audio_duration
        
    start_sample = int(start * sr)
    end_sample = int(end * sr)
    y_slice = y[start_sample:end_sample]
    
    audio_buffer = io.BytesIO()
    sf.write(audio_buffer, y_slice, sr, format='WAV', subtype='PCM_16')
    audio_buffer.seek(0)
    
    # 修改1: 更改文件参数名为'file'
    files = [('file', ('audio_slice.wav', audio_buffer, 'application/octet-stream'))]
    
    # 修改2: 更新payload以符合yunwu.ai API要求
    payload = {
        "model": "whisper-1",  # 必填参数
        "response_format": "verbose_json",  # 选择包含更多信息的输出格式
        "temperature": 0,  # 默认值0
        "timestamp_granularities[]": "segment" # 只请求确认支持的句子/段落级别时间戳
    }
    
    # 只有当不启用自动检测且指定了语言时，才添加language参数
    if not AUTO_DETECT_LANGUAGE and WHISPER_LANGUAGE:
        payload["language"] = WHISPER_LANGUAGE
    
    start_time = time.time()
    headers = {'Authorization': f'Bearer {load_key("whisper.whisperX_302_api_key")}'}
    response = requests.request("POST", url, headers=headers, data=payload, files=files)
    
    # 修改3: 处理响应格式差异
    if response.status_code == 200:
        try:
            response_json = response.json()
            
         # 如果启用自动语言检测，检查响应中是否包含语言信息
            if AUTO_DETECT_LANGUAGE:
                # 注意：yunwu.ai API可能不直接返回检测到的语言
                # 这里我们需要使用大模型来分析转录文本并识别语言
                if 'text' in response_json and response_json['text'].strip():
                    detected_language = detect_language_with_llm(response_json['text'])
                    if detected_language:
                        rprint(f"[green]✓ Detected language: {detected_language}[/green]")
                        update_key("whisper.detected_language", detected_language)
            
            # 检查API是否返回了有效的 segments
            if 'segments' not in response_json or not response_json.get('segments'):
                rprint(f"[bold red]错误：API响应中缺少 'segments'。[/bold red]")
                rprint(f"[bold yellow]收到的响应: {json.dumps(response_json, indent=2, ensure_ascii=False)}[/bold yellow]")
                raise ValueError("ASR API response is missing segments.")
            
            # 检查是否存在word-level时间戳，如果不存在则打印警告
            if 'words' not in response_json['segments'][0]:
                rprint(f"[bold yellow]警告：API未返回单词级时间戳。将继续处理句子/段落级时间戳。[/bold yellow]")
            
            if start is not None:
                for segment in response_json.get('segments', []):
                    if 'start' in segment:
                        segment['start'] += start
                    if 'end' in segment:
                        segment['end'] += start
                    # 确保words字段存在，如果不存在则创建空列表
                    if 'words' not in segment:
                        segment['words'] = []
                    for word in segment.get('words', []):
                        if 'start' in word:
                            word['start'] += start
                        if 'end' in word:
                            word['end'] += start
            
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(response_json, f, indent=4, ensure_ascii=False)
            
            elapsed_time = time.time() - start_time
            rprint(f"[green]✓ Transcription completed in {elapsed_time:.2f} seconds[/green]")
            return response_json
        except json.JSONDecodeError:
            rprint(f"[red]✗ Failed to parse response: {response.text}[/red]")
            raise ValueError("Failed to parse ASR response JSON.")
    else:
        rprint(f"[red]✗ API request failed with status code: {response.status_code}, message: {response.text}[/red]")
        raise RuntimeError(f"ASR API error {response.status_code}: {response.text}")

    # 使用LLM检测文本语言
def detect_language_with_llm(text):
    try:
        prompt = f"""Detect the language of the following text and return only the ISO 639-1 language code (e.g., 'en' for English, 'zh' for Chinese):\n\n{text[:100]}..."""
        response = ask_gpt(prompt, log_title="language_detection")
        # 提取并标准化语言代码
        lang_code = response.strip().lower()
        # 简单验证常见语言代码
        common_langs = ['en', 'zh', 'ja', 'ko', 'fr', 'de', 'es', 'it', 'pt', 'ru', 'ar']
        if lang_code in common_langs:
            return lang_code
        # 如果返回格式不标准，提取第一个单词
        return lang_code.split()[0]
    except Exception as e:
        rprint(f"[yellow]⚠️ Failed to detect language: {e}[/yellow]")
        return "en"  # 默认返回英语

if __name__ == "__main__":  
    # 这里应该有实际的音频文件路径
    _RAW_AUDIO_FILE = "path/to/audio.wav"
    result = transcribe_audio_302(_RAW_AUDIO_FILE, _RAW_AUDIO_FILE)
    rprint(result)
