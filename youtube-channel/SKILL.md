---
name: youtube-channel
description: Make 16:9 YouTube-style videos locally in ComfyUI - a named AI narrator (fixed face + cloned voice) on camera, randomly intercut with generated b-roll footage of the subject, joined into one video. Takes a genre, narrator description, narrator setting, subject and length. Use when the user asks for a YouTube video, documentary, explainer or channel content (not vertical reels; that's comfy-influencer-reel).
---

# YouTube channel videos (local ComfyUI)

Everything goes through `yt.py` in this folder. It reuses the reel skill's engine (`../comfy-influencer-reel/reel.py`: job runner, uploads, joiner, silence detection) and its base workflows. No credits are spent.

```
narrators/<id>/profile.json      name, pronoun, identity, outfit, scene (setting), voice_style, personality, seed
narrators/<id>/portrait.png      16:9 narrator still in their setting; every on-camera segment starts from it
narrators/<id>/voice.wav         cloned voice, used for on-camera AND voice-over segments
projects/<slug>/plan.json        the video plan (script + shot list); outputs go to ComfyUI output/youtube/<slug>/
```

## 0. ComfyUI must be running
`curl -s http://127.0.0.1:8188/queue`. If it's down: the user double-clicks `../comfy-influencer-reel/start-comfyui.bat`, or (with their OK) launch it via WMI exactly as in the reel skill's section 0. Never use a Claude background Bash or `Start-Process`: those servers die silently.

## 1. Inputs from the user

| Input | Example | Becomes |
|---|---|---|
| Genre | conspiracy, true crime, history, science explainer | tone of the script + `style` of the footage |
| Narrator description | "academic, late 30s, British accent, mid-tone, interesting cadence, authoritative" | `new-narrator` identity / voice_style / personality |
| Narrator setting | podcast studio | `--scene` (+ outfit) |
| Subject | JFK assassination conspiracy | script + shot list |
| Length | 1 minute | `seconds` |
| On-camera share | default 0.4 | `narrator_share` |

If a narrator matching the description already exists (`python yt.py list`), reuse it.

## 2. New narrator (once per narrator)
```bash
python yt.py new-narrator --id vale --name "Dr. Simon Vale" --pronoun he --identity "..." --outfit "..." \
  --scene "a moody podcast studio ... microphone on a boom arm positioned to the side so it does not cover his face" \
  --voice-style "a mid-toned, articulate male voice with an educated southern English British accent ..." \
  --personality "..." --seed 1963
```
- Makes a 16:9 portrait (Z-Image), then invents the voice with LTX-2 and trims it to voice.wav (~3 min).
- Have the user approve the portrait and the voice sample (`youtube/narrators/<id>_voice_source_*.mp4`) before making videos.
- Keep microphones and props away from the mouth, or lip-sync is hidden.

## 3. Write the plan (projects/<slug>/plan.json)
See the docstring at the top of `yt.py` for the schema. Guidelines:
- **Segments of ~22-27 words** (each becomes one clip ≤10 s: words/3.3 + a 1 s pause before the cut; the final one gets 1.2 s). **Words for a length: ≈ (seconds - 1.2 - (segments - 1)) x 3.3**, e.g. 60 s ≈ 7 segments ≈ 175 words, 120 s ≈ 14 segments ≈ 350 words.
- Every segment is a complete thought ending at a sentence end, because each is a separate clip and cut.
- Write a `shot` for **every** segment (the random split decides which become footage). Good shots: places, objects, documents, atmospheres, wide or distant views. Avoid: recreations of real people's faces, graphic violence or the moment of a real death, legible text (it comes out garbled).
- `style` sets the look of all footage (era, film stock, colour, lighting). `ambience` is the footage's background sound; keep "no music".
- Genre tone: write in the narrator's `personality`. For **conspiracy / true crime / history**, attribute claims and separate documented fact from theory ("officially...", "critics argue...", "the committee concluded..."). Don't present a theory as established fact, and keep dates and findings accurate.
- Force a segment's type with `"type": "narrator"` / `"footage"` if needed (e.g. a line that only works on camera).

Always run `python yt.py make --plan <plan> --dry-run` first and show the user the split, durations and script.

## 4. Render
**Launch it as an independent process.** Claude background tasks are killed after ~30 min, which stops a long render partway through:
```powershell
$log = "$env:TEMP\influencer_reel\<slug>_render.log"
$skill = "$env:USERPROFILE\.claude\skills\youtube-channel"
$cmd = "cmd.exe /c python -u -W ignore `"$skill\yt.py`" make --plan `"$skill\projects\<slug>\plan.json`" > `"$log`" 2>&1"
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = $skill }
```
Then follow the log with a Monitor (`tail -n 0 -F <log> | grep --line-buffered -E "running segment|MISSING|joined|Video:|Traceback|Error"`), re-armed every 30 min. Per segment: footage gets a still (Z-Image, ~20 s) then a clip; narrator segments start from the portrait. Each clip is LTX-2.3 ID-LoRA at 1280x704 with the narrator's voice (~2.5-4 min each, so a 1-minute video is ~25 min). Every segment's workflow is saved to the ComfyUI sidebar under `YouTube/<title>/`. Re-running the same command resumes: finished segments with identical settings are reused (`.spec.json` sidecars). If a run was killed mid-segment, ComfyUI may still have finished that clip without a sidecar. Copy the previous attempt's `.spec.json` (with seed +100 for a retry) to adopt it instead of re-rendering.

Output: `ComfyUI/output/youtube/<slug>/<slug>_final.mp4` (hard cuts, 1 s+ of silence after the last word). Check it with PyAV: a frame strip per segment (on-camera face matches the portrait; footage has nobody lip-syncing) and per-segment silence (printed by the tool).

## How videos are built (narration-first, default since 2026-10-04)
1. **Narration:** the whole script is spoken by Chatterbox TTS (ComfyUI node pack `comfyui_fill-chatterbox`, models in `ComfyUI/models/chatterbox/chatterbox/`) in the narrator's cloned voice. Settings live in `profile.json` -> `tts` (Vale: `voice.wav`, **cfg_weight 0.4, temperature 0.5**, exaggeration 0.5). Tuned by ear 2026-10-05: in Chatterbox, HIGHER cfg_weight pulls the voice toward the model's default American accent (1.0 = American) and lower follows the reference clip's accent (0.3 = very British but slow; 0.4 = chosen). Temperature 0.5 stops the accent drifting between takes (at 0.8 it wandered British/American/Australian). For new narrators, start at cfg 0.4 / temp 0.5 and test 3 seeds of the same passage.
- **Accent anchoring (2026-10-05):** even at cfg 0.4 / temp 0.5 the accent depended on the seed (seed 101 British, 2080 American) because Vale's original LTX-made clip has only a mild accent. Fix: use an approved Chatterbox take as the new reference (`voice_v2_british.wav` = first 10 s of the user-approved cfg 0.4 test). Same timbre, clearly British, and 3/3 seeds tested British. For any narrator whose accent drifts, bootstrap the same way: generate takes, let the user pick one, and make a 10 s sentence-ending cut of it the reference. It's recorded in takes of up to 80 words that break only between segments. Each take is Whisper-checked (ends with the script's last words, every segment ending found) and re-taken with a new seed up to 2 times. Measured pace: ~3.2 words/s including pauses (3.1-3.7 per take).
2. **Slicing:** Whisper word timings put each cut in the middle of the pause after a segment's last word, padded with silence to LTX's 0.32 s frame grid, so each clip is exactly as long as its narration slice. The final slice gets 1 s of silence.
3. **Clips:** every segment uses the **LTX-2.3 Image+Audio** template (`ltx2_3_ia2v.json`) with its narration slice. On-camera segments lip-sync the narrator's portrait; footage segments animate the Z-Image still under the voice-over. Duration is passed as secs+0.01, because the template computes `int(secs*25+1)`, and 9.28*25 = 231.999… would snap a whole step down.
4. **Assembly** (`narration.assemble`): hard cuts, each clip trimmed to exactly its slice; the soundtrack is the continuous narration itself, played 40 ms early (`AV_LEAD`, since IA2V lips run 40-60 ms ahead of their input).
Files: `output/youtube/<slug>/narration/` (takes + `.spec.json`, `seg##.wav`, `narration.wav`), clips `<slug>_##_<type>_av_*.mp4` (+ spec, resumable), `<slug>_final.mp4`.
`--legacy` runs the old method (LTX ID-LoRA speech per segment), which has audible resets at every cut.

### Plan sizing for narration-first
- **≤28 words per segment** (10 s VRAM cap at ~3 w/s); 22-27 is ideal. **Words ≈ seconds x 3.1** (20 min ≈ 3,700 words ≈ 160 segments).
- Overnight / long renders: launch `watchdog.py --plan ...` detached via WMI. It restarts ComfyUI if it dies and re-runs `make` (which resumes) until `<slug>_final.mp4` exists. Logs: `%TEMP%/influencer_reel/<slug>_watchdog.log` and `<slug>_render.log`. `after_then.py --pid N --plan ...` chains a render after another process exits.
- Rendering cost: ~2.5-3 min per clip, so a 20-minute video (~160 clips) is ~7-8 hours.

### 1080p (benchmark 2026-10-05)
Set `"width": 1920, "height": 1088` in plan.json (1080 isn't a multiple of 64). Clips over ~4.5 s are split into chained parts automatically (the threshold scales with pixel count).
Measured on a 15 s, 4-segment video (2 on camera, 2 footage): **13 min total**, clips 76-147 s each for 2.4-3.8 s of video, **peak VRAM 15.8 of 16.3 GB** (ComfyUI already partially unloading). So 1080p works but sits at the VRAM ceiling. Keep segments short (~10-14 words), close other GPU apps, and expect ~50 s of compute per second of video (704p: ~28 s). A 20-minute 1080p video would be ~17-18 h. The narrator portrait is 1280x720 and gets upscaled; regenerate it at 1920x1080 for crisper on-camera shots (the face would change).

**Cheaper 1080p (recommended): render at 704p and upscale.** Add `"upscale_to": [1920, 1080]` to plan.json (leave width/height unset). This writes `<slug>_final_1080p.mp4` beside the 704p file: a streaming Lanczos resize to cover 1920x1080, a centre crop, and a light unsharp mask (`narration.upscale_video`). Benchmark (same 15 s script): **704p + upscale = 7 min 49 s + 25 s** vs **native 1080p = 13 min**. A 20-minute video would be about 10 h vs 17-18 h. Native 1080p has more genuine fine detail (skin and hair texture); the upscale looks crisp at normal viewing size, with slightly harder edges from the sharpening. Peak VRAM was about 15.7 GB in both runs (ComfyUI fills spare VRAM with weights), so VRAM isn't the limit at 704p.

## Rules built into the tool
- Random split (seeded by `seed`): first and last segments are on camera, never 3 footage segments in a row (runs are broken at their 3rd slot), then topped up to ~`narrator_share` on camera. `"type"` in a segment forces it (e.g. chapter openers on camera).
- Cuts are hard cuts. Every on-camera segment restarts from the portrait, so there's no face drift across a long video (unlike chained reels).
- Before each cut the prompt tells the narrator to stop talking. After the last word: ≥1 s of silence, enforced by holding the last frame if needed.
- **Spoken-word check (Whisper):** after every segment, `speech_check` transcribes the clip with faster-whisper small.en (`~/.cache/faster-whisper/small.en`, downloaded with curl because Python's HTTPS fails on this machine) and confirms the script's last two words are the last ones spoken. If not, the segment is re-rendered once with a new seed. Measured 2026-10-04: LTX-2.3 speaks the exact script and stretches it to fill the clip, so a tight cut usually still has every word (6 of 7 JFK segments). Extra time doesn't create a pause, it just slows the speech, so there's no extra-time re-render. Whisper's word timings also give the gap used for the 1 s end padding.

- **If Whisper flags the same segment twice, trim that segment's text by 2-3 words** and re-run the same command (only that segment re-renders). JFK segment 6 lost "him" on two seeds at 25 words, and was fixed at 23.

## Limits (RTX 5070 Ti 16 GB)
- 1280x704 only (1080p would OOM). ≤10 s per segment.
- Voice-over (footage) segments rely on ID-LoRA producing narration with no on-screen speaker. If a footage clip shows someone mouthing words, add "no people" to the shot, or force that segment to `narrator`.
- Content: label videos as AI-generated. Conspiracy content must stay clearly framed as theory vs. record.
