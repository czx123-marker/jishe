from __future__ import annotations

import argparse
import re
import threading
import time
import traceback
from pathlib import Path
from queue import Empty, Queue
from typing import Iterator

import gradio as gr

from qwen_comfy_clone.config import ProjectConfig
from qwen_comfy_clone.languages import known_languages
from qwen_comfy_clone.local_models import resolve_local_model_source
from qwen_comfy_clone.logging_utils import ensure_console_logging, get_logger, run_log_capture
from qwen_comfy_clone.pipeline import SubtitleVoiceClonePipeline
from qwen_comfy_clone.subtitles import prepare_paired_srt_input
from qwen_comfy_clone.tts import LocalQwenTTS


logger = get_logger("webui")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
ASR_LANGUAGE_CHOICES = ["auto", "zh", "en", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"]
PREFERRED_LOCAL_MODEL_PATHS = [
    Path(r"C:\Users\sc57\.cache\modelscope\hub\models\Qwen\Qwen3-TTS-12Hz-1___7B-Base"),
]


def _detect_default_tts_model_path() -> str:
    for candidate in PREFERRED_LOCAL_MODEL_PATHS:
        if candidate.exists():
            return str(candidate)
    try:
        return resolve_local_model_source(DEFAULT_MODEL_ID, None, local_files_only=True)
    except Exception:
        return ""


def _build_config(
    *,
    model_id: str,
    model_path: str,
    comfyui_models_dir: str,
    allow_online: bool,
    source_language: str,
    target_language: str,
    asr_language: str,
    asr_model_path: str,
    device: str,
    dtype: str,
    attn_implementation: str,
    reference_mode: str,
    ref_audio_max_seconds: float,
    do_sample: bool,
    seed: int,
) -> ProjectConfig:
    config = ProjectConfig()
    config.subtitle.source_language = source_language
    config.subtitle.target_language = target_language
    config.asr.language = None if asr_language.lower() == "auto" else asr_language
    config.asr.local_files_only = not allow_online
    config.tts.local_files_only = not allow_online
    config.tts.model_id = model_id.strip() or config.tts.model_id
    config.tts.model_path = model_path.strip() or None
    config.tts.comfyui_models_dir = comfyui_models_dir.strip() or None
    config.asr.model_path = asr_model_path.strip() or None
    config.tts.device = device.strip() or config.tts.device
    config.tts.dtype = dtype
    config.tts.attn_implementation = attn_implementation.strip() or config.tts.attn_implementation
    config.reference.mode = reference_mode
    config.reference.ref_audio_max_seconds = ref_audio_max_seconds
    config.tts.do_sample = do_sample
    config.tts.seed = seed
    return config


def _make_output_dir(output_root: str, run_name: str) -> Path:
    root = Path(output_root.strip() or (PROJECT_ROOT / "runs" / "webui")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    if run_name.strip():
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", run_name.strip()).strip("-") or "webui-run"
    else:
        slug = time.strftime("%Y%m%d-%H%M%S")
    output_dir = root / slug
    suffix = 1
    while output_dir.exists():
        output_dir = root / f"{slug}-{suffix:02d}"
        suffix += 1
    return output_dir


def _drain_log_queue(log_queue: Queue[str], log_lines: list[str]) -> None:
    while True:
        try:
            log_lines.append(log_queue.get_nowait())
        except Empty:
            break


def run_pipeline_stream(
    audio_path: str | None,
    source_srt_path: str | None,
    target_srt_path: str | None,
    model_id: str,
    model_path: str,
    comfyui_models_dir: str,
    allow_online: bool,
    source_language: str,
    target_language: str,
    asr_language: str,
    asr_model_path: str,
    device: str,
    dtype: str,
    attn_implementation: str,
    reference_mode: str,
    ref_audio_max_seconds: float,
    do_sample: bool,
    seed: int,
    output_root: str,
    run_name: str,
) -> Iterator[tuple[str, str | None, str, str | None, str | None, str]]:
    ensure_console_logging()
    if not audio_path:
        yield ("缺少源音频文件", None, "", None, None, "")
        return
    if not source_srt_path:
        yield ("缺少源语言 SRT 文件", None, "", None, None, "")
        return
    if not target_srt_path:
        yield ("缺少目标语言 SRT 文件", None, "", None, None, "")
        return

    config = _build_config(
        model_id=model_id,
        model_path=model_path,
        comfyui_models_dir=comfyui_models_dir,
        allow_online=allow_online,
        source_language=source_language,
        target_language=target_language,
        asr_language=asr_language,
        asr_model_path=asr_model_path,
        device=device,
        dtype=dtype,
        attn_implementation=attn_implementation,
        reference_mode=reference_mode,
        ref_audio_max_seconds=ref_audio_max_seconds,
        do_sample=do_sample,
        seed=seed,
    )
    output_dir = _make_output_dir(output_root, run_name)
    paired_subtitles_dir = prepare_paired_srt_input(Path(source_srt_path), Path(target_srt_path), output_dir)
    log_path = output_dir / "backend.log"
    log_queue: Queue[str] = Queue()
    log_lines: list[str] = []
    state: dict[str, object] = {"result": None, "error": None}

    def worker() -> None:
        with run_log_capture(log_path, queue=log_queue):
            try:
                logger.info("WebUI 任务开始")
                logger.info("当前使用双语 SRT：源语言=%s，目标语言=%s", source_srt_path, target_srt_path)
                pipeline = SubtitleVoiceClonePipeline(config)
                state["result"] = pipeline.run(
                    audio_path=Path(audio_path),
                    subtitle_path=paired_subtitles_dir,
                    output_dir=output_dir,
                    target_language=target_language,
                )
                logger.info("WebUI 任务已完成")
            except Exception:
                state["error"] = traceback.format_exc()
                logger.exception("WebUI 任务失败")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    while thread.is_alive() or not log_queue.empty():
        _drain_log_queue(log_queue, log_lines)
        yield (
            "运行中",
            None,
            str(output_dir),
            None,
            str(log_path) if log_path.exists() else None,
            "\n".join(log_lines),
        )
        time.sleep(0.25)

    _drain_log_queue(log_queue, log_lines)
    logs_text = "\n".join(log_lines)
    if state["error"]:
        yield (
            "运行失败",
            None,
            str(output_dir),
            None,
            str(log_path) if log_path.exists() else None,
            logs_text,
        )
        return

    result = state["result"]
    if result is None:
        yield (
            "运行失败：未返回结果",
            None,
            str(output_dir),
            None,
            str(log_path) if log_path.exists() else None,
            logs_text,
        )
        return

    yield (
        "运行完成",
        str(result.final_audio_path),
        str(output_dir),
        str(result.manifest_path),
        str(log_path),
        logs_text,
    )


def resolve_model_source_ui(
    model_id: str,
    model_path: str,
    comfyui_models_dir: str,
    allow_online: bool,
    device: str,
    dtype: str,
    attn_implementation: str,
    ref_audio_max_seconds: float,
) -> str:
    config = ProjectConfig()
    config.tts.model_id = model_id.strip() or config.tts.model_id
    config.tts.model_path = model_path.strip() or None
    config.tts.comfyui_models_dir = comfyui_models_dir.strip() or None
    config.tts.local_files_only = not allow_online
    config.tts.device = device.strip() or config.tts.device
    config.tts.dtype = dtype
    config.tts.attn_implementation = attn_implementation.strip() or config.tts.attn_implementation
    tts = LocalQwenTTS(config.tts, ref_audio_max_seconds=ref_audio_max_seconds)
    return tts.resolve_model_source()


def build_demo() -> gr.Blocks:
    default_model_path = _detect_default_tts_model_path()
    with gr.Blocks(title="Qwen3-TTS 本地配音测试台") as demo:
        gr.Markdown(
            """
            # Qwen3-TTS 本地配音测试台
            本项目输入双语 SRT 文件：

            - `源语言 SRT`：作为参考音频对应的文本
            - `目标语言 SRT`：作为最终要合成的目标文本

            说明：
            - 默认工作流使用 `.srt`
            - 页面会实时显示后台日志
            - 每次运行都会在输出目录里写入 `backend.log`
            """
        )

        with gr.Row():
            audio_input = gr.Audio(label="源音频", type="filepath")

        with gr.Row():
            source_srt_input = gr.File(label="源语言 SRT（参考文本）", type="filepath", file_types=[".srt"])
            target_srt_input = gr.File(label="目标语言 SRT（目标文本）", type="filepath", file_types=[".srt"])

        with gr.Row():
            model_id = gr.Textbox(label="模型 ID", value=DEFAULT_MODEL_ID)
            model_path = gr.Textbox(label="本地模型路径", value=default_model_path, placeholder=r"C:\models\Qwen3-TTS-12Hz-1.7B-Base")
            comfyui_models_dir = gr.Textbox(label="ComfyUI 模型目录", placeholder=r"C:\ComfyUI\models\Qwen3-TTS")

        with gr.Row():
            allow_online = gr.Checkbox(label="允许在线回退", value=False)
            device = gr.Textbox(label="设备", value="cuda:0")
            dtype = gr.Dropdown(label="精度", choices=["float16", "bfloat16", "float32"], value="bfloat16")
            attn_implementation = gr.Dropdown(label="注意力实现", choices=["auto", "sdpa", "eager", "flash_attention_2"], value="sdpa")

        with gr.Row():
            source_language = gr.Dropdown(label="源语言", choices=known_languages(), value="Chinese")
            target_language = gr.Dropdown(label="目标语言", choices=known_languages(), value="English")
            asr_language = gr.Dropdown(label="ASR 语言", choices=ASR_LANGUAGE_CHOICES, value="zh")
            asr_model_path = gr.Textbox(label="ASR 模型路径", placeholder=r"C:\models\faster-whisper-small")

        with gr.Row():
            reference_mode = gr.Dropdown(label="参考音频模式", choices=["global", "per_segment"], value="global")
            ref_audio_max_seconds = gr.Number(label="参考音频最长秒数", value=30.0, precision=2)
            do_sample = gr.Checkbox(label="启用采样", value=False)
            seed = gr.Number(label="随机种子", value=1234, precision=0)

        with gr.Row():
            output_root = gr.Textbox(label="输出根目录", value=str(PROJECT_ROOT / "runs" / "webui"))
            run_name = gr.Textbox(label="任务名称", placeholder="可选，不填则按时间生成")

        with gr.Row():
            resolve_button = gr.Button("解析模型路径", variant="secondary")
            run_button = gr.Button("开始运行", variant="primary")

        resolved_model_source = gr.Textbox(label="解析后的模型路径", lines=2)
        status = gr.Textbox(label="运行状态", value="空闲")
        final_audio = gr.Audio(label="最终音频", type="filepath")
        output_dir = gr.Textbox(label="输出目录")
        manifest_file = gr.File(label="结果清单 manifest.json")
        backend_log_file = gr.File(label="后台日志 backend.log")
        logs = gr.Textbox(label="后台实时日志", lines=24, autoscroll=True)

        resolve_button.click(
            fn=resolve_model_source_ui,
            inputs=[
                model_id,
                model_path,
                comfyui_models_dir,
                allow_online,
                device,
                dtype,
                attn_implementation,
                ref_audio_max_seconds,
            ],
            outputs=[resolved_model_source],
        )
        run_button.click(
            fn=run_pipeline_stream,
            inputs=[
                audio_input,
                source_srt_input,
                target_srt_input,
                model_id,
                model_path,
                comfyui_models_dir,
                allow_online,
                source_language,
                target_language,
                asr_language,
                asr_model_path,
                device,
                dtype,
                attn_implementation,
                reference_mode,
                ref_audio_max_seconds,
                do_sample,
                seed,
                output_root,
                run_name,
            ],
            outputs=[
                status,
                final_audio,
                output_dir,
                manifest_file,
                backend_log_file,
                logs,
            ],
        )
    return demo


def launch_webui(
    *,
    server_name: str = "127.0.0.1",
    server_port: int = 7860,
    share: bool = False,
    inbrowser: bool = False,
) -> None:
    ensure_console_logging()
    logger.info("正在启动 WebUI：%s:%s", server_name, server_port)
    demo = build_demo()
    demo.queue(default_concurrency_limit=1).launch(
        server_name=server_name,
        server_port=server_port,
        share=share,
        inbrowser=inbrowser,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="启动 Qwen3-TTS 本地配音测试台 WebUI")
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--share", action="store_true")
    parser.add_argument("--inbrowser", action="store_true")
    args = parser.parse_args(argv)
    launch_webui(server_name=args.host, server_port=args.port, share=args.share, inbrowser=args.inbrowser)


if __name__ == "__main__":
    main()
