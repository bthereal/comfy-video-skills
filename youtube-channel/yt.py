"""YouTube videos: a named narrator (fixed face + cloned voice) on camera, randomly intercut with generated b-roll
footage, over one continuous narration. 16:9 (1280x704 by default; optional upscale to 1080p).

  python yt.py list
  python yt.py make --plan projects/<slug>/plan.json [--dry-run]
  python yt.py new-narrator --id ada --name "Dr. Ada Price" --pronoun she --identity "..." --outfit "..." \
                            --scene "..." --voice-style "..." [--personality "..."] [--camera "..."] [--lighting "..."] \
                            [--ambience "..."] [--sample-line "..."] [--seed N]

Every topic-specific detail comes from the plan (title, genre, style, ambience, segments + shots) or the narrator's
profile (look, setting, camera, lighting, voice). See README.md for the plan format and how to write one.
Requires COMFYUI_DIR and a running ComfyUI (start-comfyui.bat in the repo root).
"""
import argparse, json, random, sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL.parent))
from engine import comfy, narration as N, video as V  # noqa: E402

NARR = SKILL / "narrators"
W, H = 1280, 704  # 16:9 at the same pixel count as the 704x1280 reels (fits 16 GB VRAM); multiples of 64
REQUIRED = ("title", "slug", "narrator", "style", "segments")


# ---------------------------------------------------------------- narrators
def load(nid):
    p = NARR / nid / "profile.json"
    if not p.exists():
        known = ", ".join(d.name for d in NARR.iterdir() if (d / "profile.json").exists()) if NARR.exists() else ""
        raise SystemExit(f"No narrator '{nid}'. Known: {known or 'none'}")
    return json.loads(p.read_text(encoding="utf-8-sig"))


def cmd_list(a):
    for d in sorted(NARR.iterdir()) if NARR.exists() else []:
        if (d / "profile.json").exists():
            p = json.loads((d / "profile.json").read_text(encoding="utf-8-sig"))
            print(f"{p['id']:8} {p['name']:18} {p['identity'][:90]}...")


def cmd_new_narrator(a):
    comfy.require_server()
    nid = a.id.lower()
    d = NARR / nid
    if (d / "profile.json").exists():
        raise SystemExit(f"'{nid}' already exists")
    d.mkdir(parents=True, exist_ok=True)
    prof = {"id": nid, "name": a.name, "pronoun": a.pronoun, "identity": a.identity, "outfit": a.outfit,
            "scene": a.scene, "camera": a.camera, "lighting": a.lighting, "default_action": a.default_action,
            "voice_style": a.voice_style, "personality": a.personality, "ambience": a.ambience, "seed": a.seed,
            "tts": dict(N.DEFAULT_TTS)}
    print("1/2 portrait (Z-Image Turbo, 16:9)")
    still = (f"16:9 photograph. {a.camera}. {a.identity} Wearing {a.outfit}, in {a.scene}. Looking straight into "
             f"the camera with a composed, confident expression, mouth closed. {a.lighting} Sharp focus on the face.")
    wf, out = comfy.make_portrait(still, 1280, 720, a.seed, f"youtube/narrators/{nid}_portrait")
    (d / "portrait.png").write_bytes(Path(out).read_bytes())
    comfy.save_to_ui(wf, f"YouTube/Narrators/{a.name} - portrait")
    print("2/2 voice (LTX-2 invents it once; Chatterbox clones it for every video)")
    img = comfy.upload(d / "portrait.png", f"yt_{nid}__portrait.png")
    line = a.sample_line or ("Hello, and welcome. Today I want to talk about something I find genuinely fascinating, "
                             "and by the end I think you'll see it in a whole new light.")
    prompt = (f"{V.person_visual(prof, a.outfit, a.scene, None)} {a.pronoun.capitalize()} says with {a.voice_style}: "
              f"\"{line}\" Clear close-microphone voice. {a.ambience}")
    _, out = comfy.invent_voice(img, prompt, a.seed, W, H, f"youtube/narrators/{nid}_voice_source")
    comfy.extract_voice(out, d / "voice.wav")
    (d / "profile.json").write_text(json.dumps(prof, indent=2), encoding="utf-8")
    print(f"\nCreated {a.name}: {d}\n  Approve portrait.png and the voice ({out}) before making videos.")


# ---------------------------------------------------------------- planning
def assign_types(n, share, rng, forced):
    """First/last = narrator; never 3 footage in a row; ~share on camera (seeded). forced[i] overrides."""
    types = [forced[i] or ("narrator" if i in (0, n - 1) else None) for i in range(n)]
    trial = [t or "footage" for t in types]
    free = {i for i in range(n) if types[i] is None}
    for i in range(n - 2):
        if trial[i] == trial[i + 1] == trial[i + 2] == "footage":
            cand = [i + 2] if (i + 2) in free else ([j for j in (i, i + 1) if j in free] or [i + 1])
            trial[rng.choice(cand)] = "narrator"
    pool = [j for j in free if trial[j] == "footage"]
    rng.shuffle(pool)
    for j in pool[:max(0, round(share * n) - trial.count("narrator"))]:
        trial[j] = "narrator"
    return trial


