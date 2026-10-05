"""YouTube video tool: a named narrator (fixed face + cloned voice) intercut with generated footage, 16:9.

  python yt.py list
  python yt.py new-narrator --id vale --name "Dr. Simon Vale" --pronoun he --identity "..." --outfit "..." \
                            --scene "..." --voice-style "..." [--personality "..."] [--sample-line "..."] [--seed N]
  python yt.py make --plan projects/<slug>/plan.json [--dry-run]

plan.json:
  {"title": "...", "slug": "jfk-conspiracy", "narrator": "vale", "seconds": 60, "seed": 1963,
   "narrator_share": 0.4,                    # fraction of segments on camera (randomised with rules)
   "style": "cinematic documentary still, 1960s, 35mm film grain, muted colour",   # look of all footage
   "ambience": "Low, tense ambient atmosphere, no music.",                         # footage [SOUNDS]
   "segments": [{"text": "exact narration", "shot": "what the footage shows (used if this becomes footage)",
                 "type": "narrator|footage (optional: forces it)"} , ...]}

Rules for the random split: first and last segments are always the narrator, never more than two footage
segments in a row, and otherwise ~narrator_share of segments go to the narrator (seeded, so repeatable).
Each segment is rendered separately (LTX-2.3 ID-LoRA: the narrator's voice for both on-camera and voice-over),
then hard-cut together. Every segment leaves a pause before its cut; the last word is followed by >= 1 s of silence.
Requires ComfyUI on 127.0.0.1:8188 (start with ../comfy-influencer-reel/start-comfyui.bat).
"""
import argparse, json, math, random, shutil, sys, time
from pathlib import Path

SKILL = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL.parent / "comfy-influencer-reel"))
import reel  # shared ComfyUI helpers: job runner, uploads, joiner, silence detection  # noqa: E402

NARR = SKILL / "narrators"
W, H = 1280, 704           # 16:9, same pixel count as the 704x1280 reels (proven to fit 16 GB VRAM); /64
IMG_W, IMG_H = 1280, 720   # Z-Image stills; the video workflow centre-crops to 1280x704
SEG_MAX = 10.0
TAIL_JOIN, TAIL_FINAL_PLAN, TAIL_FINAL = 0.6, 1.2, 1.0  # LTX stretches speech to fill the clip unless told to stop


# ---------------------------------------------------------------- narrators
def load(nid):
    p = NARR / nid / "profile.json"
    if not p.exists():
        known = ", ".join(d.name for d in NARR.iterdir() if (d / "profile.json").exists()) if NARR.exists() else ""
        raise SystemExit(f"No narrator '{nid}'. Known: {known or 'none'}")
    return json.loads(p.read_text(encoding="utf-8"))


def poss(p):
    return {"she": "her", "he": "his"}.get(p, "their")


def cmd_list(a):
    for d in sorted(NARR.iterdir()) if NARR.exists() else []:
        if (d / "profile.json").exists():
            p = json.loads((d / "profile.json").read_text(encoding="utf-8"))
            print(f"{p['id']:8} {p['name']:18} {p['identity'][:90]}...")


def narrator_visual(prof, action):
    p = prof["pronoun"]
    return (f"Medium shot, 16:9 YouTube video, static camera on a tripod. {prof['identity']} "
            f"{p.capitalize()} is wearing {prof['outfit']}, in {prof['scene']}. {p.capitalize()} looks directly at "
            f"the camera and is speaking, {poss(p)} mouth opens and closes naturally as {p} talks, {action} "
            f"Professional studio lighting, shallow depth of field, realistic skin texture.")


