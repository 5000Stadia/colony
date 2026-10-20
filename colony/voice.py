"""Replies read aloud in a natural voice made on this machine (Kokoro, open weights): nothing leaves it, nothing is
paid for. Optional: `colony voice --setup` installs it in colony's own folder, apart from colony's Python; without it,
Chat reads with the phone's own voice."""
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time

from . import board

MODEL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
# its English voices, as the person picks them in Chat, by accent and voice; Fable is shown as Arthur, since Fable
# is also a model's name
VOICES = (("af_heart", "Heart"), ("af_bella", "Bella"), ("af_nicole", "Nicole"), ("af_aoede", "Aoede"), ("af_kore", "Kore"),
          ("af_sarah", "Sarah"), ("af_nova", "Nova"), ("af_sky", "Sky"), ("af_alloy", "Alloy"), ("af_jessica", "Jessica"),
          ("af_river", "River"), ("am_michael", "Michael"), ("am_fenrir", "Fenrir"), ("am_puck", "Puck"), ("am_echo", "Echo"),
          ("am_eric", "Eric"), ("am_liam", "Liam"), ("am_onyx", "Onyx"), ("am_adam", "Adam"), ("bf_emma", "Emma"),
          ("bf_isabella", "Isabella"), ("bf_alice", "Alice"), ("bf_lily", "Lily"), ("bm_fable", "Arthur"), ("bm_george", "George"),
          ("bm_lewis", "Lewis"), ("bm_daniel", "Daniel"))
GROUPS = (("af_", "American, women"), ("am_", "American, men"), ("bf_", "British, women"), ("bm_", "British, men"))
DEFAULT = "am_echo"          # the voice out of the box; the person's own choice stands after it
IDLE = 600                 # seconds without a word before Kokoro is let go: its memory back, a moment's start next time
KEEP = 300                 # clips kept, newest first: a reply played again plays at once
_lock, _worker = threading.Lock(), {}


def folder():
    return board.home() / "voice"


def python():
    return folder() / "env" / "bin" / "python"


def ready():
    return python().exists() and all((folder() / f).exists() for f in FILES)


def setup(say=print):
    """Its own Python with kokoro-onnx, and the model (about 340 MB, once)."""
    import sys
    import urllib.request
    folder().mkdir(parents=True, exist_ok=True)
    if not python().exists():
        say("Making its own Python environment…")
        subprocess.run([sys.executable, "-m", "venv", str(folder() / "env")], check=True)
    say("Installing kokoro-onnx…")
    subprocess.run([str(python()), "-m", "pip", "install", "-q", "kokoro-onnx", "soundfile"], check=True)
    for name in FILES:
        if not (folder() / name).exists():
            say(f"Downloading {name}…")
            part = folder() / (name + ".part")
            urllib.request.urlretrieve(MODEL + name, part)
            part.rename(folder() / name)
    say("Ready: Chat reads replies in Kokoro's voice.")


def warm():
    """Kokoro loaded and waiting, so the first words come without its start."""
    if ready():
        with _lock:
            _start()


def _start():
    _worker["used"] = time.time()
    w = _worker.get("proc")
    if not w or w.poll() is not None:
        w = subprocess.Popen([str(python()), "-u", str(Path(__file__).with_name("voice_worker.py")), str(folder())],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        _worker["proc"] = w
        json.loads(w.stdout.readline() or "{}")       # loaded
        if not _worker.get("watch"):
            _worker["watch"] = threading.Thread(target=_let_go, daemon=True)
            _worker["watch"].start()
    return w


def _let_go():
    """Kokoro holds about 800 MB while loaded: after IDLE seconds unasked, it is stopped, and the next ask starts it."""
    while True:
        time.sleep(30)
        with _lock:
            w = _worker.get("proc")
            if w and w.poll() is None and time.time() - _worker.get("used", 0) > IDLE:
                w.terminate()
                try:
                    w.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    w.kill()
                _worker["proc"] = None


def options(chosen=None):
    """The voices as a picker's options, grouped by accent and voice."""
    from html import escape
    return "".join(f"<optgroup label='{escape(label)}'>" + "".join(
        f"<option value='{v}'{' selected' if v == chosen else ''}>{escape(n)}</option>" for v, n in VOICES if v.startswith(pre))
        + "</optgroup>" for pre, label in GROUPS)


def _ask(line):
    w = _start()
    w.stdin.write(json.dumps(line) + "\n")
    w.stdin.flush()
    return json.loads(w.stdout.readline() or '{"error": "the voice stopped"}')


def speak(text, voice=None):
    """An MP3 of the text in the voice: kept, so the same words again come at once. None where it can't be made."""
    voice = voice if voice in dict(VOICES) else DEFAULT
    text = " ".join(text.split())[:1200]
    if not text or not ready():
        return None
    cache = folder() / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / (hashlib.sha1(f"{voice}\n{text}".encode()).hexdigest() + ".mp3")
    if out.exists():
        return out
    with _lock:                                          # one at a time: the worker reads one line, answers one
        if not out.exists():
            got = _ask({"text": text, "voice": voice, "out": str(out)})
            if got.get("error"):
                return None
    old = list(cache.glob("*.wav")) + sorted(cache.glob("*.mp3"), key=lambda f: f.stat().st_mtime)[:-KEEP]
    for f in old:
        f.unlink(missing_ok=True)
    return out if out.exists() else None


def heard(ask, agent, made):
    """The last few asks for a clip, to see what a phone asks for: when, which bytes, whether it was made."""
    log = folder() / "asks.log"
    try:
        lines = log.read_text().splitlines()[-40:] if log.exists() else []
        lines.append(f"{board.now()} range={ask or '-'} made={made} {agent[:80]}")
        log.write_text("\n".join(lines) + "\n")
    except OSError:
        pass
