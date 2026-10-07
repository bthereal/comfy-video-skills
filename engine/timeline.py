"""Timeline: the one build pipeline for every video, in any format, with any cast of actors.

A plan is a list of lines; each line becomes one clip (or more, past the VRAM cap) on a single timeline:
  {"speaker": id, "text": ...}            an actor on camera, lip-synced to their line (their voice, any language)
  {"actor": id, "seconds": n}             an actor on camera, not talking (an action: "sips her coffee")
  {"footage": "...", "voice": id, "text"} b-roll under someone's voice-over (or silent with "seconds")
  {"shot": name, "seconds": n}            a silent establishing shot from video/shots/<name>.png
Plan options: format ("16x9" / "9x16"), cast {id: {look, angle, action, gaze, solo_gaze, camera, scene, outfit}},
continuous (one unbroken shot per actor run, reel-style), narration ("flowing": consecutive lines of one voice are one
take; "per_line": every line recorded separately), auto_footage ({"share": 0.4}: spoken lines with a "broll"
description may be shown as footage), solo, style / camera_motion (footage look), camera, sfx, upscale_to
(no_text: legacy, see NO_TEXT).
"""
import json, math, random
from pathlib import Path

from . import comfy, narration as N, video as V, actors as A

SHOTS = comfy.ROOT / "video" / "shots"
SOLO = " Only one person is in the frame; nobody else appears anywhere in the shot."
NO_TEXT = " No on-screen text, captions, banners, logos or graphics."
# ^ legacy "no_text" option, kept only so older plans (episode 1) still resume: naming banners makes the model draw
#   them (tested 3/3 with it, 0 without), like "not smiling" brings smiles. Never name what you don't want.
SR = 24000  # Chatterbox's sample rate; other audio is resampled to it


# ---------------------------------------------------------------- plan
def kind(ln):
    if ln.get("shot"):
        return "shot"
    if ln.get("footage"):
        return "footage"
    if ln.get("speaker") and ln.get("text"):
        return "spoken"
    if ln.get("actor"):
        return "action"
    raise SystemExit(f"line {ln} is none of: speaker+text, actor+seconds, footage, shot+seconds")


def who(ln):
    return ln.get("speaker") or ln.get("actor") or ln.get("voice")


def fmt_of(plan):
    f = plan.get("format", "16x9")
    if f not in A.FORMATS:
        raise SystemExit(f"format must be one of {', '.join(A.FORMATS)} (got {f})")
    return f


def normalise(plan):
    """Convenience forms: a single-actor "script" becomes spoken lines."""
    if plan.get("script") and not plan.get("lines"):
        if len(plan.get("cast", {})) != 1:
            raise SystemExit("'script' needs exactly one cast member; otherwise write 'lines'")
        sid = next(iter(plan["cast"]))
        plan["lines"] = [{"speaker": sid, "text": t} for t in N.split_script(plan["script"])]
    return plan


def validate(plan):
    missing = [k for k in ("title", "slug", "cast", "lines") if not plan.get(k)]
    if missing:
        raise SystemExit(f"plan is missing: {', '.join(missing)} (see video/README.md)")
    fmt_of(plan)
    for i, ln in enumerate(plan["lines"], 1):
        k = kind(ln)
        if k in ("spoken", "action") and who(ln) not in plan["cast"]:
            raise SystemExit(f"line {i}: '{who(ln)}' isn't in the cast")
        if k == "footage" and ln.get("voice") and ln["voice"] not in plan["cast"]:
            raise SystemExit(f"line {i}: voice '{ln['voice']}' isn't in the cast")
        if k in ("action", "shot") or (k == "footage" and not ln.get("text")):
            if not ln.get("seconds"):
                raise SystemExit(f"line {i}: a {k} line without speech needs 'seconds'")
        if k == "shot" and not (SHOTS / f"{ln['shot']}.png").exists():
            raise SystemExit(f"line {i}: no video/shots/{ln['shot']}.png (make it with: video.py shot ...)")
        if k == "footage" and not plan.get("style"):
            raise SystemExit("footage lines need a plan 'style' (the look of all footage)")


