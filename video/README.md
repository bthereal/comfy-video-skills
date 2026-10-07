# video

One tool for every kind of video with AI actors: someone talking to camera, a conversation, actors doing things, a narrated documentary with b-roll, a language lesson with captions. Choose **16:9** (YouTube, landscape) or **9:16** (reels, shorts, TikTok). Actors come from the shared [`../actors/`](../actors/README.md) store, and any actor works in any format: a look image for a new format is made the first time it's needed.

It's **narration-first**: every line's voice is recorded first (Chatterbox, in each actor's cloned voice and language, checked by Whisper), then each line becomes an LTX-2.3 image+audio clip lip-synced to it, and everything is assembled over the narration.

## Quick start

```bash
python video.py make --plan plans/maya-progressive-overload/plan.json --dry-run   # a 9:16 reel
python video.py make --plan plans/moon-landing/plan.json --dry-run               # a 16:9 documentary with b-roll
python video.py make --plan plans/<slug>/plan.json                                # render
```
Output: `<ComfyUI>/output/videos/<slug>/<slug>_final.mp4` (plus `_1080p.mp4` with `upscale_to`). Re-running a plan resumes: finished narration takes and clips with identical settings are reused.

With Claude Code, just ask: *"a 16:9 video of Jane dining in a café in Paris"*, *"a 20-second reel of Maya about hydration at the gym"*, *"a 10-minute documentary on the Roswell incident narrated by Vale"*. The skill writes the plan and script, shows you a dry run, and renders.

## A plan

```json
{
  "title": "Jane in Paris", "slug": "jane-paris", "format": "16x9", "seed": 6100,
  "solo": true,
  "cast": {"jane": {"scene": "a bright Paris café terrace", "outfit": "a Breton top and a beige trench coat",
                    "action": "speaking clearly, lips and jaw forming every word, a calm, attentive, neutral expression."}},
  "lines": [
    {"speaker": "jane", "text": "Bonjour ! Bienvenue dans mon café préféré.", "caption": "Bonjour ! Bienvenue dans mon café préféré.\nHello! Welcome to my favourite café."},
    {"actor": "jane", "seconds": 3.2, "action": "takes a sip of coffee and looks out at the street"},
    {"speaker": "jane", "lang": "en", "text": "The secret to a Paris café? Never rush."}
  ]
}
```

### Lines (one clip each, in order)

| Line | What it is | Fields |
|---|---|---|
| `{"speaker", "text"}` | An actor on camera, lip-synced to their line | `lang` (default: their main language), `caption`, `action`, `gaze`, `look`, `angle`, `camera`, `pause_after`, `check`, `broll` |
| `{"actor", "seconds"}` | An actor on camera, **not talking** (silent: no sound yet) | `action`, `gaze`, `look`, `angle`, `camera` |
| `{"footage", ...}` | B-roll: a generated scene described by `footage`, under someone's voice-over (`voice` + `text`) or silent (`seconds`) | needs a plan `style` |
| `{"shot", "seconds"}` | A silent establishing shot from `shots/<name>.png` (`video.py shot`) | `prompt` (what moves) |

- **Languages:** a line is in the speaker's main language unless `lang` says otherwise. If the actor has a voice for that language (actors `voices`), they speak it natively; if not, they say it in their own accent, which suits a learner (and the word check is skipped). Words in `{braces}` inside a line are said in the actor's other-language voice (their other language; `alt_lang` picks one if they have several), e.g. *"Spaniards say {vale} all the time"*, crossfaded into the rest.
- **`caption`:** burned in; a second line (after `\n`) is shown smaller, e.g. a translation.
- **Where they look:** `gaze` replaces "looks directly at the camera"; lines whose gaze mentions the camera use the look's front-on image. `angle: "left"/"right"` uses the look turned towards that edge of the frame (made on demand). `image` names a file in the actor's folder instead.
- **`check: false`:** no word check (deliberately mispronounced learner attempts).
- **`retake_audio` / `retake_video`:** set by QA (or by hand) for a fresh take / render of just that line.

### Plan options

| Field | Meaning |
|---|---|
| `title`, `slug` | Name, and the output folder name (lowercase-with-dashes). |
| `format` | `16x9` (1280x704) or `9x16` (704x1280). `width`/`height` override (multiples of 64; native 1080p is ~2x slower - prefer `upscale_to`). |
| `cast` | `{actor_id: {...}}`, defaults for that actor's lines: `look` (saved look), or `scene` + `outfit` (+ `ambience`) to create one; `action`, `gaze`, `solo_gaze`, `angle`, `image`, `camera`. |
| `seed` | Drives voice takes, motion and the b-roll split. |
| `narration` | `flowing` (default): consecutive lines in the same voice are one continuous take, sliced at the line boundaries - a monologue keeps its natural flow. `per_line`: every line recorded separately. |
| `continuous` | `true`: consecutive lines by the same actor continue from the previous clip's last frame (one unbroken shot, reel-style). Lines by the same actor with the same image always do. |
| `auto_footage` | `{"share": 0.4}`: spoken lines with a `broll` description may be shown as footage under their voice. First and last stay on camera, never three footage lines in a row, about `share` on camera; `"type": "footage"`/`"on_camera"` forces a line. |
| `style`, `camera_motion` | The look of all footage (genre, era, film stock, colour, light) and its camera move. |
| `camera` | Overrides the framing (e.g. `"Static camera on a tripod on the kitchen island, medium shot"` when an actor needs both hands). |
| `solo` | Prompts say only one person is in frame and use `solo_gaze` (a direction, not a person: naming the off-camera person makes the model draw them at the edge). Recommended for dialogues. |
| `no_text` | Legacy (kept so older plans still resume): adds "no on-screen text, captions, banners..." to prompts - which turned out to *cause* invented banners. Don't use it; re-render a line that gets a banner instead. |
| `sfx` | Sound effects mixed into the soundtrack: `[{"file": "alarm.wav", "line": 3, "offset": -0.5, "gain": 0.5, "loop": true}]` (file relative to the plan's folder). |
| `upscale_to` | e.g. `[1920, 1080]` or `[1080, 1920]`: an extra Lanczos-upscaled copy (~2 s per second of video). |
| `script` | Shortcut for one actor: a single text, split into lines at sentence ends. |
| `output`, `upload_prefix` | Output folder (default `videos`) and upload names - only for converted old plans. |

## Writing the script

- **Budget:** English ≈ 3 words per second including pauses (15 s ≈ 42 words, 1 min ≈ 185, 20 min ≈ 3,700); Slovak ≈ 2.5. Leave 1 s at the end.
- **Lines** of at most ~26 words, each a complete thought that ends at a sentence end (the picture can cut after any line).
- Write in each actor's `personality` and language; spell numbers as spoken; no stage directions, hashtags or emojis.
- **Describe speaking, not smiling.** Asking for "a bright smile" (or even "not smiling") makes the model hold a smile instead of lip-syncing. Use "speaking clearly, lips and jaw forming every word, a calm, attentive, neutral expression".
- **Scenes with props:** one beat per line, restate where the props are in later lines' `action`, and have them talk *during* the action rather than in a silent line.
- **Factual topics:** attribute claims and keep the documented record apart from theory.

## QA and targeted repair

```bash
python video.py qa --plan plans/<slug>/plan.json                      # report.json + report_flagged.png, with timestamps
python video.py qa --plan plans/<slug>/plan.json --fix --from-report  # mark the flagged lines
python video.py make --plan plans/<slug>/plan.json                    # re-renders only those (+ any clip chained to them)
python video.py qa --plan plans/<slug>/plan.json --lines 72,90        # re-check
```
It flags **speech** that's repeated, has extra words, a drawn-out word, a long pause mid-line or a pace far below the speaker's norm (per-line takes), and **extra people** in single-person shots (a person detector; tall boxes only, so hands don't count). Lines that don't lip-sync (an actor laughing through a line) still need a human eye: mark them with `retake_video` + 1 and a speaking `action`. On a 20-minute dialogue, 40 fixes take ~1.5 h instead of a full ~8 h render.

## Long renders

Anything over ~25 minutes of work: run `../engine/watchdog.py --plan plans/<slug>/plan.json` detached (the SKILL shows how). It restarts ComfyUI if it dies and re-runs `make` until the video exists. Logs: `%TEMP%/influencer_reel/<slug>_watchdog.log` and `<slug>_render.log`.

## Benchmarks (RTX 5070 Ti 16 GB, 64 GB RAM)

| Video | Time |
|---|---|
| 15 s reel, 9:16 | ~6-8 min |
| 2 min documentary, 16:9 (14 lines) | ~40 min |
| 20 min dialogue, 16:9 (252 lines, 271 clips) | 7.7 h, overnight with the watchdog |
| 15 s, native 1920x1088 | 13 min, peak VRAM 15.8 / 16.3 GB |
| 15 s, 1280x704 + upscale to 1080p | 7 min 49 s + 25 s |

## Old plans

`video.py convert --plan <old plan.json>` turns an old `comfy-influencer-reel` reel plan or `youtube-channel` narrator/dialogue plan into this format under `plans/<slug>/`.
