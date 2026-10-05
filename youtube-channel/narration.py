"""Narration-first pipeline: one continuous TTS narration (Chatterbox, cloned voice), cut at natural pauses,
then video built to match each slice (LTX-2.3 image+audio lip-sync for the narrator, footage under voice-over).
The final soundtrack is the continuous narration itself, so speech never resets at a cut.
"""
import json, math, re, shutil, sys, tempfile, difflib
from pathlib import Path

SKILL = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL.parent / "comfy-influencer-reel"))
import reel  # noqa: E402

TMP = Path(tempfile.gettempdir()) / "influencer_reel"
CHATTERBOX_WF = SKILL / "workflows" / "chatterbox_tts.json"
FPS = 25
GRID = 8 / FPS            # LTX clips are 8n+1 frames -> every clip length is a multiple of 0.32 s
LEAD = 0.12               # silence kept before the first word of a slice
END_SILENCE = 1.0         # the 1-second rule after the final word
AV_LEAD = 0.04            # play narration 1 frame early: LTX IA2V lips run 40-60 ms ahead of their input audio (measured)


# ---------------------------------------------------------------- audio helpers
def load_audio(path):
    import av, numpy as np
    c = av.open(str(path)); sr = c.streams.audio[0].codec_context.sample_rate
    a = np.concatenate([(lambda x: x.mean(axis=0) if x.ndim > 1 else x.reshape(-1))(f.to_ndarray()) for f in c.decode(audio=0)])
    a = a.astype("float32")
    if abs(a).max() > 1.5:  # int16 samples
        a /= 32768.0
    return a, sr


def save_wav(path, a, sr):
    import soundfile as sf
    sf.write(str(path), a, sr, subtype="PCM_16")
    return Path(path)


# ---------------------------------------------------------------- TTS
def tts_take(text, voice_upload_name, seed, tag, cfg_weight=0.5, exaggeration=0.5, temperature=0.8):
    """One continuous Chatterbox take (keep each take under ~80 words / ~28 s)."""
    wf = TMP / f"{tag.replace('/', '_')}_tts.json"
    shutil.copy(CHATTERBOX_WF, wf)
    reel.set_slots(wf, {"4.text": text, "6.audio": voice_upload_name, "4.seed": seed, "4.keep_model_loaded": False,
                        "4.cfg_weight": cfg_weight, "4.exaggeration": exaggeration, "4.temperature": temperature,
                        "8.filename_prefix": f"youtube/{tag}", "8.format": "flac"})
    return reel.run(wf, f"narration take {tag}")


def extra_words(words, text):
    """Words the take INSERTED relative to the script (TTS repeats/hallucinations, e.g. a phrase said twice).
    Number words vs digits and split/merged words show up as 'replace', not 'insert', so they don't count."""
    want = reel._norm(text)
    got = [t for w in words for t in reel._norm(w.word)]
    sm = difflib.SequenceMatcher(None, want, got, autojunk=False)
    return [" ".join(got[j1:j2]) for op, i1, i2, j1, j2 in sm.get_opcodes() if op == "insert" and j2 - j1 >= 2] + \
           [" ".join(got[j1:j2]) for op, i1, i2, j1, j2 in sm.get_opcodes()
            if op == "replace" and (j2 - j1) - (i2 - i1) >= 3]


def ends_complete(words, text):
    """True if the transcript ends with the script's last two words (fuzzy; number words may be digits)."""
    want = reel._norm(text)[-2:]
    got = [t for w in words for t in reel._norm(w.word)][-4:]
    if not got:
        return False
    # compare with spaces removed, so "any more" == "anymore", "per cent" == "percent"
    target = "".join(want)
    for off in (0, 1):
        for k in (1, 2, 3):
            if len(got) >= k + off:
                cand = "".join(got[len(got) - off - k:len(got) - off])
                if difflib.SequenceMatcher(None, target, cand).ratio() >= 0.8:
                    return True
    return want[-1] in reel._NUMWORDS and any(c.isdigit() for c in got[-1])


