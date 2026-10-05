"""Influencer reel tool: named influencers with fixed face + voice, reels on any topic.

  python reel.py list
  python reel.py new   --id maya --name "Maya" --identity "..." --outfit "..." --scene "..." --voice-style "..." [--personality "..."] [--seed N]
  python reel.py look  --id maya --look gym --outfit "..." --scene "..." [--ambience "..."]
  python reel.py reel  --id maya --script "exact words" [--look gym] [--action "..."] [--seconds 10] [--seed N] [--title slug]
                       [--scene "..." --outfit "..." --ambience "..."]   # new setting -> look made on the fly
                       (--seconds > 10 renders chained segments and joins them; '|' in --script marks segment breaks)
  python reel.py save-ui --id maya          # (re)publish the influencer's base workflows to the ComfyUI sidebar

Requires ComfyUI running on 127.0.0.1:8188 and comfy-cli importable as `python -m comfy_cli`.
Every run is also saved to ComfyUI's Workflows sidebar under Influencers/<Name>/.
"""
import argparse, json, math, os, re, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

SKILL = Path(__file__).resolve().parent
INFL = SKILL / "influencers"
WF = SKILL / "workflows"
HOST = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
if not os.environ.get("COMFYUI_DIR"):
    raise SystemExit("COMFYUI_DIR is not set. Set it to your ComfyUI install folder, e.g.\n"
                     '  setx COMFYUI_DIR "C:\\path\\to\\ComfyUI"   (then open a new terminal)')
COMFY_DIR = Path(os.environ["COMFYUI_DIR"])  # your ComfyUI install (required)
if not (COMFY_DIR / "main.py").exists():
    raise SystemExit(f"COMFYUI_DIR={COMFY_DIR} doesn't look like a ComfyUI install (no main.py there)")
COMFY_OUT = COMFY_DIR / "output"

W, H = 704, 1280  # proven on 16 GB; dims must be /64


# ---------------------------------------------------------------- comfy helpers
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


def upload(path, name):
    """Upload under a unique name (e.g. maya__gym.png) so influencers never overwrite each other's inputs."""
    tmp = Path(tempfile.gettempdir()) / "influencer_reel" / name
    tmp.parent.mkdir(exist_ok=True)
    if Path(path).resolve() != tmp.resolve():
        shutil.copy(path, tmp)
    env = cli("upload", str(tmp), "--overwrite")
    return env["data"]["uploads"][0]["cloud_name"]


def upload_look(prof, look_name):
    return upload(INFL / prof["id"] / prof["looks"][look_name]["image"], f"{prof['id']}__{look_name}.png")


def upload_voice(prof):
    return upload(INFL / prof["id"] / "voice.wav", f"{prof['id']}__voice.wav")


def set_slots(wf_path, slots):
    args = [f"{k}={v if isinstance(v, str) else json.dumps(v)}" for k, v in slots.items()]
    cli("workflow", "set-slot", str(wf_path), *args, "--in-place")


def http(method, path, body=None, timeout=30, tries=6):
    """Small JSON request to ComfyUI with retries (local connections occasionally reset on this machine)."""
    data = json.dumps(body).encode() if body is not None else None
    for i in range(tries):
        try:
            req = urllib.request.Request(HOST + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"} if data else {})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError:
            raise
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(3 + 2 * i)


_OBJECT_INFO = None


def object_info():
    """Node catalog (~1.9 MB). Python's urllib read of it gets reset repeatedly on this machine while curl doesn't,
    so fetch with curl, keep a disk cache, and fall back to the cache if the live fetch fails."""
    global _OBJECT_INFO
    if _OBJECT_INFO is not None:
        return _OBJECT_INFO
    cache = Path(tempfile.gettempdir()) / "influencer_reel" / "object_info.json"
    cache.parent.mkdir(exist_ok=True)
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
    """Convert UI->API locally, submit once (safe against connection resets), poll history for the output."""
    from comfy_cli.workflow_to_api import convert_ui_to_api
    print(f"  running {label} (this can take a few minutes)...", flush=True)
    t = time.time()
    api = convert_ui_to_api(json.loads(Path(wf_path).read_text(encoding="utf-8")), object_info())
    marker = f"{Path(wf_path).stem}-{time.time():.3f}"
    body = {"prompt": api, "client_id": "influencer-reel", "extra_data": {"reel_marker": marker}}
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
    d = Path(tempfile.gettempdir()) / "influencer_reel"
    d.mkdir(exist_ok=True)
    dst = d / f"{tag}_{int(time.time())}.json"
    shutil.copy(WF / base, dst)
    return dst


def slug(s, n=40):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n] or "reel"


