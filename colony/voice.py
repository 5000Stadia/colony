"""Replies read aloud in a natural voice made on this machine (Kokoro, open weights): nothing leaves it, nothing is
paid for. Optional: `colony voice --setup` installs it in colony's own folder, apart from colony's Python; without it,
Chat reads with the phone's own voice."""
import hashlib
import json
from pathlib import Path
import subprocess
import threading

from . import board

MODEL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
# a few of its voices, as the person picks them in Chat (American, then British)
VOICES = (("af_heart", "Heart"), ("af_bella", "Bella"), ("af_nicole", "Nicole"), ("am_michael", "Michael"),
          ("am_fenrir", "Fenrir"), ("bf_emma", "Emma"), ("bm_george", "George"), ("bm_fable", "Fable"))
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
    w = _worker.get("proc")
    if not w or w.poll() is not None:
        w = subprocess.Popen([str(python()), "-u", str(Path(__file__).with_name("voice_worker.py")), str(folder())],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        _worker["proc"] = w
        json.loads(w.stdout.readline() or "{}")       # loaded
    return w


def _ask(line):
    w = _start()
    w.stdin.write(json.dumps(line) + "\n")
    w.stdin.flush()
    return json.loads(w.stdout.readline() or '{"error": "the voice stopped"}')


def speak(text, voice="af_heart"):
    """A WAV of the text in the voice: kept, so the same words again come at once. None where it can't be made."""
    voice = voice if voice in dict(VOICES) else "af_heart"
    text = " ".join(text.split())[:1200]
    if not text or not ready():
        return None
    cache = folder() / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / (hashlib.sha1(f"{voice}\n{text}".encode()).hexdigest() + ".wav")
    if out.exists():
        return out
    with _lock:                                          # one at a time: the worker reads one line, answers one
        if not out.exists():
            got = _ask({"text": text, "voice": voice, "out": str(out)})
            if got.get("error"):
                return None
    old = sorted(cache.glob("*.wav"), key=lambda f: f.stat().st_mtime)[:-KEEP]
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