def cmd_new_narrator(a):
    nid = a.id.lower()
    d = NARR / nid
    if (d / "profile.json").exists():
        raise SystemExit(f"'{nid}' already exists")
    d.mkdir(parents=True, exist_ok=True)
    prof = {"id": nid, "name": a.name, "pronoun": a.pronoun, "identity": a.identity, "outfit": a.outfit,
            "scene": a.scene, "voice_style": a.voice_style, "personality": a.personality, "seed": a.seed,
            "ambience": a.ambience}

    print("1/2 portrait (Z-Image Turbo, 16:9)")
    wf = reel.work_copy("z_image_turbo.json", f"yt_{nid}_portrait")
    still = (f"16:9 medium shot photograph for a YouTube video. {a.identity} Wearing {a.outfit}, in {a.scene}. "
             f"Seated, looking straight into the camera with a composed, confident expression, mouth closed, "
             f"framed from the waist up, slightly off-centre. Professional studio lighting, shallow depth of field, "
             f"realistic skin texture, sharp focus on the face.")
    reel.set_slots(wf, {"57.text": still, "57.width": IMG_W, "57.height": IMG_H, "57.seed": a.seed,
                        "9.filename_prefix": f"youtube/narrators/{nid}_portrait"})
    out = reel.run(wf, "portrait")
    shutil.copy(out, d / "portrait.png")
    reel.save_to_ui(wf, f"YouTube/Narrators/{a.name} - portrait")

    print("2/2 voice sample (LTX-2 invents the voice once; ID-LoRA reuses it)")
    img = reel.upload(d / "portrait.png", f"yt_{nid}__portrait.png")
    wf = reel.work_copy("ltx2_i2v_distilled.json", f"yt_{nid}_voice")
    line = a.sample_line or ("Now, this is where the story gets genuinely interesting. Because the official account "
                             "leaves a number of questions unanswered, and some of them are rather difficult to ignore.")
    text = (f"{narrator_visual(prof, 'with measured, expressive hand gestures.')} {a.pronoun.capitalize()} says with "
            f"{a.voice_style}: \"{line}\" Clear close-microphone voice, quiet studio room tone, no music.")
    data = json.loads(wf.read_text(encoding="utf-8"))
    for n in data["nodes"]:  # top-level widgets: 98=[image], 102=[mode,w,h,...], 92=[frames,text,ckpt,te,ups,seed]
        if n["id"] == 98: n["widgets_values"][0] = img
        if n["id"] == 102: n["widgets_values"][1:3] = [W, H]
        if n["id"] == 75: n["widgets_values"][0] = f"youtube/narrators/{nid}_voice_source"
        if n["id"] == 92: n["widgets_values"][0], n["widgets_values"][1], n["widgets_values"][5] = 249, text, a.seed
    wf.write_text(json.dumps(data), encoding="utf-8")
    out = reel.run(wf, "voice sample video")
    reel.extract_voice(out, d / "voice.wav")
    (d / "profile.json").write_text(json.dumps(prof, indent=2), encoding="utf-8")
    print(f"\nCreated {a.name}: {d}\n  Approve portrait.png and the voice ({out}) before making videos.")


# ---------------------------------------------------------------- planning
def assign_types(n, share, rng, forced):
    """First/last = narrator; never 3 footage in a row; otherwise ~share narrator. forced[i] overrides."""
    types = [None] * n
    for i in range(n):
        if forced[i]:
            types[i] = forced[i]
        elif i in (0, n - 1):
            types[i] = "narrator"
    # constructive (the old rejection sampling fell back to strict alternation when many types were forced):
    # 1) everything free starts as footage; 2) break any run of 3 footage by turning a random member of the run into
    # narrator (only free slots); 3) top up random free footage slots to reach the target narrator share.
    trial = [t or "footage" for t in types]
    free = {i for i in range(n) if types[i] is None}
    i = 0
    while i <= n - 3:
        if trial[i] == trial[i + 1] == trial[i + 2] == "footage":
            # prefer the 3rd slot (fewest conversions overall), else a free one, else the middle
            cand = [i + 2] if (i + 2) in free else ([j for j in (i, i + 1) if j in free] or [i + 1])
            trial[rng.choice(cand)] = "narrator"
        i += 1
    want = round(share * n) - trial.count("narrator")
    pool = [j for j in free if trial[j] == "footage"]
    rng.shuffle(pool)
    for j in pool[:max(0, want)]:
        trial[j] = "narrator"
    return trial


