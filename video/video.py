"""Videos of any shape with any cast: talking to camera, conversations, actions, narrated b-roll - 16:9 or 9:16.
The plan says everything (see README.md); actors come from ../actors/.

  python video.py make --plan plans/<slug>/plan.json [--dry-run]
  python video.py qa --plan plans/<slug>/plan.json [--fix [--from-report]] [--lines 12,40]
  python video.py shot --ids monica,clive --name studio-wide --prompt "..." [--format 16x9]   # establishing image
  python video.py convert --plan <old reel/YouTube plan.json> [--out plans/<slug>/plan.json]  # old format -> new

Requires COMFYUI_DIR and a running ComfyUI (start-comfyui.bat in the repo root).
"""
import argparse, json, re, sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL.parent))
from engine import comfy, narration as N, actors as A, timeline as T  # noqa: E402

for _s in (sys.stdout, sys.stderr):  # scripts may be in any language; Windows consoles default to cp1252
    _s.reconfigure(encoding="utf-8", errors="replace")


def load_plan(path):
    plan = json.loads(Path(path).resolve().read_text(encoding="utf-8-sig"))
    if "lines" not in plan and "script" not in plan:
        raise SystemExit("this looks like an old reel/YouTube plan: convert it first with `video.py convert --plan ...`")
    return plan


def cmd_make(a):
    T.make(load_plan(a.plan), a.plan, a.dry_run)


def cmd_shot(a):
    """One image of several actors together (Flux.2 edit with each actor's look image as a reference), e.g. an
    establishing wide shot of two presenters at their table. Saved to shots/<name>.png."""
    comfy.require_server()
    actors = [A.load(i) for i in a.ids.split(",")]
    ups = [comfy.upload(A.path(p, rel), A.upload_key("vid_", p, rel))
           for p in actors for rel in [A.look_image(p, a.look, a.format)]]
    who = " ".join(f"Reference image {k + 1} shows {p['name']}: keep {A.poss(p)} exact face, hair and clothing."
                   for k, p in enumerate(actors))
    wf, out = comfy.flux_edit(ups, f"{who} {a.prompt}", a.seed, f"videos/shots/{a.name}", label=f"shot {a.name}",
                              size=A.FORMATS[a.format])
    dst = T.SHOTS / f"{a.name}.png"
    dst.parent.mkdir(exist_ok=True)
    dst.write_bytes(Path(out).read_bytes())
    comfy.save_to_ui(wf, f"Videos/Shots/{a.name} (Flux.2 edit)")
    print(f"Saved {dst} - use it as {{\"shot\": \"{a.name}\", \"seconds\": 2.56}}")


def cmd_qa(a):
    """Check a rendered video line by line (speech glitches, extra people in single-person shots). --fix marks the
    flagged lines in the plan (retake_audio / retake_video + solo) so the next `make` re-renders only those."""
    from engine import qa
    plan_path = Path(a.plan).resolve()
    plan = T.normalise(load_plan(plan_path))
    out_dir = comfy.COMFY_OUT / plan.get("output", "videos") / plan["slug"]
    try:  # Whisper and the person detector need the VRAM ComfyUI may still be holding
        comfy.http("POST", "/free", {"unload_models": True, "free_memory": True})
    except Exception:
        pass
    as_footage = T.footage_plan(plan, plan.get("seed", 1))

    def files(t):
        one = qa.newest(out_dir, f"{t}_0*.mp4")
        if one:
            return [one]
        parts = sorted({int(m.group(1)) for p in out_dir.glob(f"{t}_p*_0*.mp4") if (m := re.search(r"_p(\d+)_0", p.name))})
        return [qa.newest(out_dir, f"{t}_p{k}_0*.mp4") for k in parts]

    only = {int(x) for x in a.lines.split(",")} if a.lines else None
    def resolved(ln):  # the language each line is actually spoken in (an actor's own unless the line says)
        sid = ln.get("speaker") or ln.get("voice")
        return {**ln, "lang": ln.get("lang") or A.main_language(A.load(sid))} if sid and ln.get("text") else ln

    lines = [{**ln, "_skip": True} if (only and i not in only) else
             resolved({**ln, "footage": ln.get("broll")} if as_footage.get(i - 1) else ln)
             for i, ln in enumerate(plan["lines"], 1)]
    if a.from_report:  # reuse the last full scan (e.g. after reviewing it) instead of scanning again
        rep = json.loads((out_dir / "qa" / "report.json").read_text(encoding="utf-8"))
        rows, flagged = rep["all"], rep["flagged"]
        med = {tuple(k.split("/")): v for k, v in rep["medians"].items()}
    else:
        rows, flagged, med = qa.scan({**plan, "lines": lines}, out_dir,
                                     lambda i, ln: T.clip_tag(plan, i, ln, as_footage), files,
                                     skip=lambda ln: ln.get("_skip"))
    print("\nTypical pace (words/s): " + ", ".join(f"{k[0]}/{k[1]} {v:.2f}" for k, v in sorted(med.items())))
    print(f"{len(flagged)} flagged line(s) of {len(rows)}:")
    for r in flagged:
        why = r["speech_issues"] + ([r["people_issue"]] if r["people_issue"] else [])
        print(f"  {r['line']:3}. {r.get('at', ''):>7} {r['speaker'] or 'shot':7} {'; '.join(why):55} | {r['text'][:45]}")
    name = "recheck" if only else "report"
    print(f"Report: {out_dir / 'qa' / (name + '.json')}  (contact sheet: {name}_flagged.png)")
    if a.fix and flagged:
        raw = json.loads(plan_path.read_text(encoding="utf-8-sig"))
        for r in flagged:
            ln = raw["lines"][r["line"] - 1]
            if r["speech_issues"]:
                ln["retake_audio"] = ln.get("retake_audio", 0) + 1
            if r["people_issue"]:
                ln["retake_video"] = ln.get("retake_video", 0) + 1
                if not ln.get("shot"):
                    ln["solo"] = True
        plan_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Marked {len(flagged)} line(s) for re-rendering in {plan_path.name}; run `make` (only those re-render), "
              f"then `qa --lines ...` to re-check them.")


