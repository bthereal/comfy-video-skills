"""YouTube videos: a named narrator (fixed face + cloned voice) on camera, randomly intercut with generated b-roll
footage, over one continuous narration. 16:9 (1280x704 by default; optional upscale to 1080p).

  python yt.py list
  python yt.py make --plan projects/<slug>/plan.json [--dry-run]
  python yt.py new-narrator --id ada --name "Dr. Ada Price" --pronoun she --identity "..." --outfit "..." \
                            --scene "..." --voice-style "..." [--personality "..."] [--camera "..."] [--lighting "..."] \
                            [--ambience "..."] [--sample-line "..."] [--seed N]
  python yt.py angle --id clive --toward left [--name side]        # turned portrait for dialogue lines
  python yt.py group --ids monica,clive --name studio-wide --prompt "..."   # both in one shot (establishing)

Every topic-specific detail comes from the plan (title, genre, style, ambience, segments + shots) or the narrator's
profile (look, setting, camera, lighting, voice). See README.md for the plan format and how to write one.
Requires COMFYUI_DIR and a running ComfyUI (start-comfyui.bat in the repo root).
"""
import argparse, json, random, sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL.parent))
from engine import comfy, narration as N, video as V  # noqa: E402

for _s in (sys.stdout, sys.stderr):  # scripts may be in any language; Windows consoles default to cp1252
    _s.reconfigure(encoding="utf-8", errors="replace")

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


def cmd_angle(a):
    """A turned version of a character's portrait (same face, clothes and set), for talking to someone off camera."""
    comfy.require_server()
    prof = load(a.id)
    d = NARR / prof["id"]
    p = prof.get("pronoun", "they")
    prompt = (f"Keep the exact same person from the reference image: identical face, facial features, skin tone, "
              f"hairstyle, hair colour and clothing, in the same place with the same lighting and camera position. "
              f"{p.capitalize()} has turned {poss_of(p)} head and shoulders clearly three-quarters towards the "
              f"{a.toward} edge of the frame, eyes looking towards the {a.toward}, attentive, mouth closed. "
              f"{p.capitalize()} is the only person in the image: no other people, no figures or shoulders in the "
              f"foreground. {a.extra or ''}")
    img = comfy.upload(d / "portrait.png", f"yt_{prof['id']}__portrait.png")
    wf, out = comfy.flux_edit([img], prompt, a.seed or prof["seed"], f"youtube/narrators/{prof['id']}_{a.name}",
                              label=f"{prof['name']} {a.name}")
    (d / f"portrait_{a.name}.png").write_bytes(Path(out).read_bytes())
    comfy.save_to_ui(wf, f"YouTube/Narrators/{prof['name']} - {a.name} (Flux.2 edit)")
    print(f"Saved {d / f'portrait_{a.name}.png'} - use it with \"image\": \"portrait_{a.name}.png\" in a dialogue cast")


def cmd_group(a):
    """One shot of several characters together (Flux.2 edit with each portrait as a reference), e.g. an
    establishing wide shot of two presenters at their table. Saved to shots/<name>.png."""
    comfy.require_server()
    profs = [load(i) for i in a.ids.split(",")]
    ups = [comfy.upload(NARR / p["id"] / "portrait.png", f"yt_{p['id']}__portrait.png") for p in profs]
    who = " ".join(f"Reference image {k + 1} shows {p['name']}: keep {poss_of(p.get('pronoun', 'they'))} exact face, "
                   f"hair and clothing." for k, p in enumerate(profs))
    wf, out = comfy.flux_edit(ups, f"{who} {a.prompt}", a.seed, f"youtube/shots/{a.name}", label=f"group shot {a.name}")
    dst = SKILL / "shots" / f"{a.name}.png"
    dst.parent.mkdir(exist_ok=True)
    dst.write_bytes(Path(out).read_bytes())
    comfy.save_to_ui(wf, f"YouTube/Shots/{a.name} (Flux.2 edit)")
    print(f"Saved {dst}")


