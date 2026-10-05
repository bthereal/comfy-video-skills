"""Narration (audio side, shared by both skills): Chatterbox TTS in a persona's cloned voice, Whisper word checks,
and slicing one continuous narration into per-segment pieces on LTX's frame grid.
"""
import difflib, json, math, re, shutil
from pathlib import Path

from . import comfy

FPS = comfy.FPS
GRID = 8 / FPS            # LTX clips are 8n+1 frames -> every clip length is a multiple of 0.32 s
LEAD = 0.12               # silence kept before the first word of a slice
END_SILENCE = 1.0         # the 1-second rule after the final word
NWPS = 3.0                # Chatterbox speaking rate incl. pauses (measured ~3.0-3.2 words/s)
TAKE_MAX_WORDS = 80       # words per continuous TTS take (~28 s); takes break only between segments
SEG_MAX_WORDS = 26        # auto-split target when a plan gives one long script instead of segments
DEFAULT_TTS = {"voice": "voice.wav", "cfg_weight": 0.4, "exaggeration": 0.5, "temperature": 0.5}
# cfg_weight: LOWER follows the reference clip's accent (0.3 very close but slow), HIGHER drifts to the model's
# default American (1.0). temperature 0.5 keeps the accent from wandering between takes.

WHISPER_DIR = Path.home() / ".cache" / "faster-whisper" / "small.en"
_WHISPER = None
_NUMWORDS = set("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
                "seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand "
                "million first second third fourth fifth sixth seventh eighth ninth tenth".split())


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


def md5(path):
    import hashlib
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def reuse(folder, pattern, spec):
    """Resume support: newest file matching pattern whose sidecar .spec.json equals spec."""
    for m in sorted(Path(folder).glob(pattern), reverse=True):
        sp = Path(str(m) + ".spec.json")
        if not sp.exists():
            sp = m.with_suffix(".spec.json")
        if sp.exists() and json.loads(sp.read_text(encoding="utf-8")) == spec:
            return m
    return None


# ---------------------------------------------------------------- script helpers
def split_script(script, max_words=SEG_MAX_WORDS):
    """Split a plain script into segments at sentence ends, each <= max_words where possible. '|' forces a break."""
    parts = [p.strip() for p in script.split("|")] if "|" in script else [script]
    segs = []
    for part in parts:
        cur = []
        for s in re.split(r"(?<=[.!?])\s+", " ".join(part.split())):
            if cur and len(" ".join(cur + [s]).split()) > max_words:
                segs.append(" ".join(cur)); cur = []
            cur.append(s)
        if cur:
            segs.append(" ".join(cur))
    return [s for s in segs if s]


def estimate_seconds(texts):
    return [len(t.split()) / NWPS + 0.45 for t in texts]


# ---------------------------------------------------------------- Whisper
def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", s.lower().replace("-", " ")).split()


def whisper():
    global _WHISPER
    if _WHISPER is None:
        if not WHISPER_DIR.exists():
            raise SystemExit(f"Whisper model not found at {WHISPER_DIR} - download Systran/faster-whisper-small.en there")
        from faster_whisper import WhisperModel
        try:
            _WHISPER = WhisperModel(str(WHISPER_DIR), device="cuda", compute_type="float16")
        except Exception:
            _WHISPER = WhisperModel(str(WHISPER_DIR), device="cpu", compute_type="int8")
    return _WHISPER


def words_with_times(path):
    segs, _ = whisper().transcribe(str(path), language="en", word_timestamps=True, beam_size=5)
    return [w for s in segs for w in s.words]


def extra_words(words, text):
    """Words the take INSERTED relative to the script (TTS repeats/hallucinations, e.g. a phrase said twice).
    Number words vs digits and split/merged words show up as 'replace', not 'insert', so they don't count."""
    want = _norm(text)
    got = [t for w in words for t in _norm(w.word)]
    sm = difflib.SequenceMatcher(None, want, got, autojunk=False)
    return [" ".join(got[j1:j2]) for op, i1, i2, j1, j2 in sm.get_opcodes() if op == "insert" and j2 - j1 >= 2] + \
           [" ".join(got[j1:j2]) for op, i1, i2, j1, j2 in sm.get_opcodes()
            if op == "replace" and (j2 - j1) - (i2 - i1) >= 3]


def ends_complete(words, text):
    """True if the transcript ends with the script's last two words (fuzzy; number words may be digits)."""
    want = _norm(text)[-2:]
    got = [t for w in words for t in _norm(w.word)][-4:]
    if not got:
        return False
    target = "".join(want)  # spaces removed, so "any more" == "anymore", "per cent" == "percent"
    for off in (0, 1):
        for k in (1, 2, 3):
            if len(got) >= k + off:
                cand = "".join(got[len(got) - off - k:len(got) - off])
                if difflib.SequenceMatcher(None, target, cand).ratio() >= 0.8:
                    return True
    return want[-1] in _NUMWORDS and any(c.isdigit() for c in got[-1])


