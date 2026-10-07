---
name: video
description: Make videos locally in ComfyUI with AI actors (fixed face + cloned voice from the actors skill) - any shape the user asks for, 16:9 (YouTube, landscape) or 9:16 (reel, short, TikTok, vertical): someone talking to camera, a conversation between several actors, actors doing things, narrated documentaries with b-roll, language lessons with captions, any length (overnight for long ones). Use for any request to make, render, fix or QA a video with characters, e.g. "a 16:9 video of Jane dining in a cafe in Paris" or "a reel of Maya about hydration".
---

# Videos (local ComfyUI)

Tool: `video.py` in this folder; shared code in `../engine/` (`timeline.py` is the pipeline), workflows in `../workflows/`, actors in `../actors/`. **Plan-driven and narration-first**: every line's voice is recorded first (Chatterbox, in each actor's cloned voice and language, checked by Whisper), then each line becomes an LTX-2.3 image+audio clip lip-synced to it, assembled over the narration. README.md has the full plan format.

## 0. Preconditions
- `COMFYUI_DIR` set; ComfyUI up (`curl -s http://127.0.0.1:8188/queue`). If it's down, ask the user to double-click `../start-comfyui.bat`, or (with their OK) launch it detached:
  ```powershell
  Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = "cmd.exe /c `"$((Resolve-Path ..\start-comfyui.bat).Path)`""; CurrentDirectory = $env:COMFYUI_DIR }
  ```
  Never from a Claude background Bash or `Start-Process` (they die with the task).
- The actors must exist (`python ../actors/actors.py list`); otherwise create them with the **actors** skill first.

## 1. Request -> plan (`plans/<slug>/plan.json`)
*"Create a 16:9 video of Jane dining in a cafe in Paris"*:

| From the request | Plan |
|---|---|
| "16:9", "YouTube", "landscape", "widescreen" | `"format": "16x9"` |
| "reel", "short", "TikTok", "vertical", "9:16", "story" | `"format": "9x16"` (+ usually `"continuous": true`: one unbroken selfie-style shot) |
| neither | ask; if they don't mind, 16x9 |
| who | `cast` (actor ids); missing actors -> actors skill |
| where / wearing | the cast member's `look` (saved) or `scene` + `outfit` (a new look, made on first run) + `ambience` |
| what they do | `action` on lines; silent beats as `{"actor", "seconds", "action"}` lines |
| what is said | **write the script** (below); exact words if given |
| talking to each other | dialogue: `angle` per cast member, `"solo": true`, a `shot` to establish |
| documentary / explainer | one narrator, lines with a `broll` description, `"auto_footage": {"share": 0.4}`, a `style` |
| length, captions, 1080p | `seconds` (estimate), `caption` per line, `upscale_to` |

If the request doesn't say what's said ("Jane dining in a cafe"), propose an approach - Jane talking to camera about the cafe, a short conversation, or mostly silent action beats with a line or two - and confirm. Silent lines have no sound (ambient sound isn't supported yet), so prefer some speech.

**Write the script yourself**: in each actor's `personality` and **language** (a line without `lang` is in the speaker's main language; `lang` for others; `{braces}` for words in the other language inside a line, e.g. a teacher quoting a phrase). **Budget**: English ≈ 3 words/s; Slovak ≈ 2.5; leave 1 s at the end. Lines ≤ 26 words, each a complete thought ending at a sentence end; spell numbers as spoken; no stage directions, hashtags or emojis. Factual topics: attribute claims and keep fact and theory apart. Show the user the script before rendering.

**Prompting lessons (keep them):**
- Describe **speaking**, never smiling: `"action": "speaking clearly, lips and jaw forming every word, a calm, attentive, neutral expression"`. "A bright smile" - and even "not smiling" - makes the model hold a smile instead of lip-syncing.
- Dialogue: `"solo": true` + a `solo_gaze` per cast member ("turns slightly towards the right edge of the frame and talks in that direction,"); naming the off-camera person draws a phantom at the frame edge.
- **Never name what you don't want.** The model reacts to the words, not the negation: "not smiling" brings smiles, "no captions or banners" brings banners (tested: 3 of 3 renders with it had an invented name banner, 0 without). If a line gets an invented banner or caption, re-render it (`retake_video` + 1). Don't use the old `no_text` option.
- Actions with props: one beat per line, restate where props are in later lines, talk *during* the action.

## 2. Check and render
```bash
python video.py make --plan plans/<slug>/plan.json --dry-run     # always: show the user the lines and timings
python -u video.py make --plan plans/<slug>/plan.json             # background Bash if < ~25 min of work
```
- Cost: ~1.5-3 min per clip (a line of ≤ 10 s); a 15 s reel ≈ 6-8 min; 2 min of dialogue ≈ 45 min; 20 min ≈ 7-8 h. New looks ~2.5 min each (once).
- Longer: launch the watchdog **detached** (background tasks die after ~30 min):
  ```powershell
  $log = "$env:TEMP\influencer_reel\<slug>_watchdog_console.log"
  $cmd = "cmd.exe /c set PYTHONIOENCODING=utf-8&& python -u -W ignore `"$((Resolve-Path ..\engine\watchdog.py).Path)`" --plan `"$((Resolve-Path plans\<slug>\plan.json).Path)`" > `"$log`" 2>&1"
  Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = (Get-Location).Path }
  ```
  then wait on `%TEMP%/influencer_reel/<slug>_watchdog.log` for a new `DONE` / `gave up` line (it's appended to, not overwritten).
- Re-running resumes: identical takes and clips are reused; only changed lines re-render.

## 3. Check, QA and deliver
Output: `$COMFYUI_DIR/output/<plan "output", default videos>/<slug>/<slug>_final.mp4`. Check audio = video length and a frame strip (faces match the looks; nobody extra in frame). For anything over ~1 min: `python video.py qa --plan ...` -> review flagged lines with the user (`report_flagged.png`, timestamps) -> `qa --fix --from-report` -> `make` (only those re-render) -> `qa --lines ...`. Lines the user reports (e.g. "not speaking at 13:39"): find the line from the QA report's timestamps, set `retake_video` + 1 (and the speaking `action` above), `make`. Send the video; remind the user to label it AI-generated.

## Safeguards (in ../engine/)
Whisper re-takes lines with missing or repeated words, drawn-out words or long pauses; sound after a line's last word is trimmed; cut points land in natural pauses on LTX's 0.32 s grid; the final word gets 1 s of silence; slices over the VRAM cap render as chained parts; assembly streams (any length).