def cmd_qa(a):
    """Check a rendered dialogue video line by line (slow/stretched speech, extra people in shot). --fix marks the
    flagged lines in the plan (retake_audio / retake_video + solo) so the next `make` re-renders only those."""
    import re
    from engine import qa
    plan_path = Path(a.plan).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    if not plan.get("lines"):
        raise SystemExit("qa works on dialogue plans (with 'lines')")
    slug = plan["slug"]
    out_dir = comfy.COMFY_OUT / "youtube" / slug
    try:  # Whisper and the person detector need the VRAM ComfyUI may still be holding
        comfy.http("POST", "/free", {"unload_models": True, "free_memory": True})
    except Exception:
        pass

    def tag(i, ln):
        return f"{slug}_{i:03d}_shot_{ln['shot']}" if ln.get("shot") else f"{slug}_{i:03d}_{ln['speaker']}"

    def files(t):
        one = qa.newest(out_dir, f"{t}_0*.mp4")
        if one:
            return [one]
        parts = sorted({int(m.group(1)) for p in out_dir.glob(f"{t}_p*_0*.mp4") if (m := re.search(r"_p(\d+)_0", p.name))})
        return [qa.newest(out_dir, f"{t}_p{k}_0*.mp4") for k in parts]

    only = {int(x) for x in a.lines.split(",")} if a.lines else None
    sub = {**plan, "lines": [ln if (only is None or i in only) else {**ln, "_skip": True}
                             for i, ln in enumerate(plan["lines"], 1)]} if only else plan
    if a.from_report:  # reuse the last full scan (e.g. after reviewing it) instead of scanning again
        rep = json.loads((out_dir / "qa" / "report.json").read_text(encoding="utf-8"))
        rows, flagged = rep["all"], rep["flagged"]
        med = {tuple(k.split("/")): v for k, v in rep["medians"].items()}
    else:
        rows, flagged, med = qa.scan(sub, out_dir, tag, files, skip=lambda ln: ln.get("_skip"))
    print("\nTypical pace (words/s): " + ", ".join(f"{k[0]}/{k[1]} {v:.2f}" for k, v in sorted(med.items())))
    print(f"{len(flagged)} flagged line(s) of {len(rows)}:")
    for r in flagged:
        why = r["speech_issues"] + ([r["people_issue"]] if r["people_issue"] else [])
        print(f"  {r['line']:3}. {r['speaker'] or 'shot':7} {'; '.join(why):60} | {r['text'][:50]}")
    name = "recheck" if only else "report"
    print(f"Report: {out_dir / 'qa' / (name + '.json')}  (contact sheet: {name}_flagged.png)")
    if a.fix and flagged:
        for r in flagged:
            ln = plan["lines"][r["line"] - 1]
            if r["speech_issues"]:
                ln["retake_audio"] = ln.get("retake_audio", 0) + 1
            if r["people_issue"]:
                ln["retake_video"] = ln.get("retake_video", 0) + 1
                if not ln.get("shot"):
                    ln["solo"] = True
        plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Marked {len(flagged)} line(s) for re-rendering in {plan_path.name}; run `make` (only those re-render), "
              f"then `qa --lines ...` to re-check them.")


def poss_of(pronoun):
    return {"she": "her", "he": "his"}.get(pronoun, "their")


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


# ---------------------------------------------------------------- dialogue
def voice_for(prof, lang):
    """(tts settings, native) for a speaker saying a line in lang. profile "voices": {lang: tts} adds languages
    (e.g. Monica's Spanish via Chatterbox Multilingual); otherwise the main tts is used, and if that's another
    language the line comes out in the speaker's own accent (a learner's attempt) and isn't word-checked."""
    if lang in prof.get("voices", {}):
        return N.tts_settings({"tts": prof["voices"][lang]}), True
    tts = N.tts_settings(prof)
    return tts, tts.get("language", "en") == lang


def validate_dialogue(plan):
    missing = [k for k in ("title", "slug", "cast", "lines") if not plan.get(k)]
    if missing:
        raise SystemExit(f"plan is missing: {', '.join(missing)} (see README 'Dialogue videos')")
    for i, ln in enumerate(plan["lines"], 1):
        if ln.get("shot"):
            if not (SKILL / "shots" / f"{ln['shot']}.png").exists() or not ln.get("seconds"):
                raise SystemExit(f"line {i}: shot '{ln['shot']}' needs shots/{ln['shot']}.png (yt.py group) and 'seconds'")
        elif not ln.get("text") or ln.get("speaker") not in plan["cast"]:
            raise SystemExit(f"line {i} needs 'text' and a 'speaker' listed in 'cast' (or a 'shot')")


