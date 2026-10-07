"""Video side (shared by both skills): prompt builders, LTX-2.3 image+audio clips, rendering a sequence of clips with
resume + chaining, streaming assembly over the continuous narration, and the optional upscale.

All subject matter comes from the persona profile and the plan; nothing here is topic-specific.
"""
import json, math
from pathlib import Path

from . import comfy
from . import narration as N

FPS = comfy.FPS
AV_LEAD = 0.04          # play narration 1 frame early: IA2V lips run 40-60 ms ahead of their input audio (measured)
BASE_PIXELS = 1280 * 704
SPLIT_AT_BASE = 10.4    # at 1280x704 (or 704x1280) a clip longer than this risks 16 GB VRAM; scales with pixel count


# ---------------------------------------------------------------- prompts
def poss(pronoun):
    return {"she": "her", "he": "his"}.get(pronoun, "their")


def person_visual(prof, outfit, scene, action, speaking=True, gaze="looks directly at the camera"):
    """On-camera shot of a persona talking (or, speaking=False, silent). camera/lighting/default_action come from
    the profile; gaze can redirect the line (e.g. "turns to shout at someone off camera to the right")."""
    p = prof.get("pronoun", "they")
    camera = prof.get("camera", "Medium shot, static camera")
    lighting = prof.get("lighting", "Soft, natural light, realistic skin texture.")
    action = action or prof.get("default_action", "with small natural head movements and light hand gestures.")
    talk = (f"{p.capitalize()} {gaze} and is speaking, {poss(p)} mouth opens and closes "
            f"naturally as {p} talks," if speaking else f"{p.capitalize()} is silent, not talking, mouth closed,")
    return f"{camera}. {prof['identity']} {p.capitalize()} is wearing {outfit}, in {scene}. {talk} {action} {lighting}"


def footage_visual(plan, shot):
    """B-roll shot under voice-over. The plan's style carries the genre/era/look."""
    motion = plan.get("camera_motion", "Slow, smooth camera movement (a gentle push-in or drift).")
    return (f"{plan['style']}. {shot} {motion} No one in the shot is speaking to the camera; this is b-roll under a "
            f"voice-over narration.")


def still_prompt(plan, shot):
    return f"{plan['style']}. {shot} No text, no captions, no watermark."


# ---------------------------------------------------------------- clips
def render_ia2v(image_upload, audio_upload, secs, prompt, prefix, seed, w, h):
    """One LTX-2.3 image+audio clip: animates the image, lip-synced to the audio slice. prefix = output path prefix."""
    wf = comfy.work_copy("ltx2_3_ia2v.json", prefix)
    # (the template's input-image resize is linked to the Width/Height inputs, so vertical/horizontal both just work)
    comfy.set_slots(wf, {"269.image": image_upload, "276.audio": audio_upload, "341.filename_prefix": prefix,
                         "340.value": prompt, "340.value_5": False, "340.value_1": w, "340.value_2": h,
                         # +0.01: the template computes frames = int(duration*25 + 1); 9.28*25 = 231.99999... would
                         # truncate and snap DOWN a whole 8-frame step
                         "340.value_3": FPS, "340.value_4": round(secs + 0.01, 3), "340.start_index": 0,
                         "340.noise_seed": seed})
    return wf, comfy.run(wf, f"{Path(prefix).name} ({secs:.2f}s)")


