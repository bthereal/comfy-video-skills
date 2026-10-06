"""Post-render QA for dialogue videos: finds lines whose speech is slow or stretched, and clips where an extra
person appears in a single-speaker shot. Results go to <output>/qa/report.json (+ a contact sheet of flagged clips);
`yt.py qa --plan ... --fix` marks flagged lines in the plan so the next `make` re-renders only those.

Detection is deliberately simple and fast:
  speech  Whisper word timings per line -> words/second vs that speaker's typical pace in that language, the longest
          single word (stretched "slow-motion" syllables) and the longest gap inside the line.
  people  torchvision Faster R-CNN (COCO) person boxes on a few frames per clip; >1 person in a single-speaker shot.
"""
import json, statistics
from pathlib import Path

from . import comfy, narration as N

# Calibrated on episode 1 (252 lines): natural emphasis ("Sooo...") stays under these; real glitches (a phrase said
# twice after a long pause, a 2-3 s drawn-out word) go well over.
SLOW_VS_MEDIAN = 0.6     # flag a line slower than this fraction of the speaker's median pace (lines of 4+ words)
STRETCH_PER_CHAR = N.STRETCH_PER_CHAR  # same thresholds the narration check uses while recording
MAX_GAP = N.MAX_GAP
PERSON_SCORE, PERSON_MIN_AREA = 0.75, 0.015  # detector confidence; box area as a fraction of the frame
PERSON_MIN_HEIGHT = 0.33  # a person box must be this tall (fraction of frame): the speaker's own hand isn't
_DET = None