def words_with_times(path):
    if reel._WHISPER is None:
        from faster_whisper import WhisperModel
        try:
            reel._WHISPER = WhisperModel(str(reel.WHISPER_DIR), device="cuda", compute_type="float16")
        except Exception:
            reel._WHISPER = WhisperModel(str(reel.WHISPER_DIR), device="cpu", compute_type="int8")
    segs, _ = reel._WHISPER.transcribe(str(path), language="en", word_timestamps=True, beam_size=5)
    return [w for s in segs for w in s.words]


def segment_bounds(words, seg_texts):
    """For each segment, (first_word_start, last_word_end) in the take, by walking the transcript and matching each
    segment's final word (fuzzy; numbers allowed to collapse into digits)."""
    norm = reel._norm

    def tok(w):
        t = norm(w.word)
        return "".join(t) if t else ""

    def like(a, b):
        return difflib.SequenceMatcher(None, a, b).ratio() >= 0.75 or \
            (a in reel._NUMWORDS and any(ch.isdigit() for ch in b))

    bounds, i = [], 0
    for k, text in enumerate(seg_texts):
        t_words = norm(text)
        target, target2 = t_words[-1], "".join(t_words[-2:])  # "more" or "anymore" (= "any more")
        start = words[i].start
        j = i
        if k == len(seg_texts) - 1:
            j = len(words) - 1
        else:
            nxt = norm(seg_texts[k + 1])
            nxt1, nxt2 = nxt[0], "".join(nxt[:2])
            # expected position: this segment's share of words, so a match far too early is rejected
            min_m = i + max(1, int(len(t_words) * 0.6))
            best = None
            for m in range(min_m, len(words)):
                w = tok(words[m])
                ok = like(target, w) or like(target2, w) or w.endswith(target2)
                if not ok:
                    continue
                gap = (words[m + 1].start - words[m].end) if m + 1 < len(words) else 9
                # anchor: what follows must be the start of the next segment. The first word alone can be misheard
                # ("Hoax"->"Hooke's", "That's"->"thus"), so the 2nd/3rd heard words may match the 2nd/3rd script words.
                n1 = tok(words[m + 1]) if m + 1 < len(words) else ""
                n23 = [tok(words[x]) for x in (m + 2, m + 3) if x < len(words)]
                anchored = like(nxt1, n1) or like(nxt2, n1) or n1.startswith(nxt1) or \
                    any(like(sw, hw) for sw in nxt[1:3] for hw in n23 if len(sw) >= 3)
                if gap > 0.12 and anchored:
                    best = m
                    break
            if best is None:
                raise SystemExit(f"could not find the end of segment {k + 1} ('{target}') in the narration")
            j = best
        bounds.append((start, words[j].end))
        i = j + 1
    return bounds


def slice_narration(take_audio, sr, bounds, final_index):
    """Cut a take into per-segment slices. Each slice runs from LEAD before its first word to the midpoint of the
    following pause, then is padded with silence to the 0.32 s grid (so video length == audio length exactly).
    Returns list of numpy arrays."""
    import numpy as np
    out = []
    for k, (s, e) in enumerate(bounds):
        a0 = max(0.0, s - LEAD) if k == 0 else out_end
        if k + 1 < len(bounds):
            cut = (e + bounds[k + 1][0]) / 2
        else:
            cut = min(len(take_audio) / sr, e + 0.25)
        piece = take_audio[int(a0 * sr):int(cut * sr)]
        tail = END_SILENCE if (k == len(bounds) - 1 and final_index) else 0.0
        need = len(piece) / sr + tail
        target = max(1, math.ceil(need / GRID - 1e-9)) * GRID  # round UP to the 0.32 s grid (pad with silence)
        pad = int(round(target * sr)) - len(piece)
        piece = np.concatenate([piece, np.zeros(max(0, pad), "float32")])[: int(round(target * sr))]
        out.append(piece)
        out_end = cut
    return out