# ---------------------------------------------------------------- profiles
def load(pid):
    p = INFL / pid / "profile.json"
    if not p.exists():
        raise SystemExit(f"No influencer '{pid}'. Known: {', '.join(x.name for x in INFL.iterdir() if x.is_dir())}")
    return json.loads(p.read_text(encoding="utf-8"))


def store(prof):
    (INFL / prof["id"] / "profile.json").write_text(json.dumps(prof, indent=2), encoding="utf-8")


def pronoun(prof):
    return prof.get("pronoun", "she")


def noun(prof):
    return {"she": "woman", "he": "man"}.get(pronoun(prof), "person")


# ---------------------------------------------------------------- prompt builders
def visual(prof, look, action):
    p = pronoun(prof)
    return (f"Vertical selfie-style handheld phone video, medium close-up. {prof['identity']} "
            f"{p.capitalize()} is wearing {look['outfit']}, in {look['scene']}. "
            f"{p.capitalize()} looks directly at the camera and is speaking, {'her' if p == 'she' else 'his' if p == 'he' else 'their'} "
            f"mouth opens and closes naturally as {p} talks, {action} "
            f"Natural light, realistic skin texture, social media reel look.")


def tagged_prompt(prof, look, script, action, ending=None):
    """ending: None (plain), "final" (silent 1s hold after the last word) or "join" (brief pause before the cut)."""
    p = pronoun(prof)
    vis = visual(prof, look, action)
    sounds = (f"The speaker has {prof['voice_style']}, close to the phone microphone. "
              f"{look.get('ambience', 'Quiet room tone, no music.')}")
    if ending == "final":
        vis += (f" After the last word {p} stops talking completely and holds a warm smile and a small nod in silence "
                f"for a full second until the clip ends.")
        sounds += " The speech finishes a full second before the end of the clip, leaving only quiet ambience."
    elif ending == "join":
        vis += f" After the last word {p} stops talking and pauses in silence for the final half second of the clip."
        sounds += " The speech finishes about half a second before the end of the clip, followed by quiet."
    return f"[VISUAL]: {vis}\n[SPEECH]: {script}\n[SOUNDS]: {sounds}"


DEFAULT_ACTION = ("with small natural head movements, raised eyebrows and a light hand gesture on each point, "
                  "finishing with a confident nod and a smile.")


# ---------------------------------------------------------------- commands
def cmd_list(a):
    for d in sorted(INFL.iterdir()):
        if (d / "profile.json").exists():
            p = json.loads((d / "profile.json").read_text(encoding="utf-8"))
            print(f"{p['id']:10} {p['name']:12} looks: {', '.join(p['looks'])}")
            print(f"{'':10} {p['identity'][:110]}...")


