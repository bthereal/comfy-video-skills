"""Actors: reusable characters (fixed face + cloned voice(s) + looks) shared by every skill.

  actors/<id>/actor.json      who they are: identity (fixed physical traits), personality, pronoun, seed,
                              tts (main voice) + voices {lang: tts} (other languages),
                              looks {name: {outfit, scene, ambience, images {format: file}}},
                              formats {format: {camera, lighting, default_action}} (optional per-format overrides)
  actors/<id>/...             reference voice clip(s) and look images (paths in actor.json are relative to here)

Who someone is doesn't depend on the format; how they're framed does. A look's image for a format ("9x16" for
reels, "16x9" for YouTube, plus angled versions like "16x9_left" for dialogue) is created on demand the first time a
skill needs it: a Flux.2 edit of an existing image of that actor, which keeps the face.
"""
import json, re
from pathlib import Path

from . import comfy

ACTORS = comfy.ROOT / "actors"
FORMATS = {"9x16": (704, 1280), "16x9": (1280, 704)}  # the render sizes; looks are generated at these too
FRAMING = {  # default framing per format (an actor's "formats" entry or a plan overrides it)
    "9x16": {"camera": "Vertical selfie-style handheld phone video, medium close-up",
             "lighting": "Natural light, realistic skin texture, social media reel look.",
             "default_action": "with small natural head movements and a light hand gesture on each point.",
             "still": "Vertical smartphone selfie photo, framed from mid-chest up, centered, looking straight into the "
                      "camera, mouth closed. Natural light, realistic skin texture, phone front camera."},
    "16x9": {"camera": "Medium shot, static camera on a tripod, framed from the waist up",
             "lighting": "Soft, flattering light, shallow depth of field, realistic skin texture.",
             "default_action": "with measured, expressive hand gestures and occasional nods for emphasis.",
             "still": "16:9 photograph, medium shot from a static camera on a tripod, framed from the chest up, looking "
                      "straight into the camera, mouth closed. Soft, flattering light, realistic skin texture."},
}
SAMPLE_LINES = {  # what a new actor says when their voice is invented (in their main language)
    "en": "Hello, and welcome. Today I want to talk about something I find genuinely fascinating, and by the end I "
          "think you'll see it in a whole new light.",
    "es": "Hola, bienvenidos. Hoy quiero hablaros de algo que me parece fascinante, y al final creo que lo veréis de "
          "una forma completamente nueva.",
    "fr": "Bonjour et bienvenue. Aujourd'hui, je veux vous parler de quelque chose qui me passionne, et à la fin, je "
          "pense que vous le verrez d'un tout autre œil.",
    "de": "Hallo und herzlich willkommen. Heute möchte ich über etwas sprechen, das ich wirklich faszinierend finde.",
    "it": "Ciao e benvenuti. Oggi voglio parlarvi di qualcosa che trovo davvero affascinante.",
    "pt": "Olá e bem-vindos. Hoje quero falar sobre algo que acho realmente fascinante.",
}
MTL_LANGS = {"ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it", "ja", "ko", "ms", "nl", "no", "pl",
             "pt", "ru", "sv", "sw", "tr", "zh"}  # Chatterbox Multilingual


# ---------------------------------------------------------------- store
def ids():
    return sorted(d.name for d in ACTORS.iterdir() if (d / "actor.json").exists()) if ACTORS.exists() else []


def load(aid):
    p = ACTORS / aid / "actor.json"
    if not p.exists():
        raise SystemExit(f"No actor '{aid}'. Known: {', '.join(ids()) or 'none'} (create one: actors/actors.py new)")
    a = json.loads(p.read_text(encoding="utf-8-sig"))
    a["_dir"] = p.parent
    return a


