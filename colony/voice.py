"""Replies read aloud in a natural voice made on this machine (Kokoro, open weights): nothing leaves it, nothing is
paid for. Optional: `colony voice --setup` installs it in colony's own folder, apart from colony's Python; without it,
Chat reads with the phone's own voice."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading
import time
import unicodedata

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
# What a voice says when it is chosen, after its name: a sentence that ranges across the sounds of speech (a question,
# an exclamation, numbers, a time), so voices are compared by ear. Each voice's is made once and kept for good, apart
# from the clips kept for a while (samples/, about 64 KB each, 1.7 MB for them all), so trying voices never waits.
SAMPLE = ("Here's how I sound. The quick brown fox jumps over the lazy dog, and at 3:45 on a breezy Thursday, "
          "would you believe it, the jazz quartet played twelve encores!")
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


def sample(voice):
    """A voice's sample: its name, then SAMPLE."""
    return f"{dict(VOICES).get(voice, voice)}. {SAMPLE}"


def make_samples():
    """Every voice's sample, those not made yet, one after another: run in the background as the board starts, for the
    board it started with (its home changed under it, as a test's board ends in the process running the tests, it stops)."""
    mine = board.home()
    if ready():
        for v, _ in VOICES:
            if board.home() != mine:
                return
            speak(sample(v), v)


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

# Written forms the voice reads as written ("$100" as "dollar one hundred", "4h" as "four aitch", 🎉 as "party popper"),
# put as said. espeak reads plain numbers, ordinals, % and & well, so those are left to it; Kokoro's own pronouncer
# (misaki) gets money right but runs "10-20" together and spells README, so it is not used.
MONTHS = "January February March April May June July August September October November December".split()
MON = {m[:3].lower(): m for m in MONTHS} | {"sept": "September"}
UNITS = {"ms": "milliseconds", "kb": "kilobytes", "mb": "megabytes", "gb": "gigabytes", "tb": "terabytes",
         "kg": "kilograms", "km": "kilometers", "cm": "centimeters", "mph": "miles per hour"}
SPANS = {"d": "day", "h": "hour", "hr": "hour", "hrs": "hour", "m": "minute", "min": "minute", "mins": "minute",
         "s": "second", "sec": "second", "secs": "second"}
SCALES = {"k": "thousand", "m": "million", "mn": "million", "b": "billion", "bn": "billion", "t": "trillion"}
CASH = {"$": ("dollar", "cent"), "£": ("pound", "pence"), "€": ("euro", "cent")}
SAID = ((r"\be\.g\.", "for example"), (r"\bi\.e\.", "that is"), (r"\bvs\.?(?=\s)", "versus"), (r"\bw/(?=\s)", "with"),
        (r"\s*(?:->|→|=>)\s*", " to "), (r"~(?=\d)", "about "), (r"#(?=\d)", "number "), (r"\b24/7\b", "twenty four seven"))