def cmd_new(a):
    pid = a.id.lower()
    d = INFL / pid
    if (d / "profile.json").exists():
        raise SystemExit(f"'{pid}' already exists")
    (d / "looks").mkdir(parents=True, exist_ok=True)
    prof = {"id": pid, "name": a.name, "pronoun": a.pronoun, "identity": a.identity,
            "voice_style": a.voice_style, "personality": a.personality, "seed": a.seed,
            "looks": {"default": {"outfit": a.outfit, "scene": a.scene, "ambience": a.ambience,
                                  "image": "portrait.png"}}}

    print("1/2 portrait (Z-Image Turbo)")
    wf = work_copy("z_image_turbo.json", f"{pid}_portrait")
    still = (f"Vertical smartphone selfie photo. {a.identity} Wearing {a.outfit}, in {a.scene}. "
             f"Looking straight into the camera with a warm, confident expression, mouth closed, framed from "
             f"mid-chest up, centered. Soft natural light, realistic skin texture, shot on a phone front camera, "
             f"social media aesthetic, sharp focus on the face.")
    set_slots(wf, {"57.text": still, "57.width": 720, "57.height": 1280, "57.seed": a.seed,
                   "9.filename_prefix": f"influencers/{pid}/portrait"})
    out = run(wf, "portrait")
    shutil.copy(out, d / "portrait.png")
    save_to_ui(wf, f"Influencers/{a.name}/1 - Portrait (Z-Image)")

    print("2/2 voice sample (LTX-2: invents a voice once; ID-LoRA reuses it forever)")
    img = upload(d / "portrait.png", f"{pid}__default.png")
    wf = work_copy("ltx2_i2v_distilled.json", f"{pid}_voice")
    line = a.sample_line or ("Hey guys, welcome back to my channel. Today I want to share something I genuinely "
                             "love, and I think it could really help you, so stick around until the end.")
    look = prof["looks"]["default"]
    text = (f"{visual(prof, look, DEFAULT_ACTION)} {pronoun(prof).capitalize()} says with {a.voice_style}: "
            f"\"{line}\" Clear close-microphone voice, quiet room tone, no music.")
    data = json.loads(wf.read_text(encoding="utf-8"))
    for n in data["nodes"]:  # top-level widgets: 98=[image], 102=[mode,w,h,...], 92=[frames,text,ckpt,te,ups,seed]
        if n["id"] == 98: n["widgets_values"][0] = img
        if n["id"] == 102: n["widgets_values"][1:3] = [W, H]
        if n["id"] == 75: n["widgets_values"][0] = f"influencers/{pid}/voice_source"
        if n["id"] == 92: n["widgets_values"][0], n["widgets_values"][1], n["widgets_values"][5] = 249, text, a.seed
    wf.write_text(json.dumps(data), encoding="utf-8")
    out = run(wf, "voice sample video")
    extract_voice(out, d / "voice.wav")
    store(prof)
    print(f"\nCreated {a.name}: {d}\n  Check portrait.png, and listen to voice.wav (and {out}) before making reels.")


def extract_voice(mp4, wav_path, max_s=9.0):
    """Keep only the spoken part (RMS-gated), mono 16-bit, for use as the ID-LoRA reference."""
    import av, numpy as np, wave
    c = av.open(str(mp4)); sr = c.streams.audio[0].codec_context.sample_rate
    a = np.concatenate([(f.to_ndarray().mean(axis=0) if f.to_ndarray().ndim > 1 else f.to_ndarray())
                        for f in c.decode(audio=0)]).astype(float)
    win = sr // 10
    rms = np.array([np.sqrt((a[i:i + win] ** 2).mean()) for i in range(0, len(a), win)])
    voiced = np.where(rms > 0.03)[0]
    if not len(voiced):
        raise SystemExit("No speech detected in the voice sample - re-run `new` with another --seed")
    s, e = max(voiced[0] - 2, 0) * win, min((voiced[-1] + 3) * win, len(a))
    a = a[s:e][: int(sr * max_s)]
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype("<i2").tobytes())
    print(f"  voice sample: {len(a) / sr:.1f}s -> {wav_path}")


def make_look(prof, name, outfit, scene, ambience=None, seed=None):
    """Flux.2 edit of the default portrait: same face, new outfit/scene. Stores and returns the look."""
    d = INFL / prof["id"]
    print(f"New look '{name}' for {prof['name']} (Flux.2 Dev edit, keeps the face)")
    ref = upload_look(prof, "default")
    wf = work_copy("flux2_dev.json", f"{prof['id']}_look_{name}")
    p = pronoun(prof)
    edit = (f"Keep the exact same {noun(prof)} from the reference "
            f"image: identical face, facial features, skin tone, hairstyle and hair color. Change only the clothing "
            f"and the location. {p.capitalize()} is now wearing {outfit}, in {scene}. Vertical smartphone selfie "
            f"photo, framed from mid-chest up, centered, looking straight into the camera with a warm, confident "
            f"expression, mouth closed. Natural light, realistic skin texture, phone front camera.")
    set_slots(wf, {"46.image": ref, "68.text": edit, "68.vae_name": "flux2-vae.safetensors", "68.value": True,
                   "68.noise_seed": seed or prof["seed"], "9.filename_prefix": f"influencers/{prof['id']}/look_{name}"})
    out = run(wf, "look")
    dst = d / "looks" / f"{name}.png"
    shutil.copy(out, dst)
    prof["looks"][name] = {"outfit": outfit, "scene": scene,
                           "ambience": ambience or "Quiet room tone, no music.", "image": f"looks/{name}.png"}
    store(prof)
    save_to_ui(wf, f"Influencers/{prof['name']}/Look - {name} (Flux.2 edit)")
    print(f"  look saved: {dst}")
    return prof["looks"][name]


