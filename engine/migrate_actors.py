"""One-off: copy the old per-skill characters (comfy-influencer-reel/influencers/*, youtube-channel/narrators/*) into
the shared actors/ store. Copies only (the old folders are left alone), keeps every file name and value, so prompts,
voice settings and upload names stay identical and existing reels/episodes still resume.

  python engine/migrate_actors.py [--force]
"""
import argparse, json, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [(ROOT / "comfy-influencer-reel" / "influencers", "9x16"), (ROOT / "youtube-channel" / "narrators", "16x9")]
SIDE = {"monica": "right", "clive": "left", "lucia": "right", "diego": "left"}  # which way portrait_side.png faces
KEEP = ("id", "name", "pronoun", "identity", "personality", "voice_style", "language", "seed", "tts", "voices")


def convert(p, fmt):
    a = {k: p[k] for k in KEEP if k in p}
    a["formats"] = {fmt: {k: p[k] for k in ("camera", "lighting", "default_action") if k in p}}
    if "looks" in p:  # influencer: looks with one (vertical) image each
        a["looks"] = {n: {"outfit": lk["outfit"], "scene": lk["scene"], "ambience": lk.get("ambience"),
                          "images": {fmt: lk["image"]}} for n, lk in p["looks"].items()}
    else:  # narrator: one outfit/scene, a 16:9 portrait (+ maybe a turned one for dialogue)
        a["looks"] = {"default": {"outfit": p["outfit"], "scene": p["scene"], "ambience": p.get("ambience"),
                                  "images": {fmt: "portrait.png"}}}
    return a


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--force", action="store_true"); args = ap.parse_args()
    dst_root = ROOT / "actors"
    for src_root, fmt in SOURCES:
        for d in sorted(src_root.iterdir()) if src_root.exists() else []:
            prof_path = d / "profile.json"
            if not prof_path.exists():
                continue
            p = json.loads(prof_path.read_text(encoding="utf-8-sig"))
            dst = dst_root / p["id"]
            if (dst / "actor.json").exists() and not args.force:
                print(f"skip {p['id']} (already in actors/)"); continue
            shutil.copytree(d, dst, dirs_exist_ok=True)
            (dst / "profile.json").unlink(missing_ok=True)
            a = convert(p, fmt)
            if (dst / "portrait_side.png").exists() and p["id"] in SIDE:
                a["looks"]["default"]["images"][f"{fmt}_{SIDE[p['id']]}"] = "portrait_side.png"
            (dst / "actor.json").write_text(json.dumps(a, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"{p['id']:8} <- {d.relative_to(ROOT)}  looks: {', '.join(f'{n} {list(lk['images'])}' for n, lk in a['looks'].items())}")


if __name__ == "__main__":
    sys.exit(main())