def durations(segs):
    """Seconds per segment: words at ~3.3 w/s + pause before the cut (1.2 s planned after the last word).
    Snapped to LTX's 8n+1 frame grid."""
    out = []
    for i, s in enumerate(segs):
        tail = TAIL_FINAL_PLAN if i == len(segs) - 1 else TAIL_JOIN
        x = len(s["text"].split()) / reel.WPS + tail
        out.append(max(3.04, round(x * 25 / 8) * 8 / 25))
    return out


def footage_visual(plan, seg):
    return (f"16:9 cinematic documentary footage, {plan['style']}. {seg['shot']} Slow, smooth camera movement "
            f"(a gentle push-in or drift). No one in the shot is speaking to the camera; this is b-roll under a "
            f"voice-over narration.")


def prompt_for(plan, prof, seg, final):
    p = prof["pronoun"]
    if seg["type"] == "narrator":
        action = seg.get("action") or "with measured, expressive hand gestures and occasional nods for emphasis."
        vis = narrator_visual(prof, action)
        sounds = f"The speaker has {prof['voice_style']}, close to a studio microphone. {prof['ambience']}"
    else:
        vis = footage_visual(plan, seg)
        sounds = (f"Voice-over narration: the narrator has {prof['voice_style']}, recorded close to a studio "
                  f"microphone, heard over the footage. {plan.get('ambience', 'Subtle ambient atmosphere, no music.')}")
    if final:
        vis += (f" After the last word {p} stops talking and holds a composed, knowing look in silence for a full "
                f"second." if seg["type"] == "narrator" else " The narration ends a full second before the shot ends.")
        sounds += " The speech finishes a full second before the end of the clip."
    else:
        vis += (f" After the last word {p} stops talking and pauses in silence for the final half second of the clip."
                if seg["type"] == "narrator" else " The narration ends half a second before the shot ends.")
        sounds += " The speech finishes about half a second before the end of the clip, followed by quiet."
    return f"[VISUAL]: {vis}\n[SPEECH]: {seg['text']}\n[SOUNDS]: {sounds}"