def cmd_look(a):
    prof = load(a.id)
    make_look(prof, a.look, a.outfit, a.scene, a.ambience, a.seed)
    print(f"\nUse with: reel --look {a.look}")


def resolve_look(prof, a):
    """--look picks a saved look. --scene/--outfit describe one: reuse a saved look with the same
    scene+outfit, otherwise create it now (named --look, or a slug of the scene)."""
    if not (a.scene or a.outfit):
        if a.look not in prof["looks"]:
            raise SystemExit(f"Unknown look '{a.look}'. Available: {', '.join(prof['looks'])}")
        return a.look, prof["looks"][a.look]
    base = prof["looks"]["default"]
    outfit, scene = a.outfit or base["outfit"], a.scene or base["scene"]
    for name, lk in prof["looks"].items():
        if lk["outfit"] == outfit and lk["scene"] == scene:
            print(f"  reusing look '{name}'")
            return name, lk
    name = a.look if a.look != "default" else slug(scene, 30)
    if name in prof["looks"]:
        name = f"{name}-{int(time.time()) % 10000}"
    return name, make_look(prof, name, outfit, scene, a.ambience)


SEG_MAX = 10  # seconds per generated segment; 10s @704x1280 peaks at ~15.5 GB VRAM
WPS = 3.3     # spoken words per second (measured)


def split_script(script, seconds):
    """Split into segments of <= SEG_MAX seconds. '|' in the script marks explicit breaks;
    otherwise split at sentence ends, balancing word counts. Returns [(text, seconds)]."""
    if "|" in script:
        chunks = [c.strip() for c in script.split("|") if c.strip()]
    else:
        n = max(1, math.ceil(seconds / SEG_MAX))
        sents = re.split(r"(?<=[.!?])\s+", " ".join(script.split()))
        total = len(script.split())
        chunks, cur = [], []
        for i, s in enumerate(sents):
            cur.append(s)
            remaining_sents = len(sents) - i - 1
            remaining_slots = n - len(chunks) - 1
            if remaining_slots > 0 and (len(" ".join(cur).split()) >= total / n * 0.85 or remaining_sents == remaining_slots):
                chunks.append(" ".join(cur)); cur = []
        if cur:
            chunks.append(" ".join(cur))
    words = [len(c.split()) for c in chunks]
    speech = speech_seconds(seconds, len(chunks))
    tails = [TAIL_JOIN] * (len(chunks) - 1) + [TAIL_FINAL_PLAN]
    durs = [max(3.0, speech * w / sum(words) + t) for w, t in zip(words, tails)]
    snapped, carry = [], 0.0
    for x in durs:  # LTX snaps to 8n+1 frames: use lengths that map exactly, carrying rounding error forward
        s = round((x + carry) * 25 / 8) * 8 / 25
        carry += x - s
        snapped.append(s)
    return list(zip(chunks, snapped))


TAIL_FINAL = 1.0       # seconds of silence REQUIRED after the final word (enforced by holding the last frame if short)
TAIL_FINAL_PLAN = 1.2  # seconds PLANNED for it (measured with the sustained-speech detector: 1.0 planned left 1.24-1.34s)
TAIL_JOIN = 0.6        # pause PLANNED at each join; the prompt tells the speaker to stop (LTX otherwise stretches speech to fill)


def speech_seconds(seconds, n_segments):
    """Time actually available for talking once the end/join pauses are reserved."""
    return seconds - TAIL_FINAL_PLAN - TAIL_JOIN * (n_segments - 1)


