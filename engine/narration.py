"""Narration (audio side, shared by both skills): Chatterbox TTS in a persona's cloned voice, Whisper word checks,
and slicing one continuous narration into per-segment pieces on LTX's frame grid.

Other languages (profile tts.engine):
  chatterbox_mtl  Chatterbox Multilingual run in-process, optionally with fine-tuned T3 weights (e.g. Slovak),
                  cloning the persona's voice clip; segments are generated and Whisper-checked one at a time.
  piper_vc        fallback: a native Piper voice says the script, then Chatterbox voice conversion re-voices it.
Both build the take segment by segment, so cut points are exact and Whisper is only a sanity check.
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

WHISPER_ROOT = Path.home() / ".cache" / "faster-whisper"
WHISPER_MODELS = {"en": ("small.en", "Systran/faster-whisper-small.en")}   # English: small and fast
WHISPER_OTHER = ("large-v3-turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo")  # other languages need the big one
PIPER_DIR = Path.home() / ".cache" / "piper"     # <voice>/<voice>.onnx(.json), from rhasspy/piper-voices
SEG_GAP, SENT_GAP = 0.35, 0.2                     # piper_vc: pause between segments / between sentences in one
_WHISPER, _PIPER = {}, {}
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
    return re.sub(r"[^\w ]|_", "", s.lower().replace("-", " ")).split()  # \w keeps accented letters (č, ä, ô...)


def whisper(lang="en"):
    name, repo = WHISPER_MODELS.get(lang, WHISPER_OTHER)
    if name not in _WHISPER:
        d = WHISPER_ROOT / name
        if not d.exists():
            raise SystemExit(f"Whisper model not found at {d} - download {repo} there")
        from faster_whisper import WhisperModel
        try:
            _WHISPER[name] = WhisperModel(str(d), device="cuda", compute_type="float16")
        except Exception:
            _WHISPER[name] = WhisperModel(str(d), device="cpu", compute_type="int8")
    return _WHISPER[name]


def words_with_times(path, lang="en"):
    segs, _ = whisper(lang).transcribe(str(path), language=lang, word_timestamps=True, beam_size=5)
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


STRETCH_PER_CHAR = 0.23  # a word lasting longer than (letters + 2) * this is a drawn-out "slow-motion" glitch
MAX_GAP = 1.5            # a pause inside one line longer than this (seconds) is a glitch, not phrasing


def drawl(words):
    """A TTS glitch that a word check misses (all the words are there, just stretched): a drawn-out word or a long
    pause mid-line. Returns a description or None. (engine/qa.py uses the same thresholds after rendering.)"""
    ws = [w for w in words if w.word.strip()]
    for w in ws:
        letters = len("".join(_norm(w.word)))
        if w.end - w.start > (letters + 2) * STRETCH_PER_CHAR:
            return f"drawn-out word '{w.word.strip()}' ({w.end - w.start:.1f}s)"
    gaps = [(ws[k + 1].start - ws[k].end) for k in range(len(ws) - 1)]
    if gaps and max(gaps) > MAX_GAP:
        return f"{max(gaps):.1f}s pause mid-line"
    return None


def script_end(words, text, min_cover=0.75):
    """Where the script finishes inside a take that ran on (TTS sometimes keeps talking after a short line): the end
    time of the transcript word matching the script's last word, if the transcript up to there covers most of the
    script in order. None if the script isn't clearly there."""
    want = _norm(text)
    got, owner = [], []
    for k, w in enumerate(words):
        for t in _norm(w.word):
            got.append(t); owner.append(k)
    if not want or not got:
        return None
    blocks = difflib.SequenceMatcher(None, want, got, autojunk=False).get_matching_blocks()
    covered = sum(b.size for b in blocks)
    last = [b for b in blocks if b.size and b.a + b.size == len(want)]  # a block ending on the script's last word
    if covered / len(want) < min_cover or not last:
        return None
    return words[owner[last[0].b + last[0].size - 1]].end


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


