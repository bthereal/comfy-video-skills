# comfy-video-skills

Two Claude Code skills (and the Python tools behind them) that generate talking-head videos **entirely locally** in [ComfyUI](https://github.com/comfyanonymous/ComfyUI). No paid APIs are used.

| Skill | What it makes |
|---|---|
| [`comfy-influencer-reel/`](comfy-influencer-reel/README.md) | Vertical 9:16 "influencer" reels (TikTok/Shorts/Reels style) on any topic, with named AI influencers who keep the same face and voice, plus any number of outfits and scenes. |
| [`youtube-channel/`](youtube-channel/README.md) | 16:9 YouTube videos of any length: a named AI narrator on camera, randomly intercut with generated b-roll footage, over one continuous narration. Optional 1080p upscale. |

`youtube-channel` imports the shared engine in `comfy-influencer-reel/reel.py` (ComfyUI job runner, uploads, Whisper checks, joiner), so **keep both folders side by side**.

## Requirements

**Hardware.** Developed and benchmarked on Windows 11 with an RTX 5070 Ti (16 GB VRAM) and 64 GB RAM. Video clips peak at about 15.5-15.8 GB VRAM. Less VRAM needs shorter clips and lower resolution.

**Software**
- ComfyUI (tested with v0.38), running on `127.0.0.1:8188`
- Python 3.13 (the same interpreter as ComfyUI), plus `pip install -r requirements.txt`
- ComfyUI custom node pack **[ComfyUI_Fill-ChatterBox](https://github.com/filliptm/ComfyUI_Fill-ChatterBox)** for narration (youtube-channel only)

**Models** (put these in your ComfyUI `models/` folders):

| Used for | Files |
|---|---|
| Portraits and stills (Z-Image Turbo) | `diffusion_models/z_image_turbo_bf16.safetensors`, `text_encoders/qwen_3_4b.safetensors`, `vae/ae.safetensors` |
| New outfits and scenes (Flux.2 Dev edit) | `diffusion_models/flux2_dev_fp8mixed.safetensors`, `text_encoders/mistral_3_small_flux2_bf16.safetensors`, `vae/flux2-vae.safetensors`, `loras/Flux_2-Turbo-LoRA_comfyui.safetensors` |
| Inventing a new voice (LTX-2) | `checkpoints/ltx-2-19b-distilled.safetensors`, `latent_upscale_models/ltx-2-spatial-upscaler-x2-1.0.safetensors` |
| Talking video (LTX-2.3) | `checkpoints/ltx-2.3-22b-dev-fp8.safetensors`, `loras/ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16.safetensors`, `latent_upscale_models/ltx-2.3-spatial-upscaler-x2-1.1.safetensors`, `text_encoders/gemma_3_12B_it_fp4_mixed.safetensors` |
| Reels: voice cloning in video (ID-LoRA) | `loras/ltx-2.3-id-lora-talkvid-3k.safetensors` |
| YouTube: image+audio lip-sync | `loras/gemma-3-12b-it-abliterated_lora_rank64_bf16.safetensors` |
| YouTube: narration (Chatterbox TTS) | `models/chatterbox/chatterbox/{ve.safetensors, t3_cfg.safetensors, s3gen.safetensors, tokenizer.json, conds.pt}` from [ResembleAI/chatterbox](https://huggingface.co/ResembleAI/chatterbox) |
| Spoken-word checks (Whisper) | [Systran/faster-whisper-small.en](https://huggingface.co/Systran/faster-whisper-small.en) in `~/.cache/faster-whisper/small.en/` |

The ComfyUI workflow templates are in each skill's `workflows/` folder (exported from the official ComfyUI template gallery).

## Setup

> **Required: set the `COMFYUI_DIR` environment variable** to the folder containing your ComfyUI install (the one with `main.py`). There's no default: `reel.py`, `yt.py`, `watchdog.py` and `start-comfyui.bat` all stop with an error if it's missing or doesn't point at ComfyUI.
>
> ```bat
> setx COMFYUI_DIR "C:\path\to\ComfyUI"
> ```
> `setx` saves it permanently but only for **new** terminals, so close and reopen your terminal (and VS Code) afterwards.

```bash
pip install -r requirements.txt
```

1. Set `COMFYUI_DIR` as above. `COMFYUI_URL` is optional (default `http://127.0.0.1:8188`).
2. Start ComfyUI by double-clicking `comfy-influencer-reel/start-comfyui.bat` (localhost only; keep the window open).
3. **As Claude Code skills:** copy or symlink both folders into `~/.claude/skills/`, then ask Claude for "a Maya reel about hydration, 15 seconds, at the gym" or "a 10 minute YouTube video on the Roswell incident narrated by Vale". **Without Claude:** use the CLIs directly (see each README).

## Repository layout

```
comfy-influencer-reel/
  SKILL.md            instructions Claude follows (also the most detailed docs)
  reel.py             reel CLI + shared ComfyUI engine
  start-comfyui.bat   starts ComfyUI (uses COMFYUI_DIR)
  workflows/          ComfyUI workflow templates
  influencers/<id>/   profile.json, portrait.png, voice.wav, looks/*.png
youtube-channel/
  SKILL.md
  yt.py               YouTube CLI (narration-first pipeline)
  narration.py        TTS, Whisper slicing, image+audio clips, streaming assembly, upscaler
  watchdog.py         unattended/overnight renders (restarts ComfyUI, resumes)
  after_then.py       chain a render after another process exits
  workflows/          Chatterbox TTS workflow
  narrators/<id>/     profile.json, portrait.png, voice clip(s)
  projects/<slug>/    plan.json (script + shot list) for each video
```

Generated output goes to `<ComfyUI>/output/reels/` and `<ComfyUI>/output/youtube/` and isn't part of this repo.

## Notes

- All influencers and narrators (Maya, James Whitford, Dr. Simon Vale) are fictional and AI-generated. Label published videos as AI-generated, per platform rules.
- Scripts about health, finance or history should be fact-checked before publishing. The YouTube skill presents conspiracy content as theory versus documented record.
