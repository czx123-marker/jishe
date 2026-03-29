from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qwen_comfy_clone.config import ProjectConfig
from qwen_comfy_clone.logging_utils import ensure_console_logging, get_logger, run_log_capture
from qwen_comfy_clone.pipeline import SubtitleVoiceClonePipeline
from qwen_comfy_clone.subtitles import prepare_paired_srt_input
from qwen_comfy_clone.tts import LocalQwenTTS


logger = get_logger("cli")


def _apply_overrides(config: ProjectConfig, args: argparse.Namespace) -> ProjectConfig:
    if getattr(args, "allow_online", False):
        config.tts.local_files_only = False
        config.asr.local_files_only = False
    if getattr(args, "model_path", None):
        config.tts.model_path = str(args.model_path)
    if getattr(args, "model_id", None):
        config.tts.model_id = args.model_id
    if getattr(args, "comfyui_models_dir", None):
        config.tts.comfyui_models_dir = str(args.comfyui_models_dir)
    if getattr(args, "asr_model_path", None):
        config.asr.model_path = str(args.asr_model_path)
    if getattr(args, "device", None):
        config.tts.device = args.device
    if getattr(args, "dtype", None):
        config.tts.dtype = args.dtype
    if getattr(args, "attn_implementation", None):
        config.tts.attn_implementation = args.attn_implementation
    if hasattr(args, "do_sample") and args.do_sample is not None:
        config.tts.do_sample = args.do_sample
    if getattr(args, "seed", None) is not None:
        config.tts.seed = args.seed
    if getattr(args, "reference_mode", None):
        config.reference.mode = args.reference_mode
    if getattr(args, "ref_audio_max_seconds", None) is not None:
        config.reference.ref_audio_max_seconds = args.ref_audio_max_seconds
    if getattr(args, "align_mode", None):
        config.alignment.mode = args.align_mode
    if getattr(args, "align_snap_window_ms", None) is not None:
        config.alignment.snap_window_ms = args.align_snap_window_ms
    if getattr(args, "align_fade_ms", None) is not None:
        config.alignment.fade_ms = args.align_fade_ms
    if getattr(args, "align_fallback", None):
        config.alignment.fallback = args.align_fallback
    return config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Clean local-first subtitle voice clone pipeline for Qwen3-TTS")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_runtime(target: argparse.ArgumentParser) -> None:
        target.add_argument("--config", type=Path, default=None, help="Optional JSON config file")
        target.add_argument("--model-id", type=str, default=None, help="Qwen3-TTS model id, default is the 1.7B Base model")
        target.add_argument("--model-path", type=Path, default=None, help="Explicit local Qwen3-TTS model directory")
        target.add_argument("--comfyui-models-dir", type=Path, default=None, help="Directory like ComfyUI/models/Qwen3-TTS")
        target.add_argument("--asr-model-path", type=Path, default=None, help="Optional local faster-whisper model directory")
        target.add_argument("--allow-online", action="store_true", help="Allow online model resolution if no local model is found")
        target.add_argument("--device", type=str, default=None, help="Torch device, for example cuda:0 or cpu")
        target.add_argument("--dtype", choices=["float16", "bfloat16", "float32"], default=None, help="Model dtype")
        target.add_argument("--attn-implementation", type=str, default=None, help="auto, sdpa, eager, or flash_attention_2")
        target.add_argument("--do-sample", dest="do_sample", action="store_true", default=None, help="Enable sampling")
        target.add_argument("--no-do-sample", dest="do_sample", action="store_false", help="Disable sampling")
        target.add_argument("--seed", type=int, default=None, help="Optional random seed")
        target.add_argument("--reference-mode", choices=["global", "per_segment"], default=None, help="Reuse one global prompt or build one per segment")
        target.add_argument("--ref-audio-max-seconds", type=float, default=None, help="Trim reference audio before prompt creation, -1 disables trimming")
        target.add_argument("--align-mode", choices=["target_asr"], default=None, help="Sentence alignment strategy after chunk synthesis")
        target.add_argument("--align-snap-window-ms", type=int, default=None, help="Snap sentence boundaries to low-energy points within this window")
        target.add_argument("--align-fade-ms", type=int, default=None, help="Fade in/out for sentence clips after chunk splitting")
        target.add_argument("--align-fallback", choices=["heuristic"], default=None, help="Fallback strategy when target-side alignment fails")

    run_parser = subparsers.add_parser("run", help="Run the end-to-end local pipeline")
    add_runtime(run_parser)
    run_parser.add_argument("--audio", type=Path, required=True, help="Source audio file")
    run_parser.add_argument("--subtitles", type=Path, default=None, help="Subtitle input path, usually a paired subtitle directory or a target subtitle file")
    run_parser.add_argument("--source-srt", type=Path, default=None, help="Source-language .srt used as reference text")
    run_parser.add_argument("--target-srt", type=Path, default=None, help="Target-language .srt used as synthesis text")
    run_parser.add_argument("--out-dir", type=Path, required=True, help="Output directory")
    run_parser.add_argument("--target-language", type=str, default=None, help="Target language, for example English or ja")
    run_parser.add_argument("--source-language", type=str, default=None, help="Source language label for reference text normalization")
    run_parser.add_argument("--asr-language", type=str, default=None, help="Source language for ASR, or auto")

    languages_parser = subparsers.add_parser("list-languages", help="List languages supported by the selected model")
    add_runtime(languages_parser)

    source_parser = subparsers.add_parser("show-model-source", help="Show the exact local or remote model source to be used")
    add_runtime(source_parser)

    webui_parser = subparsers.add_parser("webui", help="Launch the local Gradio test UI")
    webui_parser.add_argument("--host", type=str, default="127.0.0.1", help="Bind host")
    webui_parser.add_argument("--port", type=int, default=7860, help="Bind port")
    webui_parser.add_argument("--share", action="store_true", help="Enable Gradio share link")
    webui_parser.add_argument("--inbrowser", action="store_true", help="Open the browser automatically")

    return parser