def _speech_span(a, sr):
    """(start, end) seconds of the audible part of a clip (energy above 5% of its loud level)."""
    import numpy as np
    hop = int(sr * 0.01)
    rms = np.sqrt(np.convolve(a ** 2, np.ones(hop) / hop, mode="same"))[::hop]
    on = np.nonzero(rms > 0.05 * np.percentile(rms, 95))[0]
    return (on[0] * 0.01, (on[-1] + 1) * 0.01) if len(on) else (0.0, len(a) / sr)


def piper_speech(texts, voice, out_wav, length_scale=1.0):
    """Native-language speech for a list of segment texts, one wav. Returns (path, bounds) where bounds are each
    segment's (first_sound, last_sound) in seconds - known exactly, because each segment is synthesized separately."""
    import numpy as np
    from piper import PiperVoice, SynthesisConfig
    onnx = PIPER_DIR / voice / f"{voice}.onnx"
    if not onnx.exists():
        raise SystemExit(f"Piper voice not found at {onnx} - download {voice}.onnx and .onnx.json from "
                         f"rhasspy/piper-voices on Hugging Face there")
    if voice not in _PIPER:
        _PIPER[voice] = PiperVoice.load(str(onnx))
    v, cfg = _PIPER[voice], SynthesisConfig(length_scale=length_scale)
    sr = v.config.sample_rate
    parts, bounds, t = [np.zeros(int(LEAD * sr), "float32")], [], LEAD
    for k, text in enumerate(texts):
        seg = []
        for ch in v.synthesize(text, syn_config=cfg):  # one chunk per sentence
            seg += [ch.audio_float_array, np.zeros(int(SENT_GAP * sr), "float32")]
        seg = np.concatenate(seg[:-1])
        s, e = _speech_span(seg, sr)
        bounds.append((t + s, t + e))
        parts.append(seg); t += len(seg) / sr
        if k + 1 < len(texts):
            parts.append(np.zeros(int(SEG_GAP * sr), "float32")); t += SEG_GAP
    save_wav(out_wav, np.concatenate(parts), sr)
    return Path(out_wav), bounds


def piper_vc_take(texts, tts, voice_upload_name, seed, prefix):
    """piper_vc engine: Piper says the texts, Chatterbox VC re-voices them as the persona (timing is preserved).
    Returns (take_path, bounds)."""
    src, bounds = piper_speech(texts, tts["piper_voice"], comfy.TMP / f"{comfy.slug(prefix)}_piper.wav",
                               tts.get("length_scale", 1.0))
    up = comfy.upload(src, src.name)
    wf = comfy.work_copy("chatterbox_vc.json", prefix)
    comfy.set_slots(wf, {"1.audio": up, "2.audio": voice_upload_name, "3.seed": seed,
                         "4.filename_prefix": prefix, "4.format": "flac"})
    take = comfy.run(wf, f"narration {Path(prefix).name} (voice conversion)")
    (a_t, sr_t), (a_s, sr_s) = load_audio(take), load_audio(src)
    ratio = (len(a_t) / sr_t) / (len(a_s) / sr_s)
    return take, [(s * ratio, e * ratio) for s, e in bounds]


MTL_DIR = comfy.COMFY_DIR / "models" / "chatterbox" / "chatterbox_multilingual"  # ResembleAI/chatterbox mtl files
_MTL = {}


