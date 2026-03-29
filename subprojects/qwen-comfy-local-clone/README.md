# Qwen Comfy Local Clone

This is a clean rewrite of the subtitle-driven voice clone pipeline. It keeps the old project in place, but the new implementation lives in `src/qwen_comfy_clone/` and uses a local-first `qwen-tts` backend inspired by `ComfyUI-Qwen3-TTS`.

## What it does

1. Load target subtitles from `SRT`, `VTT`, paired subtitle directories, or `JSON`.
2. Cut the source audio by subtitle timings.
3. Use source subtitle text when present, otherwise run `faster-whisper` to build `ref_text`.
4. Build either one global voice-clone prompt or one prompt per segment.
5. Synthesize target-language speech with a local Qwen3-TTS Base model.
6. Fit clips back onto the subtitle timeline and merge them into `final.wav`.

## Local model lookup

The new backend resolves the TTS model in this order:

1. `tts.model_path`
2. a direct filesystem path placed in `tts.model_id`
3. `tts.comfyui_models_dir/<mapped-model-folder>`
4. Hugging Face cache
5. ModelScope cache
6. online model id lookup only when `local_files_only=false`

## WebUI

This project now includes a lightweight Gradio WebUI for quick testing. The default workflow uses two `.srt` files:

- source-language `.srt`: reference transcript for the source audio
- target-language `.srt`: target transcript for synthesis

The page runs the full pipeline, shows backend logs in real time, and also writes them to `backend.log` in the run directory.

Start it with:

```powershell
python -m qwen_comfy_clone.webui --host 127.0.0.1 --port 7860
```

or:

```powershell
qwen-comfy-clone-webui --host 127.0.0.1 --port 7860
```

You can also launch it from the main CLI:

```powershell
qwen-comfy-clone webui --host 127.0.0.1 --port 7860
```

Each run writes:

- `final.wav`
- `manifest.json`
- `backend.log`

## Quick start

```powershell
python -m qwen_comfy_clone.cli run `
  --audio .\input\source.wav `
  --source-srt .\input\src.srt `
  --target-srt .\input\trans.srt `
  --out-dir .\runs\comfy-local `
  --target-language English `
  --source-language zh `
  --asr-language zh `
  --config .\config.comfy.local.example.json `
  --model-path C:\models\Qwen3-TTS-12Hz-1.7B-Base
```

If your model already lives in a ComfyUI tree:

```powershell
python -m qwen_comfy_clone.cli show-model-source `
  --config .\config.comfy.local.example.json `
  --comfyui-models-dir C:\ComfyUI\models\Qwen3-TTS `
  --model-id Qwen/Qwen3-TTS-12Hz-1.7B-Base
```

## Command entrypoint

If installed with `pip install -e .`, you can also use:

```powershell
qwen-comfy-clone run --help
```

For CLI runs, `backend.log` is also written inside `--out-dir`.