# ---------------------------------------------------------------- make (legacy: per-segment speech)
def cmd_make_legacy(a):
    plan_path = Path(a.plan).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    prof = load(plan["narrator"]) if not a.dry_run or (NARR / plan["narrator"] / "profile.json").exists() \
        else {"name": plan["narrator"] + " (not created yet)"}
    segs = plan["segments"]
    rng = random.Random(plan.get("seed", 1))
    types = assign_types(len(segs), plan.get("narrator_share", 0.4), rng, [s.get("type") for s in segs])
    for s, t in zip(segs, types):
        s["type"] = t
    secs = durations(segs)
    over = [i + 1 for i, x in enumerate(secs) if x > SEG_MAX + 0.01]
    total, words = sum(secs), sum(len(s["text"].split()) for s in segs)
    print(f"'{plan['title']}' - narrator {prof['name']}, {len(segs)} segments, {words} words, ~{total:.1f}s "
          f"(target {plan.get('seconds', '?')}s), {types.count('narrator')} on camera / {types.count('footage')} footage")
    for i, (s, x) in enumerate(zip(segs, secs), 1):
        print(f"  {i}. {s['type']:8} {x:5.2f}s {len(s['text'].split()):3}w  {s['text'][:70]}")
    if over:
        raise SystemExit(f"Segments {over} are longer than {SEG_MAX:.0f}s: split them (max ~{int((SEG_MAX - TAIL_JOIN) * reel.WPS)} words each)")
    if a.dry_run:
        return

    proj = reel.COMFY_OUT / "youtube" / plan["slug"]
    proj.mkdir(parents=True, exist_ok=True)
    voice = reel.upload(NARR / prof["id"] / "voice.wav", f"yt_{prof['id']}__voice.wav")
    portrait = reel.upload(NARR / prof["id"] / "portrait.png", f"yt_{prof['id']}__portrait.png")
    seed = plan.get("seed", prof["seed"])
    outs = []
    for i, (s, x) in enumerate(zip(segs, secs), 1):
        final = i == len(segs)
        tag = f"{plan['slug']}_{i:02d}_{s['type']}"
        if s["type"] == "footage":  # 1) still image for the shot
            still = proj / f"{tag}_still.png"
            if not still.exists():
                wf = reel.work_copy("z_image_turbo.json", tag + "_still")
                reel.set_slots(wf, {"57.text": f"16:9 {plan['style']}. {s['shot']} No text, no captions, no watermark.",
                                    "57.width": IMG_W, "57.height": IMG_H, "57.seed": seed + i,
                                    "9.filename_prefix": f"youtube/{plan['slug']}/stills/{tag}"})
                shutil.copy(reel.run(wf, f"segment {i} still"), still)
            img = reel.upload(still, f"yt_{tag}_still.png")
        else:
            img = portrait
        prompt = prompt_for(plan, prof, s, final)
        for attempt in (0, 1):  # re-render once (new seed) only if Whisper says the final word wasn't spoken
            seg_secs = x
            s_seed = seed + i + 100 * attempt
            spec = {"prompt": prompt, "secs": seg_secs, "seed": s_seed, "image": img, "w": W, "h": H}
            out = next((m for m in sorted(proj.glob(f"{tag}_0*.mp4"), reverse=True)
                        if m.with_suffix(".spec.json").exists()
                        and json.loads(m.with_suffix(".spec.json").read_text(encoding="utf-8")) == spec), None)
            if out:
                print(f"  segment {i}: reusing {out.name}")
            else:
                wf = reel.work_copy("ltx2_3_id_lora.json", tag)
                reel.set_slots(wf, {"269.image": img, "276.audio": voice,
                                    "341.filename_prefix": f"youtube/{plan['slug']}/{tag}",
                                    "340.value": prompt, "340.value_1": W, "340.value_2": H, "340.value_3": 25,
                                    "340.value_4": seg_secs, "340.noise_seed": s_seed})
                out = reel.run(wf, f"segment {i}/{len(segs)} {s['type']} ({seg_secs:.2f}s)")
                out.with_suffix(".spec.json").write_text(json.dumps(spec), encoding="utf-8")
                reel.save_to_ui(wf, f"YouTube/{plan['title']}/{i:02d} {s['type']}")
            chk = reel.speech_check(out, s["text"])
            if chk is None:  # Whisper not installed: fall back to the energy-based estimate, no word check
                gap, ok = reel.trailing_silence(out), True
                print(f"  silence after last word: {gap:.2f}s (no word check: Whisper model missing)")
            else:
                gap, ok = chk["gap"], chk["complete"]
                print(f"  last word {'spoken' if ok else 'MISSING'}; {gap:.2f}s after it")
            if ok or attempt == 1:
                break
            print(f"  heard: ...{chk['heard'][-80:]}\n  re-rendering segment {i} once with a new seed")
        if not ok:
            print(f"  warning: segment {i}'s final word still sounds cut - listen at this cut, or shorten its text")
        outs.append(out)
    pad = max(0.0, TAIL_FINAL - gap)
    final_out = proj / f"{plan['slug']}_final.mp4"
    reel.join_segments(outs, final_out, pad_end=pad, chained=False)
    (proj / "plan_resolved.json").write_text(json.dumps({**plan, "segments": segs, "seconds_each": secs}, indent=2),
                                             encoding="utf-8")
    print(f"\nVideo: {final_out}")


# ---------------------------------------------------------------- make (narration-first, default)
NWPS = 3.0            # Chatterbox speaking rate (measured on Vale: 3.05 w/s at cfg 0.8, 2.9 at 0.5)
TAKE_MAX_WORDS = 80   # words per continuous TTS take (~28 s); takes break only between segments
SPLIT_AT = 10.4       # narration slices longer than this are rendered as 2+ chained clips (16 GB VRAM cap)


