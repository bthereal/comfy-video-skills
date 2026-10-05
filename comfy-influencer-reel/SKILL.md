---
name: comfy-influencer-reel
description: Make ~10s vertical talking-head "influencer reels" locally in ComfyUI on ANY topic, using named AI influencers with a fixed face and cloned voice (e.g. Maya). Also creates new influencers and new looks (outfit/scene) for existing ones. Use when the user asks for a reel/short/TikTok-style clip of an influencer talking about something, or to create/restyle an influencer.
---

# Influencer reels (local ComfyUI)

Everything goes through `reel.py` in this folder. Each influencer has a fixed **face** (portrait) and **voice** (voice.wav, cloned by LTX-2.3 ID-LoRA), plus any number of **looks** (outfit + scene). Reels can be on any topic: you write the script, the tool does the rest. No credits are spent.

```
influencers/<id>/profile.json   name, identity (fixed traits), voice_style, personality, seed, looks{}
influencers/<id>/portrait.png   default look; source of the face for every new look
influencers/<id>/voice.wav      5-9s clean speech; reused as the voice reference for every reel
influencers/<id>/looks/*.png    alternate outfits/scenes (same face, made with Flux.2 edit)
influencers/<id>/reels.jsonl    log of every reel (script, look, output path)
workflows/                      base ComfyUI workflows (don't edit; reel.py copies them)
```

Run `python reel.py list` to see influencers and their looks.

## 0. Make sure ComfyUI is running

`curl -s http://127.0.0.1:8188/queue`. If it's down:
- **Best:** the user double-clicks `start-comfyui.bat` in this folder (localhost only; keep its window open).
- **Or, with the user's OK,** launch it from PowerShell via WMI, so it isn't tied to Claude's session:
  ```powershell
  Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = "cmd.exe /c `"$env:USERPROFILE\.claude\skills\comfy-influencer-reel\start-comfyui.bat`""; CurrentDirectory = $env:COMFYUI_DIR }
  ```
  Ready in ~6-30s. Log: `ComfyUI/user/comfyui_8188.err.log`.

Don't start it with a Claude background Bash, `Start-Process`, `comfy launch` or `launch_comfyui`. On 2026-10-04 servers started those ways exited silently (no error in the log) when the session's task ended or after ~1h idle, and `comfy` isn't on PATH anyway. If idle VRAM is > ~3 GB, run `python -m comfy_cli free`.

## 1. Reel on a topic (the common case)

### Parse the request
A request like *"Create a reel for James 15 seconds long, it's a bright sunny day and he is walking alongside a canal in London, about ISAs"* maps to:

| Phrase in request | Flag | Default if absent |
|---|---|---|
| influencer name | `--id` | ask |
| "15 seconds long", "a 30s reel" | `--seconds 15` | 10 |
| setting / weather / time of day ("sunny day, alongside a canal in London") | `--scene "a canal towpath in London on a bright sunny day, narrowboats moored behind him"` | the chosen look's scene |
| clothing ("in a suit", "wearing a hoodie") | `--outfit "..."` | the default look's outfit |
| movement ("walking", "sitting at a desk", "holding a coffee") | `--action "walking slowly along the towpath, holding the phone at arm's length"` | gestures while talking |
| background sound implied by the scene | `--ambience "Light outdoor breeze, distant birdsong and city hum, no music."` | look's ambience |
| topic / exact words | write `--script` | ask |

- `--scene`/`--outfit` reuse a saved look with the same text, or create one with Flux.2 (~2.5 min extra) and save it for next time (name it with `--look canal`, or it's auto-named from the scene). Write scene text as a concrete visual description. Pick a sensible outfit for the scene (a blazer on a sunny towpath is fine; a gym needs gym wear) and say what you chose.
- Outdoor or walking shots: put the motion in `--action`, the place in `--scene`, and the sound in `--ambience`. Tested (canal, sunny): scene and ambience come through well; walking reads as gentle handheld sway rather than a clear walk. For stronger motion, describe it concretely ('walking towards the camera, background passing by, slight bounce in each step').

### Write the script
In the influencer's `personality` from profile.json, sized by the **1-second end rule**: the final word must be followed by at least 1 s of silence. 1.2 s is planned for it, plus a 1.0 s pause before each join. Talking time = seconds - 1.2 - 1.0 x (parts - 1), at ~3.3 words/s:

| Length | Parts | Talking time | Words |
|---|---|---|---|
| 10s | 1 | 8.8s | ~29 |
| 15s | 2 | 12.8s | ~42 |
| 20s | 2 | 17.8s | ~59 |
| 25s | 3 | 21.8s | ~72 |
| 30s | 3 | 26.8s | ~88 |

(Segments = ceil(seconds / 10). reel.py prints the exact target and **refuses scripts more than 8% over it** unless `--force`.)
- Hook first, concrete points, a practical close. Plain spoken words; spell numbers ("fifty thirty twenty", "five grams").
- No stage directions, hashtags or emojis. If the user gave exact words and they're too long, say so and offer a trim or a longer reel.
- **Over 10 seconds**: put ` | ` between segments at sentence ends, with roughly equal words per segment, each a complete thought (the voice resets slightly at each join). Without `|` the tool splits at sentence ends itself.
- Show the user the script (split into its parts) before or as you start rendering.

How the rule is enforced (automatic): every part's prompt tells the speaker to stop talking before the cut ("…stops talking and pauses in silence…"), and the final part holds a silent smile. If the gap after the last word is under 1 s, the last frame is held in silence until it isn't.

**Spoken-word check (Whisper):** after every segment, `speech_check` transcribes the clip with faster-whisper small.en (`~/.cache/faster-whisper/small.en`, downloaded with curl because Python's HTTPS fails on this machine) and confirms the script's last two words are the last ones spoken. If not, the segment is re-rendered once with a new seed. Measured 2026-10-04: LTX-2.3 speaks the exact script and stretches it to fill the clip, so a tight cut usually still has every word (6 of 7 JFK segments). Extra time doesn't create a pause, it just slows the speech, so there's no extra-time re-render. Whisper's word timings also give the gap used for the 1 s end padding.

**Face drift in long reels:** each part starts from the previous part's last frame, not the portrait, so the face can drift by the last part (seen in a 15 s Maya reel). Check the final frames against portrait.png. If it drifted, re-run with another `--seed`, or keep reels at 10 s where the face matters most.

### Run
In a **background** Bash. Each segment takes ~3.3 min, plus ~2.5 min if a new look is made.
```bash
python reel.py reel --id james --seconds 15 --title isa-basics \
  --scene "..." --action "..." --ambience "..." --script "part one ... | part two ..."
