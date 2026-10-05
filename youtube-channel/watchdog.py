"""Unattended render: keeps ComfyUI alive and re-runs `yt.py make` (which resumes) until the final video exists.

  python watchdog.py --plan projects/<slug>/plan.json [--attempts 12]

Launch it DETACHED (Win32_Process Create via WMI - see SKILL.md) so it survives the Claude session.
Log: %TEMP%/influencer_reel/<slug>_watchdog.log (+ <slug>_render.log for the current attempt's output).
"""
import argparse, json, os, subprocess, sys, time, urllib.request
from pathlib import Path

SKILL = Path(__file__).resolve().parent
START_BAT = SKILL.parent / "comfy-influencer-reel" / "start-comfyui.bat"
if not os.environ.get("COMFYUI_DIR"):
    raise SystemExit("COMFYUI_DIR is not set. Set it to your ComfyUI install folder, e.g.\n"
                     '  setx COMFYUI_DIR "C:\\path\\to\\ComfyUI"   (then open a new terminal)')
COMFY_DIR = Path(os.environ["COMFYUI_DIR"])  # your ComfyUI install (required)
OUT = COMFY_DIR / "output" / "youtube"
TMP = Path.home() / "AppData" / "Local" / "Temp" / "influencer_reel"


def up():
    try:
        urllib.request.urlopen("http://127.0.0.1:8188/queue", timeout=5).read()
        return True
    except Exception:
        return False


def log(msg, f):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    f.write(line + "\n"); f.flush()


def ensure_comfy(f):
    if up():
        return True
    log("ComfyUI is down - starting it", f)
    subprocess.Popen(["cmd.exe", "/c", str(START_BAT)], cwd=str(COMFY_DIR),
                     creationflags=subprocess.CREATE_NEW_CONSOLE)
    for _ in range(60):
        time.sleep(5)
        if up():
            log("ComfyUI is up", f)
            time.sleep(10)
            return True
    log("ComfyUI did not come up within 5 min", f)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--attempts", type=int, default=12)
    a = ap.parse_args()
    plan_path = Path(a.plan).resolve()
    slug = json.loads(plan_path.read_text(encoding="utf-8"))["slug"]
    final = OUT / slug / f"{slug}_final.mp4"
    TMP.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with open(TMP / f"{slug}_watchdog.log", "a", encoding="utf-8") as f:
        log(f"watchdog start: {plan_path}", f)
        for attempt in range(1, a.attempts + 1):
            if not ensure_comfy(f):
                time.sleep(60)
                continue
            log(f"attempt {attempt}: yt.py make (resumes finished segments)", f)
            with open(TMP / f"{slug}_render.log", "a", encoding="utf-8") as rl:
                rc = subprocess.call([sys.executable, "-u", "-W", "ignore", str(SKILL / "yt.py"), "make",
                                      "--plan", str(plan_path)], stdout=rl, stderr=subprocess.STDOUT, cwd=str(SKILL))
            if rc == 0 and final.exists() and final.stat().st_mtime > started:
                log(f"DONE: {final} ({(time.time() - started) / 3600:.1f} h)", f)
                return
            log(f"attempt {attempt} ended with rc={rc}; retrying in 60 s", f)
            time.sleep(60)
        log("GAVE UP after all attempts - see the render log", f)


if __name__ == "__main__":
    main()