def line_image(ln, c):
    """Which portrait a line uses: the line's "image", else the front portrait when talking to the camera, else the
    cast member's default "image" (e.g. an angled portrait_side.png for talking across the table)."""
    if ln.get("image"):
        return ln["image"]
    if "camera" in (ln.get("gaze") or ""):
        return "portrait.png"
    return c.get("image", "portrait.png")


SOLO = " Only one person is in the frame; nobody else appears anywhere in the shot."
NO_TEXT = " No on-screen text, captions, banners, logos or graphics."  # the model sometimes invents a TV lower-third


def line_prompt(prof, c, ln, plan):
    """Clip prompt for a spoken line. "solo" (line, or plan-wide) uses the cast member's "solo_gaze" - a direction
    without naming the other person, which otherwise tends to make the video model draw them at the frame edge -
    and states that only one person is in shot."""
    solo = ln.get("solo", plan.get("solo", False))
    gaze = ln.get("gaze") or (c.get("solo_gaze") if solo else None) or c.get("gaze") or "looks directly at the camera"
    p = V.person_visual(prof, prof["outfit"], prof["scene"], ln.get("action") or c.get("action"), gaze=gaze)
    no_text = ln.get("no_text", plan.get("no_text", False))
    return p + (SOLO if solo else "") + (NO_TEXT if no_text else "")