```
Other options: `--look <saved look>`, `--seed N` for a different take.

### Check and deliver
With PyAV (no ffmpeg on PATH): a frame strip across the whole reel (look for face drift at segment joins) and per-0.5s audio RMS (gaps = script too short). Send `<title>_full.mp4` from ComfyUI `output/reels/<id>/` (always produced, even for single-segment reels). reel.py prints the silence after the last word for each segment.

## 2. New look (outfit / scene) for an existing influencer

```bash
python reel.py look --id maya --look gym --outfit "..." --scene "..." [--ambience "Soft gym ambience, no music."]
```
Flux.2 Dev edits `portrait.png`, keeping the face and changing only clothes and location. Read the PNG and confirm the face still matches before using it. If it drifted, re-run with `--seed N`. The ambience text goes into the reel's [SOUNDS].

## 3. New influencer

Ask for (or propose) a name, identity (age, build, face, hair: fixed physical traits only), default outfit + scene, voice style, and personality. Then:
```bash
python reel.py new --id jordan --name "Jordan" --pronoun she \
  --identity "A ..." --outfit "..." --scene "..." \
  --voice-style "a calm, warm, low-pitched voice with a slight British accent" \
  --personality "..." --seed 1234
```
This makes the portrait (Z-Image Turbo), then one LTX-2 video in which a voice is invented from `--voice-style`, then trims it to `voice.wav`. **Have the user approve both the portrait and the voice** (send them `voice_source_*.mp4` from ComfyUI output/influencers/<id>/) before making reels. To re-roll, delete the influencer's folder and run `new` again with another `--seed`.

## ComfyUI UI

Every run is saved to the ComfyUI **Workflows sidebar** (left bar, Workflows icon) under `Influencers/<Name>/`:
- `Reel - <title>`: the exact reel run; open it, change the [SPEECH] text and press Run. Long reels save one workflow per segment (`Reel - <title>_part2`, ...); chaining and joining only happen in reel.py, so in the UI keep to ≤10s.
- `Look - <name> (Flux.2 edit)`, `1 - Portrait (Z-Image)`
- `python reel.py save-ui --id <id>` (re)publishes `Reel template` and `Look template` with the influencer's image and voice already loaded.

Workflows the user changes in the UI are separate from reel.py. reel.py always starts from `workflows/`.

## Prompt format (built by reel.py; for reference and UI edits)
```
[VISUAL]: shot, identity, "wearing <outfit>, in <scene>", "is speaking, mouth opens and closes naturally", actions, style
[SPEECH]: exact words
[SOUNDS]: "The speaker has <voice_style>, close to the phone microphone. <ambience>"
```

## Models and measured performance (RTX 5070 Ti 16 GB, 64 GB RAM, 2026-10-04)

| Step | Model | Time | Peak VRAM |
|---|---|---|---|
| Portrait 720x1280 | Z-Image Turbo | 10-20s | - |
| Look edit 768x1360 | Flux.2 Dev fp8 + turbo LoRA (8 steps) | ~160s (67 GB of model+encoder streamed from disk) | ~11 GB |
| Voice creation (new only) | LTX-2 19B distilled | ~135s | 15.1 GB |
| Reel 704x1280, 10s | LTX-2.3 22B fp8 + ID-LoRA | ~195s | 15.5 GB |
| Long reel (chained) | same, per 7-9s segment | 140-290s each (25s ≈ 10 min, 15s + new look ≈ 10 min) | 15.5 GB |

At ~95% of VRAM, close Brave, Slack, iCUE and Photos before long runs. Size is fixed at 704x1280. 1088x1920 ("1080p") is untested and likely to OOM.

## Failure handling

- **Connection reset (`WinError 10054`)**: local connections to ComfyUI occasionally reset on this machine. reel.py submits jobs itself (converts UI to API locally, one small POST tagged with a marker, then polls /history), so a reset never loses or duplicates a job. Only `set-slot` still goes through comfy-cli, with 4 retries. The comfy-mcp run tools and `comfy run` are affected, so prefer reel.py.
- **Long reel failed midway**: re-run the exact same command. Segments already rendered with identical settings are reused (a `.spec.json` sidecar next to each segment mp4).
- **ComfyUI exits / OOM**: tail `user/comfyui_8188.log`, restart (step 0), retry with `--seconds 8`.
- **Garbled or paraphrased speech**: shorten the script, simplify words, try another `--seed`.
- **Voice drifts from voice.wav**: raise `340/349.identity_guidance_scale` (default 3) in the UI, or replace voice.wav with a cleaner 5-9s clip.
- **Face drift in a new look**: re-run `look` with another `--seed`; keep identity changes out of `--outfit/--scene`.
- Content: label output as AI-generated per platform rules; fact-check health, finance or legal claims before posting.