def trailing_silence(mp4):
    """Seconds after the last spoken word. Speech = SUSTAINED sound (runs >= 0.15 s above 35% of the speaking
    level, gaps <= 40 ms bridged); short ambience bursts (clanks, footsteps, birdsong) don't count."""
    import av, numpy as np
    c = av.open(str(mp4)); sr = c.streams.audio[0].codec_context.sample_rate
    a = np.concatenate([(lambda x: x.mean(axis=0) if x.ndim > 1 else x)(f.to_ndarray()) for f in c.decode(audio=0)])
    n_video = sum(1 for _ in av.open(str(mp4)).decode(video=0))
    a = a[: int(n_video / 25 * sr)].astype(float)
    hop = 0.02
    w = int(sr * hop)
    r = np.array([np.sqrt((a[i:i + w] ** 2).mean()) for i in range(0, len(a) - w + 1, w)])
    speech_level = float(np.median(r[r > np.percentile(r, 50)])) if len(r) else 0.1
    above = r > 0.35 * speech_level
    runs, start, gap = [], None, 0
    for i, on in enumerate(above):
        if on:
            start = i if start is None else start
            gap = 0
        elif start is not None:
            gap += 1
            if gap > 2:
                runs.append((start, i - gap)); start, gap = None, 0
    if start is not None:
        runs.append((start, len(above) - 1 - gap))
    speech_runs = [(s, e) for s, e in runs if (e - s + 1) * hop >= 0.15]
    return len(a) / sr - ((speech_runs[-1][1] + 1) * hop if speech_runs else 0.0)


# ---------------------------------------------------------------- spoken-word check (Whisper)
WHISPER_DIR = Path.home() / ".cache" / "faster-whisper" / "small.en"  # downloaded with curl (Python's HTTPS fails here)
_WHISPER = None
_NUMWORDS = set("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
                "seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand "
                "million first second third fourth fifth sixth seventh eighth ninth tenth".split())


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", s.lower().replace("-", " ")).split()


def speech_check(mp4, script):
    """Transcribe the clip and check the script's final word was actually spoken (LTX stretches speech to fill the
    clip, so a tight ending is usually fine - but occasionally the last word is cut). Returns None if Whisper isn't
    installed, else {complete, gap (s after the last word), heard}."""
    global _WHISPER
    if not WHISPER_DIR.exists():
        return None
    import av, difflib
    if _WHISPER is None:
        from faster_whisper import WhisperModel
        try:
            _WHISPER = WhisperModel(str(WHISPER_DIR), device="cuda", compute_type="float16")
        except Exception:
            _WHISPER = WhisperModel(str(WHISPER_DIR), device="cpu", compute_type="int8")
    dur = sum(1 for _ in av.open(str(mp4)).decode(video=0)) / 25
    segs, _ = _WHISPER.transcribe(str(mp4), language="en", word_timestamps=True, beam_size=5)
    words = [w for s in segs for w in s.words]
    heard = " ".join(w.word.strip() for w in words)
    want, got = _norm(script), _norm(heard)
    if not want:
        return {"complete": True, "gap": dur, "heard": heard}
    def same(a, b):
        return difflib.SequenceMatcher(None, a, b).ratio() >= 0.75 or \
            (a in _NUMWORDS and any(ch.isdigit() for ch in b))  # "sixty three" is transcribed as "1963"
    # the script's last two words must be the transcript's last two (allowing one stray trailing token)
    end = want[-2:]
    complete = any(len(got) >= len(end) + off and all(same(w, g) for w, g in zip(end, got[len(got) - off - len(end):len(got) - off]))
                   for off in (0, 1))
    if not complete and want[-1] in _NUMWORDS and got and any(ch.isdigit() for ch in got[-1]):
        complete = True  # several number words collapse into one numeric token
    gap = dur - (words[-1].end if words else 0.0)
    return {"complete": complete, "gap": max(0.0, gap), "heard": heard}


CONTINUE_ACTION = ("continuing to talk naturally with small head movements and light hand gestures, "
                   "keeping the same pose and framing.")


def last_frame_png(mp4, png):
    import av
    last = None
    for f in av.open(str(mp4)).decode(video=0):
        last = f
    last.to_image().save(png)


