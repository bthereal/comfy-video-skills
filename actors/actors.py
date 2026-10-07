"""Actors: create and manage reusable characters (face, voice(s), looks) that any skill can use.

  python actors.py list
  python actors.py show --id jane
  python actors.py new  --id jane --name "Jane" --pronoun she --identity "..." --outfit "..." --scene "..." \
                        --voice-style "..." --languages fr,en [--personality "..."] [--format 16x9|9x16] [--seed N]
  python actors.py look --id jane --look paris-cafe --outfit "..." --scene "..." [--ambience "..."] [--format 16x9]
  python actors.py image --id jane [--look default] --format 9x16 [--angle left|right]   # make a look image now
  python actors.py voice --id jane --lang en            # add a language (same cloned voice)
  python actors.py sample --id jane --lang fr [--text "..."]   # record a test line to approve the voice

Everything an actor is lives in actors/<id>/actor.json (see README.md). Requires COMFYUI_DIR, and a running ComfyUI
for anything that makes images or audio (start-comfyui.bat in the repo root).
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import comfy, narration as N, actors as A  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8", errors="replace")


def languages(actor):
    return [N.tts_settings(actor).get("language", "en")] + list(actor.get("voices", {}))


def cmd_list(a):
    for aid in A.ids():
        p = A.load(aid)
        print(f"{p['id']:10} {p['name']:18} speaks: {', '.join(languages(p)):10} looks: {', '.join(p.get('looks', {}))}")
        print(f"{'':10} {p['identity'][:110]}...")


def cmd_show(a):
    p = A.load(a.id)
    print(f"{p['name']} ({p['id']}, {p.get('pronoun', 'they')}) - {p['_dir']}")
    print(f"  identity:    {p['identity']}")
    print(f"  personality: {p.get('personality') or '-'}")
    print(f"  speaks:      {', '.join(languages(p))}  (main voice: {N.tts_settings(p)['voice']})")
    for name, lk in p.get("looks", {}).items():
        imgs = ", ".join(f"{k}" for k in lk.get("images", {})) or "none yet (made on first use)"
        print(f"  look '{name}': {lk['outfit']} | in {lk['scene'][:70]}... | images: {imgs}")


def cmd_new(a):
    comfy.require_server()
    A.create(a.id, a.name, a.pronoun, a.identity, a.outfit, a.scene, a.voice_style, a.languages.split(","),
             a.personality, a.ambience, a.sample_line, a.seed, a.format)


def cmd_look(a):
    comfy.require_server()
    rel = A.add_look(A.load(a.id), a.look, a.outfit, a.scene, a.ambience, a.format)
    print(f"look '{a.look}' created: {rel}")


def cmd_image(a):
    comfy.require_server()
    actor = A.load(a.id)
    print(A.path(actor, A.look_image(actor, a.look, a.format, a.angle)))


def cmd_voice(a):
    actor = A.load(a.id)
    if a.lang in languages(actor):
        raise SystemExit(f"{actor['name']} already speaks '{a.lang}'")
    actor.setdefault("voices", {})[a.lang] = A.tts_for_language(a.lang, N.tts_settings(actor)["voice"])
    A.save(actor)
    print(f"{actor['name']} now speaks {', '.join(languages(actor))} (same voice clip). Approve it with: "
          f"actors.py sample --id {actor['id']} --lang {a.lang}")


def cmd_sample(a):
    """Record one line in a language, in the actor's voice, for the user to approve."""
    comfy.require_server()
    actor = A.load(a.id)
    tts, native = A.voice_for(actor, a.lang)
    text = a.text or A.SAMPLE_LINES.get(a.lang) or raise_(f"no built-in sample for '{a.lang}': pass --text")
    vfile = A.path(actor, tts["voice"])
    out_dir = A.path(actor, "samples"); out_dir.mkdir(exist_ok=True)
    ln = {"name": f"{a.lang}_{a.seed}", "text": text, "tts": tts, "voice_path": vfile, "check": native,
          "voice_upload": comfy.upload(vfile, A.upload_key("actor_", actor, tts["voice"])), "seed": a.seed}
    audio, sr, _ = N.speak_line(ln, out_dir, f"actors/{actor['id']}/samples")
    N.free_mtl()
    dst = out_dir / f"{a.lang}_{a.seed}.wav"
    N.save_wav(dst, audio, sr)
    print(f"{actor['name']} ({a.lang}{'' if native else ', in their own accent'}): {dst}")


def raise_(msg):
    raise SystemExit(msg)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("show"); s.add_argument("--id", required=True)
    n = sub.add_parser("new")
    n.add_argument("--id", required=True); n.add_argument("--name", required=True)
    n.add_argument("--pronoun", default="they", choices=["she", "he", "they"])
    n.add_argument("--identity", required=True, help="fixed physical traits only: age, build, face, eyes, hair, skin")
    n.add_argument("--outfit", required=True); n.add_argument("--scene", required=True, help="their default setting")
    n.add_argument("--voice-style", required=True, help="e.g. 'a warm, bright voice with a light Parisian accent'")
    n.add_argument("--languages", default="en", help="comma-separated, main language first, e.g. fr,en")
    n.add_argument("--personality", default=""); n.add_argument("--sample-line", help="in the main language")
    n.add_argument("--ambience", default="Quiet room tone, no music.")
    n.add_argument("--format", default="16x9", choices=list(A.FORMATS), help="shape of the first portrait")
    n.add_argument("--seed", type=int, default=1234)
    lk = sub.add_parser("look")
    lk.add_argument("--id", required=True); lk.add_argument("--look", required=True)
    lk.add_argument("--outfit", required=True); lk.add_argument("--scene", required=True); lk.add_argument("--ambience")
    lk.add_argument("--format", default="16x9", choices=list(A.FORMATS))
    im = sub.add_parser("image")
    im.add_argument("--id", required=True); im.add_argument("--look", default="default")
    im.add_argument("--format", required=True, choices=list(A.FORMATS)); im.add_argument("--angle", choices=["left", "right"])
    v = sub.add_parser("voice"); v.add_argument("--id", required=True); v.add_argument("--lang", required=True)
    sp = sub.add_parser("sample")
    sp.add_argument("--id", required=True); sp.add_argument("--lang", required=True); sp.add_argument("--text")
    sp.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    {"list": cmd_list, "show": cmd_show, "new": cmd_new, "look": cmd_look, "image": cmd_image, "voice": cmd_voice,
     "sample": cmd_sample}[a.cmd](a)


if __name__ == "__main__":
    main()
