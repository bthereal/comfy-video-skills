"""Unattended render for either skill: keeps ComfyUI alive and re-runs `make` (which resumes finished takes/clips)
until the final video exists. Works out the skill from the plan: "influencer" -> reel.py, "narrator" -> yt.py.

  python engine/watchdog.py --plan youtube-channel/projects/<slug>/plan.json [--attempts 12]

Launch it DETACHED (Win32_Process Create via WMI - see the skills' SKILL.md) so it survives the Claude session.
Logs: %TEMP%/influencer_reel/<slug>_watchdog.log and <slug>_render.log.
"""
import argparse, json, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import comfy  # noqa: E402  (also enforces COMFYUI_DIR)

START_BAT = comfy.ROOT / "start-comfyui.bat"


def log(msg, f):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    f.write(line + "\n"); f.flush()


def ensure_comfy(f):
    if comfy.server_up():
        return True
    log("ComfyUI is down - starting it", f)
    subprocess.Popen(["cmd.exe", "/c", str(START_BAT)], cwd=str(comfy.COMFY_DIR),
                     creationflags=subprocess.CREATE_NEW_CONSOLE)
    for _ in range(60):
        time.sleep(5)
        if comfy.server_up():
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
    plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    slug = plan["slug"]
    if "influencer" in plan:
        tool, kind = comfy.ROOT / "comfy-influencer-reel" / "reel.py", "reels"
    elif "narrator" in plan:
        tool, kind = comfy.ROOT / "youtube-channel" / "yt.py", "youtube"
    else:
        raise SystemExit("plan has neither 'influencer' (reel) nor 'narrator' (YouTube)")
    final = comfy.COMFY_OUT / kind / slug / f"{slug}_final.mp4"
    started = time.time()
    with open(comfy.TMP / f"{slug}_watchdog.log", "a", encoding="utf-8") as f:
        log(f"watchdog start: {plan_path} ({tool.name})", f)
        for attempt in range(1, a.attempts + 1):
            if not ensure_comfy(f):
                time.sleep(60)
                continue
            log(f"attempt {attempt}: {tool.name} make (resumes finished takes and clips)", f)
            with open(comfy.TMP / f"{slug}_render.log", "a", encoding="utf-8") as rl:
                rc = subprocess.call([sys.executable, "-u", "-W", "ignore", str(tool), "make", "--plan", str(plan_path)],
                                     stdout=rl, stderr=subprocess.STDOUT, cwd=str(tool.parent))
            if rc == 0 and final.exists() and final.stat().st_mtime > started:
                log(f"DONE: {final} ({(time.time() - started) / 3600:.1f} h)", f)
                return
            log(f"attempt {attempt} ended with rc={rc}; retrying in 60 s", f)
            time.sleep(60)
        log("GAVE UP after all attempts - see the render log", f)


if __name__ == "__main__":
    main()