# ---------------------------------------------------------------- converting old plans
def convert(old):
    """An old comfy-influencer-reel or youtube-channel plan -> a video plan."""
    if old.get("lines") and old.get("cast"):  # YouTube dialogue: already lines; keep its names so it still resumes
        return {**old, "format": "16x9", "narration": "per_line", "output": "youtube", "upload_prefix": "yt_"}
    keep = {k: old[k] for k in ("title", "slug", "seed", "upscale_to", "width", "height", "topic", "genre", "seconds")
            if k in old}
    if old.get("influencer"):  # reel: one actor, one continuous shot
        aid = old["influencer"]
        c = {k: old[k] for k in ("look", "scene", "outfit", "ambience", "action") if k in old}
        lines = []
        if old.get("intro"):
            lines.append({"actor": aid, "seconds": old["intro"]["seconds"], "action": old["intro"]["action"]})
        segs = old.get("segments") or [{"text": t} for t in N.split_script(old.get("script", ""))]
        for s in segs:
            s = s if isinstance(s, dict) else {"text": s}
            lines.append({"speaker": aid, **{k: s[k] for k in ("text", "action", "gaze") if k in s}})
        plan = {**keep, "format": "9x16", "continuous": True, "narration": "flowing", "cast": {aid: c}, "lines": lines}
        if old.get("camera"):
            plan["camera"] = old["camera"]
        if old.get("sfx"):  # segment numbers -> line numbers
            plan["sfx"] = [{**{k: v for k, v in fx.items() if k != "segment"},
                            "line": fx.get("segment", 1) + (1 if old.get("intro") else 0)} for fx in old["sfx"]]
        return plan
    if old.get("narrator"):  # YouTube narrator + b-roll
        aid = old["narrator"]
        c = {"action": old["narrator_action"]} if old.get("narrator_action") else {}
        lines = [{"speaker": aid, "text": s["text"], **({"broll": s["shot"]} if s.get("shot") else {}),
                  **({"type": "on_camera" if s["type"] == "narrator" else "footage"} if s.get("type") else {}),
                  **({"action": s["action"]} if s.get("action") else {})} for s in old["segments"]]
        plan = {**keep, "format": "16x9", "narration": "flowing", "cast": {aid: c}, "lines": lines,
                "auto_footage": {"share": old.get("narrator_share", 0.4)}}
        for k in ("style", "ambience", "camera_motion"):
            if k in old:
                plan[k] = old[k]
        return plan
    raise SystemExit("not an old reel (influencer), YouTube narrator (narrator) or dialogue (cast + lines) plan")


def cmd_convert(a):
    src = Path(a.plan).resolve()
    plan = convert(json.loads(src.read_text(encoding="utf-8-sig")))
    dst = Path(a.out) if a.out else SKILL / "plans" / plan["slug"] / "plan.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    for f in src.parent.iterdir():  # bring the plan's own files along (sound effects, a builder script...)
        if f.is_file() and f.name != "plan.json" and not (dst.parent / f.name).exists():
            (dst.parent / f.name).write_bytes(f.read_bytes())
    dst.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{src} -> {dst}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make"); m.add_argument("--plan", required=True); m.add_argument("--dry-run", action="store_true")
    q = sub.add_parser("qa")
    q.add_argument("--plan", required=True); q.add_argument("--fix", action="store_true")
    q.add_argument("--lines", help="comma-separated line numbers to check (default: all)")
    q.add_argument("--from-report", action="store_true", help="with --fix: mark lines from the last report, no re-scan")
    s = sub.add_parser("shot")
    s.add_argument("--ids", required=True, help="comma-separated actor ids, e.g. monica,clive")
    s.add_argument("--name", required=True); s.add_argument("--prompt", required=True)
    s.add_argument("--look", default="default"); s.add_argument("--format", default="16x9", choices=list(A.FORMATS))
    s.add_argument("--seed", type=int, default=1)
    c = sub.add_parser("convert"); c.add_argument("--plan", required=True); c.add_argument("--out")
    a = ap.parse_args()
    {"make": cmd_make, "qa": cmd_qa, "shot": cmd_shot, "convert": cmd_convert}[a.cmd](a)


if __name__ == "__main__":
    main()