def main(argv: list[str] | None = None) -> None:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list or args_list[0] not in {"run", "list-languages", "show-model-source", "webui"}:
        args_list = ["run", *args_list]

    args = build_parser().parse_args(args_list)
    ensure_console_logging()
    config = ProjectConfig.from_file(args.config) if args.config else ProjectConfig()
    config = _apply_overrides(config, args)

    if args.command == "webui":
        from qwen_comfy_clone.webui import launch_webui

        launch_webui(server_name=args.host, server_port=args.port, share=args.share, inbrowser=args.inbrowser)
        return

    if args.command == "list-languages":
        tts = LocalQwenTTS(config.tts, ref_audio_max_seconds=config.reference.ref_audio_max_seconds)
        for language in tts.get_supported_languages():
            print(language)
        return

    if args.command == "show-model-source":
        tts = LocalQwenTTS(config.tts, ref_audio_max_seconds=config.reference.ref_audio_max_seconds)
        print(tts.resolve_model_source())
        return

    if args.source_language:
        config.subtitle.source_language = args.source_language
    if args.target_language:
        config.subtitle.target_language = args.target_language
    if args.asr_language is not None:
        config.asr.language = None if args.asr_language.lower() == "auto" else args.asr_language

    subtitle_input = args.subtitles
    if args.source_srt or args.target_srt:
        if not args.source_srt or not args.target_srt:
            raise SystemExit("Using paired SRT input requires both --source-srt and --target-srt.")
        subtitle_input = prepare_paired_srt_input(args.source_srt, args.target_srt, args.out_dir)
        logger.info("Prepared paired SRT input directory: %s", subtitle_input)
    if subtitle_input is None:
        raise SystemExit("Provide --subtitles, or both --source-srt and --target-srt.")

    pipeline = SubtitleVoiceClonePipeline(config)
    log_path = args.out_dir / "backend.log"
    with run_log_capture(log_path):
        logger.info("Received CLI run request.")
        result = pipeline.run(
            audio_path=args.audio,
            subtitle_path=subtitle_input,
            output_dir=args.out_dir,
            target_language=args.target_language,
        )
    print(f"Final audio: {result.final_audio_path}")
    print(f"Manifest: {result.manifest_path}")
    print(f"Backend log: {log_path}")


if __name__ == "__main__":
    main()
