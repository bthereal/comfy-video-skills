# Prompting guide

You don't need to write plans or remember commands. Describe what you want to Claude Code (with the `actors` and `video` skills linked, see the [README](README.md#using-them-as-claude-code-skills)) and it will:

1. **Create any actors** that don't exist yet, then show you their portrait and a voice sample in each language for approval.
2. **Write the plan and script** for the video, and show you a dry run: every line, who says it, in which language, roughly how long it takes.
3. **Render** once you're happy. Short videos take minutes; long ones run unattended overnight.
4. **Check and repair:** QA flags glitches, and you can point at timestamps ("she isn't speaking at 3:16") to re-render just those lines.

This guide lists what you can ask for, how each phrase maps to the options underneath, and example prompts to copy.

---

## 1. Creating an actor

An actor is a reusable character: a fixed face, a cloned voice (in one or more languages), a personality, and any number of looks. Create them once and use them in any video.

### What to include

| Say | Becomes | Notes |
|---|---|---|
| A name ("an actor named Jane") | `id` and `name` | Any fictional name. |
| He / she / they | `pronoun` | Used in the visual prompts. |
| **Fixed physical traits**: age, build, skin, face, eyes, hair | `identity` | Only things that never change. Clothes and setting belong to a look; expressions to the video. |
| What they wear and where they usually are | the `default` look (`outfit` + `scene`) | If you don't say, Claude picks something that fits. |
| Languages, main one first ("speaks French, and English with a French accent") | `languages` | Built-in voices: ar da de el en es fi fr he hi it ja ko ms nl no pl pt ru sv sw tr zh. Others (like Slovak) need a fine-tune. See the README's Languages section. |
| How they sound: timbre, pace, energy, accent | `voice_style` | "a warm, low voice with a soft Edinburgh accent, unhurried". |
| How they talk and think | `personality` | Used whenever Claude writes their lines. |
| Mainly for reels / mainly for landscape | first portrait `9x16` / `16x9` | Only affects the first image; they work in both formats either way. |

Never base an actor on a real person's face, name or voice.

### Examples

**Minimal:**
> Create an actor named Jane, brown eyes, speaks French.

Claude fills the gaps (age, hair, outfit, a Paris setting, voice) and tells you what it chose.

**Detailed:**
> Create an actor called Tomás: a man in his late forties, stocky build, olive skin, short grey beard, kind dark eyes, thinning black hair. He usually wears a flour-dusted white chef's jacket in a small, busy restaurant kitchen in Seville. He speaks Spanish, and English with a strong Andalusian accent. Deep, warm, slightly gravelly voice, talks quickly when excited. Personality: passionate home-style cook, impatient with shortcuts, tells stories about his mother's recipes.

**A learner (speaks one language, attempts another):**
> Create Priya, a British Indian woman in her mid-twenties, slim, long black hair in a braid, round glasses. Casual: a mustard cardigan in a cosy London flat. She only speaks English, with a South London accent, upbeat and chatty. She's learning Italian, so she should sound like a beginner when she tries it.

(Her Italian attempts are spoken in her English voice, so they sound like a learner, and the word check is skipped for them.)

**For vertical content:**
> Create a fitness creator for reels: Kai, early thirties, athletic, light brown skin, shaved head, big friendly grin. Black gym vest in a bright modern gym. English, energetic Australian accent.

### Approving and adjusting
- *"Re-roll Jane's face, she looks too young"*: a new seed or an adjusted identity.
- *"Make Tomás's voice deeper"* or *"his accent keeps drifting American"*: Claude re-invents the voice, lowers `cfg_weight`, or uses a 10-second cut of a take you like as the new reference.
- *"Let me hear Jane in English"*: a sample in that language.
- *"Jane should also speak German"*: adds a language (same voice).

---

## 2. Scenes and looks

A **look** is an outfit plus a setting plus its background sound. Each actor has a `default` look, and you can add as many as you like. A look's image for each format is made the first time a video needs it, keeping the actor's face.

| Say | Becomes |
|---|---|
| "in her gym look", "in the canal look" | a saved `look` |
| "at a café terrace in Paris", "on a rainy Tokyo street at night" | a new look's `scene` |
| "wearing a red raincoat" | a new look's `outfit` (otherwise the default look's) |
| "with the sound of rain", "busy café chatter" | `ambience` (used as context; silent beats are still silent, see Limits) |
| "she's sitting at a marble table with a croissant and coffee" | put props and furniture in the scene so the first image has them |

Describe scenes **visually and concretely**: place, time of day, light, furniture, what's on the table. *"A bright Paris café terrace, small round marble tables, wicker chairs, a Haussmann facade, soft morning light"* works far better than *"a nice café"*.

