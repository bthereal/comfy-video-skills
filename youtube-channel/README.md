# youtube-channel

16:9 YouTube videos of any length, made locally in ComfyUI. A named AI narrator appears on camera, randomly intercut with generated b-roll footage, over **one continuous narration** so speech never resets at a cut. Optional 1080p upscale.

Built-in narrator: **Dr. Simon Vale** (`vale`), a British academic in a podcast studio. Mid-toned, authoritative voice; Chatterbox `cfg_weight` 0.4 and `temperature` 0.5 with `voice_v2_british.wav`.

Example plans in `projects/`: `jfk-conspiracy` (1 min), `moon-landing` (2 min), `moon-landing-20min` (20 min, 159 segments), `moon-landing-1080p` (native 1080p benchmark) and `moon-landing-704up` (704p plus upscale benchmark).

## Pipeline (narration-first)

1. **Narration:** Chatterbox TTS speaks the whole script in the narrator's cloned voice, in takes of up to 80 words. Whisper checks each take: it must end with the script's last words, contain no inserted or repeated words, and have every segment ending present. Failing takes are re-recorded with new seeds; if that keeps failing, the take's segments are recorded one at a time.
2. **Slicing:** Whisper word timings place each cut in the pause after a segment's last word (anchored on the next segment's first words), padded to LTX's 0.32 s frame grid. The last word gets at least 1 s of silence.
3. **Clips:** each slice gets one LTX-2.3 **image+audio** clip. On-camera segments lip-sync the narrator's portrait; footage segments animate a Z-Image still under the voice-over. Slices too long for VRAM are split into chained parts.
4. **Assembly:** hard cuts, each clip trimmed to exactly its slice, with the continuous narration as the soundtrack (streamed, so any length fits in RAM). Optional Lanczos + sharpen upscale to 1920x1080.

The on-camera/footage split is random but seeded: first and last segments are on camera, there are never 3 footage segments in a row, and about 40% are on camera. Force a segment with `"type": "narrator"` or `"footage"`.

## Usage

```bash
python yt.py list
python yt.py make --plan projects/moon-landing/plan.json --dry-run   # shows split, timings and script
python yt.py make --plan projects/moon-landing/plan.json             # render (resumes if re-run)
python yt.py new-narrator --id ada --name "Dr. Ada Price" --pronoun she --identity "..." --outfit "..." \
  --scene "..." --voice-style "..." --personality "..." --seed 42
```

**Long or overnight renders:** launch `watchdog.py --plan <plan>` as a detached process. It restarts ComfyUI if it dies and re-runs `make`, which reuses finished takes and clips, until the final video exists. Logs go to `%TEMP%/influencer_reel/<slug>_watchdog.log` and `<slug>_render.log`. `after_then.py --pid N --plan <plan>` starts a render when another process exits.

### plan.json

```json
{
  "title": "Was the Moon Landing Faked", "slug": "moon-landing", "narrator": "vale",
  "seconds": 120, "seed": 1969, "narrator_share": 0.4,
  "style": "cinematic documentary still, Apollo era aesthetic, 35mm film grain",
  "ambience": "Low, atmospheric ambient hum, no music.",
  "upscale_to": [1920, 1080],
  "segments": [ {"text": "exact narration (22-27 words)", "shot": "what the footage shows", "type": "narrator"} ]
}
```

- **Script sizing:** about 3.1 words per second including pauses, so 1 min is about 185 words and 20 min about 3,700 words. Keep segments to ≤28 words; each should be a complete thought ending at a sentence end.
- **Shots:** places, objects and atmospheres. Avoid recreating real people's faces, graphic violence and legible text (it comes out garbled).
- **Resolution:** the default is 1280x704. For native 1080p set `"width": 1920, "height": 1088`; `"upscale_to"` is the cheaper route (see benchmark below).

## Benchmarks (RTX 5070 Ti 16 GB, 64 GB RAM)

| Video | Time |
|---|---|
| 2 min, 704p (14 segments) | ~40 min |
| 20 min, 704p (159 segments) | 9.2 h (overnight with watchdog) |
| 15 s, native 1080p | 13 min, peak VRAM 15.8 / 16.3 GB |
| 15 s, 704p + upscale to 1080p | 7 min 49 s + 25 s upscale |

By eye, the upscale is close to native 1080p, so it's the recommended way to make 1080p.

## Files

- `yt.py`: CLI: plans, the random split, narration takes, clip rendering, resume, assembly (`--legacy` = old per-segment LTX speech).
- `narration.py`: Chatterbox takes, Whisper checks (`ends_complete`, `extra_words`, `segment_bounds`), grid slicing, `render_ia2v`, streaming `assemble`, `upscale_video`.
- `watchdog.py`, `after_then.py`: unattended rendering.
- `workflows/chatterbox_tts.json`: the TTS workflow. The video workflows live in `../comfy-influencer-reel/workflows/`.
- `narrators/vale/`: `profile.json` (look, voice description, personality, TTS settings), `portrait.png`, `voice.wav` (original LTX-invented voice), `voice_v2_british.wav` (current reference).
- `SKILL.md`: the full operating notes Claude follows, including everything learned while tuning (accent anchoring, pacing, VRAM caps).

## Voice tuning notes

Chatterbox's `cfg_weight` works the opposite way to what you might expect. **Higher values pull towards the model's default American accent** (1.0 sounded American); lower values follow the reference clip (0.3 was very British but slow). `temperature` 0.5 stops the accent drifting between takes. If a narrator's accent still varies with the seed, pick a take you like and use a 10 s cut of it as the new reference clip.