def footage_plan(plan, seed):
    """auto_footage: which spoken lines (those with a "broll" description) are shown as footage. Seeded: first and
    last on camera, never three footage lines in a row, about `share` on camera. A line's "type" ("footage" /
    "on_camera") forces it. Returns {line_index: True} for lines shown as footage."""
    lines = plan["lines"]
    af = plan.get("auto_footage")
    out = {i: True for i, ln in enumerate(lines) if ln.get("type") == "footage" and ln.get("broll")}
    if not af:
        return out
    idx = [i for i, ln in enumerate(lines) if kind(ln) == "spoken"]
    n = len(idx)

    def forced(ln):  # a line's own choice, if any
        t = ln.get("type")
        if t == "footage" and ln.get("broll"):
            return "footage"
        if t in ("on_camera", "narrator") or not ln.get("broll"):
            return "narrator"  # (no b-roll description: has to stay on camera)
        return None

    types = [forced(lines[i]) or ("narrator" if k in (0, n - 1) else None) for k, i in enumerate(idx)]
    trial = [t or "footage" for t in types]
    free = {k for k in range(n) if types[k] is None}
    rng = random.Random(seed)
    for k in range(n - 2):
        if trial[k] == trial[k + 1] == trial[k + 2] == "footage":
            cand = [k + 2] if (k + 2) in free else ([j for j in (k, k + 1) if j in free] or [k + 1])
            trial[rng.choice(cand)] = "narrator"
    pool = [j for j in free if trial[j] == "footage"]
    rng.shuffle(pool)
    for j in pool[:max(0, round(af.get("share", 0.4) * n) - trial.count("narrator"))]:
        trial[j] = "narrator"
    out.update({idx[k]: True for k in range(n) if trial[k] == "footage"})
    return out


def resolve_looks(plan, actors, fmt, create):
    """A cast member with "scene"/"outfit" gets a matching saved look, or a new one (named "look", or after the
    scene) created on demand."""
    for aid, c in plan["cast"].items():
        c = c or {}
        if not (c.get("scene") or c.get("outfit")):
            continue
        actor = actors[aid]
        base = A.look(actor, "default")
        outfit, scene = c.get("outfit") or base["outfit"], c.get("scene") or base["scene"]
        match = next((n for n, lk in actor["looks"].items() if lk["outfit"] == outfit and lk["scene"] == scene), None)
        if match:
            c["look"] = match
        elif create:
            name = c.get("look") if c.get("look") and c["look"] not in actor["looks"] else comfy.slug(scene, 30)
            A.add_look(actor, name, outfit, scene, c.get("ambience"), fmt)
            c["look"] = name
        else:  # dry run: report it, don't make it
            c["look"] = f"(new look '{c.get('look') or comfy.slug(scene, 30)}': made on first run)"


# ---------------------------------------------------------------- per-line pieces
def line_image(actor, ln, c, fmt, create=True):
    """Which actor image a line starts from: the line's "image" (a file in the actor's folder), else the look's
    front-on image when talking to the camera, else the cast member's "image", else the look turned to the cast
    member's (or line's) "angle" ("left"/"right"; made on demand). create=False: None if not made yet."""
    if ln.get("image"):
        return ln["image"]
    look = ln.get("look") or c.get("look", "default")
    if look.startswith("("):
        return None
    if "camera" in (ln.get("gaze") or ""):
        return A.look_image(actor, look, fmt, create=create)
    if c.get("image"):
        return c["image"]
    return A.look_image(actor, look, fmt, ln.get("angle") or c.get("angle"), create=create)


def actor_prompt(actor, c, ln, plan, fmt, speaking=True):
    """Clip prompt for an actor on camera. "solo" (line or plan) uses the cast member's "solo_gaze" - a direction
    without naming the other person, who otherwise tends to get drawn in at the frame edge - and says only one person
    is in shot; "no_text" is legacy (see NO_TEXT)."""
    solo = ln.get("solo", plan.get("solo", False))
    gaze = ln.get("gaze") or (c.get("solo_gaze") if solo else None) or c.get("gaze") or "looks directly at the camera"
    lk = A.look(actor, ln.get("look") or c.get("look", "default"))
    prof = A.framing(actor, fmt)
    cam = ln.get("camera") or c.get("camera") or plan.get("camera")
    if cam:
        prof = {**prof, "camera": cam}
    p = V.person_visual(prof, lk["outfit"], lk["scene"], ln.get("action") or c.get("action"), speaking=speaking,
                        gaze=gaze)
    return p + (SOLO if solo else "") + (NO_TEXT if ln.get("no_text", plan.get("no_text", False)) else "")


