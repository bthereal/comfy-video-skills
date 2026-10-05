---
name: comfy-influencer-reel
description: Make vertical (9:16) talking-head "influencer reels" locally in ComfyUI on ANY topic, using named AI influencers with a fixed face, cloned voice and saved looks (e.g. Maya, James). Also creates new influencers and new looks (outfit/scene). Use when the user asks for a reel/short/TikTok-style clip of an influencer talking about something, or to create/restyle an influencer.
---

# Influencer reels (local ComfyUI)

Tool: `reel.py` in this folder; shared code in `../engine/`, shared workflows in `../workflows/`. **Plan-driven and narration-first**: Chatterbox speaks the whole script in the influencer's voice, Whisper checks it, and LTX-2.3 image+audio clips are lip-synced to it as one continuous shot. No credits are spent. The README has the full plan format.

## 0. Preconditions
- `COMFYUI_DIR` must be set (the tools stop with an error otherwise). If it's missing, ask the user to `setx COMFYUI_DIR "<ComfyUI folder>"`.
- ComfyUI must be up: `curl -s http://127.0.0.1:8188/queue`. If it's down, ask the user to double-click `../start-comfyui.bat`, or (with their OK) launch it detached so it outlives this session:
  ```powershell
  Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = "cmd.exe /c `"$((Resolve-Path ..\start-comfyui.bat).Path)`""; CurrentDirectory = $env:COMFYUI_DIR }
  ```
  Never start it from a Claude background Bash or `Start-Process`: those die silently when the session task ends.

## 1. Turn the request into a plan (`plans/<slug>/plan.json`)
Parse the request. *"Maya, 15 seconds, at the beach at sunset, about hydration"* becomes:

| From the request | Plan field |
|---|---|
| influencer | `influencer` (check `python reel.py list`) |
| "15 seconds" | `seconds` (default 15) |
| place, weather, time | `scene` (concrete visual description) + `ambience` (sound, always "no music") |
| clothing | `outfit` (default: the default look's outfit) |
| movement | `action` ("walking slowly along the shore, holding the phone at arm's length") |
| saved setting ("in her gym look") | `look` |
| topic or exact words | write `segments` |

**Write the script yourself**, in the influencer's `personality` (profile.json) and **in their language** (`profile.language` / `tts.language`; e.g. Slovak for `sk`, using natural native phrasing). **Words ≈ (seconds − 1) × 3** for English (Slovak ≈ × 2.75): 10 s is ~27, 15 s ~42, 30 s ~87. Use segments of ≤26 words, each ending at a sentence end. Hook first, concrete points, a short close. Spell numbers as spoken. No stage directions, hashtags or emojis. If the user gave exact words, use them verbatim. Show the user the script before rendering.

## 2. Check and render
```bash
python reel.py make --plan plans/<slug>/plan.json --dry-run
python -u reel.py make --plan plans/<slug>/plan.json        # background Bash; ~6-8 min for 15 s
```
A new `scene`/`outfit` creates a look first with Flux.2 (~3 min, saved for reuse). Re-running resumes from finished takes and clips. `quick --id ... --script ...` writes the plan from CLI arguments and makes it.

Over ~25 min of work (long reels or many looks), launch `../engine/watchdog.py --plan <plan>` detached instead (it restarts ComfyUI and resumes).

## 3. Check and deliver
Output: `$COMFYUI_DIR/output/reels/<slug>/<slug>_final.mp4`. Check with PyAV: a frame strip (the face matches the look image; one continuous shot) and the length versus the narration. Send the mp4.

## New influencer / new look
- `python reel.py new --id <id> --name ... --pronoun ... --identity "<fixed physical traits>" --outfit ... --scene ... --voice-style "..." --personality "..." --seed N`. Have the user approve `portrait.png` and the voice sample before making reels.
- `python reel.py look --id <id> --look <name> --outfit ... --scene ... [--ambience ...]`. Read the PNG and confirm the face matches; re-run with `--seed` if it drifted.

## Voice tuning (profile.json → `tts`)
Defaults: `cfg_weight` 0.4, `temperature` 0.5, `exaggeration` 0.5. In Chatterbox, **higher cfg_weight drifts towards the model's default American accent** and lower follows the reference clip (0.3 = closest but slow). If the accent varies by seed, generate a few takes, let the user pick one, and save a 10 s sentence-ending cut as the new `tts.voice`.

## Other languages
A profile's `tts.engine` picks the voice: none = English Chatterbox; `chatterbox_mtl` = Chatterbox Multilingual (optionally a fine-tune via `tts.t3_weights`, e.g. Slovak); `piper_vc` = native Piper voice re-voiced by Chatterbox voice conversion (fallback). Setup and downloads: root README → Languages. Whisper checks run in `tts.language` with `large-v3-turbo`; it's weaker outside English, so if it warns, listen rather than trust it. To make a new non-English influencer: `new` as usual, then edit `tts` and add the language to `personality`.

## Built-in safeguards (in `../engine/`)
- Takes are rejected and re-recorded if Whisper finds a missing ending or inserted/repeated words. A take that keeps failing is recorded segment by segment.
- Cut points are anchored on the next segment's opening words, and every final word is followed by 1 s of silence.
- Clips are trimmed to exactly their narration slice. The narration plays 40 ms early (IA2V lips lead their audio).
- Slices over the VRAM cap (~10.4 s at 704x1280, scaled by pixel count) are split into chained parts.
- Assembly streams one clip at a time, so any length fits in RAM.

## Limits
- 16 GB VRAM: 704x1280 is the default; for 1080p use `upscale_to: [1080, 1920]` rather than native.
- Content: fictional personas only. Never use a real person's face or voice. Label output as AI-generated, and fact-check health, finance or legal claims.
