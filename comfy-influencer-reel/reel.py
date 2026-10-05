"""Influencer reels: named influencers (fixed face + cloned voice + saved looks) talking to camera on any topic.
Vertical 704x1280. Narration-first, same pipeline as youtube-channel: Chatterbox speaks the whole script in the
influencer's voice, then LTX-2.3 image+audio clips are lip-synced to it as one continuous shot.

  python reel.py list
  python reel.py make --plan plans/<slug>/plan.json [--dry-run]          # the normal way (see README for the format)
  python reel.py quick --id maya --seconds 15 --script "..." [--look gym | --scene "..." --outfit "..."] [--action "..."]
                                                                          # writes plans/<slug>/plan.json, then makes it
  python reel.py look --id maya --look beach --outfit "..." --scene "..." [--ambience "..."]
  python reel.py new  --id jordan --name "Jordan" --pronoun she --identity "..." --outfit "..." --scene "..." \
                      --voice-style "..." [--personality "..."] [--sample-line "..."] [--seed N]
  python reel.py save-ui --id maya       # publish ready-to-run workflows to the ComfyUI sidebar

Requires COMFYUI_DIR and a running ComfyUI (start-comfyui.bat in the repo root).
"""
import argparse, json, sys, time
from pathlib import Path

SKILL = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL.parent))
from engine import comfy, narration as N, video as V  # noqa: E402

INFL = SKILL / "influencers"
PLANS = SKILL / "plans"
W, H = 704, 1280  # 9:16; proven on 16 GB VRAM; must be multiples of 64

SELFIE_CAMERA = "Vertical selfie-style handheld phone video, medium close-up"
SELFIE_LIGHTING = "Natural light, realistic skin texture, social media reel look."


# ---------------------------------------------------------------- profiles
def load(pid):
    p = INFL / pid / "profile.json"
    if not p.exists():
        known = ", ".join(x.name for x in INFL.iterdir() if (x / "profile.json").exists())
        raise SystemExit(f"No influencer '{pid}'. Known: {known}")
    return json.loads(p.read_text(encoding="utf-8"))


def store(prof):
    (INFL / prof["id"] / "profile.json").write_text(json.dumps(prof, indent=2), encoding="utf-8")


def noun(prof):
    return {"she": "woman", "he": "man"}.get(prof.get("pronoun"), "person")


def upload_look(prof, name):
    return comfy.upload(INFL / prof["id"] / prof["looks"][name]["image"], f"{prof['id']}__{name}.png")


# ---------------------------------------------------------------- looks
def make_look(prof, name, outfit, scene, ambience=None, seed=None):
    """Flux.2 Dev edit of the default portrait: same face, new outfit/scene. Stores and returns the look."""
    print(f"New look '{name}' for {prof['name']} (Flux.2 Dev edit, keeps the face)")
    wf = comfy.work_copy("flux2_dev.json", f"{prof['id']}_look_{name}")
    p = prof.get("pronoun", "they")
    edit = (f"Keep the exact same {noun(prof)} from the reference image: identical face, facial features, skin tone, "
            f"hairstyle and hair color. Change only the clothing and the location. {p.capitalize()} is now wearing "
            f"{outfit}, in {scene}. Vertical smartphone selfie photo, framed from mid-chest up, centered, looking "
            f"straight into the camera with a warm, confident expression, mouth closed. Natural light, realistic skin "
            f"texture, phone front camera.")
    comfy.set_slots(wf, {"46.image": upload_look(prof, "default"), "68.text": edit,
                         "68.vae_name": "flux2-vae.safetensors", "68.value": True, "68.noise_seed": seed or prof["seed"],
                         "9.filename_prefix": f"influencers/{prof['id']}/look_{name}"})
    out = comfy.run(wf, "look")
    dst = INFL / prof["id"] / "looks" / f"{name}.png"
    dst.parent.mkdir(exist_ok=True)
    dst.write_bytes(Path(out).read_bytes())
    prof["looks"][name] = {"outfit": outfit, "scene": scene,
                           "ambience": ambience or "Quiet room tone, no music.", "image": f"looks/{name}.png"}
    store(prof)
    comfy.save_to_ui(wf, f"Influencers/{prof['name']}/Look - {name} (Flux.2 edit)")
    return prof["looks"][name]


def resolve_look(prof, plan):
    """plan["look"] picks a saved look; plan["scene"]/["outfit"] describe one: reuse a saved look with the same
    scene+outfit, otherwise create it (named plan["look"], or a slug of the scene)."""
    look = plan.get("look", "default")
    if not (plan.get("scene") or plan.get("outfit")):
        if look not in prof["looks"]:
            raise SystemExit(f"Unknown look '{look}'. Available: {', '.join(prof['looks'])}")
        return look, prof["looks"][look]
    base = prof["looks"]["default"]
    outfit, scene = plan.get("outfit") or base["outfit"], plan.get("scene") or base["scene"]
    for name, lk in prof["looks"].items():
        if lk["outfit"] == outfit and lk["scene"] == scene:
            print(f"  reusing look '{name}'")
            return name, lk
    name = look if look != "default" else comfy.slug(scene, 30)
    if name in prof["looks"]:
        name = f"{name}-{int(time.time()) % 10000}"
    return name, make_look(prof, name, outfit, scene, plan.get("ambience"))


