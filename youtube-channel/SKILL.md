---
name: youtube-channel
description: Make 16:9 YouTube-style videos locally in ComfyUI - a named AI narrator (fixed face + cloned voice) on camera, randomly intercut with generated b-roll footage, over one continuous narration. Takes a genre, narrator, setting, subject and length; any length (overnight for long videos). Use when the user asks for a YouTube video, documentary, explainer or channel content (not vertical reels; that's comfy-influencer-reel).
---

# YouTube channel videos (local ComfyUI)

Tool: `yt.py` in this folder; shared code in `../engine/`, shared workflows in `../workflows/`. **Plan-driven and narration-first**: Chatterbox speaks the whole script in the narrator's cloned voice, Whisper checks it, then each segment is an LTX-2.3 image+audio clip (narrator lip-sync, or footage under voice-over), hard-cut over the unbroken narration. All subject matter lives in the plan; the narrator's look, setting, camera, lighting and voice live in `narrators/<id>/profile.json`. The README has the full plan format.

## 0. Preconditions
Same as the reel skill: `COMFYUI_DIR` must be set and ComfyUI running (`../start-comfyui.bat`, or the user-approved detached WMI launch). Never start ComfyUI from a background Bash.

## 1. Inputs
Genre, subject/angle, length and narrator (`python yt.py list`, or create one with `new-narrator`: approve the portrait and voice first). Optional: on-camera share (default 0.4), and 1080p (`upscale_to`).

## 2. Write the plan (`projects/<slug>/plan.json`)
Required: `title`, `slug`, `narrator`, `style`, `segments`. Usual: `genre`, `seconds`, `seed`, `ambience`, `narrator_share`, `upscale_to`.
- **Word budget:** ~3.1 words/s including pauses. 1 min ≈ 185 words ≈ 7 segments; 20 min ≈ 3,700 words ≈ 150 segments.
- **Segments:** 22-27 words (max 28), each a complete thought ending at a sentence end, in the narrator's `personality`. Spell numbers as spoken.
- **A `shot` for every segment**, because the random split decides which become footage. Use places, objects, documents and atmospheres; never real people's faces, graphic violence or legible text.
- **`style`** sets the look of all footage (genre, era, film stock, colour, light). **`ambience`** is the footage sound ("no music").
- Optional: `"type"` to force narrator/footage, `"action"` per segment, `narrator_action`, `camera_motion`.
- **Factual or conspiracy genres:** attribute claims, separate the documented record from theory, and keep dates and figures accurate. Don't present a theory as fact.

Always run `--dry-run` and show the user the split, the timings and the script.

## 3. Render
- **Short (< ~25 min of work, i.e. under ~1 min of video):** `python -u yt.py make --plan projects/<slug>/plan.json` in a background Bash.
- **Anything longer:** launch the watchdog **detached** (background tasks are killed after ~30 min):
  ```powershell
  $log = "$env:TEMP\influencer_reel\<slug>_watchdog_console.log"
  $cmd = "cmd.exe /c python -u -W ignore `"$((Resolve-Path ..\engine\watchdog.py).Path)`" --plan `"$((Resolve-Path projects\<slug>\plan.json).Path)`" > `"$log`" 2>&1"
  Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = (Get-Location).Path }
  ```
  Follow `%TEMP%/influencer_reel/<slug>_render.log` with a Monitor (`grep --line-buffered -E "narration:|re-taking|separately|Traceback|Error|unreachable|assembled|Video:"`), re-armed every 30 min.
- Cost: ~2.5-3 min per clip at 1280x704, so 2 min of video ≈ 40 min and 20 min ≈ 9 h. Narration takes ~30-45 s per 80 words.
- Re-running resumes: identical takes and clips are reused, and only changed segments re-render.

## 4. Check and deliver
Output: `$COMFYUI_DIR/output/youtube/<slug>/<slug>_final.mp4` (and `_1080p.mp4`). Check: audio and video lengths are equal; a frame per chapter (the narrator matches the portrait; footage has nobody speaking); narration warnings in the log. Send the video. Remind the user to label it AI-generated and to fact-check.

## Dialogue videos and QA
For two or more people (presenters, a scene with characters): a plan with `cast` + `lines` (README "Dialogue videos"). Make an angled portrait per speaker (`yt.py angle`) and an establishing shot (`yt.py group`); set `"solo": true` and a `solo_gaze` per cast member so off-camera people aren't drawn into frame. After a long render, run `yt.py qa`, review the flagged lines with the user (report_flagged.png), then `qa --fix --from-report` and `make` again: only flagged lines re-render.

## Other languages
If the narrator's profile has `tts.language` other than English, write every segment in that language and size the script by its speaking rate (Slovak with Chatterbox ≈ 2.2-2.8 words/s; budget ~2.5). Engines and downloads: root README → Languages.

## Voice tuning (profile.json → `tts`)
Vale uses `voice_v2_british.wav`, `cfg_weight` 0.4 and `temperature` 0.5. **Higher cfg_weight pulls towards the model's default American accent**; lower follows the reference clip. Temperature 0.5 stops the accent drifting between takes. If the accent still varies by seed, bootstrap: the user picks a take, and a 10 s cut of it becomes the new reference.

## Built-in safeguards (in `../engine/`)
- Whisper rejects takes with missing endings or inserted/repeated words. Each take is re-recorded up to 3 times, then recorded segment by segment.
- Cut points are anchored on the next segment's opening words. Slices are padded to LTX's 0.32 s grid. The final word gets 1 s of silence.
- Slices over the VRAM cap (10.4 s at 1280x704, scaled by pixel count) render as chained parts.
- Clips are trimmed to their slice, the narration plays 40 ms early, and assembly streams one clip at a time (any length).
