"""ComfyUI plumbing shared by both skills: paths/config, job submission, uploads, workflow copies, UI publishing.

Requires the COMFYUI_DIR environment variable (your ComfyUI install folder). COMFYUI_URL defaults to
http://127.0.0.1:8188. Workflows come from the repo's top-level workflows/ folder.
"""
import json, os, re, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # repo root
WF = ROOT / "workflows"                                  # shared ComfyUI workflow templates
TMP = Path(tempfile.gettempdir()) / "influencer_reel"    # scratch: workflow copies, uploads, logs
TMP.mkdir(exist_ok=True)
HOST = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
if not os.environ.get("COMFYUI_DIR"):
    raise SystemExit("COMFYUI_DIR is not set. Set it to your ComfyUI install folder, e.g.\n"
                     '  setx COMFYUI_DIR "C:\\path\\to\\ComfyUI"   (then open a new terminal)')
COMFY_DIR = Path(os.environ["COMFYUI_DIR"])  # your ComfyUI install (required)
if not (COMFY_DIR / "main.py").exists():
    raise SystemExit(f"COMFYUI_DIR={COMFY_DIR} doesn't look like a ComfyUI install (no main.py there)")
COMFY_OUT = COMFY_DIR / "output"
FPS = 25


def cli(*args, retries=4):
    """Run a comfy-cli verb, return parsed envelope. Retries the flaky /object_info connection resets."""
    for attempt in range(1, retries + 1):
        p = subprocess.run([sys.executable, "-W", "ignore", "-m", "comfy_cli", *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        try:
            env = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else None
        except json.JSONDecodeError:
            env = None
        if env and env.get("ok"):
            return env
        transient = "ConnectionResetError" in p.stderr or "10054" in p.stderr or "10054" in p.stdout
        if not transient or attempt == retries:
            raise SystemExit(f"comfy {' '.join(args[:2])} failed:\n{(env or {}).get('error') or p.stderr[-1500:]}")
        print(f"  connection reset, retry {attempt}...", flush=True)
        time.sleep(10)


def server_up():
    try:
        urllib.request.urlopen(HOST + "/queue", timeout=5).read()
        return True
    except Exception:
        return False


def require_server():
    if not server_up():
        raise SystemExit(f"ComfyUI is not running on {HOST}. Start it by double-clicking start-comfyui.bat "
                         f"in the repo root.")


def upload(path, name):
    """Upload a file into ComfyUI's input folder under a unique name (e.g. maya__gym.png)."""
    tmp = TMP / name
    if Path(path).resolve() != tmp.resolve():
        shutil.copy(path, tmp)
    env = cli("upload", str(tmp), "--overwrite")
    return env["data"]["uploads"][0]["cloud_name"]


def set_slots(wf_path, slots):
    args = [f"{k}={v if isinstance(v, str) else json.dumps(v)}" for k, v in slots.items()]
    cli("workflow", "set-slot", str(wf_path), *args, "--in-place")


def http(method, path, body=None, timeout=30, tries=6):
    """Small JSON request to ComfyUI with retries (local connections occasionally reset on Windows)."""
    data = json.dumps(body).encode() if body is not None else None
    for i in range(tries):
        try:
            req = urllib.request.Request(HOST + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"} if data else {})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError:
            raise
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 + 2 * i)


_OBJECT_INFO = None


def object_info():
    """Node catalog (~1.9 MB). Python's urllib read of it gets reset on some Windows setups while curl doesn't,
    so fetch with curl, keep a disk cache, and fall back to the cache if the live fetch fails."""
    global _OBJECT_INFO
    if _OBJECT_INFO is not None:
        return _OBJECT_INFO
    cache = TMP / "object_info.json"
    part = Path(str(cache) + ".part")
    for i in range(6):
        p = subprocess.run(["curl", "-s", "--fail", "-m", "60", "-o", str(part), HOST + "/object_info"])
        if p.returncode == 0:
            try:
                _OBJECT_INFO = json.loads(part.read_text(encoding="utf-8"))
                part.replace(cache)
                return _OBJECT_INFO
            except (json.JSONDecodeError, OSError):
                pass
        time.sleep(3 + 2 * i)
    if cache.exists():
        print("  using cached node catalog (live fetch failed)", flush=True)
        _OBJECT_INFO = json.loads(cache.read_text(encoding="utf-8"))
        return _OBJECT_INFO
    return http("GET", "/object_info", timeout=60, tries=8)


def find_submitted(marker):
    """prompt_id of a job we already submitted (looked up by marker) in the queue or history, else None."""
    q = http("GET", "/queue")
    for item in q.get("queue_running", []) + q.get("queue_pending", []):
        if item[3].get("reel_marker") == marker:
            return item[1]
    for pid, h in (http("GET", "/history?max_items=20") or {}).items():
        if h.get("prompt", [None] * 4)[3].get("reel_marker") == marker:
            return pid
    return None


def run(wf_path, label):
    """Convert UI->API locally, submit once (safe against connection resets), poll history for the output file."""
    from comfy_cli.workflow_to_api import convert_ui_to_api
    print(f"  running {label} (this can take a few minutes)...", flush=True)
    t = time.time()
    api = convert_ui_to_api(json.loads(Path(wf_path).read_text(encoding="utf-8")), object_info())
    marker = f"{Path(wf_path).stem}-{time.time():.3f}"
    body = {"prompt": api, "client_id": "comfy-video-skills", "extra_data": {"reel_marker": marker}}
    pid = None
    for i in range(6):
        try:
            r = http("POST", "/prompt", body, tries=1)
            if r.get("node_errors"):
                raise SystemExit(f"{label}: ComfyUI rejected the workflow: {json.dumps(r['node_errors'])[:1500]}")
            pid = r["prompt_id"]
            break
        except urllib.error.HTTPError as e:
            raise SystemExit(f"{label}: ComfyUI rejected the workflow ({e.code}): {e.read()[:1500]!r}")
        except SystemExit:
            raise
        except Exception:
            time.sleep(3)
            pid = find_submitted(marker)  # did the reset happen after ComfyUI accepted it?
            if pid:
                break
            print(f"  submit retry {i + 1}...", flush=True)
    if not pid:
        raise SystemExit(f"{label}: could not submit to ComfyUI")
    down_since = None
    while True:
        time.sleep(5)
        if time.time() - t > 3600:
            raise SystemExit(f"{label}: timed out after 1h (prompt {pid})")
        try:
            h = (http("GET", f"/history/{pid}", tries=2) or {}).get(pid)
            down_since = None
        except Exception:
            down_since = down_since or time.time()
            if time.time() - down_since > 120:  # ComfyUI crashed/exited: fail fast so a watchdog can restart + resume
                raise SystemExit(f"{label}: ComfyUI unreachable for 2 min (prompt {pid}) - it probably crashed")
            continue
        if not h:
            continue
        st = h.get("status", {})
        if st.get("status_str") == "error":
            err = next((m[1] for m in st.get("messages", []) if m[0] == "execution_error"), {})
            raise SystemExit(f"{label} failed in {err.get('node_type')}: {err.get('exception_message', '')[:800]}"
                             f"\n(check ComfyUI's log: user/comfyui_8188.err.log)")
        files = [o for node in h.get("outputs", {}).values() for v in node.values() if isinstance(v, list)
                 for o in v if isinstance(o, dict) and o.get("type") == "output" and "filename" in o]
        if st.get("completed") and files:
            out = COMFY_OUT / files[0].get("subfolder", "") / files[0]["filename"]
            print(f"  done in {time.time() - t:.0f}s -> {out}")
            return out
        if st.get("completed"):
            raise SystemExit(f"{label}: finished but produced no output file (prompt {pid})")


def save_to_ui(wf_path, ui_name):
    """Publish a frontend-format workflow to ComfyUI's Workflows sidebar (user/default/workflows/...)."""
    rel = "workflows/" + ui_name.strip("/") + ".json"
    url = f"{HOST}/api/userdata/{urllib.parse.quote(rel, safe='')}?overwrite=true"
    req = urllib.request.Request(url, data=Path(wf_path).read_bytes(), method="POST",
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()
    print(f"  saved to ComfyUI sidebar: Workflows > {ui_name}")


def work_copy(base, tag):
    """Fresh copy of a shared workflow template (workflows/<base>) to edit and run."""
    dst = TMP / f"{tag.replace('/', '_')}_{int(time.time())}.json"
    shutil.copy(WF / base, dst)
    return dst


def slug(s, n=40):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n] or "video"


def last_frame_png(mp4, png):
    import av
    last = None
    for f in av.open(str(mp4)).decode(video=0):
        last = f
    last.to_image().save(png)


# ---------------------------------------------------------------- creating a persona (influencer or narrator)
def make_portrait(prompt, width, height, seed, prefix, label="portrait"):
    """Z-Image Turbo still. Returns (workflow copy, output png)."""
    wf = work_copy("z_image_turbo.json", f"{prefix}_{label}")
    set_slots(wf, {"57.text": prompt, "57.width": width, "57.height": height, "57.seed": seed,
                   "9.filename_prefix": prefix})
    return wf, run(wf, label)


def invent_voice(image_upload, prompt, seed, w, h, prefix):
    """LTX-2 (distilled) image-to-video with speech: invents a voice from the text description, once per persona.
    Its audio becomes the persona's reference clip for Chatterbox. Returns (workflow copy, output mp4)."""
    wf = work_copy("ltx2_i2v_distilled.json", f"{prefix.replace('/', '_')}_voice")
    data = json.loads(wf.read_text(encoding="utf-8"))
    for n in data["nodes"]:  # top-level widgets: 98=[image], 102=[mode,w,h,...], 92=[frames,text,ckpt,te,ups,seed]
        if n["id"] == 98: n["widgets_values"][0] = image_upload
        if n["id"] == 102: n["widgets_values"][1:3] = [w, h]
        if n["id"] == 75: n["widgets_values"][0] = prefix
        if n["id"] == 92: n["widgets_values"][0], n["widgets_values"][1], n["widgets_values"][5] = 249, prompt, seed
    wf.write_text(json.dumps(data), encoding="utf-8")
    return wf, run(wf, "voice sample video")


def extract_voice(mp4, wav_path, max_s=9.0):
    """Keep only the spoken part (RMS-gated) of a clip, mono 16-bit, as a voice reference."""
    import av, numpy as np, wave
    c = av.open(str(mp4)); sr = c.streams.audio[0].codec_context.sample_rate
    a = np.concatenate([(f.to_ndarray().mean(axis=0) if f.to_ndarray().ndim > 1 else f.to_ndarray())
                        for f in c.decode(audio=0)]).astype(float)
    win = sr // 10
    rms = np.array([np.sqrt((a[i:i + win] ** 2).mean()) for i in range(0, len(a), win)])
    voiced = np.where(rms > 0.03)[0]
    if not len(voiced):
        raise SystemExit("No speech detected in the voice sample - re-run with another --seed")
    s, e = max(voiced[0] - 2, 0) * win, min((voiced[-1] + 3) * win, len(a))
    a = a[s:e][: int(sr * max_s)]
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype("<i2").tobytes())
    print(f"  voice sample: {len(a) / sr:.1f}s -> {wav_path}")