def validate(plan):
    missing = [k for k in REQUIRED if not plan.get(k)]
    if missing:
        raise SystemExit(f"plan is missing: {', '.join(missing)} (see README 'Writing a plan')")
    for i, s in enumerate(plan["segments"], 1):
        if not s.get("text"):
            raise SystemExit(f"segment {i} has no text")
        if s.get("type") != "narrator" and not s.get("shot"):
            raise SystemExit(f"segment {i} needs a 'shot' (it may be chosen for footage)")


# ---------------------------------------------------------------- make
def cmd_make(a):
    plan = json.loads(Path(a.plan).resolve().read_text(encoding="utf-8-sig"))
    validate(plan)
    prof = load(plan["narrator"])
    segs = plan["segments"]
    seed = plan.get("seed", prof["seed"])
    types = assign_types(len(segs), plan.get("narrator_share", 0.4), random.Random(seed), [s.get("type") for s in segs])
    for s, t in zip(segs, types):
        s["type"] = t
    texts = [s["text"] for s in segs]
    est = N.estimate_seconds(texts)
    print(f"'{plan['title']}' ({plan.get('genre', 'no genre set')}) - narrator {prof['name']}, {len(segs)} segments, "
          f"{sum(len(t.split()) for t in texts)} words, ~{sum(est) + 1:.0f}s (target {plan.get('seconds', '?')}s), "
          f"{types.count('narrator')} on camera / {types.count('footage')} footage")
    for i, (s, x) in enumerate(zip(segs, est), 1):
        print(f"  {i}. {s['type']:8} ~{x:4.1f}s {len(s['text'].split()):3}w  {s['text'][:70]}")
    too_long = [i + 1 for i, x in enumerate(est) if x > 10]
    if too_long:
        raise SystemExit(f"Segments {too_long} would run over 10s: keep segments to <= {int(9.4 * N.NWPS)} words")
    if a.dry_run:
        return
    comfy.require_server()
    slug = plan["slug"]
    out_prefix = f"youtube/{slug}"
    out_dir = comfy.COMFY_OUT / "youtube" / slug
    tts = N.tts_settings(prof)
    voice = comfy.upload(NARR / prof["id"] / tts["voice"], f"yt_{prof['id']}__{tts['voice']}")
    slices, sr, secs, narration = N.narrate(texts, tts, seed, out_dir / "narration", out_prefix, voice)

    vw, vh = plan.get("width", W), plan.get("height", H)
    if vw % 64 or vh % 64:
        raise SystemExit(f"width/height must be multiples of 64 (got {vw}x{vh}; use 1920x1088 for native '1080p')")
    portrait = comfy.upload(NARR / prof["id"] / "portrait.png", f"yt_{prof['id']}__portrait.png")
    narrator_action = plan.get("narrator_action")  # optional: overrides the profile's default_action for this video
    items = []
    for i, s in enumerate(segs, 1):
        tag = f"{slug}_{i:02d}_{s['type']}"
        if s["type"] == "footage":
            still = out_dir / f"{slug}_{i:02d}_still.png"
            if not still.exists():
                wf, out = comfy.make_portrait(V.still_prompt(plan, s["shot"]), vw, round(vw * 9 / 16), seed + i,
                                              f"{out_prefix}/stills/{tag}", label=f"segment {i} still")
                still.parent.mkdir(parents=True, exist_ok=True)
                still.write_bytes(Path(out).read_bytes())
            items.append({"tag": tag, "image": comfy.upload(still, f"{slug}_{i:02d}_still.png"),
                          "prompt": V.footage_visual(plan, s["shot"]), "seed": seed + i, "label": f"segment {i} footage"})
        else:
            items.append({"tag": tag, "image": portrait, "seed": seed + i, "label": f"segment {i} narrator",
                          "prompt": V.person_visual(prof, prof["outfit"], prof["scene"], s.get("action") or narrator_action)})
    clips, clip_secs = V.render_clips(items, slices, sr, out_dir, out_prefix, vw, vh, f"YouTube/{plan['title']}")
    final = V.finish(clips, clip_secs, narration, sr, out_dir, slug, plan)
    (out_dir / "plan_resolved.json").write_text(json.dumps({**plan, "segments": segs, "seconds_each": secs}, indent=2),
                                                encoding="utf-8")
    print(f"\nVideo: {final}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    n = sub.add_parser("new-narrator")
    n.add_argument("--id", required=True); n.add_argument("--name", required=True)
    n.add_argument("--pronoun", default="they", choices=["she", "he", "they"])
    n.add_argument("--identity", required=True, help="fixed physical traits: age, build, face, hair")
    n.add_argument("--outfit", required=True); n.add_argument("--scene", required=True, help="where they're filmed")
    n.add_argument("--voice-style", required=True); n.add_argument("--personality", default="")
    n.add_argument("--camera", default="Medium shot, static camera on a tripod, framed from the waist up")
    n.add_argument("--lighting", default="Soft, flattering light, shallow depth of field, realistic skin texture.")
    n.add_argument("--default-action", default="with measured, expressive hand gestures and occasional nods for emphasis.")
    n.add_argument("--ambience", default="Quiet room tone, no music."); n.add_argument("--sample-line")
    n.add_argument("--seed", type=int, default=1234)
    m = sub.add_parser("make")
    m.add_argument("--plan", required=True); m.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    {"list": cmd_list, "new-narrator": cmd_new_narrator, "make": cmd_make}[a.cmd](a)


if __name__ == "__main__":
    main()