# ---------------------------------------------------------------- make
def segments_of(plan):
    if plan.get("segments"):
        return [s["text"] if isinstance(s, dict) else s for s in plan["segments"]]
    if plan.get("script"):
        return N.split_script(plan["script"])
    raise SystemExit("plan needs 'segments' (list of texts) or 'script' (one text, auto-split at sentences)")


def cmd_make(a):
    plan = json.loads(Path(a.plan).resolve().read_text(encoding="utf-8"))
    prof = load(plan["influencer"])
    texts = segments_of(plan)
    est = N.estimate_seconds(texts)
    words = sum(len(t.split()) for t in texts)
    target = plan.get("seconds")
    print(f"'{plan['title']}' - {prof['name']}, {len(texts)} segment(s), {words} words, ~{sum(est) + 1:.0f}s"
          + (f" (target {target}s, ~{round((target - 1) * N.NWPS)} words)" if target else ""))
    for i, (t, x) in enumerate(zip(texts, est), 1):
        print(f"  {i}. ~{x:4.1f}s {len(t.split()):3}w  {t[:80]}")
    if a.dry_run:
        return
    comfy.require_server()
    look_name, look = resolve_look(prof, plan)
    slug = plan["slug"]
    out_prefix = f"reels/{slug}"
    out_dir = comfy.COMFY_OUT / "reels" / slug
    seed = plan.get("seed", prof["seed"])
    tts = N.tts_settings(prof)
    voice = comfy.upload(INFL / prof["id"] / tts["voice"], f"{prof['id']}__{tts['voice']}")
    slices, sr, secs, narration = N.narrate(texts, tts, seed, out_dir / "narration", out_prefix, voice)
    action = plan.get("action")  # e.g. "walking slowly, holding the phone at arm's length"
    prompt = V.person_visual(prof, look["outfit"], look["scene"], action)
    first_img = upload_look(prof, look_name)
    items = [{"tag": f"{slug}_{i:02d}", "image": first_img, "prompt": prompt, "seed": seed + i, "label": f"segment {i}"}
             for i in range(1, len(texts) + 1)]
    w, h = plan.get("width", W), plan.get("height", H)
    clips, clip_secs = V.render_clips(items, slices, sr, out_dir, out_prefix, w, h,
                                      f"Influencers/{prof['name']}/{plan['title']}", chain_all=True)
    final = V.finish(clips, clip_secs, narration, sr, out_dir, slug, plan)
    print(f"\nReel: {final}")


def cmd_quick(a):
    """Write a plan from CLI arguments (so the reel is reproducible), then make it."""
    title = a.title or " ".join(a.script.replace("|", " ").split()[:6])
    slug = comfy.slug(f"{a.id}-{title}")
    plan = {"title": title, "slug": slug, "influencer": a.id, "seconds": a.seconds, "seed": a.seed or 424242,
            "look": a.look, "segments": [{"text": t} for t in N.split_script(a.script)]}
    for k in ("scene", "outfit", "ambience", "action"):
        if getattr(a, k):
            plan[k] = getattr(a, k)
    path = PLANS / slug / "plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"plan written: {path}")
    a.plan, a.dry_run = str(path), a.dry_run
    cmd_make(a)


# ---------------------------------------------------------------- personas
def cmd_list(a):
    for d in sorted(INFL.iterdir()):
        if (d / "profile.json").exists():
            p = json.loads((d / "profile.json").read_text(encoding="utf-8"))
            print(f"{p['id']:10} {p['name']:16} looks: {', '.join(p['looks'])}")
            print(f"{'':10} {p['identity'][:110]}...")