def render_clips(items, slices, sr, out_dir, out_prefix, w, h, ui_folder, chain_all=False, owners=None):
    """Render one or more clips per narration slice.
    items[i]: {"tag", "image" (upload name; ignored when chained), "prompt", "seed", "label", "continue"?}.
    A slice longer than the VRAM cap is split into parts on the 0.32 s grid; later parts start from the previous
    part's last frame. chain_all=True also chains across items (reels: one continuous shot); an item with
    "continue": true chains from the previous clip on its own (e.g. the same speaker's next line).
    owners (optional list) receives the item index of every clip. Resumable: a clip whose .spec.json matches is
    reused. Returns (clips, clip_secs)."""
    out_dir = Path(out_dir); ndir = out_dir / "narration"
    split_at = SPLIT_AT_BASE * BASE_PIXELS / (w * h)
    clips, clip_secs, prev = [], [], None
    for i, (it, sl) in enumerate(zip(items, slices), 1):
        x = len(sl) / sr
        wav = N.save_wav(ndir / f"seg{i:02d}.wav", sl, sr)
        n_parts = max(1, math.ceil(x / split_at - 1e-9))
        units = round(x / N.GRID)
        part_units = [units // n_parts + (1 if k < units % n_parts else 0) for k in range(n_parts)]
        img = it["image"] if not ((chain_all or it.get("continue")) and prev) else None
        pos = 0.0
        for p, u in enumerate(part_units):
            p_secs = u * N.GRID
            ptag = it["tag"] if n_parts == 1 else f"{it['tag']}_p{p + 1}"
            pwav = wav if n_parts == 1 else N.save_wav(ndir / f"seg{i:02d}_p{p + 1}.wav",
                                                        sl[int(round(pos * sr)):int(round((pos + p_secs) * sr))], sr)
            if img is None:  # continue from the previous clip's last frame
                png = comfy.TMP / f"{prev.stem}_last.png"
                comfy.last_frame_png(prev, png)
                img = comfy.upload(png, png.name)
            spec = {"prompt": it["prompt"], "secs": round(p_secs, 3), "seed": it["seed"] + 50 * p, "image": img,
                    "audio": N.md5(pwav), "w": w, "h": h}
            out = N.reuse(out_dir, f"{ptag}_0*.mp4", spec)
            if out:
                print(f"  {it['label']}{'' if n_parts == 1 else f' part {p + 1}'}: reusing {out.name}")
            else:
                audio = comfy.upload(pwav, f"{Path(out_prefix).name}_{ptag}.wav")
                wf, out = render_ia2v(img, audio, p_secs, it["prompt"], f"{out_prefix}/{ptag}", spec["seed"], w, h)
                out.with_suffix(".spec.json").write_text(json.dumps(spec), encoding="utf-8")
                comfy.save_to_ui(wf, f"{ui_folder}/{ptag}")
            clips.append(out); clip_secs.append(p_secs)
            if owners is not None:
                owners.append(i - 1)
            prev, img, pos = out, None, pos + p_secs
    return clips, clip_secs


# ---------------------------------------------------------------- sound effects
def mix_sfx(narration, sr, slice_secs, sfx, base_dir):
    """Mix plan sound effects into the narration track. Each: {"file" (relative to the plan's folder), "segment"
    (1-based; starts with that segment's slice), "offset" (seconds, may be negative), "gain" (default 0.5),
    "loop" (repeat to the end of the video)}. Returns a new array; the narration is unchanged if sfx is empty."""
    import numpy as np, librosa
    out = narration.copy()
    for fx in sfx:
        a, fsr = N.load_audio(Path(base_dir) / fx["file"])
        if fsr != sr:
            a = librosa.resample(a, orig_sr=fsr, target_sr=sr)
        start = max(0, int(round((sum(slice_secs[:fx.get("segment", 1) - 1]) + fx.get("offset", 0.0)) * sr)))
        if fx.get("loop"):
            a = np.tile(a, math.ceil((len(out) - start) / len(a)) + 1)
        a = a[:max(0, len(out) - start)] * fx.get("gain", 0.5)
        out[start:start + len(a)] += a
        print(f"  sfx {fx['file']} at {start / sr:.2f}s")
    peak = abs(out).max()
    return out / peak * 0.98 if peak > 0.98 else out


# ---------------------------------------------------------------- assembly
_FONTS = {}


def draw_caption(img, text):
    """Burn a caption into an RGB frame (numpy): first line large, any further lines (e.g. a translation) smaller,
    on a translucent box near the bottom."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    h, w = img.shape[:2]
    def font(size, bold):
        key = (size, bold)
        if key not in _FONTS:
            try:
                _FONTS[key] = ImageFont.truetype("arialbd.ttf" if bold else "arial.ttf", size)
            except OSError:
                _FONTS[key] = ImageFont.load_default()
        return _FONTS[key]
    s = min(h, w)  # sized by the short side, so vertical frames get the same text size as landscape ones
    first, rest = text.split("\n")[0], text.split("\n")[1:]
    im = Image.fromarray(img).convert("RGBA"); layer = Image.new("RGBA", im.size); d = ImageDraw.Draw(layer)
    lines, fonts = [], []
    for t, f in [(first, font(round(s * 0.052), True))] + [(r, font(round(s * 0.038), False)) for r in rest]:
        cur = []  # wrap to 92% of the frame width (a 9:16 frame is narrow)
        for word in t.split():
            if cur and d.textlength(" ".join(cur + [word]), font=f) > w * 0.92:
                lines.append(" ".join(cur)); fonts.append(f); cur = []
            cur.append(word)
        lines.append(" ".join(cur)); fonts.append(f)
    sizes = [d.textbbox((0, 0), t, font=f) for t, f in zip(lines, fonts)]
    gap = round(s * 0.012); pad = round(s * 0.022)
    tw = max(b[2] - b[0] for b in sizes); th = sum(b[3] - b[1] for b in sizes) + gap * (len(lines) - 1)
    x0, y1 = (w - tw) // 2, h - round(s * 0.07)
    d.rounded_rectangle((x0 - pad, y1 - th - pad, x0 + tw + pad, y1 + pad), radius=pad, fill=(0, 0, 0, 150))
    y = y1 - th
    for t, f, b in zip(lines, fonts, sizes):
        d.text(((w - (b[2] - b[0])) // 2 - b[0], y - b[1]), t, font=f,
               fill=(255, 255, 255, 255) if f is fonts[0] else (255, 225, 140, 255))
        y += b[3] - b[1] + gap
    return np.asarray(Image.alpha_composite(im, layer).convert("RGB"))


def assemble(clips, narration, sr, out, slice_secs, captions=None):
    """Hard-cut the clips' frames (each trimmed to EXACTLY its narration slice - LTX returns slice + 1 frame); the
    soundtrack is the continuous narration. Streams one clip at a time, so any length fits in RAM.
    captions (optional, one per clip, text or None) are burned into that clip's frames."""
    import av, numpy as np
    captions = captions or [None] * len(clips)
    wants = [int(round(secs * FPS)) for secs in slice_secs]
    n_audio = int(round(sum(wants) / FPS * sr))
    a = narration[int(AV_LEAD * sr):int(AV_LEAD * sr) + n_audio]  # starts in the lead-in silence, so nothing is lost
    if len(a) < n_audio:
        a = np.concatenate([a, np.zeros(n_audio - len(a), "float32")])
    a = np.stack([a, a])
    first = av.open(str(clips[0])); w, h = first.streams.video[0].codec_context.width, first.streams.video[0].codec_context.height
    first.close()
    o = av.open(str(out), "w")
    vs = o.add_stream("libx264", rate=FPS)
    vs.width, vs.height, vs.pix_fmt = w, h, "yuv420p"
    vs.options = {"crf": "18", "preset": "medium"}
    as_ = o.add_stream("aac", rate=sr, layout="stereo")
    vi = ai = 0

    def audio_until(t):
        nonlocal ai
        while ai < a.shape[1] and ai / sr <= t:
            af = av.AudioFrame.from_ndarray(np.ascontiguousarray(a[:, ai:ai + 1024]), format="fltp", layout="stereo")
            af.sample_rate, af.pts = sr, ai
            for pkt in as_.encode(af): o.mux(pkt)
            ai += 1024

    for p, want, cap in zip(clips, wants, captions):
        c = av.open(str(p)); n = 0
        for f in c.decode(video=0):
            if n >= want:
                break
            img = f.to_ndarray(format="rgb24")
            if img.shape[1] != w or img.shape[0] != h:
                raise SystemExit(f"{Path(p).name} is {img.shape[1]}x{img.shape[0]}, expected {w}x{h}")
            if cap:
                img = draw_caption(img, cap)
            audio_until(vi / FPS)
            for pkt in vs.encode(av.VideoFrame.from_ndarray(img, format="rgb24")): o.mux(pkt)
            vi += 1; n += 1
        c.close()
        if n < want:
            o.close()
            raise SystemExit(f"{Path(p).name} has {n} frames but its narration slice needs {want}")
    audio_until(1e9)
    for pkt in vs.encode(): o.mux(pkt)
    for pkt in as_.encode(): o.mux(pkt)
    o.close()
    print(f"  assembled {len(clips)} clips -> {out} ({vi / FPS:.2f}s video, {n_audio / sr:.2f}s narration)")


def upscale_video(src, dst, w, h, sharpen=True):
    """Streaming Lanczos upscale (+ light unsharp mask) to exactly w x h: scale to cover, then centre-crop.
    Audio is re-encoded unchanged. ~2 s per second of video."""
    import av, time
    from PIL import Image, ImageFilter
    t0 = time.time()
    src_c = av.open(str(src))
    vin, ain = src_c.streams.video[0], (src_c.streams.audio[0] if src_c.streams.audio else None)
    o = av.open(str(dst), "w")
    vs = o.add_stream("libx264", rate=vin.average_rate or FPS)
    vs.width, vs.height, vs.pix_fmt = w, h, "yuv420p"
    vs.options = {"crf": "18", "preset": "medium"}
    aout = o.add_stream("aac", rate=ain.codec_context.sample_rate, layout=ain.codec_context.layout.name) if ain else None
    sw, sh = vin.codec_context.width, vin.codec_context.height
    scale = max(w / sw, h / sh)
    rw, rh = round(sw * scale), round(sh * scale)
    box = ((rw - w) // 2, (rh - h) // 2, (rw - w) // 2 + w, (rh - h) // 2 + h)
    n = 0
    for pkt in src_c.demux(*(s for s in (vin, ain) if s)):
        for f in pkt.decode():
            if pkt.stream.type == "video":
                img = f.to_image().resize((rw, rh), Image.LANCZOS).crop(box)
                if sharpen:
                    img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=2))
                vf = av.VideoFrame.from_image(img); vf.pts = None
                for p in vs.encode(vf): o.mux(p)
                n += 1
            elif aout is not None:
                f.pts = None
                for p in aout.encode(f): o.mux(p)
    for p in vs.encode(): o.mux(p)
    if aout is not None:
        for p in aout.encode(): o.mux(p)
    o.close(); src_c.close()
    print(f"  upscaled {n} frames {sw}x{sh} -> {w}x{h} in {time.time() - t0:.0f}s -> {dst}")


def finish(clips, clip_secs, narration, sr, out_dir, slug, plan, captions=None):
    """Assemble (+ optional per-clip captions) + optional upscale_to. Returns the final path."""
    final = Path(out_dir) / f"{slug}_final.mp4"
    assemble(clips, narration, sr, final, clip_secs, captions)
    if plan.get("upscale_to"):
        uw, uh = plan["upscale_to"]
        upscale_video(final, Path(out_dir) / f"{slug}_final_{min(uw, uh)}p.mp4", uw, uh)  # 1920x1080 -> _1080p
    return final