def mtl_model(t3_weights=None):
    """Chatterbox Multilingual, run in this process (the ComfyUI node can't load fine-tuned T3 weights).
    t3_weights: optional fine-tune (path relative to models/chatterbox), e.g. the Slovak
    chatterbox_sk/t3_sk_v2.2.safetensors from pekiskol/chatterbox-tts-slovak."""
    key = t3_weights or "base"
    if key not in _MTL:
        import sys, torch
        _MTL.clear()
        sys.path.insert(0, str(comfy.COMFY_DIR / "custom_nodes" / "comfyui_fill-chatterbox" / "local_chatterbox"))
        from chatterbox import mtl_tts
        if not (MTL_DIR / "t3_mtl23ls_v2.safetensors").exists():
            raise SystemExit(f"Chatterbox Multilingual not found in {MTL_DIR} - download ve.pt, t3_mtl23ls_v2.safetensors,"
                             f" s3gen.pt, grapheme_mtl_merged_expanded_v1.json, conds.pt and Cangjie5_TC.json from "
                             f"ResembleAI/chatterbox there")
        try:  # free ComfyUI's VRAM first (LTX may still be loaded)
            comfy.http("POST", "/free", {"unload_models": True, "free_memory": True})
        except Exception:
            pass
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        m = mtl_tts.ChatterboxMultilingualTTS.from_local(str(MTL_DIR), dev)
        if t3_weights:
            from safetensors.torch import load_file
            state = load_file(str(MTL_DIR.parent / t3_weights), device="cpu")
            n = m.t3.text_emb.weight.shape[0]  # fine-tunes may differ in text vocab size: trim or pad (their recipe)
            for k in ("text_emb.weight", "text_head.weight"):
                w = state[k]
                state[k] = w[:n] if len(w) >= n else torch.cat([w, w.mean(0, keepdim=True).repeat(n - len(w), 1)])
            m.t3.load_state_dict(state, strict=True)
            m.t3.to(dev).eval()
        _MTL[key] = (m, mtl_tts)
    return _MTL[key]


def free_mtl():
    if _MTL:
        import torch
        _MTL.clear()
        torch.cuda.empty_cache()


def mtl_take(texts, tts, voice_path, seed, out_file):
    """chatterbox_mtl engine: each segment is generated separately (fine-tunes lose coherence on long inputs, and
    the cut points are then exact), checked with Whisper in the profile's language, then joined with SEG_GAP pauses.
    Returns (take_path, bounds)."""
    import numpy as np, torch
    m, mod = mtl_model(tts.get("t3_weights"))
    lang = tts["language"]
    mod.SUPPORTED_LANGUAGES.setdefault(lang, lang)  # e.g. 'sk': the tokenizer has the token, the list doesn't
    sr = m.sr
    parts, bounds, t = [np.zeros(int(LEAD * sr), "float32")], [], LEAD
    for k, text in enumerate(texts):
        for attempt in range(4):
            torch.manual_seed(seed + k + 1000 * attempt)
            a = m.generate(text, language_id=lang, audio_prompt_path=str(voice_path),
                           exaggeration=tts.get("exaggeration", 0.5), cfg_weight=tts.get("cfg_weight", 0.5),
                           temperature=tts.get("temperature", 0.8)).squeeze(0).cpu().numpy().astype("float32")
            chk = save_wav(comfy.TMP / "mtl_check.wav", a, sr)
            ws = words_with_times(chk, lang)
            problem = ("ending missing" if not ends_complete(ws, text) else
                       f"inserted words {extra_words(ws, text)}" if extra_words(ws, text) else drawl(ws))
            if not problem:
                break
            end = script_end(ws, text)  # said the line, then ran on: keep the line, cut the rest
            if end is not None:
                a = a[:int((end + 0.15) * sr)]
                kept = [w for w in ws if w.end <= end + 0.01]
                if ends_complete(kept, text) and not extra_words(kept, text) and not drawl(kept):
                    print(f"  segment {k + 1}: ran on after the line - cut at {end:.2f}s")
                    break
            print(f"  segment {k + 1}: {problem} (heard '{' '.join(w.word.strip() for w in ws)}') - re-taking")
        else:
            print(f"  segment {k + 1}: warning - kept the last take; listen to it (Whisper is weak in some languages)")
        s, e = _speech_span(a, sr)
        a = a[max(0, int((s - 0.05) * sr)):int((e + 0.1) * sr)]
        s, e = _speech_span(a, sr)
        bounds.append((t + s, t + e))
        parts.append(a); t += len(a) / sr
        if k + 1 < len(texts):
            parts.append(np.zeros(int(SEG_GAP * sr), "float32")); t += SEG_GAP
    import soundfile as sf
    sf.write(str(out_file), np.concatenate(parts), sr, format="FLAC")
    return Path(out_file), bounds