def cmd_new(a):
    comfy.require_server()
    pid = a.id.lower()
    d = INFL / pid
    if (d / "profile.json").exists():
        raise SystemExit(f"'{pid}' already exists")
    (d / "looks").mkdir(parents=True, exist_ok=True)
    prof = {"id": pid, "name": a.name, "pronoun": a.pronoun, "identity": a.identity, "voice_style": a.voice_style,
            "personality": a.personality, "seed": a.seed, "camera": a.camera, "lighting": a.lighting,
            "default_action": a.default_action, "tts": dict(N.DEFAULT_TTS),
            "looks": {"default": {"outfit": a.outfit, "scene": a.scene, "ambience": a.ambience, "image": "portrait.png"}}}
    print("1/2 portrait (Z-Image Turbo)")
    still = (f"Vertical smartphone selfie photo. {a.identity} Wearing {a.outfit}, in {a.scene}. Looking straight "
             f"into the camera with a warm, confident expression, mouth closed, framed from mid-chest up, centered. "
             f"{a.lighting} Sharp focus on the face.")
    wf, out = comfy.make_portrait(still, 720, 1280, a.seed, f"influencers/{pid}/portrait")
    (d / "portrait.png").write_bytes(Path(out).read_bytes())
    comfy.save_to_ui(wf, f"Influencers/{a.name}/1 - Portrait (Z-Image)")
    print("2/2 voice (LTX-2 invents it once; Chatterbox clones it for every reel)")
    img = comfy.upload(d / "portrait.png", f"{pid}__default.png")
    line = a.sample_line or ("Hello, and welcome. Today I want to talk about something I find genuinely fascinating, "
                             "and by the end I think you'll see it in a whole new light.")
    prompt = (f"{V.person_visual(prof, a.outfit, a.scene, None)} {a.pronoun.capitalize()} says with {a.voice_style}: "
              f"\"{line}\" Clear close-microphone voice. {a.ambience}")
    _, out = comfy.invent_voice(img, prompt, a.seed, W, H, f"influencers/{pid}/voice_source")
    comfy.extract_voice(out, d / "voice.wav")
    store(prof)
    print(f"\nCreated {a.name}: {d}\n  Approve portrait.png and the voice ({out}) before making reels. If the accent "
          f"drifts between takes, see the README's 'Voice tuning'.")


def cmd_look(a):
    comfy.require_server()
    make_look(load(a.id), a.look, a.outfit, a.scene, a.ambience, a.seed)


def cmd_save_ui(a):
    """Publish ready-to-run workflows (voice preloaded / portrait preloaded) to the ComfyUI sidebar."""
    comfy.require_server()
    prof = load(a.id)
    tts = N.tts_settings(prof)
    wf = comfy.work_copy("chatterbox_tts.json", f"{prof['id']}_tts_template")
    comfy.set_slots(wf, {"6.audio": comfy.upload(INFL / prof["id"] / tts["voice"], f"{prof['id']}__{tts['voice']}"),
                         "4.text": "Replace with the narration.", "4.cfg_weight": tts["cfg_weight"],
                         "4.exaggeration": tts["exaggeration"], "4.temperature": tts["temperature"],
                         "8.filename_prefix": f"reels/ui/{prof['id']}_narration"})
    comfy.save_to_ui(wf, f"Influencers/{prof['name']}/1 Narration (Chatterbox)")
    look = prof["looks"]["default"]
    wf = comfy.work_copy("ltx2_3_ia2v.json", f"{prof['id']}_ia2v_template")
    comfy.set_slots(wf, {"269.image": upload_look(prof, "default"), "340.value_5": False,
                         "340.value": V.person_visual(prof, look["outfit"], look["scene"], None),
                         "340.value_1": W, "340.value_2": H, "340.value_3": comfy.FPS,
                         "341.filename_prefix": f"reels/ui/{prof['id']}_clip"})
    comfy.save_to_ui(wf, f"Influencers/{prof['name']}/2 Clip (load the narration audio, set duration)")



def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    m = sub.add_parser("make"); m.add_argument("--plan", required=True); m.add_argument("--dry-run", action="store_true")
    q = sub.add_parser("quick")
    q.add_argument("--id", required=True); q.add_argument("--script", required=True)
    q.add_argument("--seconds", type=int, default=15); q.add_argument("--title"); q.add_argument("--seed", type=int)
    q.add_argument("--look", default="default"); q.add_argument("--scene"); q.add_argument("--outfit")
    q.add_argument("--ambience"); q.add_argument("--action"); q.add_argument("--dry-run", action="store_true")
    n = sub.add_parser("new")
    n.add_argument("--id", required=True); n.add_argument("--name", required=True)
    n.add_argument("--pronoun", default="she", choices=["she", "he", "they"])
    n.add_argument("--identity", required=True, help="fixed physical traits: age, build, face, hair")
    n.add_argument("--outfit", required=True); n.add_argument("--scene", required=True)
    n.add_argument("--voice-style", required=True, help='e.g. "an upbeat, friendly, conversational tone"')
    n.add_argument("--personality", default=""); n.add_argument("--sample-line")
    n.add_argument("--ambience", default="Quiet room tone, no music.")
    n.add_argument("--camera", default=SELFIE_CAMERA); n.add_argument("--lighting", default=SELFIE_LIGHTING)
    n.add_argument("--default-action", default="with small natural head movements, raised eyebrows and a light hand "
                                                "gesture on each point, finishing with a confident nod and a smile.")
    n.add_argument("--seed", type=int, default=424242)
    lk = sub.add_parser("look")
    lk.add_argument("--id", required=True); lk.add_argument("--look", required=True)
    lk.add_argument("--outfit", required=True); lk.add_argument("--scene", required=True)
    lk.add_argument("--ambience"); lk.add_argument("--seed", type=int)
    s = sub.add_parser("save-ui"); s.add_argument("--id", required=True)
    a = ap.parse_args()
    {"list": cmd_list, "make": cmd_make, "quick": cmd_quick, "new": cmd_new, "look": cmd_look,
     "save-ui": cmd_save_ui}[a.cmd](a)


if __name__ == "__main__":
    main()
