# youtube-channel

16:9 YouTube videos of any length, made locally in ComfyUI: a named AI narrator appears on camera, randomly intercut with generated b-roll footage, over **one continuous narration**, so speech never resets at a cut. Default 1280x704, with an optional upscale to 1920x1080.

Included narrator: **Dr. Simon Vale** (`vale`), a British academic in a podcast studio. Mid-toned and authoritative; Chatterbox `cfg_weight` 0.4 and `temperature` 0.5 with the `voice_v2_british.wav` reference.

## Quick start

```bash
python yt.py list                                                       # narrators
python yt.py make --plan projects/moon-landing/plan.json --dry-run     # split, timings, script
python yt.py make --plan projects/moon-landing/plan.json               # ~40 min for 2 min of video
```
Output: `<ComfyUI>/output/youtube/<slug>/<slug>_final.mp4` (plus `_1080p.mp4` with `upscale_to`).

## Writing a plan

A plan is one JSON file, `projects/<slug>/plan.json`. **Everything about the video's subject lives here**: title, genre, the script, what the footage shows, the visual style and the background sound. The narrator's look, setting, camera framing, lighting and voice live in `narrators/<id>/profile.json`.

### 1. Decide the brief
- **Genre and tone** (history, science explainer, true crime, conspiracy, finance...). It shapes how you write the script and the `style`.
- **Subject and angle**: what the video argues or explains.
- **Length**: it sets the word budget below.
- **Narrator**: `python yt.py list`, or create one (see [New narrators](#new-narrators)).

### 2. Size the script
Narration runs at about **3.1 words per second including pauses**:

| Length | Words | Segments (~25 words each) |
|---|---|---|
| 1 min | ~185 | ~7 |
| 2 min | ~370 | ~14 |
| 5 min | ~930 | ~37 |
| 10 min | ~1,850 | ~74 |
| 20 min | ~3,700 | ~150 |

### 3. Write the segments
Each segment is one clip: either the narrator on camera, or footage under voice-over (decided by the random split, step 5).
- **`text`**: the exact narration, **22-27 words, at most 28**, ending at a sentence end. Each should be a complete thought, because the picture may cut after any segment.
- Write in the narrator's `personality`. Spell numbers as spoken ("nineteen sixty nine"); avoid abbreviations the voice might misread.
- **`shot`**: what the footage shows if this segment becomes b-roll. **Every segment needs one** unless you force it to `"type": "narrator"`. Good shots are places, objects, documents, landscapes and atmospheres, described concretely (subject, framing, light). Avoid recreating real people's faces, graphic violence and legible text (it comes out garbled).
- Optional per segment: `"type": "narrator"` / `"footage"` to force it, and `"action"` (what the narrator does on camera for that line).
- For factual genres, keep documented fact and theory clearly separated in the wording ("officially...", "critics argue...", "the committee concluded...").

### 4. Set the look and sound of the footage
- **`style`**: prefixed to every footage still and clip, so it sets genre, era, film stock, colour and lighting. For example: `"cinematic documentary still, Apollo era aesthetic, 35mm film grain, high contrast, realistic"`, or `"bright, clean modern explainer footage, soft daylight, shallow depth of field"`.
- **`ambience`**: background sound under the footage, e.g. `"Low, atmospheric ambient hum, no music."`
- Optional **`camera_motion`**: default `"Slow, smooth camera movement (a gentle push-in or drift)."`
- Optional **`narrator_action`**: the narrator's on-camera gestures for this video (overrides the profile's `default_action`).

### 5. The on-camera / footage split
It's random but repeatable from `seed`: the first and last segments are on camera, there are never 3 footage segments in a row, and about `narrator_share` (default 0.4) of segments are on camera. Change `seed` for a different arrangement, or force individual segments with `"type"`.

### 6. The plan file

```json
{
  "title": "Was the Moon Landing Faked",
  "slug": "moon-landing",
  "genre": "conspiracy",
  "narrator": "vale",
  "seconds": 120,
  "seed": 1969,
  "narrator_share": 0.4,
  "style": "cinematic documentary still, Apollo era aesthetic, 35mm film grain, high contrast, realistic",
  "ambience": "Low, atmospheric ambient hum, no music.",
  "upscale_to": [1920, 1080],
  "segments": [
    {"type": "narrator", "text": "In July nineteen sixty nine, around six hundred million people watched Neil Armstrong step onto the Moon. Yet a stubborn minority insist it never happened at all.",
     "shot": "The blue Earth rising over the grey cratered lunar horizon against a pitch black sky."},
    {"text": "So today, let's do something slightly unusual for a conspiracy channel. Let's take the hoax claims seriously, one by one, and test them against the evidence.",
     "shot": "A dim study desk covered with old black and white space mission photographs, a brass magnifying glass and handwritten notes, warm lamp light."}
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `title` | yes | Human-readable name (also the ComfyUI sidebar folder). |
| `slug` | yes | Folder name for outputs; lowercase-with-dashes. |
| `narrator` | yes | Narrator `id`. |
| `style` | yes | Visual style of all footage (genre, era, film look). |
| `segments` | yes | `[{"text", "shot", "type"?, "action"?}]`. |
| `genre` | no | Shown in the dry-run; use it to steer your writing and `style`. |
| `seconds` | no | Target length; used only for the estimate. |
| `seed` | no | Drives the split, the voice takes and the motion. Defaults to the narrator's seed. |
| `narrator_share` | no | Fraction on camera (default 0.4). |
| `ambience`, `camera_motion`, `narrator_action` | no | See step 4. |
| `width`, `height` | no | Default 1280x704. Native 1080p: 1920x1088 (about twice as slow; see Benchmarks). |
| `upscale_to` | no | `[1920, 1080]` writes a 1080p copy in ~2 s per second of video. This is the recommended route to 1080p. |

### 7. Check, then render
```bash
python yt.py make --plan projects/<slug>/plan.json --dry-run
python yt.py make --plan projects/<slug>/plan.json
```
The dry run prints every segment's type, estimated length and word count, and rejects segments that are too long. Re-running resumes: finished narration takes and clips with identical settings are reused.

**Long videos (overnight):** launch the watchdog detached. It restarts ComfyUI if it dies and re-runs `make` until the final video exists:
```bash
python ../engine/watchdog.py --plan projects/<slug>/plan.json
```
Logs go to `%TEMP%/influencer_reel/<slug>_watchdog.log` and `<slug>_render.log`. `engine/after_then.py --pid N --plan ...` starts a render when another process exits.

## New narrators

```bash
python yt.py new-narrator --id ada --name "Dr. Ada Price" --pronoun she \
  --identity "A woman in her fifties, ... (fixed physical traits only)" \
  --outfit "..." --scene "a bright university lab with whiteboards behind her" \
  --camera "Medium shot, static camera on a tripod, framed from the waist up" \
  --lighting "Soft daylight from a window, shallow depth of field, realistic skin texture." \
  --voice-style "a warm, clear voice with a gentle Scottish accent and an unhurried cadence" \
  --personality "..." --ambience "Quiet room tone, no music." --seed 42
```
This makes a 16:9 portrait, invents the voice once with LTX-2 and saves `voice.wav`. Approve both before making videos. Keep microphones and props away from the mouth, or lip-sync is hidden. The voice settings live in `profile.json` → `tts`.

## Benchmarks (RTX 5070 Ti 16 GB, 64 GB RAM)

| Video | Time |
|---|---|
| 2 min, 1280x704 (14 segments) | ~40 min |
| 20 min, 1280x704 (159 segments) | 9.2 h (overnight with the watchdog) |
| 15 s, native 1920x1088 | 13 min, peak VRAM 15.8 / 16.3 GB |
| 15 s, 1280x704 + upscale to 1080p | 7 min 49 s + 25 s |

By eye, the upscale is close to native 1080p, so it's the recommended way to make 1080p.

## Other languages

Narrators can speak other languages through the same `tts.engine` options as influencers (Chatterbox Multilingual, optional fine-tunes such as Slovak, or Piper + voice conversion). See the root README's [Languages](../README.md#languages) section. Write the plan's segments in that language and resize the word budget to its speaking rate.

## Voice tuning

In Chatterbox, **lower `cfg_weight` follows the reference clip's accent** (0.3 very closely, but slowly) and higher values drift towards the model's default American accent (1.0 sounded American). `temperature` 0.5 keeps the accent consistent between takes. If a narrator's accent still varies with the seed, choose a take you like and save a 10-second cut of it as the new reference (`tts.voice`). That's how Vale's `voice_v2_british.wav` was made.