def make_dialogue(plan, a):
    """Several speakers, one clip per line: each line in its speaker's voice and language, lip-synced to that
    speaker's portrait (single shots cut like a multi-camera studio). Per line: speaker, text, lang, caption, gaze,
    action, image, pause_after, check - or a silent {"shot", "seconds", "prompt"} (e.g. an establishing wide shot from
    shots/<name>.png). Per cast member: gaze, action, image (defaults for their lines)."""
    validate_dialogue(plan)
    cast, lines = plan["cast"], plan["lines"]
    profs = {cid: load(cid) for cid in cast}
    seed = plan.get("seed", 1)
    print(f"'{plan['title']}' - dialogue, {len(lines)} lines, cast: {', '.join(p['name'] for p in profs.values())}")
    for i, ln in enumerate(lines, 1):
        if ln.get("shot"):
            print(f"  {i:3}. [shot {ln['shot']}, {ln['seconds']}s silent]")
            continue
        tts, native = voice_for(profs[ln["speaker"]], ln.get("lang", "en"))
        how = "" if native else " (accented attempt)"
        print(f"  {i:3}. {profs[ln['speaker']]['name']:8} {ln.get('lang', 'en')}{how:20} "
              f"{line_image(ln, cast[ln['speaker']] or {}):20} {ln['text'][:50]}")  # {braces}: words in the other language
    if a.dry_run:
        return
    comfy.require_server()
    slug = plan["slug"]
    out_prefix, out_dir = f"youtube/{slug}", comfy.COMFY_OUT / "youtube" / slug
    vw, vh = plan.get("width", W), plan.get("height", H)
    uploads, narr, n_spoken = {}, [], 0

    def up(path, key):
        uploads[key] = uploads.get(key) or comfy.upload(path, key)
        return uploads[key]

    for ln in lines:
        if ln.get("shot"):
            narr.append({"silence": ln["seconds"]})
            continue
        n_spoken += 1  # narration takes are numbered by spoken line, so adding shots doesn't re-record anything
        prof = profs[ln["speaker"]]
        lang = ln.get("lang", "en")
        tts, native = voice_for(prof, lang)
        vfile = NARR / prof["id"] / tts["voice"]
        base = seed + n_spoken + 10000 * ln.get("retake_audio", 0)  # QA repair: a new take
        entry = {"name": f"line{n_spoken:03d}", "text": N.plain(ln["text"]), "tts": tts, "voice_path": vfile,
                 "voice_upload": up(vfile, f"yt_{prof['id']}__{tts['voice']}"),
                 "check": native and ln.get("check", True), "pause_after": ln.get("pause_after"), "seed": base}
        alt = ln.get("alt_lang", "es" if lang == "en" else "en")  # the language inside {braces}
        alt_tts, alt_native = voice_for(prof, alt)
        if "{" in ln["text"] and alt_native:  # e.g. Monica's Spanish words inside an English line, in her Spanish voice
            entry["parts"] = []
            for k, (is_alt, chunk) in enumerate(N.split_parts(ln["text"])):
                t_ = alt_tts if is_alt else tts
                vf = NARR / prof["id"] / t_["voice"]
                entry["parts"].append({"text": chunk, "tts": t_, "voice_path": vf, "check": entry["check"],
                                       "voice_upload": up(vf, f"yt_{prof['id']}__{t_['voice']}"), "seed": base + 100 * k})
        narr.append(entry)
    slices, sr, secs, narration = N.narrate_lines(narr, out_dir / "narration", out_prefix)
    items, prev_key = [], None
    for i, ln in enumerate(lines, 1):
        if ln.get("shot"):
            items.append({"tag": f"{slug}_{i:03d}_shot_{ln['shot']}", "seed": seed + i, "label": f"line {i} shot",
                          "image": up(SKILL / "shots" / f"{ln['shot']}.png", f"yt_shot__{ln['shot']}.png"),
                          "prompt": f"Static wide shot. {ln.get('prompt', '')} No one speaks to the camera; natural, "
                                    f"subtle movement. Realistic, soft light."})
            prev_key = None
            continue
        prof, c = profs[ln["speaker"]], cast[ln["speaker"]] or {}
        img = line_image(ln, c)
        key = (prof["id"], img)
        items.append({"tag": f"{slug}_{i:03d}_{prof['id']}", "label": f"line {i} {prof['name']}",
                      "seed": seed + i + 10000 * ln.get("retake_video", 0),  # QA repair: a new render
                      "image": up(NARR / prof["id"] / img, f"yt_{prof['id']}__{img}"),
                      "continue": key == prev_key,  # same speaker and same angle: carry on, no jump cut
                      "prompt": line_prompt(prof, c, ln, plan)})
        prev_key = key
    owners = []
    clips, clip_secs = V.render_clips(items, slices, sr, out_dir, out_prefix, vw, vh, f"YouTube/{plan['title']}",
                                      owners=owners)
    final = V.finish(clips, clip_secs, narration, sr, out_dir, slug, plan,
                     captions=[lines[o].get("caption") for o in owners])
    print(f"\nVideo: {final}")


# ---------------------------------------------------------------- make
def cmd_make(a):
    plan = json.loads(Path(a.plan).resolve().read_text(encoding="utf-8-sig"))
    if plan.get("lines"):
        return make_dialogue(plan, a)
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
    slices, sr, secs, narration = N.narrate(texts, tts, seed, out_dir / "narration", out_prefix, voice,
                                            NARR / prof["id"] / tts["voice"])

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
    g = sub.add_parser("angle")
    g.add_argument("--id", required=True); g.add_argument("--toward", required=True, choices=["left", "right"])
    g.add_argument("--name", default="side"); g.add_argument("--extra"); g.add_argument("--seed", type=int)
    gr = sub.add_parser("group")
    gr.add_argument("--ids", required=True, help="comma-separated narrator ids, e.g. monica,clive")
    gr.add_argument("--name", required=True); gr.add_argument("--prompt", required=True)
    gr.add_argument("--seed", type=int, default=1)
    q = sub.add_parser("qa")
    q.add_argument("--plan", required=True); q.add_argument("--fix", action="store_true")
    q.add_argument("--lines", help="comma-separated line numbers to check (default: all)")
    q.add_argument("--from-report", action="store_true", help="with --fix: mark lines from the last report, no re-scan")
    a = ap.parse_args()
    {"list": cmd_list, "new-narrator": cmd_new_narrator, "make": cmd_make, "angle": cmd_angle,
     "group": cmd_group, "qa": cmd_qa}[a.cmd](a)


if __name__ == "__main__":
    main()