def _md5(path):
    import hashlib
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def _reuse(folder, pattern, spec):
    """Newest file matching pattern whose sidecar <file>.spec.json equals spec (resume support)."""
    for m in sorted(folder.glob(pattern), reverse=True):
        sp = Path(str(m) + ".spec.json")
        if not sp.exists():
            sp = m.with_suffix(".spec.json")
        if sp.exists() and json.loads(sp.read_text(encoding="utf-8")) == spec:
            return m
    return None


def cmd_make(a):
    """Narration first: whole script -> continuous Chatterbox narration in the cloned voice -> sliced at natural
    pauses -> one clip per slice (LTX-2.3 image+audio: narrator lip-sync, or footage) -> hard cuts over the
    unbroken narration track."""
    if a.legacy:
        return cmd_make_legacy(a)
    import numpy as np
    import narration as N
    plan = json.loads(Path(a.plan).resolve().read_text(encoding="utf-8"))
    prof = load(plan["narrator"]) if not a.dry_run or (NARR / plan["narrator"] / "profile.json").exists() \
        else {"name": plan["narrator"] + " (not created yet)"}
    segs = plan["segments"]
    types = assign_types(len(segs), plan.get("narrator_share", 0.4), random.Random(plan.get("seed", 1)),
                         [s.get("type") for s in segs])
    for s, t in zip(segs, types):
        s["type"] = t
    est = [len(s["text"].split()) / NWPS + 0.45 for s in segs]
    words = sum(len(s["text"].split()) for s in segs)
    print(f"'{plan['title']}' - narrator {prof['name']}, {len(segs)} segments, {words} words, ~{sum(est) + 1:.0f}s "
          f"(target {plan.get('seconds', '?')}s), {types.count('narrator')} on camera / {types.count('footage')} footage")
    for i, (s, x) in enumerate(zip(segs, est), 1):
        print(f"  {i}. {s['type']:8} ~{x:4.1f}s {len(s['text'].split()):3}w  {s['text'][:70]}")
    too_long = [i + 1 for i, x in enumerate(est) if x > SEG_MAX]
    if too_long:
        raise SystemExit(f"Segments {too_long} would run over {SEG_MAX:.0f}s at {NWPS} words/s: keep segments to "
                         f"<= {int((SEG_MAX - 0.6) * NWPS)} words")
    if a.dry_run:
        return

    proj = reel.COMFY_OUT / "youtube" / plan["slug"]
    ndir = proj / "narration"
    ndir.mkdir(parents=True, exist_ok=True)
    seed = plan.get("seed", prof["seed"])
    tts = {"voice": "voice.wav", "cfg_weight": 0.5, "exaggeration": 0.5, "temperature": 0.8, **prof.get("tts", {})}
    tts.pop("engine", None)
    voice_up = reel.upload(NARR / prof["id"] / tts["voice"], f"yt_{prof['id']}__{tts['voice']}")

    # 1) continuous narration, in takes that only break between segments
    takes, cur = [], []
    for i, s in enumerate(segs):
        w = len(s["text"].split())
        if cur and sum(len(segs[j]["text"].split()) for j in cur) + w > TAKE_MAX_WORDS:
            takes.append(cur); cur = []
        cur.append(i)
    takes.append(cur)
    def record(idxs, name, base_seed, tries=3):
        """One take covering segments idxs; returns (take_path, bounds) or (None, None) if every try dropped words."""
        text = " ".join(segs[i]["text"] for i in idxs)
        for attempt in range(tries):  # must end with the script's last words and contain every segment ending
            spec = {"text": text, **tts, "seed": base_seed + 1000 * attempt}
            take = _reuse(ndir, f"{name}_0*.flac", spec)
            if take:
                print(f"  narration {name}: reusing {take.name}")
            else:
                take = N.tts_take(text, voice_up, spec["seed"], f"{plan['slug']}/narration/{name}",
                                  tts["cfg_weight"], tts["exaggeration"], tts["temperature"])
                Path(str(take) + ".spec.json").write_text(json.dumps(spec), encoding="utf-8")
            ws = N.words_with_times(take)
            if not N.ends_complete(ws, text):
                print(f"  {name}: last words missing ('...{' '.join(w.word.strip() for w in ws[-4:])}') - re-taking")
                continue
            extra = N.extra_words(ws, text)
            if extra:  # TTS repeated or invented a phrase (seen: "...we destroyed that technology to do that anymore...")
                print(f"  {name}: inserted words {extra} - re-taking")
                continue
            try:
                return take, N.segment_bounds(ws, [segs[i]["text"] for i in idxs])
            except SystemExit as e:
                print(f"  {name}: {e} - re-taking")
        return None, None

    slices, sr = [], None
    for t, idxs in enumerate(takes):
        last_take = t == len(takes) - 1
        take, bounds = record(idxs, f"take{t + 1:02d}", seed + 10 * t)
        if take:
            a_, sr = N.load_audio(take)
            slices += N.slice_narration(a_, sr, bounds, final_index=last_take)
            continue
        # fallback: Chatterbox sometimes drops the end of a longer take (e.g. a quoted sentence) - record each
        # segment of this take on its own, which is far more reliable for short text
        print(f"  take {t + 1} kept dropping words - recording its {len(idxs)} segments separately")
        for k, i in enumerate(idxs):
            take, bounds = record([i], f"take{t + 1:02d}_seg{i + 1:03d}", seed + 10 * t + 5 + k, tries=4)
            if not take:
                raise SystemExit(f"segment {i + 1} keeps dropping words even on its own; simplify: "
                                 f"{segs[i]['text'][:80]}...")
            a_, sr = N.load_audio(take)
            slices += N.slice_narration(a_, sr, bounds, final_index=last_take and k == len(idxs) - 1)
    secs = [len(x) / sr for x in slices]
    narration = np.concatenate(slices)
    N.save_wav(ndir / "narration.wav", narration, sr)
    print(f"  narration: {len(narration) / sr:.2f}s in {len(takes)} take(s); segments "
          + ", ".join(f"{x:.2f}" for x in secs))
    if max(secs) > SEG_MAX + 0.7:
        print(f"  warning: a segment slice is {max(secs):.2f}s - long clips risk running out of VRAM")

    # 2) one clip per slice
    portrait = reel.upload(NARR / prof["id"] / "portrait.png", f"yt_{prof['id']}__portrait.png")
    vw, vh = plan.get("width", W), plan.get("height", H)
    if vw % 64 or vh % 64:
        raise SystemExit(f"width/height must be multiples of 64 (got {vw}x{vh}; use 1920x1088 for '1080p')")
    split_at = SPLIT_AT * (W * H) / (vw * vh)   # same VRAM load per clip as a 10.4 s clip at 1280x704
    iw, ih = vw, round(vw * 9 / 16)               # stills rendered at the output width (16:9)
    print(f"  video {vw}x{vh}; clips longer than {split_at:.1f}s are split into chained parts")
    clips, clip_secs = [], []
    for i, (s, x) in enumerate(zip(segs, secs), 1):
        tag = f"{plan['slug']}_{i:02d}_{s['type']}_av"
        wav = N.save_wav(ndir / f"seg{i:02d}.wav", slices[i - 1], sr)
        if s["type"] == "footage":
            still = proj / f"{plan['slug']}_{i:02d}_footage_still.png"
            if not still.exists():
                wf = reel.work_copy("z_image_turbo.json", tag + "_still")
                reel.set_slots(wf, {"57.text": f"16:9 {plan['style']}. {s['shot']} No text, no captions, no watermark.",
                                    "57.width": iw, "57.height": ih, "57.seed": seed + i,
                                    "9.filename_prefix": f"youtube/{plan['slug']}/stills/{tag}"})
                shutil.copy(reel.run(wf, f"segment {i} still"), still)
            img = reel.upload(still, f"yt_{plan['slug']}_{i:02d}_footage_still.png")
            prompt = footage_visual(plan, s)
        else:
            img = portrait
            prompt = narrator_visual(prof, s.get("action") or "with measured, expressive hand gestures and "
                                                                  "occasional nods for emphasis.")
        # VRAM cap: a slice longer than SPLIT_AT is rendered as consecutive parts on the 0.32 s grid; each later part
        # starts from the previous part's last frame, so it plays as one continuous shot over continuous audio.
        n_parts = max(1, math.ceil(x / split_at - 1e-9))
        grid_units = round(x / N.GRID)
        part_units = [grid_units // n_parts + (1 if k < grid_units % n_parts else 0) for k in range(n_parts)]
        pos = 0
        for p, units in enumerate(part_units):
            p_secs = units * N.GRID
            ptag = tag if n_parts == 1 else f"{tag}_p{p + 1}"
            pwav = wav if n_parts == 1 else N.save_wav(ndir / f"seg{i:02d}_p{p + 1}.wav",
                                                        slices[i - 1][int(round(pos * sr)):int(round((pos + p_secs) * sr))], sr)
            spec = {"prompt": prompt, "secs": round(p_secs, 3), "seed": seed + i + 50 * p, "image": img,
                    "audio": _md5(pwav), **({} if (vw, vh) == (W, H) else {"w": vw, "h": vh})}
            out = _reuse(proj, f"{ptag}_0*.mp4", spec)
            if out:
                print(f"  segment {i}{'' if n_parts == 1 else f' part {p + 1}'}: reusing {out.name}")
            else:
                audio = reel.upload(pwav, f"yt_{plan['slug']}_seg{i:02d}{'' if n_parts == 1 else f'_p{p + 1}'}.wav")
                wf, out = N.render_ia2v(img, audio, p_secs, prompt, f"{plan['slug']}/{ptag}", spec["seed"], vw, vh)
                out.with_suffix(".spec.json").write_text(json.dumps(spec), encoding="utf-8")
                reel.save_to_ui(wf, f"YouTube/{plan['title']}/{i:02d} {s['type']}{'' if n_parts == 1 else f' p{p + 1}'}")
            clips.append(out)
            clip_secs.append(p_secs)
            pos += p_secs
            if p + 1 < n_parts:  # next part continues from this part's last frame
                png = N.TMP / f"{ptag}_last.png"
                reel.last_frame_png(out, png)
                img = reel.upload(png, f"{ptag}_last.png")

    # 3) hard cuts over the unbroken narration
    final_out = proj / f"{plan['slug']}_final.mp4"
    N.assemble(clips, narration, sr, final_out, clip_secs)
    if plan.get("upscale_to"):  # e.g. [1920, 1080]: render at 704p, then a fast Lanczos+sharpen upscale (~2 s per video second)
        uw, uh = plan["upscale_to"]
        N.upscale_video(final_out, proj / f"{plan['slug']}_final_{uh}p.mp4", uw, uh)
    (proj / "plan_resolved.json").write_text(json.dumps({**plan, "segments": segs, "seconds_each": secs}, indent=2),
                                             encoding="utf-8")
    print(f"\nVideo: {final_out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    n = sub.add_parser("new-narrator")
    n.add_argument("--id", required=True); n.add_argument("--name", required=True)
    n.add_argument("--pronoun", default="he", choices=["she", "he", "they"])
    n.add_argument("--identity", required=True); n.add_argument("--outfit", required=True)
    n.add_argument("--scene", required=True); n.add_argument("--voice-style", required=True)
    n.add_argument("--personality", default=""); n.add_argument("--sample-line")
    n.add_argument("--ambience", default="Quiet studio room tone, no music."); n.add_argument("--seed", type=int, default=1234)
    m = sub.add_parser("make")
    m.add_argument("--plan", required=True); m.add_argument("--dry-run", action="store_true")
    m.add_argument("--legacy", action="store_true", help="old method: speech generated per segment by LTX ID-LoRA")
    a = ap.parse_args()
    if a.cmd not in ("list",) and not (a.cmd == "make" and a.dry_run) and not reel.server_up():
        raise SystemExit("ComfyUI is not running on 127.0.0.1:8188 - double-click "
                         "comfy-influencer-reel/start-comfyui.bat")
    {"list": cmd_list, "new-narrator": cmd_new_narrator, "make": cmd_make}[a.cmd](a)


if __name__ == "__main__":
    main()