def voice_entry(plan, actors, ln, n, up, prefix):
    """Narration entry for a voiced line (spoken, or footage voice-over): its speaker's voice for its language;
    {braces} parts in the speaker's other-language voice."""
    actor = actors[ln.get("speaker") or ln["voice"]]
    lang = ln.get("lang") or A.main_language(actor)
    tts, native = A.voice_for(actor, lang)
    vfile = A.path(actor, tts["voice"])
    base = plan.get("seed", 1) + n + 10000 * ln.get("retake_audio", 0)  # QA repair: a new take
    e = {"name": f"line{n:03d}", "text": N.plain(ln["text"]), "tts": tts, "voice_path": vfile,
         "voice_upload": up(vfile, A.upload_key(prefix, actor, tts["voice"])), "speaker": actor["id"],
         "check": native and ln.get("check", True), "pause_after": ln.get("pause_after"), "seed": base}
    others = [l for l in [A.main_language(actor), *actor.get("voices", {})] if l != lang]
    alt = ln.get("alt_lang") or (others[0] if others else ("es" if lang == "en" else "en"))  # language in {braces}
    alt_tts, alt_native = A.voice_for(actor, alt)
    if "{" in ln["text"] and alt_native:
        e["parts"] = []
        for k, (is_alt, chunk) in enumerate(N.split_parts(ln["text"])):
            t_ = alt_tts if is_alt else tts
            vf = A.path(actor, t_["voice"])
            e["parts"].append({"text": chunk, "tts": t_, "voice_path": vf, "check": e["check"],
                               "voice_upload": up(vf, A.upload_key(prefix, actor, t_["voice"])), "seed": base + 100 * k})
    return e


def silence(secs, sr=SR):
    import numpy as np
    return np.zeros(int(round(max(1, round(secs / N.GRID)) * N.GRID * sr)), "float32")


def narrate(plan, entries, out_dir, out_prefix):
    """Audio for every line, on LTX's grid. per_line: each voiced line recorded separately (dialogue). flowing:
    consecutive lines in the same voice (no language switches inside) are one continuous take, sliced at the line
    boundaries, so a monologue keeps its natural flow. Silent lines are silence. Returns (slices, sr, secs, full)."""
    import numpy as np, librosa
    nar_dir = Path(out_dir) / "narration"
    if plan.get("narration", "flowing") == "per_line":
        return N.narrate_lines(entries, nar_dir, out_prefix)
    nar_dir.mkdir(parents=True, exist_ok=True)

    def joinable(e):
        return "silence" not in e and not e.get("parts") and e.get("check", True)

    def key(e):
        return (e["speaker"], json.dumps(e["tts"], sort_keys=True))

    pieces, i = [], 0
    while i < len(entries):
        e = entries[i]
        if "silence" in e:
            pieces.append(silence(e["silence"])); i += 1
            continue
        j = i + 1
        if joinable(e):
            while j < len(entries) and joinable(entries[j]) and key(entries[j]) == key(e):
                j += 1
            run = entries[i:j]
            seed = max(x["seed"] for x in run)
            sl, sr, _, _ = N.narrate([x["text"] for x in run], e["tts"], seed, nar_dir, out_prefix, e["voice_upload"],
                                     e["voice_path"], name=f"run{e['name'][4:]}_", final=False)
        else:
            sl, sr, _, _ = N.narrate_lines([e], nar_dir, out_prefix, end_silence=False, free=False)
        pieces += [librosa.resample(s, orig_sr=sr, target_sr=SR) if sr != SR else s for s in sl]
        i = j
    N.free_mtl()
    last = pieces[-1]  # the 1 s hold after the final word
    pieces[-1] = np.concatenate([last, silence(N.END_SILENCE)])
    secs = [len(x) / SR for x in pieces]
    full = np.concatenate(pieces)
    N.save_wav(nar_dir / "narration.wav", full, SR)
    print(f"  narration: {len(full) / SR:.2f}s in {len(entries)} lines")
    return pieces, SR, secs, full