def narrate(texts, tts, seed, out_dir, out_prefix, voice_upload_name, voice_path=None, name="take", final=True):
    """Speak all segment texts as one continuous narration and slice it per segment.
    texts: segment strings. tts: {voice, cfg_weight, exaggeration, temperature}. out_dir: local folder for takes
    (ComfyUI output/<out_prefix>/narration). name prefixes the take files (several runs can share a folder); final=False
    leaves the 1 s end silence off (the run isn't the end of the video). Returns (slices, sr, secs, narration_array)."""
    import numpy as np
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    takes, cur = [], []
    for i, t in enumerate(texts):
        if cur and sum(len(texts[j].split()) for j in cur) + len(t.split()) > TAKE_MAX_WORDS:
            takes.append(cur); cur = []
        cur.append(i)
    takes.append(cur)

    def record_local(idxs, name, base_seed):
        # piper_vc / chatterbox_mtl build the take segment by segment, so the cut points are exact and Whisper (weak
        # in some languages) isn't needed to find them; here it only warns if the ending sounds wrong.
        text = " ".join(texts[i] for i in idxs)
        spec = {"text": text, **tts, "seed": base_seed}
        take = reuse(out_dir, f"{name}_0*.flac", spec)
        if take:
            print(f"  narration {name}: reusing {take.name}")
            bounds = json.loads(Path(str(take) + ".bounds.json").read_text(encoding="utf-8"))
        else:
            seg_texts = [texts[i] for i in idxs]
            if tts["engine"] == "piper_vc":
                take, bounds = piper_vc_take(seg_texts, tts, voice_upload_name, base_seed,
                                             f"{out_prefix}/narration/{name}")
            else:
                print(f"  running narration {name} (Chatterbox {tts['language']}, {len(seg_texts)} segment(s))...")
                take, bounds = mtl_take(seg_texts, tts, voice_path, base_seed, out_dir / f"{name}_00001.flac")
            Path(str(take) + ".bounds.json").write_text(json.dumps(bounds), encoding="utf-8")
            Path(str(take) + ".spec.json").write_text(json.dumps(spec), encoding="utf-8")
        ws = words_with_times(take, tts.get("language", "en"))
        if not ends_complete(ws, text):
            print(f"  {name}: warning - Whisper heard the ending as '...{' '.join(w.word.strip() for w in ws[-3:])}'"
                  f" (listen to it; Whisper is weak in some languages)")
        return take, bounds

    def record(idxs, name, base_seed, tries=3):
        if tts.get("engine") in ("piper_vc", "chatterbox_mtl"):
            return record_local(idxs, name, base_seed)
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
        take, bounds = record(idxs, f"{name}{t + 1:02d}", seed + 10 * t)
        if take:
            a_, sr = load_audio(take)
            slices += slice_narration(a_, sr, bounds, final_index=last_take and final)
            continue
        # Chatterbox sometimes drops the end of a longer take: record that take's segments one at a time instead
        print(f"  take {t + 1} kept dropping words - recording its {len(idxs)} segments separately")
        for k, i in enumerate(idxs):
            take, bounds = record([i], f"{name}{t + 1:02d}_seg{i + 1:03d}", seed + 10 * t + 5 + k, tries=4)
            if not take:
                raise SystemExit(f"segment {i + 1} keeps dropping words even on its own; simplify: {texts[i][:80]}...")
            a_, sr = load_audio(take)
            slices += slice_narration(a_, sr, bounds, final_index=last_take and final and k == len(idxs) - 1)
    if final:
        free_mtl()  # give the VRAM back before the video clips
    secs = [len(x) / sr for x in slices]
    full = np.concatenate(slices)
    save_wav(out_dir / "narration.wav", full, sr)
    print(f"  narration: {len(full) / sr:.2f}s in {len(takes)} take(s); segments " + ", ".join(f"{x:.2f}" for x in secs))
    return slices, sr, secs, full