def newest(folder, pattern):
    files = sorted(Path(folder).glob(pattern), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


# ---------------------------------------------------------------- speech
def syllables(text):
    """Rough syllable count (vowel groups; good enough for English and Spanish pace comparisons)."""
    import re
    return sum(max(1, len(re.findall(r"[aeiouyáéíóúü]+", w))) for w in N._norm(text))


def speech_metrics(take, lang, text):
    ws = [w for w in N.words_with_times(take, lang) if w.word.strip()]
    if len(ws) < 2:
        return {"words": len(ws)}
    extra = N.extra_words(ws, text)  # a phrase said twice, or words the script doesn't have
    voiced = sum(w.end - w.start for w in ws)  # time actually speaking (pauses between words excluded)
    syl = syllables(text)
    a, sr = N.load_audio(take)
    _, e = N._speech_span(a, sr)
    tail = e - (N.script_end(ws, text) or ws[-1].end)  # sound after the last real word (babble Whisper ignores)
    art = {"syllables": syl, "art_rate": round(syl / voiced, 2) if voiced > 0 else 0, "tail": round(tail, 2)}
    span = ws[-1].end - ws[0].start
    durs = [(w.end - w.start, w.word.strip()) for w in ws]
    stretch = max((d / (len("".join(N._norm(t))) + 2), d, t) for d, t in durs)
    gaps = [ws[k + 1].start - ws[k].end for k in range(len(ws) - 1)]
    return {"words": len(ws), "span": round(span, 2), "rate": round(len(ws) / span, 2) if span > 0 else 0,
            "stretch": round(stretch[0], 3), "stretch_word": stretch[2], "stretch_secs": round(stretch[1], 2),
            "max_gap": round(max(gaps), 2), "extra": extra, "heard": " ".join(w.word.strip() for w in ws), **art}


def speech_flags(rows, baseline=None):
    """Adds row["speech_issues"] using each (speaker, lang)'s median pace as the baseline (or a given baseline, e.g.
    from the full run when re-checking a few lines)."""
    groups = {}
    for r in rows:
        if r.get("rate") and r["words"] >= 4:
            groups.setdefault((r["speaker"], r["lang"]), []).append(r["rate"])
    med = baseline or {k: statistics.median(v) for k, v in groups.items()}
    for r in rows:
        issues = []
        if not r.get("check", True):  # a deliberately accented/slow learner attempt
            r["speech_issues"] = issues
            continue
        if r.get("extra"):
            issues.append(f"extra/repeated words: '{'; '.join(r['extra'])[:60]}'")
        m = med.get((r["speaker"], r["lang"]))
        if m and r.get("rate") and r["words"] >= 4 and r["rate"] < SLOW_VS_MEDIAN * m:
            issues.append(f"slow: {r['rate']} words/s vs usual {m:.2f}")
        if r.get("stretch", 0) > STRETCH_PER_CHAR:
            issues.append(f"stretched word '{r['stretch_word']}' ({r['stretch_secs']}s)")
        if r.get("max_gap", 0) > MAX_GAP:
            issues.append(f"{r['max_gap']}s pause mid-line")
        r["speech_issues"] = issues
    return med


# ---------------------------------------------------------------- people
def detector():
    global _DET
    if _DET is None:
        import torch, torchvision
        w = torchvision.models.detection.FasterRCNN_ResNet50_FPN_V2_Weights.DEFAULT
        _DET = (torchvision.models.detection.fasterrcnn_resnet50_fpn_v2(weights=w).eval()
                .to("cuda" if torch.cuda.is_available() else "cpu"), w.transforms())
    return _DET


def people_in(frames):
    """Max number of confident, reasonably large person boxes over the given PIL frames."""
    import torch
    model, tf = detector()
    dev = next(model.parameters()).device
    best = 0
    with torch.no_grad():
        out = model([tf(f).to(dev) for f in frames])
    for f, o in zip(frames, out):
        area = f.width * f.height
        n = sum(1 for b, l, s in zip(o["boxes"], o["labels"], o["scores"])
                if l == 1 and s >= PERSON_SCORE and (b[2] - b[0]) * (b[3] - b[1]) / area >= PERSON_MIN_AREA
                and (b[3] - b[1]) / f.height >= PERSON_MIN_HEIGHT)
        best = max(best, n)
    return best


def sample_frames(mp4, fractions=(0.15, 0.5, 0.85)):
    import av
    c = av.open(str(mp4)); n = c.streams.video[0].frames or 1
    want = {int(f * (n - 1)) for f in fractions}
    frames = [fr.to_image() for k, fr in enumerate(c.decode(video=0)) if k in want]
    c.close()
    return frames


# ---------------------------------------------------------------- run
def scan(plan, out_dir, line_tag, clip_files, skip=lambda ln: False):
    """plan: a dialogue plan. line_tag(i, ln) -> clip tag; clip_files(tag) -> [mp4 parts]; skip(ln) -> True to leave a
    line out (it still counts for numbering). Returns (rows, flagged, pace medians)."""
    import av
    lines, rows, n_spoken, t = plan["lines"], [], 0, 0.0
    print(f"QA: {sum(1 for ln in lines if not skip(ln))} lines")
    for i, ln in enumerate(lines, 1):
        start = t
        for p in clip_files(line_tag(i, ln)):  # each clip is its narration slice + 1 frame; gives the timeline
            c = av.open(str(p)); t += (c.streams.video[0].frames - 1) / comfy.FPS; c.close()
        if skip(ln):
            n_spoken += 0 if ln.get("shot") else 1
            continue
        row = {"line": i, "at": f"{int(start // 60)}:{start % 60:04.1f}", "start": round(start, 2),
               "shot": ln.get("shot"), "speaker": ln.get("speaker"), "lang": ln.get("lang", "en"),
               "text": N.plain(ln.get("text", "")), "check": ln.get("check", True) and "{" not in ln.get("text", ""),
               "clips": [str(p) for p in clip_files(line_tag(i, ln))]}
        if not ln.get("shot"):
            n_spoken += 1
            take = newest(Path(out_dir) / "narration", f"line{n_spoken:03d}_0*.flac")
            row["take"] = str(take) if take else None
            if take and "{" not in ln.get("text", ""):  # (mixed-language lines are built from parts; skipped here)
                row.update(speech_metrics(take, row["lang"], row["text"]))
        expected = 2 if ln.get("shot") else 1
        row["people"] = max((people_in(sample_frames(p)) for p in row["clips"]), default=0)
        row["people_issue"] = (f"{row['people']} people in a {'shot' if expected == 2 else 'single-speaker'} clip"
                               if row["people"] > expected else None)
        rows.append(row)
        if i % 20 == 0:
            print(f"  {i}/{len(lines)}", flush=True)
    qa = Path(out_dir) / "qa"; qa.mkdir(exist_ok=True)
    full = len(rows) == len(lines)
    baseline = None
    if not full and (qa / "report.json").exists():  # partial re-check: judge pace against the full run's medians
        prev = json.loads((qa / "report.json").read_text(encoding="utf-8"))["medians"]
        baseline = {tuple(k.split("/")): v for k, v in prev.items()}
    med = speech_flags(rows, baseline)
    flagged = [r for r in rows if r["speech_issues"] or r["people_issue"]]
    name = "report" if full else "recheck"
    (qa / f"{name}.json").write_text(json.dumps({"medians": {f"{k[0]}/{k[1]}": v for k, v in med.items()},
                                                 "flagged": flagged, "all": rows}, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
    contact_sheet(flagged, qa / f"{name}_flagged.png")
    return rows, flagged, med


def contact_sheet(flagged, out, per_row=5, w=320):
    from PIL import Image, ImageDraw
    cells = [(r, p) for r in flagged if r["people_issue"] for p in r["clips"][:1]]
    if not cells:
        return
    h = round(w * 704 / 1280) + 18
    sheet = Image.new("RGB", (per_row * w, ((len(cells) - 1) // per_row + 1) * h), "black")
    d = ImageDraw.Draw(sheet)
    for k, (r, p) in enumerate(cells):
        fr = sample_frames(p, (0.5,))[0].resize((w, h - 18))
        x, y = (k % per_row) * w, (k // per_row) * h
        sheet.paste(fr, (x, y + 18)); d.text((x + 4, y + 3), f"line {r['line']} {r['speaker']}: {r['people']} people", fill="white")
    sheet.save(out)
