# comfy-influencer-reel

Vertical (704x1280, 9:16) talking-head reels made locally in ComfyUI, on any topic, by **named AI influencers** with a fixed face, a cloned voice and any number of outfit/scene "looks".

Built-in influencers:

| ID | Name | Looks | Voice |
|---|---|---|---|
| `maya` | Maya, fitness and wellness creator | `default` (kitchen), `gym` | upbeat, friendly |
| `james` | James Whitford, British finance expert | `default` (home office), `canal` (sunny London canal) | low-pitched, West London |

## How it works

1. **Influencer:** Z-Image Turbo makes the portrait. LTX-2 invents a voice from a text description, and a clean 5-9 s clip of it becomes `voice.wav`.
2. **Look:** Flux.2 Dev edits the portrait, changing only clothes and location, so the face stays the same.
3. **Reel:** LTX-2.3 + **ID-LoRA** generates video and speech together from the look image, the voice clip and a tagged prompt (`[VISUAL]` / `[SPEECH]` / `[SOUNDS]`). Reels over 10 s are rendered as chained segments (each starts on the previous one's last frame) and joined.
4. **Checks:** Whisper confirms each segment's final words were spoken (one automatic re-render if not). The last word is always followed by at least 1 s of silence (the last frame is held if needed). Audio is kept sample-locked to video when joining.

## Usage

```bash
python reel.py list
python reel.py reel --id maya --look gym --seconds 15 --title progressive-overload \
  --script "Real talk: if your workouts feel stuck, try progressive overload. | One extra rep, or slightly heavier weights. Trust me."
python reel.py reel --id james --seconds 15 --scene "a canal towpath in London on a bright sunny day" \
  --action "walking slowly, holding the phone at arm's length" --ambience "Light breeze, birdsong, no music." --script "..."
python reel.py look --id maya --look beach --outfit "..." --scene "..."
python reel.py new  --id jordan --name "Jordan" --pronoun she --identity "..." --outfit "..." --scene "..." \
  --voice-style "a calm, warm voice with a slight Irish accent" --personality "..." --seed 1234
python reel.py save-ui --id maya     # publish ready-to-run workflows to the ComfyUI sidebar
```

- **Script length** (the 1 s end rule is built in): 10 s is about 28 words, 15 s about 41, 25 s about 71. The tool refuses scripts more than 8% too long (`--force` overrides). `|` marks segment breaks for reels over 10 s.
- `--scene` / `--outfit` make a new look on the fly (about 2.5 min, then reused). `--seed N` gives a different take.
- Output: `<ComfyUI>/output/reels/<id>/<title>_full.mp4`. Every run is also saved to the ComfyUI **Workflows** sidebar under `Influencers/<Name>/`.

## Timing (RTX 5070 Ti 16 GB)

| Step | Time |
|---|---|
| Portrait | ~20 s |
| New look (Flux.2) | ~2.5-3 min |
| Voice invention (new influencer) | ~2.5 min |
| Each reel segment (≤10 s) | ~2.5-4.5 min |

## Files

- `reel.py`: CLI plus the shared engine used by `youtube-channel` (job submission with reset-safe retries, uploads, Whisper `speech_check`, `join_segments`).
- `start-comfyui.bat`: starts ComfyUI on localhost (set `COMFYUI_DIR` if yours isn't at the default).
- `workflows/`: `z_image_turbo`, `flux2_dev`, `ltx2_i2v_distilled`, `ltx2_3_id_lora`, `ltx2_3_ia2v`.
- `SKILL.md`: the full operating notes Claude follows (request parsing, prompt formats, measured limits, failure handling).

## Known limits

- **Face drift:** chained segments over 10 s can drift by the last part. Single 10 s reels don't.
- **Walking shots** read more as handheld sway than a clear walk.
- **1080p:** native 1080p reels are untested; prefer 704x1280.