def join_segments(paths, out, pad_end=0.0, chained=True):
    """Concatenate segment mp4s. Re-encodes h264 + AAC, keeping audio sample-locked to video: every segment's audio
    is padded/trimmed to EXACTLY its kept frames (LTX audio is ~19 ms short per clip; the old overlap crossfade also
    ate 20 ms per join - together ~0.25 s of drift by the end of a 7-segment video, visible as bad lip-sync).
    Joins get a 10 ms fade-out/fade-in in place (no overlap, no length change).
    pad_end: seconds of held last frame + silence appended (enforces the 1 s rule as a last resort).
    chained=True: each next segment starts on the previous one's last frame, so that duplicate frame is dropped.
    chained=False: hard cuts between independent clips."""
    import av, numpy as np
    frames, audio, sr, layout = [], None, None, None
    for i, p in enumerate(paths):
        fr = [f.to_ndarray(format="rgb24") for f in av.open(str(p)).decode(video=0)]
        drop = 1 if (i and chained) else 0
        frames += fr[drop:]
        c = av.open(str(p)); st = c.streams.audio[0]
        sr, layout = st.codec_context.sample_rate, st.codec_context.layout.name
        a = np.concatenate([f.to_ndarray() for f in c.decode(audio=0)], axis=1).astype(np.float32)
        # sample-exact: audio for frames [drop, len(fr)) at 25 fps, zero-padded if the clip's audio is short
        start, end = round(drop * sr / 25), round(len(fr) * sr / 25)
        if a.shape[1] < end:
            a = np.concatenate([a, np.zeros((a.shape[0], end - a.shape[1]), np.float32)], axis=1)
        a = a[:, start:end]
        if i:
            x = min(int(sr * 0.01), a.shape[1], audio.shape[1])
            audio[:, -x:] *= np.linspace(1, 0, x, dtype=np.float32)
            a[:, :x] *= np.linspace(0, 1, x, dtype=np.float32)
        audio = a if audio is None else np.concatenate([audio, a], axis=1)
    assert abs(audio.shape[1] - round(len(frames) * sr / 25)) <= len(paths), "audio/video length mismatch"
    if pad_end > 0:
        n = int(math.ceil(pad_end * 25))
        frames += [frames[-1]] * n
        fade = min(int(sr * 0.03), audio.shape[1])  # soften the switch to digital silence
        audio[:, -fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
        audio = np.concatenate([audio, np.zeros((audio.shape[0], int(n / 25 * sr)), np.float32)], axis=1)
    o = av.open(str(out), "w")
    vs = o.add_stream("libx264", rate=25)
    vs.width, vs.height, vs.pix_fmt = frames[0].shape[1], frames[0].shape[0], "yuv420p"
    vs.options = {"crf": "18", "preset": "medium"}
    as_ = o.add_stream("aac", rate=sr, layout=layout)
    vi = ai = 0
    step = 1024
    # interleave by time so players stream it smoothly
    while vi < len(frames) or ai < audio.shape[1]:
        if vi < len(frames) and (ai >= audio.shape[1] or vi / 25 <= ai / sr):
            for pkt in vs.encode(av.VideoFrame.from_ndarray(frames[vi], format="rgb24")): o.mux(pkt)
            vi += 1
        else:
            af = av.AudioFrame.from_ndarray(np.ascontiguousarray(audio[:, ai:ai + step]), format="fltp", layout=layout)
            af.sample_rate, af.pts = sr, ai
            for pkt in as_.encode(af): o.mux(pkt)
            ai += step
    for pkt in vs.encode(): o.mux(pkt)
    for pkt in as_.encode(): o.mux(pkt)
    o.close()
    print(f"  joined {len(paths)} segment(s){f' + {pad_end:.2f}s end hold' if pad_end > 0 else ''} "
          f"-> {out} ({len(frames) / 25:.1f}s)")


def reuse_segment(prof, tag, spec):
    """Resume support: the newest already-rendered segment whose saved spec matches exactly."""
    for mp4 in sorted((COMFY_OUT / "reels" / prof["id"]).glob(f"{tag}_0*.mp4"), reverse=True):
        sp = mp4.with_suffix(".spec.json")
        if sp.exists() and json.loads(sp.read_text(encoding="utf-8")) == spec:
            return mp4
    return None


def cmd_reel(a):
    prof = load(a.id)
    d = INFL / prof["id"]
    look_name, look = resolve_look(prof, a)
    title = a.title or slug(" ".join(a.script.replace("|", " ").split()[:6]))
    segs = split_script(a.script, a.seconds)
    words = len(a.script.replace("|", " ").split())
    target = round(speech_seconds(a.seconds, len(segs)) * WPS)
    if words > target * 1.08 and not a.force:
        raise SystemExit(f"Script is {words} words, but {a.seconds}s with the 1-second end rule only fits ~{target} "
                         f"(speech gets ~{speech_seconds(a.seconds, len(segs)):.1f}s). Trim it, raise --seconds, "
                         f"or pass --force.")
    if words < target * 0.8:
        print(f"  note: script is {words} words; ~{target} fits {a.seconds}s - expect extra pauses")
    print(f"Reel '{title}' - {prof['name']}, look '{look_name}', {a.seconds}s in {len(segs)} segment(s): "
          + ", ".join(f"{s:.2f}s/{len(t.split())}w" for t, s in segs) + f" (target ~{target} words)")
    seed = a.seed if a.seed is not None else prof["seed"]
    voice = upload_voice(prof)
    img = upload_look(prof, look_name)
    outs = []
    for k, (text, secs) in enumerate(segs, 1):
        final = k == len(segs)
        action = (a.action + " " if a.action else "") + (DEFAULT_ACTION if final and not a.action else
                                                         "" if final else CONTINUE_ACTION)
        tag = f"{title}" if len(segs) == 1 else f"{title}_part{k}"
        prompt = tagged_prompt(prof, look, text, action.strip(), "final" if final else "join")
        # Whisper checks the final word was spoken; if not, one re-render with a new seed. (Extra time doesn't help:
        # LTX stretches the speech to fill it.) The 1 s end rule is enforced by padding after the loop.
        for attempt in (0, 1):
            s = seed + k - 1 + 100 * attempt
            seg_secs = secs
            spec = {"prompt": prompt, "secs": seg_secs, "seed": s, "image": img, "look": look_name}
            out = reuse_segment(prof, tag, spec)
            if out:
                print(f"  segment {k}/{len(segs)}: reusing {out.name} (same script/settings)")
            else:
                wf = work_copy("ltx2_3_id_lora.json", f"{prof['id']}_{tag}")
                set_slots(wf, {"269.image": img, "276.audio": voice, "341.filename_prefix": f"reels/{prof['id']}/{tag}",
                               "340.value": prompt, "340.value_1": W, "340.value_2": H, "340.value_3": 25,
                               "340.value_4": seg_secs, "340.noise_seed": s})
                out = run(wf, f"segment {k}/{len(segs)} ({seg_secs:.2f}s)" if len(segs) > 1 else "reel (LTX-2.3 ID-LoRA)")
                out.with_suffix(".spec.json").write_text(json.dumps(spec), encoding="utf-8")
                save_to_ui(wf, f"Influencers/{prof['name']}/Reel - {tag}")
            chk = speech_check(out, text)
            if chk is None:
                gap, ok = trailing_silence(out), True
                print(f"  silence after last word: {gap:.2f}s (no word check: Whisper model missing)")
            else:
                gap, ok = chk["gap"], chk["complete"]
                print(f"  last word {'spoken' if ok else 'MISSING'}; {gap:.2f}s after it")
            if ok or attempt == 1:
                break
            print(f"  heard: ...{chk['heard'][-80:]}")
            print(f"  re-rendering part {k} once with a new seed")
        if not ok:
            print(f"  warning: part {k}'s final word still sounds cut - listen at this join, or shorten that part")
        outs.append(out)
        if not final:  # next segment starts exactly where this one ended
            # named after the exact clip it came from, so resume never pairs a part with a different predecessor
            png = Path(tempfile.gettempdir()) / "influencer_reel" / f"{prof['id']}__{out.stem}_lastframe.png"
            last_frame_png(out, png)
            img = upload(png, png.name)
    pad = max(0.0, TAIL_FINAL - gap)
    if pad:
        print(f"  enforcing the 1s rule: holding the last frame for {pad:.2f}s of silence")
    final_out = outs[0].parent / f"{title}_full.mp4"
    join_segments(outs, final_out, pad_end=pad)
    with (d / "reels.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M"), "title": title, "look": look_name,
                            "seconds": a.seconds, "segments": len(segs), "script": a.script,
                            "output": str(final_out)}) + "\n")
    print(f"\nReel: {final_out}")