# ---------------------------------------------------------------- make
def make(plan, plan_path, dry_run=False):
    plan = normalise(plan)
    validate(plan)
    fmt = fmt_of(plan)
    cast, lines, slug = plan["cast"], plan["lines"], plan["slug"]
    actors = {aid: A.load(aid) for aid in cast}
    for aid in cast:
        cast[aid] = cast[aid] or {}
    seed = plan.get("seed", 1)
    as_footage = footage_plan(plan, seed)
    resolve_looks(plan, actors, fmt, create=not dry_run)
    est = N.estimate_seconds([N.plain(ln.get("text", "")) or "x" for ln in lines])
    print(f"'{plan['title']}' - {fmt}, {len(lines)} lines, cast: {', '.join(a['name'] for a in actors.values())}, "
          f"narration {plan.get('narration', 'flowing')}{', continuous' if plan.get('continuous') else ''}")
    for i, (ln, x) in enumerate(zip(lines, est)):
        k = "footage" if as_footage.get(i) else kind(ln)
        if k == "shot":
            what = f"[shot {ln['shot']}, {ln['seconds']}s silent]"
        elif k == "footage":
            what = f"[footage] {(ln.get('footage') or ln.get('broll'))[:40]}" + (f" | {ln['text'][:40]}" if ln.get("text") else f" ({ln['seconds']}s)")
        else:
            actor, c = actors[who(ln)], cast[who(ln)]
            img = line_image(actor, ln, c, fmt, create=False) or "(made on first run)"
            if k == "action":
                what = f"{actor['name']:8} [silent {ln['seconds']}s] {img:22} {ln.get('action', '')[:45]}"
            else:
                lng = ln.get("lang") or A.main_language(actor)
                _, native = A.voice_for(actor, lng)
                lang = lng + ("" if native else " (accented)")
                what = f"{actor['name']:8} {lang:14} ~{x:4.1f}s {img:22} {ln['text'][:45]}"
        print(f"  {i + 1:3}. {what}")
    if dry_run:
        return None
    comfy.require_server()
    out = plan.get("output", "videos")
    out_prefix, out_dir = f"{out}/{slug}", comfy.COMFY_OUT / out / slug
    prefix = plan.get("upload_prefix", "vid_")
    w, h = plan.get("width") or A.FORMATS[fmt][0], plan.get("height") or A.FORMATS[fmt][1]
    if w % 64 or h % 64:
        raise SystemExit(f"width/height must be multiples of 64 (got {w}x{h})")
    uploads = {}

    def up(path, key):
        uploads[key] = uploads.get(key) or comfy.upload(path, key)
        return uploads[key]

    # audio: one entry per line (narration takes are numbered by voiced line, so adding silent lines changes nothing)
    entries, n = [], 0
    for i, ln in enumerate(lines):
        if ln.get("text") and kind(ln) in ("spoken", "footage"):
            n += 1
            entries.append(voice_entry(plan, actors, ln, n, up, prefix))
        else:
            entries.append({"silence": ln["seconds"]})
    slices, sr, secs, narration = narrate(plan, entries, out_dir, out_prefix)

    # pictures: one item per line
    items, prev, prev_actor = [], None, None
    for i, ln in enumerate(lines, 1):
        k = "footage" if as_footage.get(i - 1) else kind(ln)
        if k == "shot":
            items.append({"tag": f"{slug}_{i:03d}_shot_{ln['shot']}", "seed": seed + i, "label": f"line {i} shot",
                          "image": up(SHOTS / f"{ln['shot']}.png", f"{prefix}shot__{ln['shot']}.png"),
                          "prompt": f"Static wide shot. {ln.get('prompt', '')} No one speaks to the camera; natural, "
                                    f"subtle movement. Realistic, soft light."})
            prev = prev_actor = None
            continue
        if k == "footage":
            desc = ln.get("footage") or ln["broll"]
            still = out_dir / "stills" / f"{slug}_{i:03d}_still.png"
            if not still.exists():
                _, img = comfy.make_portrait(V.still_prompt(plan, desc), w, h, seed + i, f"{out_prefix}/stills/{slug}_{i:03d}",
                                             label=f"line {i} still")
                still.parent.mkdir(parents=True, exist_ok=True)
                still.write_bytes(Path(img).read_bytes())
            items.append({"tag": f"{slug}_{i:03d}_footage", "seed": seed + i + 10000 * ln.get("retake_video", 0),
                          "label": f"line {i} footage", "image": up(still, f"{prefix}{slug}_{i:03d}_still.png"),
                          "prompt": V.footage_visual(plan, desc)})
            prev = prev_actor = None
            continue
        aid = who(ln)
        actor, c = actors[aid], cast[aid]
        img = line_image(actor, ln, c, fmt)
        key = (aid, img)
        cont = key == prev or (plan.get("continuous") and aid == prev_actor)  # same actor: no jump cut
        items.append({"tag": f"{slug}_{i:03d}_{aid}", "label": f"line {i} {actor['name']}",
                      "seed": seed + i + 10000 * ln.get("retake_video", 0),  # QA repair: a new render
                      "image": up(A.path(actor, img), A.upload_key(prefix, actor, img)), "continue": cont,
                      "prompt": actor_prompt(actor, c, ln, plan, fmt, speaking=(k == "spoken"))})
        prev, prev_actor = key, aid
    sfx = [{**fx, "segment": fx.get("line", fx.get("segment", 1))} for fx in plan.get("sfx", [])]
    narration = V.mix_sfx(narration, sr, secs, sfx, Path(plan_path).resolve().parent)
    owners = []
    clips, clip_secs = V.render_clips(items, slices, sr, out_dir, out_prefix, w, h, f"Videos/{plan['title']}",
                                      owners=owners)
    final = V.finish(clips, clip_secs, narration, sr, out_dir, slug, plan,
                     captions=[lines[o].get("caption") for o in owners])
    print(f"\nVideo: {final}")
    return final


def clip_tag(plan, i, ln, as_footage=None):
    """The clip tag make() gives line i (1-based) - for QA."""
    slug = plan["slug"]
    if ln.get("shot"):
        return f"{slug}_{i:03d}_shot_{ln['shot']}"
    if ln.get("footage") or (as_footage or {}).get(i - 1):
        return f"{slug}_{i:03d}_footage"
    return f"{slug}_{i:03d}_{who(ln)}"
