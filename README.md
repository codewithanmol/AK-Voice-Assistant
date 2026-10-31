<div align="center">

# AK Voice Assistant

**A local-first, voice-controlled AI assistant for Windows.**

Wake word · natural conversation · full PC control · holographic gesture HUD ·
self-teaching workflows · Android companion app

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-2ea44f)
![Android](https://img.shields.io/badge/Android-companion-3DDC84?logo=android&logoColor=white)

[Features](#features) · [Quick Start](#quick-start) ·
[Voice Commands](#voice-commands) · [AK Mobile](#ak-mobile-android) ·
[Author](#author) · [License](#license)

</div>

---

**Created by Anmol Tyagi** — a free, local-first, voice-controlled AI assistant
with an optional external **task engine**. Speak commands, ask questions,
control your PC, and delegate coding tasks — all by voice.

## Features

- **Voice I/O** — Speech-to-text (Google Web API or offline Vosk) + Text-to-speech (offline pyttsx3)
- **Wake word** — Say "AK" to activate (or disable for continuous listening)
- **Barge-in** — Say "ruk", "bas", "chup ho jao", "shant", "rehne do", "awaaz band", "stop"… and AK cuts off mid-sentence. 100+ Hindi/Hinglish/English phrases, word-bounded so short words never misfire.
- **Brain** — Pluggable LLM backends: Ollama (local), Groq (free cloud), or built-in rule-based fallback
- **Built-in Skills**:
  - Time / Date
  - Web search & Wikipedia lookup
  - System stats (CPU, RAM, disk, battery)
  - System doctor — one-shot full health verdict
  - Launch apps (notepad, calculator, browser, VSCode, etc.)
  - Volume control (Windows)
  - Screenshot capture
  - News headlines
  - Note taking
  - File/folder browsing
  - Weather (live via wttr.in, offline cache fallback)
  - Git helper (status / log / diff / branch / commit on a local repo)
  - Expense tracker (SQLite ledger, daily & monthly totals)
  - Bookmarks (add / list / open / remove)
  - Text tools (JSON format+minify, URL encode/decode, case transforms, palindrome check, word count, extract emails/URLs/phones)
  - System commands (shutdown, restart, lock, sleep, logoff)
- **Task Engine (optional)** — Delegate coding, explanation, and scripting tasks to any local command-line engine you configure
- **Keyboard fallback** — If no microphone is available, type your commands

## Requirements

- Python 3.10+
- Windows (primary target; Linux/Mac partial support)

## Quick Start

```powershell
# 1. Install dependencies
pip install -r requirements.txt

# 2. If pyaudio fails to install, grab a wheel from:
#    https://github.com/intxcc/pyaudio_portaudio/releases
#    Or use: pip install pipwin && pipwin install pyaudio

# 3. Run AK
python main.py

# 4. (Optional) Point AK at a local task engine for coding tasks
#    Set "brain.engine_bin" in config.json or the AK_ENGINE_BIN env var.
#    Without one, AK uses its built-in local brain.
```

## Usage

### Interactive Mode
```powershell
python main.py
```
Say the wake word "AK" (if enabled), then speak a command.

### One-Shot Mode (process a single command)
```powershell
python main.py --once "what time is it"
python main.py --once "open notepad"
echo "check cpu usage" | python main.py --once
```

### Engine-Only Mode (optional external task engine)
```powershell
python main.py --engine "create a python script that monitors cpu usage"
```

### Disable Wake Word
```powershell
python main.py --no-wake
```

## Configuration

Edit `config.json` to customize:

| Key | Description |
|-----|-------------|
| `assistant_name` | Name AK responds to |
| `wake_word` | Wake word phrase |
| `require_wake_word` | Enable/disable wake word |
| `voice.stt_engine` | `"google"` (default, free web API), `"whisper"` (local), `"auto"`, `"vosk"`, or `"text"` |
| `voice.whisper_model` | `"tiny"`, `"base"`, `"small"`, `"medium"` — bigger = more accurate, slower |
| `voice.whisper_cpu_threads` | Cores Whisper may use (default 2; ~0% CPU while idle) |
| `voice.endpoint_silence_ms` | Trailing silence before a phrase is sent for STT (default 900; was 3000) |
| `voice.wake_speech_ms` | Speech needed before the wake phrase is tested (default 300) |
| `brain.backend` | `"auto"`, `"ollama"`, `"groq"`, or `"rule"` |
| `brain.ollama_model` | Ollama model to use (e.g. `"llama3.2"`) |
| `brain.groq_model` | Groq model (e.g. `"llama-3.3-70b-versatile"`) |
| `gesture_control.enabled` | Allow the holographic gesture control to be started by voice |
| `gesture_control.autostart` | Start the gesture control together with AK |
| `visualizer.enabled` | Open the sand/cosmos window with AK |
| `visualizer.theme` | `sand`, `neon`, `fire`, `forest`, `grape`, `ice` |
| `workflow.enabled` | Master switch for the self-teaching workflow feature |
| `workflow.auto_record` | Record every task AK performs (and build the playbook) |
| `workflow.confirm_before_replay` | Ask "haan?" before AK replays a learned workflow |
| `workflow.region` | `focus` (only the focused window) or `screen` (whole desktop) |
| `workflow.fps` | How often the screen is sampled while recording |

## Voice Commands

| You say... | AK does |
|------------|-------------|
| "What time is it?" | Tells the time |
| "Search for Python tutorials" | Opens browser search |
| "Wikipedia artificial intelligence" | Reads Wikipedia summary |
| "Open notepad" | Launches Notepad |
| "System status" | Reports CPU, RAM, disk, battery |
| "Volume 50%" | Sets volume to 50% |
| "Take a screenshot" | Saves screenshot to Desktop |
| "Note buy milk" | Saves a text note |
| "Delhi ka mausam" | Live weather (offline cache if net is down) |
| "System doctor" | One-shot CPU/RAM/disk/battery health verdict |
| "Git status in Documents\myproject" | Repo status, branch, dirty-file count |
| "Git log in Documents\myproject" | Last 5 commits |
| "Git commit in Documents\myproject 'fix bug'" | Stage all + commit |
| "Expense add 250 chai" | Records an expense |
| "Expense total today" / "expense total month" | Spending total |
| "Add bookmark example.com as ak site" | Saves a bookmark |
| "Open bookmark ak site" | Opens it in the browser |
| "Format json {...}" | Pretty-prints JSON |
| "Encode hello world" | URL percent-encoding |
| "Count words in this text ..." | Word/char/line/sentence counts |
| "Find all emails in this text ..." | Pulls emails out of text |
| "News headlines technology" | Fetches tech headlines |
| "Shutdown" / "Restart" / "Lock" | System command |
| "Engine write a script" | Delegates the task to the configured task engine |
| "ak on" / "ak chalao" / "ak off" | Starts / stops the gesture HUD process |
| "Show the torus knot" | Switches the holographic structure |
| "What am I wearing?" | Camera wardrobe + expression readout |
| "What am I holding?" | Held-object detection |
| "Next structure" / "explode it" | Scene control |
| "ak seekho <kaam>" | Records the screen and learns the task as a playbook |
| "record band" | Saves the learned playbook (`.md`) |
| "workflows dikha" | Lists the learned workflows |
| "ak kaam kar do <kaam>" | Replays the learned workflow for that task |
| "Exit" / "Goodbye" | Shuts down AK |

## AK HUD - Gesture Holographic Control

`AK/ak_ui.py` runs as a **separate process** (so AK's voice loop never
blocks) and turns the webcam into a gesture interface:

- **3D holographic structures** - icosahedron, torus knot, helix, reactor,
  prism, cube, sphere and more, rendered live in a click-through,
  always-on-top overlay (charcoal glass, steel cyan, maroon accent).
- **Hand tracking** with MediaPipe - point to aim, pinch to click, fist for
  depth, peace for the command wheel, thumb for volume, swipe to switch.
- **Vision intel panel** - live camera feed with annotations for your
  **expression**, your **clothes** and the **object in your hand**, plus a
  spoken narration line that is **typed out** on screen (always visible,
  never hidden by compact mode).
- **Entity strip** right above the picture: your name, `HUMAN`, `FACE`,
  `LOOKING`, `VOICE`, `OBJECT`, `WEARING`, `GLARE`, `HANDS` - only what is
  really detected, the rest stays grey.
- **Mirror flip** - the picture is flipped left/right by default (the webcam
  hands over a mirrored stream). `M` or the `MIRROR` button switches it back
  in one keystroke; the setting is remembered.
- **Free face follow** - the panel glides with your face in **any** direction
  (left, right, up, down), not only sideways.
- **Laptop control** - volume, brightness, media keys, screenshots, window
  switching, lock, app launch.

### Run it

```
# default launcher - voice assistant + holographic gesture control together
Start AK.cmd

# gesture control only (no voice assistant)
Start AK Gesture.cmd
```

Desktop shortcuts (one click, everything comes up in parallel in the
background so you can keep working):

| Shortcut | What it starts |
|----------|----------------|
| `AK` | voice assistant + sand/live visualizer (no gesture HUD) |
| `AK Gesture` | the holographic gesture control + camera panel on top of AK |
| `AK Voice` | the voice assistant only, one shot |

The console is minimised so AK never steals focus from your work.

`Start AK.cmd` runs `main.py` - the **voice assistant only**. The gesture
control is *on demand*: say *"ak chalao"* / *"ak on"* and it comes up
(HUD + camera panel), say *"ak band"* / *"ak off"* and it goes down. The
red **AK OFF** button on the HUD window bar shuts the whole visual stack
down (HUD + camera + visualizer) in one click.

The HUD is also voice-driven: *"ak on"* / *"ak chalao"*, *"ak off"*, *"show the
torus knot"*, *"what am i wearing"*, *"next structure"*, *"explode it"*,
*"volume up"*, *"take a screenshot"*, *"lock the system"*.

The wake word works both ways: say *"ak chalao"* while AK is asleep or
awake and the holographic gesture control starts - the wake word is not
swallowed, so the HUD never loses the command to a normal chat reply.

> In always-listening mode a bare *"exit"* is ignored (a cough or a fan used
> to kill AK). Say *"ak exit"* to shut AK down.

### Gestures

| Gesture | Action |
|---------|--------|
| Point (index finger) | Aim / rotate the structure |
| Pinch | Left click, hold to drag |
| Fist | Push / pull (zoom) |
| Open palm | Release |
| Peace | Command wheel (volume, brightness, media, screenshot, lock, apps) |
| Thumb up / down | Volume up / down |
| Swipe left / right | Next / previous structure |
| Swipe up / down | Open / hide the vision panel |

### Global hotkeys

| Hotkey | Action |
|--------|--------|
| `Ctrl+Alt+J` | Toggle the vision intel panel |
| `Ctrl+Alt+H` | Toggle the holographic scene |
| `Ctrl+Alt+K` | Quit AK HUD |

### Camera panel keys

| Key | Action |
|-----|--------|
| `S` | Snapshot (PNG in `Pictures\AK`) |
| `C` | Compact mode (drops the tiles and chips, narration stays) |
| `F` | Face follow on / off |
| `M` | Mirror flip the picture |
| `H` / `?` | Shortcut help |
| `Esc` | Hide the panel |

### Architecture

```
AK (main.py)  --cmd.json-->  AK HUD process  -->  camera / HUD / system
     ^                              |
     +--------- status.json -------+
```

Both files live in `<data_path>/ak/` (`D:\YOUR_DATA_PATH\ak\` by
default). `cmd.json` is a **list** so quick commands are never lost and
`status.json` is refreshed twice a second.

## Project Structure

```
ak-assistant/
├── main.py              # Entry point & main loop
├── config.json          # User configuration
├── requirements.txt     # Python dependencies
├── README.md            # This file
├── ak_server.py         # Phone bridge (chat / voice / WebRTC call)
├── Start AK.cmd         # Standalone AK HUD launcher
├── mobile/              # Android companion app (source, APK, guide)
└── AK/
    ├── __init__.py      # Package exports
    ├── config.py        # Config loader (deep merge defaults)
    ├── voice.py         # Speaker (TTS) & Listener (STT) + wake/interrupt phrases
    ├── brain.py         # LLM conversation (Ollama, Groq, rule)
    ├── skills.py        # Built-in skills (system, web, files)
    ├── powertools.py    # Weather, git, expenses, bookmarks, text tools, doctor
    ├── engine.py        # Optional external task-engine integration
    ├── gesture_core.py  # MediaPipe hand tracking + gesture engine
    ├── holo_render.py   # 3D holographic structures & renderer
    ├── holo_ui.py       # PyQt5 HUD overlay + vision intel popup
    ├── vision_analyzer.py # Wardrobe / expression / held-object analysis
    ├── sysctl.py        # Volume, brightness, media, windows, lock, apps
    ├── ak_ui.py         # AK HUD process (camera + HUD + system control)
    ├── ak.py            # Bridge: AK <-> HUD over cmd/status JSON
    ├── ak_commands.py   # Voice router for the AK HUD commands
    ├── screen_recorder.py # Screen recorder (frames + GIF + timeline)
    ├── visualizer_py.py  # The reactive window (sand cosmos + live stages)
    ├── vis_live.py       # Live stages (ORB) and the stage registry
    ├── vis_core.py       # Shared particle emitter + hsv helpers
    ├── face_portrait.py  # FACE stage - the photo as a particle portrait
    ├── face_motion.py    # WALK stage - the photo on a body, room after room
    ├── future_hud.py     # The futuristic HUD instrument layer
    ├── workflow_learn.py  # Playbook (.md) builder/parser, store, replayer
    └── workflow_coach.py  # Records a session and writes the playbook
```

## Self-teaching workflows (AK seeksho)

AK can now learn a task by *watching it happen* and then do the same task on
its own the next time - no re-teaching, no engine round-trip.

**How it works**

1. AK records the screen while the work happens (`AK/screen_recorder.py`):
   one PNG per step, an animated `recording.gif` replay and a `timeline.json`.
2. Every **window change is a step** (`Chrome - OBS Studio` ->
   `Windows Terminal` -> `VS Code`), and AK's own actions are logged with the
   exact command it ran - so the steps are real, not guessed.
3. At the end AK writes a **playbook** - a normal `.md` file you can read and
   edit yourself (`D:\YOUR_DATA_PATH\workflows\<name>.md`):

   ```markdown
   ---
   name: install-obs-studio
   task: install obs studio
   tags: install, obs, studio
   ---
   ## Steps
   1. **Search the package**
      - command: `winget search obs`
      - shot: [step-02.png](frames/step-02.png)
   ```

   The same file also carries a machine block (`ak-workflow-data`), so AK can
   read its own playbooks back.
4. Next time you ask for that work, AK **finds the playbook by meaning**
   (keyword + synonym matching, not exact string) and replays the very same
   steps on its own.

**Voice commands**

| You say... | AK does |
|------------|---------|
| "ak seekho install obs studio" | Starts recording - do the work by hand |
| "record band" / "seekhna band" | Stops, writes the `.md` playbook |
| "workflows dikha" | Lists every learned workflow |
| "ak kaam kar do install obs studio" | Finds the playbook and replays it |
| "workflow delete install-obs-studio" | Forgets that workflow |

**Safety rails**

- Only the *task* lanes record (`workflow.auto_record`), never a private chat.
- A failing step never aborts the run - AK reports which step broke.
- `workflow.confirm_before_replay` makes AK ask "haan?" before it replays.
- The screen is only written to `D:\YOUR_DATA_PATH\workflows\sessions\`.

## AK Mobile (Android)

`mobile/` contains **AK Mobile**, an Android companion app: chat, voice input
and a live WebRTC call, all powered by the laptop.

1. Start the bridge: `python ak_server.py` (prints the LAN address; a random
   password is generated on first run and saved to `security/server_secret.txt`).
2. Install `mobile/AK-Mobile.apk` on the phone.
3. In the app settings enter the laptop address and the server password.

Source, build steps and the full API are documented in
[`mobile/README.md`](mobile/README.md).

## Author

**Anmol Tyagi** — cybersecurity enthusiast and software developer.

His work spans cybersecurity, ethical hacking, OSINT, malware analysis, digital
forensics, threat intelligence, reverse engineering, AI automation engineering
and AI agent development. He builds practical, intelligent security solutions
and custom software from the ground up.

- **Focus** — cybersecurity · digital forensics · OSINT · AI/ML · software
  development · browser development · Windows customization · hardware/IoT ·
  VR · automation
- **Stack** — C, C++, Python, Java, JavaScript/TypeScript, PHP, Objective-C,
  React, Next.js, Node.js, React Native, PyQt5, Qt, MySQL, MongoDB, Docker,
  PowerShell
- **Projects** — ANcore Browser, AncoreSearch, DIY 3DOF PC-VR, StegVault,
  Dairy Ledger Pro, AK Voice Assistant
- **Tagline** — *"Decoding organizational structure and logic through a
  technical lens."*

## Hinglish routing (no engine round-trip)

Most skill handlers are written English-first, so spoken Hinglish used to fall
past all of them and land on engine — 15-35 s and a pegged CPU for a
question that had a local answer. `AK/hindi_routes.py` sits in front of the
handlers (called from `main.py:_handle_skill`) and understands the phrasings
actually spoken. Everything below stays on the machine.

| You say | AK does |
| --- | --- |
| `mai kaun hun`, `mera naam kya hai` | answers from the local profile |
| `mujhe kya yaad hai`, `meri yaad dikha` | lists remembered facts |
| `notes dikhao`, `note list karo` | local notes list |
| `5 km ko miles me`, `70 kg in pounds` | unit conversion |
| `500 ka 15 percent`, `15 percent of 500` | percentage |
| `12*8 karo`, `50 plus 20`, `100 mein se 30 minus` | maths, Hinglish operators |
| `4 digit pin banao`, `8 digit ka password banao` | numeric PIN / password |
| `reminder lagao 10 minute baad chai peene ko` | reminder (duration first) |
| `5 din baad exam yaad dilana` | reminder, filler stripped from the task |
| `bookmark add example.com as ak site` | bookmark (reversed word order too) |
| `bookmarks dikhao` | bookmark list |
| `mausam batao delhi ka` | weather, city extracted cleanly |
| `shayari sunao`, `ek fact batao`, `motivation do` | local smalltalk |
| `volume 50 percent`, `volume 30`, `volume up` | volume (`50%` / `50` / `+10`) |

Two guards keep this layer honest:

- `_VIZ_STOPWORDS` in `main.py` strips grammar glue (`ka`, `ki`, `se`, `hai`…)
  out of `_VIZ_SHAPES`. The visualizer runs *before* the skill handlers, so a
  single word like `ka` in the shape vocabulary used to steal any Hinglish
  sentence containing it.
- `_password()` returns `(kind, length)`, so `pin` routes to `pin_generate()`
  and never to the password generator — a PIN is numeric, a password is not.

## Drawing lanes (voice)

AK can draw, and it can also animate. Two different engines, one rule to
remember: **say an art word to get Paint, leave it out and you get the
visualizer.**

| You say | AK does |
| --- | --- |
| `sunset painting banao`, `logo banao`, `kutta ka sketch` | renders a PNG and opens it in MS Paint |
| `kutta banao`, `pizza dikhao`, `star banao` | visualizer lane, no Paint |
| `red apple painting` | colour artwork built around that colour |
| `apple painting` | colourless ink drawing (no colour mentioned = mono) |
| `snake game banao`, `website banao`, `apk banao` | build lane (code, not a drawing) |

The gate lives in `main.py`: `_PAINT_ART_RE` (art words), `_PAINT_ONLY_SUBJECTS`
(always-Paint subjects), `_PAINT_SKIP_HEADS` (never-Paint heads), with shape
detection in `_detect_shape_request` and code detection in
`_detect_build_request`.

### Paint generator

`AK/paintgen.py` works fully offline with Pillow: `detect_paint()` turns an
utterance into a spec, `render_paint()` paints it. Nothing is generated over
the network, no API key needed.

- Output: `D:\YOUR_DATA_PATH\Builds\paint\<subject>_<timestamp>.png`
- Opened in the real `mspaint.exe`; if Paint is missing the PNG is opened in
  the default viewer instead
- Built-in subjects: sun, sunset, night, mountains, ocean, forest, tree,
  flower, butterfly, heart, star, mandala, circuit, network, waves, cube, orb,
  sphere, poster, rainbow, bird, fish, leaf, cloud, tiles, spiral
- Silhouette subjects: dog, cat, lion, horse, house, car, plane, rocket, cup,
  cake, ice cream, pizza, burger, apple, book, phone, laptop, crown, skull,
  ghost, umbrella, candle, guitar, camera, key, lock, watch
- `logo` / `brand` render a round emblem with the brand initials
- Size hints: `bada` -> 1600x1000, `chhota` -> 720x720, default 1280x800
- Optional words: `text <words>` puts a caption on a poster or logo

### Visualizer lane

`AK/visualizer_py.py` supplies the shape vocabulary (430+ words) that
`_detect_shape_request` matches, with `_PHRASE_COLLAPSE` for multi-word
shapes (`ice cream`, `hot dog`) and `_SHAPE_BLOCK_WORDS` so questions like
`heart rate check karo` never turn into artwork.

#### Sand window - the reactive cosmos

The sand/stardust window opens automatically with AK (`visualizer.enabled`
in `config.json`) and reacts to everything you say:

- **Speech reaction** - the first frame of an utterance fires a shockwave
  through the dust; the tide surges, motes rise and meteors streak.
- **Phrase colour** - whatever was just spoken is hashed into a hue, so the
  whole field slowly takes on the colour of the words (the caption follows
  the same colour).
- **Animation switch button** - the glassy `ANIM` pill in the top-right of
  the window switches the animation (mouse click) or press `TAB`:
  `AUTO -> ORB -> FACE -> WALK -> TIDE -> VORTEX -> BLOOM -> RIVER -> STORM -> PRISM`
  (cross-faded over ~1.1 s, never a hard cut).
- `T` cycles the theme, `E` fires a universe shape, `ESC` closes the window.

#### Voice Reactive Orb

`AK/vis_live.py` holds one simple stage: `ORB`.

- A **round cloud of free particles** (1600 points) - every particle drifts
  on its own (a slow swirl of the whole cloud + a personal noise wobble)
  while staying inside a round volume, so it reads as a breathing sphere
  of light instead of a rigid shell.
- The cloud **breathes with the microphone**: it swells on every syllable,
  the swirl spins up, and a loud peak throws the particles outwards.
- The **colour comes from the voice itself** (`voice_hue`): the spectral
  centre of what is being said decides the hue - deep bass -> indigo, mid
  voice -> cyan/teal, bright vowel -> gold, sharp sibilant -> rose.
- The front of the cloud is lit and the back fades away, so it looks round
  and deep - and it is drawn additively into a fading layer, so it glows
  softly and leaves a short trail.
- `AUTO` (the default) shows the sand universe while nothing happens and
  switches to the orb the moment somebody talks.
- `L` cycles to the orb, `A`/`0` returns to `AUTO`.
- `AK_VIS_ANIM=ORB` starts the window on the orb (handy for testing).

#### The face portrait - AK out of particles

`AK/face_portrait.py` is the second live stage: `FACE`. Drop your own photo
at `AK/ak_portrait.png` (or point `AK_VIS_PORTRAIT` at any image) and it is
taken apart and rebuilt every frame.

- **The photo becomes a cloud** - 8000 particles are sampled from the image,
  weighted towards the edges and the features, so the hair, the eyes and the
  lips keep their shape. Every particle keeps its own little noise wobble and
  springs back to its place, so the picture is never quite still.
- **The light gives it depth** - the luminance of the photo is turned into a
  fake normal and lit from the top left, so the face has a real key light
  instead of a flat print. The eyes, the brows and the lips burn a little
  brighter than the skin.
- **The skeleton stays vector** - the face oval (a whisper), the lips, both
  eyes and the brows are kept as a thin glowing skeleton on top of the
  particles, drawn with premultiplied light so the additive blend behaves.
- **MediaPipe does the anatomy** - the face landmarker (model in
  `AK/face_landmarker.task`) finds the 478 landmarks and the blendshapes of
  the photo once; the portrait is then cached in `AK/.cache/portrait_*.npz`,
  so the window starts instantly next time.
- **No emotions, only life** - the face never wears an expression. The mouth
  opens while AK talks (with the microphone level), the eyes blink on their
  own rhythm and the head breathes slowly. Nothing else moves the face.
- **It follows the cursor** - the head drifts a little towards the mouse.
- `L` cycles between `ORB`, `FACE` and `WALK`, `AK_VIS_ANIM=FACE` starts there.

#### The walking stage - the photo on a body, room after room

`AK/face_motion.py` is the third live stage: `WALK`. The same photo is the
head of a body that is grown out of light - torso, arms, legs, feet - and the
world is a row of rooms that is longer than the window, so the camera follows
the figure and every room feels like a new screen.

- **The rooms come one after another** - `lounge` (he sits down on the sofa),
  `play` (he chases the ball and kicks it), `desk` (he sits at the PC and the
  keys flicker while he types) and `window` (he stops and looks out). Then he
  walks on to the next room and the cycle repeats.
- **The body is a real skeleton** - the walk cycle drives the hips, the knees,
  the ankles and the swinging arms; the dust comes up where a foot lands, and
  the shadow follows him.
- **The head is still the photo** - the particles of the picture sit on the
  neck, the landmarks are drawn as light on top of them. There is no outline
  around the face - the picture alone is the face.
- `AK_VIS_ANIM=WALK` starts the window on it.

#### The futuristic HUD layer

`AK/future_hud.py` draws a dark-glass instrument layer on top of the scene -
it is live right now in the visualizer window, no extra window needed.

The design language: **one accent hue driven by the voice**, everything else
is cool neutral glass; plates are *chamfered* (cut corners), never plain
rounded rectangles; every value moves on a spring, so nothing snaps.

- **Corner brackets** in all four corners that breathe with the voice.
- **Brand plate** (top-left): the `AK` wordmark, a `VOICE CORE` label and a
  state chip (`LISTENING` / `SPEAKING` / `THINKING`) with a pulsing dot and a
  spring-driven level line.
- **Animation plate** (top-right): the `ANIM` switch, chamfered like the
  rest, with a slow turning arc as the icon.
- **Telemetry plate** (top-right, below): live voice level in dB, the
  spectral centre in Hz/kHz and the FPS - big mono numerals, letterspaced
  micro labels.
- **Spectrum column** (left edge): 24 live bands growing out of a baseline,
  with peak-hold markers. It lives on the edge so the spoken caption never
  collides with it.
- **Ticker** (bottom-right): the sentence AK is saying, typed out character
  by character with a blinking caret and a travelling underline; a long line
  scrolls so the newest words stay visible.
- **Instrument ring** around the orb: rotating major/minor ticks, a radar
  sweep, the live waveform riding on the ring, a level arc that grows with
  the voice and a flash that pops outwards on a loud syllable.
- A whisper-thin perspective floor and a slow scan line drift through the
  glass, and the whole cluster reveals itself in a staggered, eased entrance.
- **Real frosted glass** - the plates blur what is behind them (a cheap
  down-sampled blur pass) instead of just tinting it.
- **Cinematic post pass** - a quarter-resolution bloom every other frame, a
  cached vignette and a fine film grain, all in one sweep over the frame.
- **Kinetic type** - the ticker types character by character, the numbers
  roll, the labels are letterspaced micro caps.
- **Adaptive quality** - the layer measures its own cost and drops its
  luxuries (grain, vignette, bloom, glass blur) before the animation is
  allowed to stutter. `AK_VIS_HUD_QUALITY=0..1` locks it.
- **Adaptive layout** - small windows get a tighter arrangement so nothing
  overlaps.
- Press `H` to hide/show the whole layer.

A tiny health file is written next to the state file while the window runs -
`ak_vis_fps.json` holds the live fps and the current HUD quality, handy when
the window feels slow.

## License

MIT — free to use, modify, and share.