# ---------------------------------------------------------------- video
def render_ia2v(image_upload, audio_upload, secs, prompt, tag, seed, w=1280, h=704):
    wf = reel.work_copy("ltx2_3_ia2v.json", tag.replace("/", "_"))
    reel.set_slots(wf, {"269.image": image_upload, "276.audio": audio_upload, "341.filename_prefix": f"youtube/{tag}",
                        "340.value": prompt, "340.value_5": False, "340.value_1": w, "340.value_2": h,
                        # +0.01: the template computes frames = int(duration*25 + 1); 9.28*25 = 231.99999... would
                        # truncate and snap DOWN a whole 8-frame step (measured: 9.28 s came out as 9.00 s)
                        "340.value_3": FPS, "340.value_4": round(secs + 0.01, 3), "340.start_index": 0,
                        "340.noise_seed": seed})
    out = reel.run(wf, f"{tag} ({secs:.2f}s)")
    return wf, out


def upscale_video(src, dst, w=1920, h=1080, sharpen=True):
    """Streaming Lanczos upscale (+ light unsharp mask) to exactly w x h: scale to cover, then centre-crop
    (1280x704 is 1.82:1, true 1080p is 1.78:1). Audio is decoded and re-encoded unchanged. Returns seconds taken."""
    import av, time as _t, numpy as np
    from PIL import Image, ImageFilter
    t0 = _t.time()
    src_c = av.open(str(src))
    vin, ain = src_c.streams.video[0], (src_c.streams.audio[0] if src_c.streams.audio else None)
    fps = vin.average_rate or FPS
    o = av.open(str(dst), "w")
    vs = o.add_stream("libx264", rate=fps)
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
                vf = av.VideoFrame.from_image(img)
                vf.pts = None
                for p in vs.encode(vf): o.mux(p)
                n += 1
            elif aout is not None:
                f.pts = None
                for p in aout.encode(f): o.mux(p)
    for p in vs.encode(): o.mux(p)
    if aout is not None:
        for p in aout.encode(): o.mux(p)
    o.close(); src_c.close()
    took = _t.time() - t0
    print(f"  upscaled {n} frames {sw}x{sh} -> {w}x{h} in {took:.0f}s -> {dst}")
    return took


def assemble(clips, narration, sr, out, slice_secs):
    """Hard-cut the clips' frames; the soundtrack is the continuous narration (not the clips' own audio).
    Each clip is trimmed to EXACTLY its narration slice (LTX returns 8n+1 frames = slice + 1 frame), so lips stay
    locked to the narration across every cut."""
    # STREAMING: one clip decoded at a time (holding every frame of a 20 min video needed ~80 GB RAM -> MemoryError)
    import av, numpy as np
    wants = [int(round(secs * FPS)) for secs in slice_secs]
    total = sum(wants)
    n_audio = int(round(total / FPS * sr))
    a = narration[int(AV_LEAD * sr):int(AV_LEAD * sr) + n_audio]  # starts in the lead-in silence, so nothing is lost
    if len(a) < n_audio:
        a = np.concatenate([a, np.zeros(n_audio - len(a), "float32")])
    a = np.stack([a, a])  # stereo
    first = av.open(str(clips[0])); w, h = first.streams.video[0].codec_context.width, first.streams.video[0].codec_context.height
    first.close()
    o = av.open(str(out), "w")
    vs = o.add_stream("libx264", rate=FPS)
    vs.width, vs.height, vs.pix_fmt = w, h, "yuv420p"
    vs.options = {"crf": "18", "preset": "medium"}
    as_ = o.add_stream("aac", rate=sr, layout="stereo")
    vi = ai = 0

    def audio_until(t):  # keep audio interleaved just ahead of the video
        nonlocal ai
        while ai < a.shape[1] and ai / sr <= t:
            af = av.AudioFrame.from_ndarray(np.ascontiguousarray(a[:, ai:ai + 1024]), format="fltp", layout="stereo")
            af.sample_rate, af.pts = sr, ai
            for pkt in as_.encode(af): o.mux(pkt)
            ai += 1024

    for p, want in zip(clips, wants):
        c = av.open(str(p)); n = 0
        for f in c.decode(video=0):
            if n >= want:
                break
            img = f.to_ndarray(format="rgb24")
            if img.shape[1] != w or img.shape[0] != h:
                raise SystemExit(f"{Path(p).name} is {img.shape[1]}x{img.shape[0]}, expected {w}x{h}")
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