def save(actor):
    data = {k: v for k, v in actor.items() if not k.startswith("_")}
    (actor["_dir"] / "actor.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def path(actor, rel):
    return actor["_dir"] / rel


def noun(actor):
    return {"she": "woman", "he": "man"}.get(actor.get("pronoun"), "person")


def poss(actor):
    return {"she": "her", "he": "his"}.get(actor.get("pronoun"), "their")


# ---------------------------------------------------------------- framing and voices
def framing(actor, fmt):
    """The actor as a prompt profile for one format: identity etc. plus that format's camera/lighting/default_action
    (the actor's own "formats" entry if it has one, else the format defaults)."""
    f = {k: v for k, v in FRAMING[fmt].items() if k != "still"}
    return {**actor, **f, **actor.get("formats", {}).get(fmt, {})}


def look(actor, name="default"):
    if name not in actor.get("looks", {}):
        raise SystemExit(f"{actor['name']} has no look '{name}'. Looks: {', '.join(actor.get('looks', {}))}")
    return actor["looks"][name]


def main_language(actor):
    """The language an actor speaks when a line doesn't say (their main voice's)."""
    return actor.get("tts", {}).get("language") or actor.get("language") or "en"


def voice_for(actor, lang):
    """(tts settings, native) for the actor saying something in lang. "voices": {lang: tts} gives extra languages
    (e.g. Monica's Spanish via Chatterbox Multilingual); otherwise the main tts is used, and if that's another
    language the line comes out in the actor's own accent (a learner's attempt) and isn't word-checked."""
    from . import narration as N
    if lang in actor.get("voices", {}):
        return N.tts_settings({"tts": actor["voices"][lang]}), True
    tts = N.tts_settings(actor)
    return tts, tts.get("language", "en") == lang


def tts_for_language(lang, voice_file):
    """Voice settings for an actor speaking lang, cloned from voice_file."""
    if lang == "en":
        return {"voice": voice_file, "cfg_weight": 0.4, "exaggeration": 0.5, "temperature": 0.5}
    if lang in MTL_LANGS:
        return {"engine": "chatterbox_mtl", "language": lang, "voice": voice_file,
                "cfg_weight": 0.5, "exaggeration": 0.5, "temperature": 0.6}
    raise SystemExit(f"No built-in voice for '{lang}'. Chatterbox Multilingual covers: {', '.join(sorted(MTL_LANGS))}. "
                     f"For others use a fine-tune (tts.t3_weights, like Slovak) or the piper_vc engine (README: Languages).")


# ---------------------------------------------------------------- look images (on demand)
def _image_key(fmt, angle=None):
    return f"{fmt}_{angle}" if angle else fmt


def _any_image(actor, look_name):
    """An existing image to edit from: this look's (any format), else any image of the actor."""
    imgs = look(actor, look_name).get("images", {})
    for key in sorted(imgs, key=lambda k: ("_" in k, k)):  # prefer front-on images over angled ones
        if path(actor, imgs[key]).exists():
            return imgs[key]
    for lk in actor.get("looks", {}).values():
        for rel in lk.get("images", {}).values():
            if path(actor, rel).exists():
                return rel
    raise SystemExit(f"{actor['name']} has no images yet")


def look_image(actor, look_name, fmt, angle=None, create=True):
    """Relative path of the actor's look image for a format (and optional angle: "left"/"right" = turned towards
    that edge of the frame, for talking to someone off camera). Created on demand and saved in actor.json
    (create=False just reports: None if it doesn't exist yet)."""
    lk = look(actor, look_name)
    key = _image_key(fmt, angle)
    rel = lk.get("images", {}).get(key)
    if rel and path(actor, rel).exists():
        return rel
    if not create:
        return None
    if angle:
        base = look_image(actor, look_name, fmt)  # turn the front-on image of the same format
        p = actor.get("pronoun", "they")
        prompt = (f"Keep the exact same person from the reference image: identical face, facial features, skin tone, "
                  f"hairstyle, hair colour and clothing, in the same place with the same lighting and camera position. "
                  f"{p.capitalize()} has turned {poss(actor)} head and shoulders clearly three-quarters towards the "
                  f"{angle} edge of the frame, eyes looking towards the {angle}, attentive, mouth closed. "
                  f"{p.capitalize()} is the only person in the image: no other people, no figures or shoulders in the "
                  f"foreground.")
        src, size = base, None
    else:
        src = _any_image(actor, look_name)
        prompt = (f"Keep the exact same {noun(actor)} from the reference image: identical face, facial features, skin "
                  f"tone, hairstyle and hair colour. {actor.get('pronoun', 'they').capitalize()} is wearing "
                  f"{lk['outfit']}, in {lk['scene']}. {FRAMING[fmt]['still']}")
        size = FORMATS[fmt]
    print(f"  creating {actor['name']}'s '{look_name}' look for {key} (Flux.2 edit, keeps the face)")
    up = comfy.upload(path(actor, src), f"actor_{actor['id']}__{comfy.slug(src.replace('/', '_'), 60)}.png")
    wf, out = comfy.flux_edit([up], prompt, actor.get("seed", 1), f"actors/{actor['id']}/{look_name}_{key}",
                              label=f"{actor['name']} {look_name} {key}", size=size)
    rel = f"looks/{look_name}/{key}.png"
    path(actor, rel).parent.mkdir(parents=True, exist_ok=True)
    path(actor, rel).write_bytes(Path(out).read_bytes())
    lk.setdefault("images", {})[key] = rel
    save(actor)
    return rel


def upload_key(prefix, actor, rel):
    """Stable ComfyUI upload name for an actor file (clips are reused by name, so this mustn't change)."""
    return f"{prefix}{actor['id']}__{rel.replace('/', '_')}"


# ---------------------------------------------------------------- creating
def add_look(actor, name, outfit, scene, ambience=None, fmt="16x9"):
    if name in actor.get("looks", {}):
        raise SystemExit(f"{actor['name']} already has a look '{name}'")
    actor.setdefault("looks", {})[name] = {"outfit": outfit, "scene": scene,
                                           "ambience": ambience or "Quiet room tone, no music.", "images": {}}
    save(actor)
    return look_image(actor, name, fmt)


def create(aid, name, pronoun, identity, outfit, scene, voice_style, languages=("en",), personality="",
           ambience="Quiet room tone, no music.", sample_line=None, seed=1234, fmt="16x9"):
    """A new actor: a portrait (Z-Image Turbo) in their default look, then a voice invented once by LTX-2 speaking
    their main language (Chatterbox clones it for everything they say; other languages use the same clip)."""
    from . import video as V
    aid = re.sub(r"[^a-z0-9_-]", "", aid.lower())
    d = ACTORS / aid
    if (d / "actor.json").exists():
        raise SystemExit(f"actor '{aid}' already exists")
    languages = list(languages)
    main = languages[0]
    voices = {lang: tts_for_language(lang, "voice.wav") for lang in languages}
    d.mkdir(parents=True, exist_ok=True)
    actor = {"id": aid, "name": name, "pronoun": pronoun, "identity": identity, "personality": personality,
             "voice_style": voice_style, "language": main, "seed": seed, "tts": voices.pop(main), "voices": voices,
             "looks": {"default": {"outfit": outfit, "scene": scene, "ambience": ambience, "images": {}}}, "_dir": d}
    w, h = FORMATS[fmt]
    print(f"1/2 portrait (Z-Image Turbo, {fmt})")
    still = f"{FRAMING[fmt]['still']} {identity} Wearing {outfit}, in {scene}. Sharp focus on the face."
    wf, out = comfy.make_portrait(still, w, h, seed, f"actors/{aid}/portrait")
    rel = f"looks/default/{fmt}.png"
    path(actor, rel).parent.mkdir(parents=True, exist_ok=True)
    path(actor, rel).write_bytes(Path(out).read_bytes())
    actor["looks"]["default"]["images"][fmt] = rel
    save(actor)
    comfy.save_to_ui(wf, f"Actors/{name}/Portrait (Z-Image)")
    print(f"2/2 voice ({main}; LTX-2 invents it once, Chatterbox clones it for every line)")
    line = sample_line or SAMPLE_LINES.get(main)
    if not line:
        raise SystemExit(f"no sample line for '{main}': pass --sample-line in that language")
    img = comfy.upload(path(actor, rel), upload_key("actor_", actor, rel))
    prompt = (f"{V.person_visual(framing(actor, fmt), outfit, scene, None)} {pronoun.capitalize()} says with "
              f"{voice_style}: \"{line}\" Clear close-microphone voice. {ambience}")
    _, vid = comfy.invent_voice(img, prompt, seed, w, h, f"actors/{aid}/voice_source")
    comfy.extract_voice(vid, d / "voice.wav")
    save(actor)
    print(f"\nCreated {name}: {d}\n  Approve the portrait ({rel}) and the voice ({vid}) before using {name}.")
    return actor
