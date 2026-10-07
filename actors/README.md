# actors

Reusable AI characters shared by every skill: a fixed face, a cloned voice (in one or more languages), a personality, and any number of **looks** (outfit + setting). Create an actor once and use them in any video (the `video` skill): talking to camera, in a conversation, narrating, in 16:9 or 9:16.

Included examples: `maya` (fitness creator), `james` (British finance expert) and `vale` (British academic narrator). Every other actor you create stays local: `actors/*` is git-ignored apart from these three.

## Creating an actor

```bash
python actors.py new --id jane --name "Jane" --pronoun she \
  --identity "A woman in her early thirties, slim build, fair skin with light freckles, brown eyes, shoulder-length wavy chestnut hair" \
  --outfit "a navy and white striped Breton top" --scene "a bright Paris cafe terrace" \
  --voice-style "a warm, bright young woman's voice, native Parisian French, relaxed pace" \
  --languages fr,en --personality "Curious, witty, patient; explains things simply" --seed 5150
python actors.py sample --id jane --lang fr      # listen before using her
python actors.py sample --id jane --lang en
```

1. **Portrait:** Z-Image Turbo makes the first portrait in the default look (`--format 16x9` or `9x16`).
2. **Voice:** LTX-2 invents a voice once, speaking a line in the main language. A clean clip becomes `voice.wav`, and Chatterbox clones it for everything the actor says. Other languages use the same clip, through Chatterbox Multilingual (or English Chatterbox for English).
3. **Approve:** look at the portrait and listen to a `sample` in each language before using the actor. To re-roll a face, delete the folder and use another `--seed`.

Keep `--identity` to fixed physical traits (age, build, skin, face, eyes, hair). Clothes and setting belong to looks, expressions to the video. Don't base an actor on a real person.

## Commands

| Command | Does |
|---|---|
| `list` / `show --id x` | Who exists, what they speak, their looks and which images exist |
| `new ...` | Create an actor (portrait + voice) |
| `look --id x --look name --outfit ... --scene ...` | Add a look (image made for `--format`) |
| `image --id x [--look name] --format 9x16 [--angle left]` | Make a look image now instead of on first use |
| `voice --id x --lang en` | Add a language (same voice clip) |
| `sample --id x --lang fr [--text ...]` | Record a test line in a language |

## actor.json

```json
{
  "id": "jane", "name": "Jane", "pronoun": "she",
  "identity": "A woman in her early thirties, ... (fixed physical traits)",
  "personality": "...", "voice_style": "...", "seed": 5150,
  "language": "fr",
  "tts":    {"engine": "chatterbox_mtl", "language": "fr", "voice": "voice.wav", "cfg_weight": 0.5, "exaggeration": 0.5, "temperature": 0.6},
  "voices": {"en": {"voice": "voice.wav", "cfg_weight": 0.4, "exaggeration": 0.5, "temperature": 0.5}},
  "looks": {
    "default": {"outfit": "...", "scene": "...", "ambience": "Quiet room tone, no music.",
                "images": {"16x9": "looks/default/16x9.png", "9x16": "looks/default/9x16.png", "16x9_left": "looks/default/16x9_left.png"}}
  },
  "formats": {"9x16": {"camera": "...", "lighting": "...", "default_action": "..."}}
}
```

| Field | Meaning |
|---|---|
| `tts` | Main voice: the engine and settings for their main language (see the root README's Languages). |
| `voices` | Other languages: `{lang: settings}`. A line in a language they don't have is said in their own accent (useful for learners). |
| `looks` | Outfit, setting and background sound. `images` holds one image per format, created on demand: `9x16` (reels), `16x9` (YouTube), `16x9_left` / `16x9_right` (turned towards that edge, for dialogue). |
| `formats` | Optional per-format framing overrides (camera, lighting, default action); otherwise each format's defaults apply. |

Paths are relative to the actor's folder. Voice tuning (accent drift, cfg_weight, a 10-second reference cut from a take you like) is described in the skills' READMEs.