> Give Maya a new look: running along a beach at sunrise in a teal running top.

> Put James in a smart navy suit in a glass-walled City of London office at dusk.

---

## 3. Making a video

### The options in plain words

| Say | Option | Default |
|---|---|---|
| **Shape:** "16:9", "landscape", "YouTube", "widescreen" | `format: 16x9` | asks; 16:9 if you don't mind |
| **Shape:** "9:16", "vertical", "reel", "short", "TikTok", "story" | `format: 9x16`, usually as one continuous selfie-style shot | |
| **Length:** "15 seconds", "about 2 minutes", "a 20-minute episode" | sets the script length (≈3 English words a second) | |
| **Who:** names of actors | `cast` | |
| **Where / wearing:** a saved look, or a new scene and outfit | the cast member's `look`, or `scene` + `outfit` | their default look |
| **What happens:** "she breaks off a piece of croissant", "he walks along the towpath" | `action` on lines; a silent beat for an action without speech | a speaking action |
| **What's said:** a topic ("about hydration"), an outline, or exact words in quotes | the script; exact words are used verbatim | Claude writes it in their voice |
| **Language:** "in French", "Monica explains in English and models the Spanish" | `lang` per line; words in another language inside a line, marked automatically | each actor's main language |
| **Captions:** "with captions", "with English translations under the Spanish" | `caption` per line (a second line in smaller type) | none |
| **Conversation:** "Jane and Tomás chat across the table" | dialogue: each speaker turned towards the other, cuts between them | |
| **Establishing shot:** "open on a wide shot of them at the table" | a `shot` (both actors in one image) | |
| **B-roll / documentary:** "intercut with footage of…", "documentary style" | `broll` descriptions + automatic footage split, or explicit footage lines; a `style` for the footage look | |
| **Talking to camera vs each other** | `gaze` | to camera |
| **One unbroken shot vs cuts** | `continuous` | continuous for 9:16 single-actor videos |
| **Framing:** "static camera on the counter, she needs both hands" | `camera` | selfie (9:16) / tripod medium shot (16:9) |
| **Pauses:** "leave a pause after each phrase for viewers to repeat" | `pause_after` | 0.3 s |
| **A learner getting it wrong first:** "Clive tries it with an English accent first" | an accented attempt (spelled the way he'd say it), no word check | |
| **Sound effects:** "a phone buzzes at the start", with a sound file | `sfx` | none |
| **1080p:** "also give me a 1080p version" | `upscale_to` | 1280x704 / 704x1280 only |
| **A different take:** "try another version of that" | a new `seed` | |
| **Overnight:** anything long | detached render with the watchdog | |

### Example prompts

**Talking to camera, landscape:**
> A 16:9 video of Jane dining in a café in Paris, about 25 seconds. She tells us about her favourite café in French, with English captions, and in between she takes a bite of croissant and sips her coffee.

**Vertical reel:**
> A 20-second reel of Maya at the gym about progressive overload for beginners. Hook in the first line, one practical tip, finish on a confident nod.

**Exact words:**
> A 9:16 video of James by the canal saying exactly: "Right, here's the thing about ISAs. Use it or lose it: your allowance resets every April. So don't leave it until the last minute."

**A scene with action and props (static camera):**
> A vertical video of Radka in her kitchen. She takes a ham out of the oven and puts it on the island in front of the camera while greeting us in Slovak, then looks worried about the smell and shouts off camera to her friend Jarka to open the window. Static camera on the island so she can use both hands. Keep her expression worried, not smiling.

**A conversation:**
> A 16:9 conversation between Tomás and Priya in his restaurant kitchen, about 1 minute. Priya asks how to make a proper tortilla española. Tomás explains in English, saying the Spanish names of things in Spanish, and Priya tries to repeat them with her English accent. Open with a wide shot of the two of them at the counter. Captions on the Spanish words with English translations.

**A documentary with b-roll:**
> A 3-minute 16:9 documentary narrated by Vale on the history of the Paris catacombs. About 40% Vale on camera, the rest footage: dark limestone tunnels, walls of stacked bones, 18th-century Paris streets, an old engraving-style map. Moody, desaturated, 35mm film look. Keep the documented history separate from the legends.

**A language lesson:**
> A 5-minute 16:9 Spanish lesson with Monica and Clive. First a short scene of Lucía and Diego at a market stall buying fruit, with Spanish captions and English translations. Then back to the studio: Monica explains three key phrases, Clive tries each one with an English accent first and then gets it right, and Monica says each phrase to camera with a pause for viewers to repeat. Finish with a quick quiz.

### After the render
- *"Run QA on it"*: a report of flagged lines with timestamps and a contact sheet.
- *"Fix everything QA found"*: only those lines re-render.
- *"At 13:39 Lucía isn't speaking, and at 10:02 there's someone at the edge of the frame"*: those lines re-render with a new seed (and a speaking-focused prompt).
- *"There's a weird text banner at 6:00"*: that line re-renders.
- *"Monica's Spanish sounds English at 10:47"*: the Spanish words get her Spanish voice.

---

## 4. One-shot prompts (actors + scenes + video in one go)

You can do everything in one message. Claude creates the actors first (and waits for your approval of their faces and voices), then writes the video.

**Everything for a reel:**
> Create a new actor, Lena: a German woman in her late twenties, tall, athletic, short platinum-blonde hair, pale blue eyes, a few freckles. She speaks German, and English with a light Berlin accent. Bright, clear voice, fast and confident. Personality: no-nonsense cycling coach, dry humour.
> Then make a 9:16 reel, about 20 seconds, of Lena on a Berlin street at dawn next to her road bike, wearing a black cycling jersey. She talks to camera in English about the one mistake beginners make with tyre pressure, with a single German word in the middle ("genau!") said natively. One continuous handheld shot. Captions in English. Also give me a 1080p version.

**A two-person scene with an establishing shot:**
> Create two actors. Aiko: a Japanese woman in her early thirties, petite, short black bob with a fringe, warm brown eyes; Japanese and English (English with a light Japanese accent); calm, precise, gently funny. Marcus: a Black British man in his late twenties, tall, broad, short twists, easy smile; English only, warm North London accent, enthusiastic.
> Then a 16:9 video, about 90 seconds, in a small Tokyo ramen bar at night (steam, a wooden counter, paper lanterns). Open on a wide shot of the two of them at the counter. Aiko teaches Marcus how to order: she says each phrase in Japanese with English captions, he has a go with his accent first, then gets it right. They talk to each other, not the camera, except for a short sign-off to camera at the end.

**A documentary series pilot:**
> Create a narrator: Dr. Elena Ruiz, a Mexican-American historian in her fifties, silver-streaked dark hair in a low bun, reading glasses, calm authoritative presence. English with a soft Mexican accent, measured and warm. She's filmed in a book-lined study with a green desk lamp.
> Then make a 10-minute 16:9 documentary narrated by Elena on the building of the Panama Canal: about a third on camera, the rest footage (jungle, steam shovels, locks under construction, sepia photographs, ships in the locks today). Archival, warm sepia-to-colour documentary look. Accurate dates, sources attributed. Render it overnight and run QA when it's done.

**Turning an existing actor into a new format:**
> Use Vale for a 9:16 short: one continuous shot of him in his studio, 30 seconds, giving the single strangest fact about the Apollo 11 mission.

---

## 5. Tips that make a big difference

- **Describe speaking, not smiling.** "A bright smile" makes the model hold a smile instead of lip-syncing. Claude writes speaking-focused actions ("lips and jaw forming every word, a calm, attentive expression") for you.
- **Never name what you don't want.** "Not smiling", "no captions", "nobody else in the shot": the model reacts to the words, not the "not". Describe what you *do* want; if something unwanted appears, re-render that line.
- **In conversations, say where people are**, not who: *"turns towards the right edge of the frame"* rather than *"looks at Clive off camera"*, which tends to draw a phantom Clive into the edge of the shot. Claude does this for you.
- **Props stay consistent if you restate them**: "the ham on the board in front of her, the oven open and empty" in each later line.
- **Talk during actions.** A character lifting something while speaking looks natural; a long silent beat followed by talking looks staged.
- **Concrete scenes** (place, light, furniture, objects) beat adjectives.
- **Shorter lines hold an action or a turn better** than long ones (the video model drifts back to "facing camera" over ~6 s).
- **Check facts** for health, finance, law and history, and **label videos as AI-generated** when you publish them.

## 6. Limits

- **Formats:** 16:9 (1280x704) and 9:16 (704x1280), with an optional upscaled 1080p copy.
- **Silent beats are silent:** there's no ambient background sound on non-speaking shots yet. Prefer some speech.
- **Languages:** built-in for the 23 Chatterbox Multilingual languages; others need a fine-tune (as Slovak has) or the Piper fallback.
- **Clip length:** each line is one clip of ≤ ~10 s; longer lines are split automatically.
- **QA** catches speech glitches and extra people, but not lip-sync failures or invented on-screen text: watch for those and name the timestamp.
- **Render time:** ~1.5-3 minutes per clip on a 16 GB GPU (a 15 s reel ≈ 6-8 min; a 20-minute dialogue ≈ 8 h overnight).