LINE_GAP = 0.3  # dialogue: default pause after each line
PART_EDGE = (0.08, 0.12)  # mixed-language line: natural lead-in / tail kept on each part (seconds)
PART_XFADE = 0.025        # ...and the parts are crossfaded together (a gap made the switches sound spliced)


def split_parts(text):
    """'Spaniards say {vale} all the time.' -> [(False, 'Spaniards say'), (True, 'vale'), (False, 'all the time.')]:
    braces mark words in the line's other language."""
    import re
    out = []
    for k, chunk in enumerate(re.split(r"[{}]", text)):
        chunk = chunk.strip(" ,")
        if not chunk:
            continue
        if out and out[-1][0] == (k % 2 == 1):  # '{Zumo.} {Zumo de naranja}': one phrase, not two tiny takes
            out[-1] = (out[-1][0], f"{out[-1][1]} {chunk}")
        else:
            out.append((k % 2 == 1, chunk))
    return out


def plain(text):
    """Line text without the language-switch braces (for captions, checks and estimates)."""
    return text.replace("{", "").replace("}", "")


def speak_line(ln, out_dir, out_prefix):
    """One dialogue line in its speaker's voice. ln: {name, text, tts, voice_upload, voice_path, check, seed}.
    check=False skips the Whisper word check (e.g. a learner's deliberately accented attempt at another language).
    Resumable via a .spec.json sidecar. Returns (audio, sr)."""
    if ln.get("parts"):  # a mixed-language line: each part in the right voice, joined with a short gap
        import numpy as np, librosa
        pieces, sr = [], None
        for k, part in enumerate(ln["parts"]):
            a, psr, take = speak_line({**part, "name": f"{ln['name']}p{k}"}, out_dir, out_prefix)
            a, _ = trim_tail(a, psr, take, part["text"], part["tts"].get("language", "en"))
            s, e = _speech_span(a, psr)
            a = a[max(0, int((s - PART_EDGE[0]) * psr)):int((e + PART_EDGE[1]) * psr)]  # keep the natural breath
            sr = sr or psr
            if psr != sr:
                a = librosa.resample(a, orig_sr=psr, target_sr=sr)
            if pieces:  # crossfade into the next language instead of a gap: sounds like one sentence, not a splice
                x = min(int(PART_XFADE * sr), len(a), len(pieces[-1]))
                ramp = np.linspace(0, 1, x, dtype="float32")
                pieces[-1][-x:] = pieces[-1][-x:] * (1 - ramp) + a[:x] * ramp
                a = a[x:]
            pieces.append(a.copy())
        return np.concatenate(pieces), sr, None
    tts, text = ln["tts"], ln["text"]
    spec = {"text": text, **tts, "seed": ln["seed"]}
    take = reuse(out_dir, f"{ln['name']}_0*.flac", spec)
    if take:
        print(f"  {ln['name']}: reusing {take.name}")
    elif tts.get("engine") == "chatterbox_mtl":
        print(f"  running {ln['name']} (Chatterbox {tts['language']})...")
        n = len(list(out_dir.glob(f"{ln['name']}_0*.flac"))) + 1  # a new file per take (never overwrite an older one)
        take, _ = mtl_take([text], tts, ln["voice_path"], ln["seed"], out_dir / f"{ln['name']}_{n:05d}.flac")
        Path(str(take) + ".spec.json").write_text(json.dumps(spec), encoding="utf-8")
    else:
        for attempt in range(3):
            take = tts_take(text, ln["voice_upload"], ln["seed"] + 1000 * attempt, f"{out_prefix}/narration/{ln['name']}",
                            tts["cfg_weight"], tts["exaggeration"], tts["temperature"])
            if not ln.get("check", True):
                break
            ws = words_with_times(take, tts.get("language", "en"))
            problem = ("ending missing" if not ends_complete(ws, text) else
                       f"inserted words {extra_words(ws, text)}" if extra_words(ws, text) else drawl(ws))
            if not problem:
                break
            print(f"  {ln['name']}: {problem} - re-taking")
        else:
            print(f"  {ln['name']}: warning - kept the last take; listen to it")
    if not Path(str(take) + ".spec.json").exists():
        Path(str(take) + ".spec.json").write_text(json.dumps(spec), encoding="utf-8")
    return (*load_audio(take), take)


