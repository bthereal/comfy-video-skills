# comfy-video-skills

Two Claude Code skills (and the Python tools behind them) that generate talking-head videos **entirely locally** in [ComfyUI](https://github.com/comfyanonymous/ComfyUI). No paid APIs are used. This is an experimental personal project to get generative AI video content working on a relatively low spec (for local AI generation) machine. Sharing the results in case it helps anyone. See below for the specs used, would recommend at least 16GB vram. This is not something I'll support long-term, was just a bit of fun.

| Skill | What it makes |
|---|---|
| [`comfy-influencer-reel/`](comfy-influencer-reel/README.md) | Vertical 9:16 "influencer" reels (TikTok/Shorts/Reels style) on any topic, with named AI influencers who keep the same face and voice, plus any number of outfits and scenes. |
| [`youtube-channel/`](youtube-channel/README.md) | 16:9 YouTube videos of any length: a named AI narrator on camera, randomly intercut with generated b-roll footage. Optional 1080p upscale. |

Both skills run the **same narration-first pipeline** on a shared engine and shared workflows:

1. **Narration:** [Chatterbox](https://huggingface.co/ResembleAI/chatterbox) speaks the whole script in the persona's cloned voice as one continuous recording, so speech never resets at a cut. Whisper checks every take for missing, repeated or extra words and re-records failures.
2. **Slicing:** Whisper word timings cut the narration into per-segment slices at natural pauses, sized to LTX's frame grid.
3. **Clips:** each slice gets an LTX-2.3 **image+audio** clip, lip-synced to the persona's portrait (or animating b-roll footage under voice-over).
4. **Assembly:** the clips are joined over the unbroken narration (streamed, so any length fits in RAM), with an optional upscale.

What a video is *about* (topic, script, setting, style, sound) lives entirely in its **plan file**, and who's speaking (face, voice, camera, lighting) lives in the **persona profile**. The code has no subject matter in it.

## Languages

English is the default. A persona can speak another language by choosing a voice engine in its `profile.json` → `tts`; nothing else in the pipeline changes (LTX lip-syncs any language).

| `tts.engine` | Use for | How it works |
|---|---|---|
| *(none)* | English | Chatterbox TTS in the ComfyUI node; whole script as one continuous take. |
| `chatterbox_mtl` | Chatterbox Multilingual's 23 languages, or a language with a fine-tune (e.g. Slovak) | Chatterbox Multilingual run in Python (optionally with fine-tuned `t3_weights`), cloning the persona's voice clip. Each segment is generated and checked separately. |
| `piper_vc` | Fallback for languages with no Chatterbox model | A native [Piper](https://github.com/OHF-Voice/piper1-gpl) voice says the script, then Chatterbox voice conversion re-voices it as the persona. Clear words, but flatter delivery. |

Example (a Slovak persona, using the community Slovak fine-tune):

```json
"tts": {
  "engine": "chatterbox_mtl",
  "language": "sk",
  "t3_weights": "chatterbox_sk/t3_sk_v2.2.safetensors",
  "voice": "voice.wav",
  "cfg_weight": 0.5, "exaggeration": 0.5, "temperature": 0.8
}
```

Write the persona's scripts in that language. Non-English word checks use Whisper `large-v3-turbo` (English uses `small.en`). Speaking rates differ: Slovak runs at about 2.75 words per second against English's ~3.

## Requirements

**Hardware.** Developed and benchmarked on Windows 11 with an RTX 5070 Ti (16 GB VRAM) and 64 GB RAM. Clips peak at about 15.5-15.8 GB VRAM. Less VRAM needs shorter clips and lower resolution.

**Software**
- ComfyUI (tested with v0.38), running on `127.0.0.1:8188`
- Python 3.13 (the same interpreter as ComfyUI), plus `pip install -r requirements.txt`
- ComfyUI custom node pack **[ComfyUI_Fill-ChatterBox](https://github.com/filliptm/ComfyUI_Fill-ChatterBox)**, **pinned to the tested commit `f7d7a16`** (2026-08-23). Custom nodes run arbitrary Python inside ComfyUI, so install the version that was reviewed rather than whatever is newest:

  ```bash
  cd "%COMFYUI_DIR%\custom_nodes"
  git clone https://github.com/filliptm/ComfyUI_Fill-ChatterBox comfyui_fill-chatterbox
  git -C comfyui_fill-chatterbox checkout f7d7a16187430abcaf91a3039b9c83aa9960816a
  pip install -r comfyui_fill-chatterbox/requirements.txt
  ```
  If you update it later, re-read its changes first, especially anything that downloads files or runs subprocesses. The reviewed version only downloads from the official `ResembleAI/chatterbox` Hugging Face repos and runs no shell commands.

**Models** (put these in your ComfyUI `models/` folders):

| Used for | Files |
|---|---|
| Portraits and stills (Z-Image Turbo) | `diffusion_models/z_image_turbo_bf16.safetensors`, `text_encoders/qwen_3_4b.safetensors`, `vae/ae.safetensors` |
| New outfits and scenes for influencers (Flux.2 Dev edit) | `diffusion_models/flux2_dev_fp8mixed.safetensors`, `text_encoders/mistral_3_small_flux2_bf16.safetensors`, `vae/flux2-vae.safetensors`, `loras/Flux_2-Turbo-LoRA_comfyui.safetensors` |
| Inventing a new persona's voice (LTX-2) | `checkpoints/ltx-2-19b-distilled.safetensors`, `latent_upscale_models/ltx-2-spatial-upscaler-x2-1.0.safetensors` |
| Talking clips (LTX-2.3 image+audio) | `checkpoints/ltx-2.3-22b-dev-fp8.safetensors`, `loras/ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16.safetensors`, `latent_upscale_models/ltx-2.3-spatial-upscaler-x2-1.1.safetensors`, `text_encoders/gemma_3_12B_it_fp4_mixed.safetensors`, `loras/gemma-3-12b-it-abliterated_lora_rank64_bf16.safetensors` |
| Narration (Chatterbox TTS) | `models/chatterbox/chatterbox/{ve.safetensors, t3_cfg.safetensors, s3gen.safetensors, tokenizer.json, conds.pt}` from [ResembleAI/chatterbox](https://huggingface.co/ResembleAI/chatterbox) |
| Word checks (Whisper) | [Systran/faster-whisper-small.en](https://huggingface.co/Systran/faster-whisper-small.en) in `~/.cache/faster-whisper/small.en/` |

Only for non-English personas (see [Languages](#languages)):

| Used for | Files |
|---|---|
| Other languages (`chatterbox_mtl`) | `models/chatterbox/chatterbox_multilingual/{ve.pt, t3_mtl23ls_v2.safetensors, s3gen.pt, grapheme_mtl_merged_expanded_v1.json, conds.pt, Cangjie5_TC.json}` from [ResembleAI/chatterbox](https://huggingface.co/ResembleAI/chatterbox) |
| Slovak fine-tune (optional) | `models/chatterbox/chatterbox_sk/t3_sk_v2.2.safetensors` from [pekiskol/chatterbox-tts-slovak](https://huggingface.co/pekiskol/chatterbox-tts-slovak) (MIT; a community model) |
| Voice conversion (`piper_vc`) | `models/chatterbox/chatterbox_vc/{s3gen.pt, conds.pt}` (the Chatterbox node downloads these on first use) |
| Native speech (`piper_vc`) | `pip install piper-tts`, plus a voice's `.onnx` and `.onnx.json` from [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) in `~/.cache/piper/<voice>/` |
| Word checks, non-English | [mobiuslabsgmbh/faster-whisper-large-v3-turbo](https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo) in `~/.cache/faster-whisper/large-v3-turbo/` |

## Setup

> **Required: set the `COMFYUI_DIR` environment variable** to the folder containing your ComfyUI install (the one with `main.py`). There's no default: the tools and `start-comfyui.bat` all stop with an error if it's missing or doesn't point at ComfyUI.
>
> ```bat
> setx COMFYUI_DIR "C:\path\to\ComfyUI"
> ```
> `setx` saves it permanently but only for **new** terminals, so close and reopen your terminal (and VS Code) afterwards.

```bash
pip install -r requirements.txt
```

1. Set `COMFYUI_DIR` as above. `COMFYUI_URL` is optional (default `http://127.0.0.1:8188`).
2. Start ComfyUI by double-clicking **`start-comfyui.bat`** in the repo root (localhost only; keep the window open).
3. Write a plan and render it. See [Writing a reel plan](comfy-influencer-reel/README.md#writing-a-plan) and [Writing a YouTube plan](youtube-channel/README.md#writing-a-plan).

### Using them as Claude Code skills
The skills import `engine/` and `workflows/` from the repo root, so **link** the skill folders into `~/.claude/skills` rather than copying them (directory junctions need no admin rights):

```bat
mklink /J "%USERPROFILE%\.claude\skills\comfy-influencer-reel" "C:\path\to\comfy-video-skills\comfy-influencer-reel"
mklink /J "%USERPROFILE%\.claude\skills\youtube-channel"      "C:\path\to\comfy-video-skills\youtube-channel"
```
Then ask Claude for, say, "a 15 second Maya reel about hydration at the gym" or "a 10 minute YouTube video on the Roswell incident narrated by Vale". Claude follows each skill's `SKILL.md`: it writes the plan, shows you the script, and renders.

## Repository layout

```
start-comfyui.bat      starts ComfyUI on localhost (uses COMFYUI_DIR)
requirements.txt
workflows/             shared ComfyUI workflow templates
  z_image_turbo.json      portraits + footage stills
  flux2_dev.json          new outfit/scene for an influencer (keeps the face)
  ltx2_i2v_distilled.json invents a new persona's voice (once)
  chatterbox_tts.json     narration in a cloned voice
  chatterbox_vc.json      re-voices speech as a persona (piper_vc engine)
  ltx2_3_ia2v.json        talking clips: image + audio -> lip-synced video
engine/                shared Python
  comfy.py                ComfyUI connection: config, job runner, uploads, persona creation helpers
  narration.py            voice engines (Chatterbox, Chatterbox Multilingual, Piper + VC), Whisper checks, slicing
  video.py                prompts, clip rendering (resume, chaining, VRAM splits), assembly, upscale
  watchdog.py             unattended/overnight renders for either skill (restarts ComfyUI, resumes)
  after_then.py           start a render when another process exits
comfy-influencer-reel/
  SKILL.md  README.md  reel.py
  influencers/<id>/       profile.json, portrait.png, voice.wav, looks/*.png
  plans/<slug>/plan.json  one example plan (your own plans go here too)
youtube-channel/
  SKILL.md  README.md  yt.py
  narrators/<id>/         profile.json, portrait.png, voice clip(s)
  projects/<slug>/plan.json  one example plan
```

Generated output goes to `<ComfyUI>/output/reels/<slug>/` and `<ComfyUI>/output/youtube/<slug>/`, not into this repo. Re-running a plan resumes: finished narration takes and clips with identical settings are reused.

## Responsible use

These tools create realistic people who talk, using cloned voices. Please use them responsibly:

- **Don't impersonate real people.** Don't use a real person's face, voice or name for an influencer or narrator, and don't clone anyone's voice from a recording without their explicit consent. The included personas (Maya, James Whitford, Dr. Simon Vale) are fictional and fully AI-generated, with faces and voices invented by the models.
- **Label AI content.** Disclose that videos are AI-generated wherever you publish them. YouTube, TikTok and Instagram all require disclosure of realistic synthetic media.
- **Don't deceive.** Don't present generated videos as genuine footage, testimonials, endorsements or news, and don't use them for scams, fraud, harassment, political deception or non-consensual content.
- **Check the facts.** Scripts about health, finance, law or history should be fact-checked before publishing. When covering conspiracy topics, keep theory clearly separated from documented record.
- **Respect model licences.** The models listed above each have their own licence and usage policy (some restrict commercial use). Check them before publishing commercially. No model weights are included in this repo.
- **Keep ComfyUI local.** It has no authentication. `start-comfyui.bat` binds it to `127.0.0.1` only; don't add `--listen` or expose port 8188 to a network.

You're responsible for what you generate and publish with these tools.

## Licence

The code is released under the [MIT License](LICENSE). The models it uses are not included and keep their own licences (see above).
