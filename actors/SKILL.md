---
name: actors
description: Create and manage reusable AI actors (fixed face, cloned voice in one or more languages, outfits/settings as "looks") stored in actors/<id>/ and usable in any video (the video skill). Use when the user asks to create, describe, change, list or give a new language or look to a character/actor/presenter/influencer/narrator.
---

# Actors (shared characters)

Tool: `actors.py` in this folder; shared code in `../engine/actors.py`. One actor = `actors/<id>/actor.json` + its reference voice clip(s) and look images. Any actor works in any format: a reel (9:16) or YouTube (16:9) creates the look image it needs the first time (Flux.2 edit that keeps the face). README.md has the full format.

## 0. Preconditions
`COMFYUI_DIR` set and ComfyUI running (see the video skill's preconditions). `list`, `show` and `voice` don't need ComfyUI.

## 1. Turn the request into fields
*"Create an actor named Jane, brown eyes, speaks French"* becomes:

| Field | How to fill it |
|---|---|
| `--id` / `--name` | lowercase id from the name (`jane`); check `python actors.py list` for clashes |
| `--pronoun` | from the request; ask only if it truly isn't clear (or use `they`) |
| `--identity` | **fixed physical traits only**: age, build, skin/complexion, face, eyes, hair (colour, length, style). Fill gaps sensibly and tell the user. No clothes, no setting, no expression: those vary by look and shot |
| `--outfit`, `--scene` | the default look; if not given, choose something that fits their personality and say so |
| `--languages` | main language first (`fr,en`). Chatterbox Multilingual covers ar da de el en es fi fr he hi it ja ko ms nl no pl pt ru sv sw tr zh; other languages need a fine-tune or piper_vc (root README: Languages) |
| `--voice-style` | in English: timbre, pace, energy and accent, e.g. "a warm, bright young woman's voice, native Parisian French, relaxed pace". For a second language with an accent, describe it here too ("…speaks English with a light French accent") |
| `--personality` | how they talk (used when writing their scripts) |
| `--format` | `9x16` if they're mainly for reels, else `16x9` (only affects the first portrait) |
| `--seed` | any number; a new one re-rolls the face |

Never base an actor on a real person (face, name or voice). Show the user the fields before running anything.

## 2. Create, then approve
```bash
python actors.py new --id jane --name "Jane" --pronoun she --identity "..." --outfit "..." --scene "..." \
  --voice-style "..." --languages fr,en --personality "..." --seed 5150          # ~4 min
python actors.py sample --id jane --lang fr        # ~30 s each: one per language
python actors.py sample --id jane --lang en
```
Send the portrait (`looks/default/<format>.png`) and the samples; iterate until approved:
- wrong face: delete `actors/<id>` and re-run `new` with another `--seed` (or adjust `--identity`)
- wrong voice: re-run with a clearer `--voice-style` / `--sample-line`; if the accent drifts towards American, lower the language's `cfg_weight` in actor.json (0.4 -> 0.3), or pick a sample the user likes and make a 10 s cut of it the new reference (`voice` in actor.json), as Vale's and Clive's `voice_v2_british.wav` were made
- add a language later: `python actors.py voice --id jane --lang en`, then `sample`

## 3. Use them anywhere
- Video plans (the video skill): put them in the plan's `cast` - with a `look`, or `scene` + `outfit` for a new one, and `"angle": "left"/"right"` for conversations (turned image made on demand). Any format works.
- Lines in their other languages use their voice for that language (`lang`, or `{braces}` inside a line).
- New looks: `python actors.py look --id jane --look paris-cafe --outfit "..." --scene "..."` (or just put `scene`/`outfit` on their cast entry in a video plan).