def cmd_save_ui(a):
    """Publish ready-to-run base workflows for this influencer (default look) to the ComfyUI sidebar."""
    prof = load(a.id)
    d = INFL / prof["id"]
    look = prof["looks"]["default"]
    img, voice = upload_look(prof, "default"), upload_voice(prof)
    wf = work_copy("ltx2_3_id_lora.json", f"{prof['id']}_template")
    script = "Replace this line with the exact words to say. About twenty eight words fills ten seconds, leaving a pause at the end."
    set_slots(wf, {"269.image": img, "276.audio": voice, "341.filename_prefix": f"reels/{prof['id']}/ui",
                   "340.value": tagged_prompt(prof, look, script, DEFAULT_ACTION, "final"),
                   "340.value_1": W, "340.value_2": H, "340.value_3": 25, "340.value_4": 10,
                   "340.noise_seed": prof["seed"]})
    save_to_ui(wf, f"Influencers/{prof['name']}/Reel template (edit SPEECH)")
    wf = work_copy("flux2_dev.json", f"{prof['id']}_look_template")
    set_slots(wf, {"46.image": img, "68.vae_name": "flux2-vae.safetensors", "68.value": True,
                   "68.text": f"Keep the exact same {noun(prof)} from the reference image: identical face, hairstyle and "
                              f"hair color. Change only the clothing and location. {pronoun(prof).capitalize()} is now "
                              f"wearing <OUTFIT>, in <SCENE>. Vertical smartphone selfie photo, mid-chest up, looking "
                              f"into the camera, mouth closed.",
                   "9.filename_prefix": f"influencers/{prof['id']}/look_ui"})
    save_to_ui(wf, f"Influencers/{prof['name']}/Look template (edit OUTFIT, SCENE)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    n = sub.add_parser("new")
    n.add_argument("--id", required=True); n.add_argument("--name", required=True)
    n.add_argument("--identity", required=True, help="fixed physical traits: age, build, face, hair")
    n.add_argument("--outfit", required=True); n.add_argument("--scene", required=True)
    n.add_argument("--voice-style", required=True, help='e.g. "an upbeat, friendly, conversational tone at moderate volume"')
    n.add_argument("--personality", default=""); n.add_argument("--pronoun", default="she", choices=["she", "he", "they"])
    n.add_argument("--ambience", default="Quiet room tone, no music."); n.add_argument("--sample-line")
    n.add_argument("--seed", type=int, default=424242)
    l = sub.add_parser("look")
    l.add_argument("--id", required=True); l.add_argument("--look", required=True)
    l.add_argument("--outfit", required=True); l.add_argument("--scene", required=True)
    l.add_argument("--ambience"); l.add_argument("--seed", type=int)
    r = sub.add_parser("reel")
    r.add_argument("--id", required=True); r.add_argument("--script", required=True)
    r.add_argument("--look", default="default", help="saved look name (or the name for a new look made from --scene/--outfit)")
    r.add_argument("--scene", help="new setting; creates (or reuses) a look via Flux.2")
    r.add_argument("--outfit", help="new clothing; defaults to the default look's outfit")
    r.add_argument("--ambience", help="background sound for a new look")
    r.add_argument("--action", help="what they do while talking, e.g. 'walking slowly, holding the phone at arm's length'")
    r.add_argument("--seconds", type=int, default=10, help=">10s is rendered as chained ~8-10s segments and joined")
    r.add_argument("--seed", type=int); r.add_argument("--title")
    r.add_argument("--force", action="store_true", help="allow a script longer than the 1-second end rule fits")
    s = sub.add_parser("save-ui"); s.add_argument("--id", required=True)
    a = ap.parse_args()
    if a.cmd != "list" and not server_up():
        raise SystemExit("ComfyUI is not running on 127.0.0.1:8188. Start it:\n"
                         "  double-click start-comfyui.bat in the skill folder")
    {"list": cmd_list, "new": cmd_new, "look": cmd_look, "reel": cmd_reel, "save-ui": cmd_save_ui}[a.cmd](a)


if __name__ == "__main__":
    main()