def _day(n):
    n = int(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _date(y, m, d):
    return f"{MONTHS[int(m) - 1]} {_day(d)}, {y}" if 1 <= int(m) <= 12 and 1 <= int(d) <= 31 else None


def _cash(sign, whole, cents):
    one, small = CASH[sign]
    n, c = int(whole.replace(",", "")), int(cents or 0)
    smalls = f"{c} {small if small == 'pence' or c == 1 else small + 's'}"
    if not n and c:
        return smalls
    return f"{whole} {one}{'' if n == 1 else 's'}" + (f" and {smalls}" if c else "")


def _number(m):
    parts = m.group(0).split(".")
    return f"{parts[0]} point {' '.join(parts[1])}" if len(parts) == 2 else " point ".join(parts)


def spoken(text):
    """The text as it is said aloud: money, dates, times, units and symbols in words, emoji left out."""
    for pat, said in SAID:
        text = re.sub(pat, said, text, flags=re.I)
    text = re.sub(r"https?://(?:www\.)?([\w-]+(?:\.[\w-]+)+)\S*?(?=[.,;:!?)]*(?:\s|$))", r"\1", text)
    # money: a range, an amount with its scale ($2.5M), then dollars and cents
    text = re.sub(r"([$£€])(\d[\d,]*(?:\.\d+)?)\s?[-–]\s?\1?(\d[\d,]*(?:\.\d+)?)\b(?![.,]?\d)",
                  lambda m: f"{m.group(2)} to {m.group(3)} {CASH[m.group(1)][0]}s", text)
    text = re.sub(r"([$£€])(\d[\d,]*(?:\.\d+)?)\s?(k|m|mn|b|bn|t|thousand|million|billion|trillion)\b",
                  lambda m: f"{m.group(2)} {SCALES.get(m.group(3).lower(), m.group(3).lower())} {CASH[m.group(1)][0]}s",
                  text, flags=re.I)
    text = re.sub(r"([$£€])(\d[\d,]*)(?:\.(\d\d))?\b", lambda m: _cash(*m.groups()), text)
    text = re.sub(r"(\d)\s?¢", r"\1 cents", text)
    # dates and times
    text = re.sub(r"\b(\d{4})-(\d\d)-(\d\d)\b", lambda m: _date(*m.groups()) or m.group(0), text)
    text = re.sub(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", lambda m: _date(m.group(3), m.group(1), m.group(2)) or m.group(0), text)
    text = re.sub(r"\b(jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\.?\s(\d{1,2})\b(?!:)",
                  lambda m: f"{MON[m.group(1).lower()]} {_day(m.group(2))}", text, flags=re.I)
    text = re.sub(r"\b(\d{1,2}):(\d\d)\b(?!:)", lambda m: m.group(1) + (
        "" if m.group(2) == "00" and re.match(r"\s?[ap]\.?m\b", text[m.end():], re.I) else " o'clock" if m.group(2) == "00"
        else f" oh {m.group(2)[1]}" if m.group(2)[0] == "0" else f" {m.group(2)}"), text)
    text = re.sub(r"\b([ap])\.m\.", lambda m: m.group(1).upper() + "M", text, flags=re.I)
    # amounts: 3x, 10-20, 4/5, per month, durations (4h 12m), units (16GB), degrees
    text = re.sub(r"\b(\d+(?:\.\d+)?)x\b", r"\1 times", text)
    text = re.sub(r"(?<![\d.-])(\d+(?:\.\d+)?)\s?[-–]\s?(\d+(?:\.\d+)?)(?![\d-])", r"\1 to \2", text)
    text = re.sub(r"\b(\d+)/(\d+)\b", r"\1 out of \2", text)
    text = re.sub(r"(?<=\w)\s?/\s?(month|mo|year|yr|week|wk|day|hour|hr)\b", lambda m: " per " + {
        "mo": "month", "yr": "year", "wk": "week", "hr": "hour"}.get(m.group(1), m.group(1)), text)
    text = re.sub(r"(?<!the )(?<!')\b(\d+)(?:\s?(ms|hrs|hr|mins|min|secs|sec)|(h|m|s|d))\b", lambda m: (
        f"{m.group(1)} {UNITS['ms']}" if m.group(2) == "ms"
        else f"{m.group(1)} {SPANS[m.group(2) or m.group(3)]}{'' if m.group(1) == '1' else 's'}"), text)
    text = re.sub(r"\b(\d+(?:\.\d+)?)\s?(kb|mb|gb|tb|kg|km|cm|mph)\b", lambda m: f"{m.group(1)} {UNITS[m.group(2).lower()]}",
                  text, flags=re.I)
    text = re.sub(r"°\s?([FC])\b", lambda m: " degrees " + ("Fahrenheit" if m.group(1) == "F" else "Celsius"), text)
    text = text.replace("°", " degrees")
    # decimals and versions: a point said, not a full stop's pause (3.14 is three point one four; 2.1.3 two point one point three)
    text = re.sub(r"(?<![\d.])\d+(?:\.\d+)+(?![\d.]*\d)", _number, text)
    # a name with a dot in it (board.py, example.com) is said with the dot
    text = re.sub(r"\b([A-Za-z][\w-]*)\.([a-z]{2,4})\b", r"\1 dot \2", text)
    # emoji and pictographs are seen, not said
    text = "".join(c for c in text if unicodedata.category(c) != "So" and c not in "‍️")
    return re.sub(r" ([.,!?;:])", r"\1", re.sub(r"[ \t]+", " ", text)).strip()


def speak(text, voice=None):
    """An MP3 of the text in the voice: kept, so the same words again come at once. None where it can't be made."""
    voice = voice if voice in dict(VOICES) else DEFAULT
    text = spoken(" ".join(text.split()))[:1200]
    if not text or not ready():
        return None
    cache = folder() / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    kept = folder() / "samples" if text == spoken(" ".join(sample(voice).split()))[:1200] else cache
    kept.mkdir(parents=True, exist_ok=True)
    out = kept / (hashlib.sha1(f"{voice}\n{text}\nlead\n".encode()).hexdigest() + ".mp3")
    if out.exists():
        return out
    with _lock:                                          # one at a time: the worker reads one line, answers one
        if not out.exists():
            got = _ask({"text": text, "voice": voice, "out": str(out)})
            if got.get("error"):
                return None
    def made(f):                                         # another ask may have trimmed it a moment ago
        try:
            return f.stat().st_mtime
        except OSError:
            return 0
    old = list(cache.glob("*.wav")) + sorted(cache.glob("*.mp3"), key=made)[:-KEEP]
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