TAIL_MAX = 0.6  # sound this long after the script's last word is the TTS babbling on, not the line


def trim_tail(a, sr, take, text, lang):
    """Chatterbox (especially Multilingual on short lines) sometimes keeps making sound after the line: mumbles or
    babble that Whisper doesn't transcribe, so word checks pass. Cut 0.25 s after the script's last word when more
    than TAIL_MAX of sound follows it. Returns (audio, seconds removed)."""
    ws = [w for w in words_with_times(take, lang) if w.word.strip()]
    if not ws:
        return a, 0.0
    last = script_end(ws, text) or ws[-1].end
    _, e = _speech_span(a, sr)
    if e - last <= TAIL_MAX:
        return a, 0.0
    cut = int((last + 0.25) * sr)
    return a[:cut], (len(a) - cut) / sr


def narrate_lines(lines, out_dir, out_prefix, end_silence=True, free=True):
    """Dialogue narration: every line spoken separately in its own speaker's voice and language, trimmed to its
    speech, followed by its pause (line "pause_after", default LINE_GAP) and padded to LTX's grid; the last line gets
    END_SILENCE (unless end_silence=False). Returns (slices, sr, secs, narration_array) like narrate()."""
    import numpy as np, librosa
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    spoken = [None if ln.get("silence") else speak_line(ln, out_dir, out_prefix) for ln in lines]
    sr = next((x[1] for x in spoken if x), 24000)
    slices = []
    for k, (ln, sp) in enumerate(zip(lines, spoken)):
        if sp is None:  # a silent shot (e.g. an establishing wide shot): just time on the grid
            slices.append(np.zeros(int(round(max(1, round(ln["silence"] / GRID)) * GRID * sr)), "float32"))
            continue
        a, asr, take = sp
        a, cut = trim_tail(a, asr, take, ln["text"], ln["tts"].get("language", "en")) if take else (a, 0.0)
        if cut:
            print(f"  {ln['name']}: removed {cut:.1f}s of sound after the last word")
        if asr != sr:
            a = librosa.resample(a, orig_sr=asr, target_sr=sr)
        s, e = _speech_span(a, sr)
        a = a[max(0, int((s - 0.05) * sr)):int((e + 0.1) * sr)]
        tail = (ln.get("pause_after") or LINE_GAP) + (END_SILENCE if end_silence and k == len(lines) - 1 else 0.0)
        piece = np.concatenate([np.zeros(int(LEAD * sr), "float32"), a, np.zeros(int(tail * sr), "float32")])
        n = int(round(max(1, math.ceil(len(piece) / sr / GRID - 1e-9)) * GRID * sr))
        slices.append(np.concatenate([piece, np.zeros(max(0, n - len(piece)), "float32")])[:n])
    if free:
        free_mtl()
    secs = [len(x) / sr for x in slices]
    full = np.concatenate(slices)
    save_wav(out_dir / "narration.wav", full, sr)
    print(f"  narration: {len(full) / sr:.2f}s in {len(lines)} lines")
    return slices, sr, secs, full


def tts_settings(profile):
    """Chatterbox personas: DEFAULT_TTS + profile overrides. piper_vc personas ({"engine": "piper_vc", "language",
    "piper_voice", "voice" (VC target clip), optional "length_scale"}) use only their own keys."""
    t = profile.get("tts", {})
    if t.get("engine") in ("piper_vc", "chatterbox_mtl"):
        return {"voice": "voice.wav", "language": "en", **t}
    tts = {**DEFAULT_TTS, **t}
    tts.pop("engine", None)
    return tts