def segment_bounds(words, seg_texts):
    """For each segment, (first_word_start, last_word_end) in the take: walk the transcript matching each segment's
    final word, anchored on the next segment's opening words (fuzzy; numbers may collapse into digits)."""
    def tok(w):
        t = _norm(w.word)
        return "".join(t) if t else ""

    def like(a, b):
        return difflib.SequenceMatcher(None, a, b).ratio() >= 0.75 or (a in _NUMWORDS and any(ch.isdigit() for ch in b))

    bounds, i = [], 0
    for k, text in enumerate(seg_texts):
        t_words = _norm(text)
        target, target2 = t_words[-1], "".join(t_words[-2:])
        start = words[i].start
        if k == len(seg_texts) - 1:
            j = len(words) - 1
        else:
            nxt = _norm(seg_texts[k + 1])
            nxt1, nxt2 = nxt[0], "".join(nxt[:2])
            min_m = i + max(1, int(len(t_words) * 0.6))  # a match far too early is rejected
            best = None
            for m in range(min_m, len(words)):
                w = tok(words[m])
                if not (like(target, w) or like(target2, w) or w.endswith(target2)):
                    continue
                gap = (words[m + 1].start - words[m].end) if m + 1 < len(words) else 9
                # what follows must start the next segment; the first word can be misheard, so 2nd/3rd may match too
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
    """Cut a take into per-segment slices: from LEAD before the first word to the midpoint of the following pause,
    padded with silence up to the 0.32 s grid (so clip length == audio length exactly). The final slice of the
    final take gets END_SILENCE after its last word."""
    import numpy as np
    out, out_end = [], 0.0
    for k, (s, e) in enumerate(bounds):
        a0 = max(0.0, s - LEAD) if k == 0 else out_end
        cut = (e + bounds[k + 1][0]) / 2 if k + 1 < len(bounds) else min(len(take_audio) / sr, e + 0.25)
        piece = take_audio[int(a0 * sr):int(cut * sr)]
        tail = END_SILENCE if (k == len(bounds) - 1 and final_index) else 0.0
        target = max(1, math.ceil((len(piece) / sr + tail) / GRID - 1e-9)) * GRID
        pad = int(round(target * sr)) - len(piece)
        out.append(np.concatenate([piece, np.zeros(max(0, pad), "float32")])[: int(round(target * sr))])
        out_end = cut
    return out


# ---------------------------------------------------------------- TTS
def tts_take(text, voice_upload_name, seed, prefix, cfg_weight, exaggeration, temperature):
    """One continuous Chatterbox take. prefix = ComfyUI output path prefix (e.g. reels/<slug>/narration/take01)."""
    wf = comfy.work_copy("chatterbox_tts.json", prefix)
    comfy.set_slots(wf, {"4.text": text, "6.audio": voice_upload_name, "4.seed": seed, "4.keep_model_loaded": False,
                         "4.cfg_weight": cfg_weight, "4.exaggeration": exaggeration, "4.temperature": temperature,
                         "8.filename_prefix": prefix, "8.format": "flac"})
    return comfy.run(wf, f"narration {Path(prefix).name}")


def narrate(texts, tts, seed, out_dir, out_prefix, voice_upload_name):
    """Speak all segment texts as one continuous narration and slice it per segment.
    texts: segment strings. tts: {voice, cfg_weight, exaggeration, temperature}. out_dir: local folder for takes
    (ComfyUI output/<out_prefix>/narration). Returns (slices, sr, secs, narration_array)."""
    import numpy as np
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    takes, cur = [], []
    for i, t in enumerate(texts):
        if cur and sum(len(texts[j].split()) for j in cur) + len(t.split()) > TAKE_MAX_WORDS:
            takes.append(cur); cur = []
        cur.append(i)
    takes.append(cur)

    def record(idxs, name, base_seed, tries=3):
        text = " ".join(texts[i] for i in idxs)
        for attempt in range(tries):
            spec = {"text": text, **tts, "seed": base_seed + 1000 * attempt}
            take = reuse(out_dir, f"{name}_0*.flac", spec)
            if take:
                print(f"  narration {name}: reusing {take.name}")
            else:
                take = tts_take(text, voice_upload_name, spec["seed"], f"{out_prefix}/narration/{name}",
                                tts["cfg_weight"], tts["exaggeration"], tts["temperature"])
                Path(str(take) + ".spec.json").write_text(json.dumps(spec), encoding="utf-8")
            ws = words_with_times(take)
            if not ends_complete(ws, text):
                print(f"  {name}: last words missing ('...{' '.join(w.word.strip() for w in ws[-4:])}') - re-taking")
                continue
            extra = extra_words(ws, text)
            if extra:
                print(f"  {name}: inserted words {extra} - re-taking")
                continue
            try:
                return take, segment_bounds(ws, [texts[i] for i in idxs])
            except SystemExit as e:
                print(f"  {name}: {e} - re-taking")
        return None, None

    slices, sr = [], None
    for t, idxs in enumerate(takes):
        last_take = t == len(takes) - 1
        take, bounds = record(idxs, f"take{t + 1:02d}", seed + 10 * t)
        if take:
            a_, sr = load_audio(take)
            slices += slice_narration(a_, sr, bounds, final_index=last_take)
            continue
        # Chatterbox sometimes drops the end of a longer take: record that take's segments one at a time instead
        print(f"  take {t + 1} kept dropping words - recording its {len(idxs)} segments separately")
        for k, i in enumerate(idxs):
            take, bounds = record([i], f"take{t + 1:02d}_seg{i + 1:03d}", seed + 10 * t + 5 + k, tries=4)
            if not take:
                raise SystemExit(f"segment {i + 1} keeps dropping words even on its own; simplify: {texts[i][:80]}...")
            a_, sr = load_audio(take)
            slices += slice_narration(a_, sr, bounds, final_index=last_take and k == len(idxs) - 1)
    secs = [len(x) / sr for x in slices]
    full = np.concatenate(slices)
    save_wav(out_dir / "narration.wav", full, sr)
    print(f"  narration: {len(full) / sr:.2f}s in {len(takes)} take(s); segments " + ", ".join(f"{x:.2f}" for x in secs))
    return slices, sr, secs, full


def tts_settings(profile):
    tts = {**DEFAULT_TTS, **profile.get("tts", {})}
    tts.pop("engine", None)
    return tts
