# comfy-influencer-reel

Vertical (704x1280, 9:16) talking-head reels made locally in ComfyUI, on any topic, by **named AI influencers** with a fixed face, a cloned voice and any number of outfit/scene **looks**. It uses the shared narration-first pipeline: Chatterbox speaks the whole script in the influencer's voice, then LTX-2.3 image+audio clips are lip-synced to it as **one continuous shot** (each clip continues from the previous clip's last frame).

Included influencers:

| ID | Name | Looks | Voice |
|---|---|---|---|
| `maya` | Maya, fitness and wellness creator | `default` (kitchen), `gym` | upbeat, friendly |
| `james` | James Whitford, British finance expert | `default` (home office), `canal` (sunny London canal) | low-pitched, West London |

## Quick start

```bash
python reel.py list                                                     # influencers and their looks
python reel.py make --plan plans/maya-progressive-overload/plan.json --dry-run
python reel.py make --plan plans/maya-progressive-overload/plan.json   # ~6-8 min for 15 s
```
Output: `<ComfyUI>/output/reels/<slug>/<slug>_final.mp4` (plus `_1080p.mp4` if `upscale_to` is set).

## Writing a plan

A plan is one JSON file, `plans/<slug>/plan.json`. It holds everything about **this reel** (topic, script, setting, movement). Who is speaking (face, voice, camera style, lighting) comes from the influencer's profile.

### 1. Pick the influencer
Run `python reel.py list`. Use an existing `id`, or create a new influencer first (see [New influencers](#new-influencers)).

### 2. Pick the length and size the script
The narrator speaks about **3 words per second**, plus a 1-second silent hold at the end:

| Length | Words |
|---|---|
| 10 s | ~27 |
| 15 s | ~42 |
| 20 s | ~57 |
| 30 s | ~87 |

**Words ≈ (seconds − 1) × 3.** Shorter scripts just give a shorter reel; `make --dry-run` prints the estimate.

### 3. Write the script as segments
- **One segment ≈ one sentence or two, at most ~26 words.** Each segment becomes one clip; longer clips risk running out of VRAM.
- End every segment at a sentence end, because the narration is cut in the pause after it.
- Write it the way the influencer talks (their `personality` in `influencers/<id>/profile.json`): hook first, one or two concrete points, a short close.
- Spell out numbers the way they should be said ("five grams", "twenty twenty six").
- No stage directions, hashtags or emojis in the text. It's spoken verbatim.
- Or give one `"script"` string instead of `"segments"`, and it's split at sentence ends automatically (`|` forces a break).

### 4. Choose the setting
- `"look": "gym"` uses a saved look (outfit + scene + ambience + image).
- **Or** describe a new one with `"scene"` (and optionally `"outfit"`, `"ambience"`). The first run creates it with Flux.2 (about 3 min; the face stays the same) and saves it to the profile for reuse. Name it with `"look"`, otherwise it's named after the scene.
- `"action"` is what they do while talking (gestures, walking, holding a product). It defaults to the profile's `default_action`.

### 5. The plan file

```json
{
  "title": "Progressive overload in 15 seconds",
  "slug": "maya-progressive-overload",
  "influencer": "maya",
  "topic": "progressive overload for gym beginners",
  "seconds": 15,
  "seed": 424242,
  "look": "gym",
  "action": "with small natural head movements, raised eyebrows and a light hand gesture on each point, finishing with a confident nod and a smile.",
  "segments": [
    {"text": "Real talk: if your workouts feel stuck, try progressive overload. It just means doing a little more each week."},
    {"text": "One extra rep, or slightly heavier weights. Small steps, every single week. Your body adapts, and you get stronger. Trust me."}
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `title` | yes | Human-readable name (also the ComfyUI sidebar folder). |
| `slug` | yes | Folder name for outputs; lowercase-with-dashes. |
| `influencer` | yes | Influencer `id`. |
| `segments` or `script` | yes | The exact words, as segments (`[{"text": ...}]`) or one string. |
| `seconds` | no | Target length; used only for the word estimate. |
| `seed` | no | Change it for a different take (voice delivery and motion). Defaults to the influencer's seed. |
| `look` | no | Saved look name (default `default`), or the name for a new look made from `scene`/`outfit`. |
| `scene`, `outfit`, `ambience` | no | Describe a new setting; it's created on the fly and saved. |
| `action` | no | Movement while talking, e.g. `"walking slowly along the towpath, holding the phone at arm's length"`. |
| `topic` | no | A note for you; not used for rendering. |
| `width`, `height` | no | Default 704x1280; must be multiples of 64. |
| `upscale_to` | no | e.g. `[1080, 1920]` for a 1080p copy (fast Lanczos + sharpen). |

### 6. Check, then render
```bash
python reel.py make --plan plans/<slug>/plan.json --dry-run   # segments, word counts, estimated length
python reel.py make --plan plans/<slug>/plan.json
```
Re-running the same plan **resumes**: narration takes and clips with identical settings are reused, so a crash or a script tweak only redoes what changed.

### Shortcut: `quick`
`quick` writes the plan for you from command-line arguments, then makes it:
```bash
python reel.py quick --id james --seconds 15 --title "isa basics" \
  --scene "a canal towpath in London on a bright sunny day" \
  --action "walking slowly, holding the phone at arm's length" \
  --ambience "Light outdoor breeze, distant birdsong, no music." \
  --script "Right, here's the thing about ISAs. ..."
```

## New influencers

```bash
python reel.py new --id jordan --name "Jordan" --pronoun she \
  --identity "A woman in her early thirties, ... (fixed physical traits only)" \
  --outfit "..." --scene "..." --voice-style "a calm, warm voice with a slight Irish accent" \
  --personality "..." --seed 1234
```
This makes the portrait (Z-Image Turbo), then invents a voice from `--voice-style` (an LTX-2 video, once) and saves a clean clip as `voice.wav`. Listen to it before making reels. Optional: `--camera`, `--lighting`, `--default-action` (selfie-reel defaults) and `--sample-line`.

New looks: `python reel.py look --id maya --look beach --outfit "..." --scene "..." [--ambience "..."]`.

**Voice tuning** lives in `profile.json` → `tts` (defaults: `cfg_weight` 0.4, `temperature` 0.5). Lower `cfg_weight` follows the reference clip's accent more closely; higher drifts towards the model's default American accent. If the accent still varies between takes, pick a take you like and save a 10 s cut of it as the new reference (`tts.voice`).

`python reel.py save-ui --id maya` publishes ready-to-run Chatterbox and image+audio workflows (voice and portrait preloaded) to the ComfyUI **Workflows** sidebar.

## Timing (RTX 5070 Ti 16 GB)

| Step | Time |
|---|---|
| New look (Flux.2) | ~2.5-3 min |
| New influencer (portrait + voice) | ~3 min |
| Narration (per ~80-word take) | ~30-45 s |
| Each clip (≤10 s) | ~1.5-3 min |
