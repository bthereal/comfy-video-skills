"""Wait for a running process (by PID) to exit, then run the watchdog on a plan.
  python after_then.py --pid 31688 --plan projects/<slug>/plan.json
"""
import argparse, subprocess, sys, time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--pid", type=int, required=True)
ap.add_argument("--plan", required=True)
a = ap.parse_args()


def alive(pid):
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


while alive(a.pid):
    time.sleep(30)
time.sleep(20)
skill = Path(__file__).resolve().parent
subprocess.call([sys.executable, "-u", "-W", "ignore", str(skill / "watchdog.py"), "--plan", a.plan], cwd=str(skill))
