"""The board's lab: pages for trying something on the person's own phone, on the board itself (same address, same
Safari or Home Screen app), apart from the board's own pages.

/lab/lockplay compares ways to keep audio playing on a locked iPhone. Locked, iOS keeps running a page's JavaScript
only while its audio plays, so a page that sets the next clip going once the last has ended may find itself stopped
before it can. Each way below plays the same twelve short clips; the person taps one, locks the phone, and the page
reports to the board every clip start, end, error, stall and change of visibility, so the results table shows how far
each way got with the screen off, without the person having to describe it."""
import base64
import datetime
import hashlib
import io
import json
import re
import shutil
import threading
import time
import wave

from . import board

COUNT = 12                # clips per way
LEAD = 20                 # seconds the server stream keeps ahead of playback, as internet radio does
GAP = 2                   # the ramp: seconds the board holds back clip 2, and how much longer each next one waits
UNLOCK = 1.0              # a "hidden" start this close before the page shows again was the unlock starting it
LATE = 3.0                # a clip starting this long after its gap ended was held up by the phone, not the board
LOG_MOST = 256 * 1024     # one batch of report rows, at most
WAYS = (
    ("A", "Today's way", "One audio element; when a clip ends, the next is set on it and played (as colony's player does now)."),
    ("B", "Loop trick", "Each clip loops, so the phone never sees it end; when it jumps back to the start, the next is set."),
    ("C", "Media source", "One continuous stream built in the page: each clip's sound is added while the one before plays."),
    ("D", "Server stream", "One endless download from the board, every clip back to back, like internet radio: no code between clips."),
    ("E", "Loop trick, playback session", "The loop trick, with Safari told this page plays media (navigator.audioSession)."),
    ("F", "Silent bridge", "Today's way, with colony's looping silence playing while the next clip is fetched and set."),
    ("G", "Silent loop underneath", "Today's way, with a second element looping colony's silence the whole run, so sound never stops between clips."),
    ("H", "Stream fed silence", "One continuous stream built in the page, as C, but each clip is asked for only when the last has played; while it waits, silence is fed in, so the sound never stops."),
)
NAMES = {k: name for k, name, _ in WAYS}
# what each way is expected to cost the phone, so the person can weigh the ways that keep playing
COST = {
    "A": "One element, one download per clip; the page's code runs only at each clip's end.",
    "B": "One element, one download per clip; the page's code runs several times a second to watch for the loop.",
    "C": "One element; the page fetches each clip and appends its sound from JavaScript, and watches the time.",
    "D": "One element and one connection held open the whole run while the board streams; no code between clips.",
    "E": "As B; only Safari's audio session setting differs.",
    "F": "As A, plus a short silent loop decoded during each fetch and a second download of each clip (the fetch).",
    "G": "As A, plus a second element decoding a silent loop continuously for the whole run.",
    "H": "One element, as C, fed a little silence from JavaScript during each wait: a tiny download once, appends every half second while waiting.",
}
MEASURES = ("bytes", "requests", "calls", "hidden_calls", "playing", "silence")


def gap(n):
    """How long the ramp holds clip n back: nothing before the first, then GAP, 2 GAP, ... as when colony's voice is
    still making the next clip."""
    return GAP * (n - 1) if n > 1 else 0
_making = {"thread": None, "error": ""}
_writing = threading.Lock()
_frames = {}              # an MP3's sound without its tags, and how long it plays, by path and change time
_held = set()             # each run's clips already held back once: Safari asks for one clip in several requests


def folder():
    return board.home() / "lab" / "lockplay"


def text(key, n):
    """What clip n of a way says: its way and number first, so a clip heard locked says where the run is."""
    last = n == COUNT
    return (f"{NAMES[key]}, clip {n} of {COUNT}. This is one of twelve clips in a row, played to see whether the next "
            "one starts by itself while the screen is locked. "
            + ("That was the last one. Unlock the phone and read the table." if last else
               f"If you can hear this with the screen off, this way is still going, and clip {n + 1} comes next."))


def clip(key, n, base=None):
    words = text(key, n)
    return (base or folder()) / "clips" / f"{key}-{n:02d}-{hashlib.sha1(words.encode()).hexdigest()[:8]}.mp3"


def ready(key):
    return sum(clip(key, n).exists() for n in range(1, COUNT + 1))


def make():
    """Start making the clips in the background, once: colony's own voice, one clip at a time, so the person's
    read-aloud waits at most one clip's making."""
    if all(ready(k) == COUNT for k in NAMES):
        return
    with _writing:
        if _making["thread"] and _making["thread"].is_alive():
            return
        _making["error"] = ""
        _making["thread"] = threading.Thread(target=_make, daemon=True)
        _making["thread"].start()


def _make():
    from . import voice
    if not voice.ready():
        _making["error"] = "Colony's voice isn't set up on this machine (colony voice --setup), so no clips can be made."
        return
    base = folder()                                # the home it started in, kept to the end of the run
    for key in NAMES:
        for n in range(1, COUNT + 1):
            out = clip(key, n, base)
            if out.exists():
                continue
            got = voice.speak(text(key, n))
            if not got:
                _making["error"] = f"The voice couldn't make {NAMES[key]}, clip {n}."
                return
            out.parent.mkdir(parents=True, exist_ok=True)
            part = out.with_suffix(".part")
            shutil.copyfile(got, part)             # the voice's own cache trims old clips: these are kept here
            part.replace(out)


def status():
    made = {k: ready(k) for k in NAMES}
    return {"made": sum(made.values()), "total": COUNT * len(NAMES), "ready": made, "error": _making["error"],
            "durations": {k: [round(sound(clip(k, n))[1], 3) for n in range(1, COUNT + 1)]
                          for k in NAMES if made[k] == COUNT}}


# MPEG audio, Layer III: enough of its frame header to find each frame, its length and how long it plays
RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
BITRATES = {3: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
            2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)}


def frame(data, at):
    """The length and play time of the Layer III frame starting at `at`, or None where none starts there."""
    if at + 4 > len(data) or data[at] != 0xFF or data[at + 1] & 0xE0 != 0xE0:
        return None
    version, layer = (data[at + 1] >> 3) & 3, (data[at + 1] >> 1) & 3
    rate, pick, pad = data[at + 2] >> 4, (data[at + 2] >> 2) & 3, (data[at + 2] >> 1) & 1
    if version == 1 or layer != 1 or rate in (0, 15) or pick == 3:
        return None
    hz, kbps = RATES[version][pick], BITRATES[3 if version == 3 else 2][rate]
    size = (144000 if version == 3 else 72000) * kbps // hz + pad
    return size, (1152 if version == 3 else 576) / hz


def strip(data):
    """An MP3's frames back to back, its tags (ID3, APE) and its encoder's summary frame (Xing, Info) left out, so
    clips joined end to end play as one; and how many seconds they play."""
    at = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        at = 10 + ((data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | data[9] & 0x7F)
        at += 10 if data[5] & 0x10 else 0
    out, seconds, first = [], 0.0, True
    while at < len(data):
        got = frame(data, at)
        # a frame is believed only where the next starts right after it (or the file ends): sound data can look like
        # a header by chance
        if got and (at + got[0] >= len(data) or frame(data, at + got[0]) or data[at + got[0]:at + got[0] + 3] == b"TAG"
                    or data[at + got[0]:at + got[0] + 8] == b"APETAGEX"):
            body = data[at:at + got[0]]
            if not (first and (b"Xing" in body[:64] or b"Info" in body[:64] or b"VBRI" in body[:64])):
                out.append(body)
                seconds += got[1]
            first = False
            at += got[0]
        elif data[at:at + 3] == b"TAG" or data[at:at + 8] == b"APETAGEX":
            break
        else:
            at += 1
    return b"".join(out), seconds


def sound(path):
    """`strip` of a kept clip, remembered while the file is unchanged."""
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return b"", 0.0
    kept = _frames.get(path)
    if not kept or kept[0] != stamp:
        kept = _frames[path] = (stamp, *strip(path.read_bytes()))
    return kept[1], kept[2]


def silence():
    """A fifth of a second of silence as a WAV: byte for byte the one colony's player bridges with."""
    raw = io.BytesIO()
    with wave.open(raw, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 1600)
    return "data:audio/wav;base64," + base64.b64encode(raw.getvalue()).decode()


def quiet(path, seconds=0.5):
    """Silent MP3 frames in a clip's own format (MPEG version, sample rate, channels), at the lowest bitrate: each
    frame's side information and sound all zeros, which decodes to silence. H feeds them while a clip is late."""
    head = sound(path)[0][:4]
    if not frame(head, 0):
        return b""
    head = bytes([0xFF, head[1] | 1, 1 << 4 | head[2] & 0x0C, head[3] & 0xC0])   # no CRC, the lowest bitrate, no padding
    size, plays = frame(head, 0)
    return (head + bytes(size - 4)) * max(1, round(seconds / plays))


def write(row):
    row = dict(row, at=board.now())
    with _writing:
        folder().mkdir(parents=True, exist_ok=True)
        with open(folder() / "log.jsonl", "a") as f:
            f.write(json.dumps(row) + "\n")


def take(rows):
    """Keep a batch of the page's report rows: only the fields it reports, each short."""
    kept = 0
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        row = {k: str(r.get(k, ""))[:200] for k in ("approach", "run", "event", "t", "vis", "why", "session", "main", "silent")}
        for k in ("clip", "gap") + MEASURES:
            try:                                   # whole numbers, but for the seconds of silence fed
                row[k] = round(float(r.get(k) or 0), 1) if k == "silence" else int(r.get(k) or 0)
            except (TypeError, ValueError):
                row[k] = 0
        write(dict(row, source="page"))
        kept += 1
    return kept


def rows():
    try:
        lines = (folder() / "log.jsonl").read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _seconds(when):
    """A report row's time as seconds, or None when it can't be read."""
    try:
        return datetime.datetime.fromisoformat(str(when).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def summary():
    """Each run, newest first: how far it got, how much of that with the screen off, and when it stopped."""
    runs = {}
    for r in rows():
        run = r.get("run")
        if not run:
            continue
        s = runs.setdefault(run, {"run": run, "approach": r.get("approach", ""), "began": "", "reached": 0,
                                  "hidden": 0, "last": "", "finished": False, "sent": 0, "errors": 0, "stalls": 0,
                                  "silent_paused": False, "server_bytes": 0, "server_requests": 0, "held": None,
                                  "ramp": "-ramp-" in run, "longest_gap_hidden": None,
                                  **{k: 0 for k in MEASURES}, "_starts": [], "_ends": {}, "_shown": [],
                                  "_order": len(runs)})
        when = r.get("t") or r.get("at", "")
        if r.get("source") == "server":
            if r.get("event") == "sent":
                s["sent"] = max(s["sent"], r.get("clip", 0))
            if r.get("event") in ("served", "stream asked"):
                s["server_requests"] += 1
            s["server_bytes"] += r.get("bytes") or 0
            if r.get("held") is not None:
                s["held"] = r["held"]
            continue
        for k in MEASURES:
            s[k] = max(s[k], r.get(k) or 0)
        s["began"] = s["began"] or when
        event = r.get("event")
        if event == "silent paused" or r.get("silent") == "paused":   # G: the phone let only one element play
            s["silent_paused"] = True
        if event == "start":
            s["reached"] = max(s["reached"], r.get("clip", 0))
            s["_starts"].append((r.get("clip", 0), _seconds(when), r.get("vis"), r.get("gap") or 0))
            s["last"] = max(s["last"], when)
        elif event == "end":
            s["_ends"][r.get("clip", 0)] = _seconds(when)
            s["last"] = max(s["last"], when)
        elif event == "visibility" and r.get("vis") == "visible":
            s["_shown"].append(_seconds(when))
        elif event == "finished":
            s["finished"], s["last"] = True, max(s["last"], when)
        elif event == "error":
            s["errors"] += 1
        elif event in ("stall", "waiting"):
            s["stalls"] += 1
    out = sorted(runs.values(), key=lambda s: s["_order"], reverse=True)
    for s in out:
        hidden, ends, shown = set(), s.pop("_ends"), [t for t in s.pop("_shown") if t is not None]
        for n, t, vis, wait in s.pop("_starts"):
            if vis != "hidden" or (t is not None and any(0 <= v - t <= UNLOCK for v in shown)):
                continue                    # with the screen on, or started by the unlock itself
            hidden.add(n)
            before = ends.get(n - 1)
            if s["ramp"] and n > 1 and (t is None or before is None or t - before <= wait + LATE):
                s["longest_gap_hidden"] = max(s["longest_gap_hidden"] or 0, wait)   # it started, hidden, after its gap
        s["hidden"] = len(hidden)
        s.pop("_order")
        s["name"] = NAMES.get(s["approach"], s["approach"])
    return out[:40]


def stream(handler, run, ramp=False):
    """Every clip's sound back to back in one chunked response, paced to stay LEAD seconds ahead of playback as
    internet radio is, so each clip sent (written to the log with its time) shows the phone still taking it. With the
    ramp, each clip is sent only once it would be ready, its gap after the last one's end: the stream goes quiet, as
    it would for a clip still being made."""
    clips = [sound(clip("D", n)) for n in range(1, COUNT + 1)]
    if not all(c[0] for c in clips):
        return send(handler, 503, b"The clips aren't made yet.", "text/plain; charset=utf-8")
    handler.protocol_version = "HTTP/1.1"            # chunked needs it; the connection closes when the stream ends
    handler.close_connection = True
    handler.send_response(200)
    handler.send_header("Content-Type", "audio/mpeg")
    handler.send_header("Transfer-Encoding", "chunked")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    began, ahead, sent, gaps = time.monotonic(), 0.0, 0, 0
    write({"source": "server", "approach": "D", "run": run, "event": "stream asked", "clip": 0,
           "why": handler.headers.get("Range", "")})
    n = 0
    try:
        for n, (body, seconds) in enumerate(clips, 1):
            gaps += gap(n) if ramp else 0
            due = ahead + gaps if ramp else ahead - LEAD
            while time.monotonic() - began < due:
                time.sleep(min(0.5, due - (time.monotonic() - began)))
            for at in range(0, len(body), 16384):
                part = body[at:at + 16384]
                handler.wfile.write(b"%X\r\n%s\r\n" % (len(part), part))
                sent += len(part)
            handler.wfile.flush()
            write({"source": "server", "approach": "D", "run": run, "event": "sent", "clip": n,
                   "gap": gap(n) if ramp else 0})
            ahead += seconds
        handler.wfile.write(b"0\r\n\r\n")
        handler.wfile.flush()
        event = "sent all"
    except (BrokenPipeError, ConnectionResetError, TimeoutError):
        event = "phone let go"
    # the stream's whole cost to the board: its bytes, and how long it held the connection open
    write({"source": "server", "approach": "D", "run": run, "event": event, "clip": n, "bytes": sent,
           "held": round(time.monotonic() - began, 1)})


def send(handler, code, body, kind, extra=()):
    handler.send_response(code)
    handler.send_header("Content-Type", kind)
    handler.send_header("Content-Length", str(len(body)))
    for k, v in extra or (("Cache-Control", "no-store"),):
        handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(body)


def send_audio(handler, body):
    """Audio answered in byte ranges, as Safari asks for it."""
    ask = handler.headers.get("Range", "")
    got = re.match(r"bytes=(\d*)-(\d*)$", ask.strip())
    start, end = 0, len(body) - 1
    if got and (got.group(1) or got.group(2)):
        if got.group(1):
            start, end = int(got.group(1)), min(int(got.group(2) or end), end)
        else:
            start = max(0, len(body) - int(got.group(2)))
    handler.send_response(206 if got else 200)
    handler.send_header("Content-Type", "audio/mpeg")
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Cache-Control", "private, max-age=86400")
    if got:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
    handler.send_header("Content-Length", str(max(end - start + 1, 0)))
    handler.end_headers()
    handler.wfile.write(body[start:end + 1])
    return len(body[start:end + 1])


SOUND = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width, initial-scale=1">
<title>Sound check</title>
<style>body { font: 18px/1.45 system-ui, sans-serif; margin: 24px 16px; max-width: 520px; background: #14161a; color: #e8e8ea }
button { display: block; width: 100%; font: inherit; font-weight: 600; padding: 18px; margin: 14px 0 6px; border-radius: 12px; border: 0;
  background: #3b6fd4; color: #fff } p.r { margin: 0 0 8px; min-height: 1.4em; color: #b9c0cc } h1 { font-size: 22px }</style>
<h1>Sound check</h1>
<p>None of colony's player is on this page. Tap each button once and listen: a tone of two seconds.</p>
<button id=a>1. One tone</button><p class=r id=ra></p>
<button id=b>2. A tone over a silent sound</button><p class=r id=rb></p>
<button id=c>3. A tone, started a moment after the tap</button><p class=r id=rc></p>
<script>
(() => {
  const wav = (secs, hz) => { const rate = 8000, n = Math.round(secs * rate), b = new DataView(new ArrayBuffer(44 + n * 2));
    const put = (at, text) => [...text].forEach((ch, i) => b.setUint8(at + i, ch.charCodeAt(0)));
    put(0, 'RIFF'); b.setUint32(4, 36 + n * 2, true); put(8, 'WAVEfmt '); b.setUint32(16, 16, true); b.setUint16(20, 1, true);
    b.setUint16(22, 1, true); b.setUint32(24, rate, true); b.setUint32(28, rate * 2, true); b.setUint16(32, 2, true); b.setUint16(34, 16, true);
    put(36, 'data'); b.setUint32(40, n * 2, true);
    for (let i = 0; i < n; i++) b.setInt16(44 + i * 2, hz ? Math.round(9000 * Math.sin(2 * Math.PI * hz * i / rate)) : 0, true);
    return URL.createObjectURL(new Blob([b.buffer], {type: 'audio/wav'})); };
  const tone = wav(2, 440), silent = wav(0.4, 0);
  const send = (m) => fetch('/chat/measure', {method: 'POST', body: new URLSearchParams({m: JSON.stringify(m)})}).catch(() => {});
  const watch = (el, name, events, t0) => ['play', 'playing', 'pause', 'ended', 'error', 'waiting', 'stalled', 'suspend'].forEach((t) =>
    el.addEventListener(t, () => events.push([name, t, Date.now() - t0, Math.round(el.currentTime * 1000)])));
  const run = (id, how) => document.getElementById(id).addEventListener('click', () => {
    const out = document.getElementById('r' + id), events = [], t0 = Date.now(), a = new Audio(tone);
    watch(a, 'tone', events, t0); out.textContent = 'Listening…';
    const say = (what) => (p) => p.then(() => events.push([what, 'play() ok', Date.now() - t0, 0]),
      (err) => events.push([what, 'play() refused: ' + (err && err.name), Date.now() - t0, 0]));
    if (how === 'over') { const h = new Audio(silent); h.loop = true; watch(h, 'silent', events, t0); say('silent')(h.play()); setTimeout(() => h.pause(), 3200); }
    if (how === 'late') setTimeout(() => say('tone')(a.play()), 400); else say('tone')(a.play());
    setTimeout(() => { const played = Math.round(a.currentTime * 1000), whole = a.ended || played > 1800;
      out.textContent = whole ? 'The page played the whole tone.' : 'Stopped after ' + played + ' ms of 2000.';
      send({when: 'bare-sound', test: how, played, ended: a.ended, paused: a.paused, events: events.slice(0, 14)}); }, 3400); });
  run('a', 'one'); run('b', 'over'); run('c', 'late');
})();
</script>"""


def get(handler, path, q):
    one = lambda k: (q.get(k) or [""])[0]
    if path == "/lab/sound":                             # a bare check that a phone plays a page's sound at all
        return send(handler, 200, SOUND.encode(), "text/html; charset=utf-8")
    if path == "/lab/lockplay":
        make()
        return send(handler, 200, page().encode(), "text/html; charset=utf-8")
    if path == "/lab/lockplay/status":
        make()
        return send(handler, 200, json.dumps(status()).encode(), "application/json")
    if path == "/lab/lockplay/silence":
        body = quiet(clip("H", 1)) if clip("H", 1).exists() else b""
        if not body:
            return send(handler, 404, b"no clips to match yet", "text/plain; charset=utf-8")
        return send(handler, 200, body, "audio/mpeg", (("Cache-Control", "private, max-age=86400"),))
    if path == "/lab/lockplay/results":
        return send(handler, 200, json.dumps(summary()).encode(), "application/json")
    if path == "/lab/lockplay/clip":
        key, n = one("a"), one("n")
        if key not in NAMES or not n.isdigit() or not 1 <= int(n) <= COUNT or not clip(key, int(n)).exists():
            return send(handler, 404, b"no such clip", "text/plain; charset=utf-8")
        f = clip(key, int(n))
        body = sound(f)[0] if one("bare") else f.read_bytes()
        wait = int(one("wait")) if one("wait").isdigit() else 0
        if wait:                                   # the ramp: the clip is late, as when the voice is still making it
            first = (one("run"), key, n)
            with _writing:
                late = first not in _held or not one("run")
                _held.add(first)
            if late:
                time.sleep(min(wait, 60))
        sent = send_audio(handler, body)
        if re.fullmatch(r"[A-Za-z0-9-]{1,60}", one("run")):        # a run's clip requests, counted for its cost
            write({"source": "server", "approach": key, "run": one("run"), "event": "served", "clip": int(n), "bytes": sent})
        return
    if path == "/lab/lockplay/stream":
        run = one("run")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,60}", run):
            return send(handler, 400, b"a run, please", "text/plain; charset=utf-8")
        return stream(handler, run, ramp=bool(one("ramp")))
    return send(handler, 404, b"nothing here", "text/plain; charset=utf-8")


def post(handler, path):
    if path != "/lab/lockplay/log":
        return send(handler, 404, b"nothing here", "text/plain; charset=utf-8")
    size = int(handler.headers.get("Content-Length") or 0)
    if size > LOG_MOST:
        return send(handler, 413, b"too much at once", "text/plain; charset=utf-8")
    try:
        got = json.loads(handler.rfile.read(size).decode("utf-8", "replace") or "[]")
    except ValueError:
        return send(handler, 400, b"not JSON", "text/plain; charset=utf-8")
    return send(handler, 200, json.dumps({"kept": take(got)}).encode(), "application/json")


def page():
    data = {"ways": [{"key": k, "name": n, "how": h} for k, n, h in WAYS], "count": COUNT, "silent": silence()}
    ways = "".join(f"<div class='way'><button data-way='{k}' disabled>{k}. {board.e(n)}</button>"
                   f"<span class='how'>{board.e(h)} <span class='made' data-made='{k}'></span>"
                   f"<br><span class='cost'>Cost: {board.e(COST[k])}</span></span></div>"
                   for k, n, h in WAYS)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
        "<title>Lock and play</title><meta name='apple-mobile-web-app-title' content='Colony'>"
        f"<style>{STYLE}</style></head><body><main>"
        "<h1>Lock and play</h1>"
        "<p>Tap one way below, lock the phone, and wait two minutes.<br>"
        "Then unlock and read the table at the bottom: it shows how far each way got with the screen off.</p>"
        "<p id='making' class='muted'>Checking the clips…</p>"
        "<p><label><input type='checkbox' id='ramp'> Wait before each next clip: 2 s, then 4 s, 6 s… (the board holds "
        "the next clip back, as when colony is still making it)</label><br><span class='muted'>With the wait on, C still "
        "fetches ahead, so its buffer may hide the gap; D's stream goes quiet for it.</span></p>"
        f"<div class='ways'>{ways}</div>"
        "<p><button id='stop' disabled>Stop</button> <span id='now' class='muted'>Nothing playing.</span></p>"
        "<p id='silentnote'></p>"
        "<p id='support' class='muted'></p>"
        "<h2>Results</h2><div id='results' class='muted'>Loading…</div>"
        "<p class='muted'>Battery and CPU can't be read from Safari: weigh them from the other columns. Fewer bytes, "
        "requests, runs of the page's code and elements playing at once mean less work for the phone.</p>"
        "<p><a href='/'>Back to the board</a></p>"
        f"</main><script type='application/json' id='lab-data'>{json.dumps(data).replace('</', '<' + chr(92) + '/')}</script>"
        f"<script>{SCRIPT}</script></body></html>")


STYLE = """
:root { color-scheme: light dark; --bg: #fafaf8; --fg: #1d1d1b; --muted: #6b6b66; --line: #ddddd6; --accent: #2f6f4f; }
@media (prefers-color-scheme: dark) { :root { --bg: #171716; --fg: #ecece6; --muted: #a0a09a; --line: #3a3a36; --accent: #7cc49d; } }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.45 -apple-system, system-ui, sans-serif; }
main { max-width: 720px; margin: 0 auto; padding: 16px; padding-top: max(16px, env(safe-area-inset-top)); }
h1 { font-size: 1.4em; margin: 0 0 8px; } h2 { font-size: 1.1em; margin-top: 28px; }
.muted, .how { color: var(--muted); } .cost { font-style: italic; }
#results { overflow-x: auto; } .how { font-size: 0.9em; }
.way { display: flex; gap: 10px; align-items: flex-start; padding: 10px 0; border-top: 1px solid var(--line); }
.way button { flex: none; width: 11em; text-align: left; }
button { font: inherit; padding: 8px 12px; border-radius: 8px; border: 1px solid var(--line); background: transparent; color: var(--fg); }
button:not(:disabled) { border-color: var(--accent); color: var(--accent); } button:disabled { opacity: 0.5; }
button.on { background: var(--accent); color: var(--bg); }
table { width: 100%; border-collapse: collapse; font-size: 0.9em; } td, th { text-align: left; padding: 6px 4px; border-top: 1px solid var(--line); vertical-align: top; }
a { color: var(--accent); }
"""

SCRIPT = r"""
(() => {
  const LAB = JSON.parse(document.getElementById('lab-data').textContent), COUNT = LAB.count;
  const NAME = Object.fromEntries(LAB.ways.map((w) => [w.key, w.name]));
  const $ = (id) => document.getElementById(id), ms = navigator.mediaSession;
  let status = null, cur = null, queue = [];
  try { queue = JSON.parse(localStorage.getItem('lockplay-unsent') || '[]'); } catch (e) { queue = []; }
  const keep = () => { try { localStorage.setItem('lockplay-unsent', JSON.stringify(queue.slice(-2000))); } catch (e) {} };
  // every report goes to the board as it happens; one that can't go now waits and goes when the page wakes
  const flush = () => {
    while (queue.length) {
      const batch = queue.slice(0, 100);
      let sent = false;
      try { sent = navigator.sendBeacon('/lab/lockplay/log', JSON.stringify(batch)); } catch (e) {}
      if (!sent) break;
      queue.splice(0, batch.length);
    }
    keep();
  };
  const log = (event, extra) => {
    queue.push(Object.assign({approach: cur ? cur.key : '', run: cur ? cur.run : '', clip: cur ? cur.clip : 0, event,
      t: new Date().toISOString(), vis: document.visibilityState}, cur ? measures(cur) : {}, extra || {}));
    keep(); flush();
  };
  // each run's requests carry its ID: the board counts them, the page finds them among its resource timings
  const gap = (n) => (cur && cur.ramp && n > 1 ? 2 * (n - 1) : 0);   // the ramp: the board holds clip n back this long
  const url = (key, n, bare) => `/lab/lockplay/clip?a=${key}&n=${n}${bare ? '&bare=1' : ''}` +
    `${gap(n) ? '&wait=' + gap(n) : ''}&run=${cur ? encodeURIComponent(cur.run) : ''}`;
  // what a run costs, as far as a page can see: the bytes and requests it fetched, how often its own code ran (all and
  // while hidden), and how many of its elements are playing now
  const measures = (c) => {
    let bytes = 0, requests = 0;
    try { performance.getEntriesByType('resource').forEach((e) => {
      if (e.name.includes(`run=${encodeURIComponent(c.run)}`)) { bytes += e.transferSize || 0; requests += 1; } }); } catch (e) {}
    return {bytes, requests, calls: c.calls, hidden_calls: c.callsHidden,
      playing: [c.audio, c.silent].filter((x) => x && !x.paused).length, silence: Math.round((c.silenceFed || 0) * 10) / 10};
  };
  const counted = (c, fn) => function (...args) {
    if (c === cur) { c.calls += 1; if (document.visibilityState === 'hidden') c.callsHidden += 1; }
    return fn.apply(this, args); };
  // every handler set on a run's elements and sources is counted as it runs
  const count = (c, t) => { const add = t.addEventListener.bind(t);
    t.addEventListener = (ev, fn, o) => add(ev, counted(c, fn), o);
    try { Object.defineProperty(t, 'onended', {set(fn) { t.addEventListener('ended', fn); }, configurable: true}); } catch (e) {}
    return t; };
  const show = (text) => { $('now').textContent = text; };
  const meta = (c, n) => { try { if (ms && window.MediaMetadata) ms.metadata = new MediaMetadata(
    {title: `clip ${n} of ${COUNT}`, artist: NAME[c.key], album: 'colony lab'}); } catch (e) {} };
  const handlers = (c, skips) => { if (!ms) return; const set = (k, f) => { try { ms.setActionHandler(k, f); } catch (e) {} };
    set('play', counted(c, () => c.audio && c.audio.play())); set('pause', counted(c, () => c.audio && c.audio.pause()));
    if (skips) { set('seekbackward', null); set('seekforward', null); } };
  // where a clip starts, ends and the run finishes, as each way sees it
  const reached = (c, n) => { if (c.stopped || c.heard === n) return; c.heard = n; c.clip = n; meta(c, n); log('start', Object.assign({gap: gap(n)}, c.both ? c.both() : {}));
    show(`Playing ${NAME[c.key]}, clip ${n} of ${COUNT}.`); };
  const ended = (c, n) => { if (c.stopped || c.ends.has(n)) return; c.ends.add(n); log('end', {clip: n}); if (n === COUNT) finish(c); };
  const finish = (c) => { if (c.done) return; c.done = true; if (c.silent) c.silent.pause(); c.clip = COUNT; log('finished'); show(`${NAME[c.key]} played all ${COUNT}.`); stopped(); };
  const SILENT = LAB.silent;
  const element = (c, own) => {
    const a = count(c, new Audio()); a.preload = 'auto'; c.audio = a;
    if (own) a.addEventListener('playing', () => { if (!(a.loop && a.src === SILENT)) reached(c, c.clip); });
    a.addEventListener('stalled', () => !c.stopped && log('stall'));
    a.addEventListener('waiting', () => !c.stopped && log('waiting'));
    a.addEventListener('error', () => !c.stopped && log('error', {why: a.error ? `media error ${a.error.code}` : 'media error'}));
    a.addEventListener('pause', () => { if (!c.stopped && !c.done && !a.ended) log('paused'); });
    c.stop = () => { c.stopped = true; a.pause(); a.removeAttribute('src'); try { a.load(); } catch (e) {} };
    return a;
  };
  const go = (c, a) => a.play().catch((err) => log('error', {why: `play refused: ${err.name}`}));
  const session = (kind) => { try { if (navigator.audioSession) navigator.audioSession.type = kind; } catch (e) {} };

  const ways = {
    // A: one element, the next clip set on it as the last ends
    A(c) { const a = element(c, true);
      const play = (n) => { c.clip = n; a.src = url(c.key, n); go(c, a); };
      a.onended = () => { ended(c, c.clip); if (c.clip < COUNT) play(c.clip + 1); };
      play(1); },
    // B: each clip loops, so it never ends; its jump back to the start sets the next
    B(c) { const a = element(c, true); let last = 0;
      const play = (n) => { c.clip = n; last = 0; a.loop = n < COUNT; a.src = url(c.key, n); go(c, a); };
      const wrapped = () => { last = 0; ended(c, c.clip); if (c.clip < COUNT) play(c.clip + 1); };
      a.addEventListener('timeupdate', () => { const t = a.currentTime; if (last > 1 && t < 1 && t < last - 1) wrapped(); else last = t; });
      a.addEventListener('seeked', () => { if (last > 1 && a.currentTime < 1) wrapped(); });
      a.addEventListener('playing', () => handlers(c, true));
      a.onended = () => ended(c, c.clip);
      play(1); },
    // C: one continuous source in the page, each clip's sound added while the one before plays
    C(c) { const MS = window.ManagedMediaSource || window.MediaSource, type = 'audio/mpeg';
      if (!MS) { log('error', {why: 'no MediaSource here'}); show('This browser has no MediaSource: C cannot run here.'); return stopped(); }
      if (!MS.isTypeSupported(type)) { log('error', {why: 'audio/mpeg not supported by MediaSource'});
        show('This browser\'s MediaSource does not take MP3 (audio/mpeg): C cannot run here.'); return stopped(); }
      const a = element(c, false), src = count(c, new MS()), ends = [0];
      a.disableRemotePlayback = true;                   // Safari's ManagedMediaSource opens only with this set
      let sb = null, next = 1, busy = false, closed = false;
      const ahead = () => { const b = sb.buffered; return b.length ? b.end(b.length - 1) - a.currentTime : 0; };
      const append = (buf) => new Promise((ok) => {
        const tryIt = (again) => {
          try { sb.appendBuffer(buf); sb.addEventListener('updateend', () => ok(true), {once: true}); }
          catch (err) {
            if (err.name === 'QuotaExceededError' && again && a.currentTime > 3) {
              log('freed played audio'); sb.addEventListener('updateend', () => tryIt(false), {once: true}); sb.remove(0, a.currentTime - 2); return; }
            log('error', {why: `append: ${err.name}`}); ok(false);
          } };
        tryIt(true); });
      const pump = async () => {
        if (!sb || busy || closed || c.stopped || sb.updating) return;
        if (next > COUNT) { try { src.endOfStream(); } catch (e) {} closed = true; return; }
        if (next > 1 && ahead() > 20) return;
        busy = true;
        try {
          const buf = await (await fetch(url(c.key, next, true))).arrayBuffer();
          if (await append(buf)) { const b = sb.buffered; ends[next] = b.length ? b.end(b.length - 1) : ends[next - 1]; next += 1; }
        } catch (err) { log('error', {why: `fetch: ${err}`}); }
        busy = false; if (!c.stopped) pump();
      };
      src.addEventListener('sourceopen', () => { sb = count(c, src.addSourceBuffer(type)); sb.mode = 'sequence'; pump(); }, {once: true});
      src.addEventListener('startstreaming', () => { log('source wants more'); pump(); });
      src.addEventListener('endstreaming', () => log('source has enough'));
      a.addEventListener('timeupdate', () => {
        const t = a.currentTime;
        for (let n = 1; n < next; n++) if (t < ends[n]) { if (c.heard && n > c.heard) ended(c, c.heard); reached(c, n); break; }
        pump(); });
      a.addEventListener('playing', () => handlers(c, false));
      a.onended = () => { if (c.heard) ended(c, c.heard); };
      c.clip = 1; a.src = URL.createObjectURL(src); go(c, a); },
    // D: one endless download from the board; the page only watches where it is
    D(c) { const a = element(c, false), lengths = (status && status.durations.D) || [], ends = [];
      lengths.reduce((sum, s, i) => (ends[i + 1] = sum + s), 0);
      a.addEventListener('timeupdate', () => { const t = a.currentTime;
        for (let n = 1; n <= COUNT; n++) if (t < (ends[n] || Infinity)) { if (c.heard && n > c.heard) ended(c, c.heard); reached(c, n); break; } });
      a.addEventListener('playing', () => handlers(c, false));
      a.onended = () => ended(c, COUNT);
      c.clip = 1; a.src = `/lab/lockplay/stream?run=${encodeURIComponent(c.run)}${c.ramp ? '&ramp=1' : ''}`; go(c, a); },
    // E: the loop trick, with Safari told this page plays media
    E(c) { ways.B(c); },
    // F: today's way, colony's looping silence playing while the next clip is fetched and set
    F(c) { const a = element(c, true);
      const play = (n) => { c.clip = n; a.loop = false; a.src = url(c.key, n); go(c, a); };
      a.onended = () => { if (a.loop) return; ended(c, c.clip); if (c.clip >= COUNT) return;
        a.loop = true; a.src = SILENT; go(c, a); log('bridge');
        fetch(url(c.key, c.clip + 1)).then(counted(c, (r) => r.arrayBuffer())).catch(() => {})
          .then(counted(c, () => { if (!c.stopped) play(c.clip + 1); })); };
      play(1); },
    // G: today's way, over a second element looping silence from the tap to the end of the run; each clip start
    // reports whether both are playing, since some iOS versions pause one element when another starts
    G(c) { const a = element(c, true), s = count(c, new Audio()), stop = c.stop; c.silent = s;
      s.loop = true; s.src = SILENT;
      c.stop = () => { stop(); s.pause(); s.removeAttribute('src'); };
      c.both = () => ({main: a.paused ? 'paused' : 'playing', silent: s.paused ? 'paused' : 'playing'});
      s.addEventListener('pause', () => { if (c.stopped || c.done) return; log('silent paused', c.both());
        $('silentnote').textContent = `The silent element was paused at clip ${c.clip}: this phone let only one element play.`; });
      s.addEventListener('playing', () => !c.stopped && log('silent playing', c.both()));
      s.play().catch((err) => log('error', {why: `silent play refused: ${err.name}`}));
      const play = (n) => { c.clip = n; a.src = url(c.key, n); go(c, a); };
      a.onended = () => { ended(c, c.clip); if (c.clip < COUNT) play(c.clip + 1); };
      play(1); },
    // H: one continuous source, as C, but each clip asked for only once the last has played (with the ramp's wait,
    // as A sees it). Silence is kept LEAD seconds ahead of what plays, since on iOS a stream that runs dry while
    // locked never recovers, and a hidden page's code may run late; each clip, when it comes, is spliced in a moment
    // after what plays now, over the silence there, so the lead adds no delay
    H(c) { const MS = window.ManagedMediaSource || window.MediaSource, type = 'audio/mpeg';
      if (!MS || !MS.isTypeSupported(type)) { log('error', {why: MS ? 'audio/mpeg not supported by MediaSource' : 'no MediaSource here'});
        show('This browser\'s MediaSource cannot play MP3 (audio/mpeg): H cannot run here.'); return stopped(); }
      const a = element(c, false), src = count(c, new MS()), starts = [], ends = [];
      a.disableRemotePlayback = true;                   // Safari's ManagedMediaSource opens only with this set
      const LEAD = 5, lengths = (status && status.durations.H) || [];
      let sb = null, quiet = null, chain = Promise.resolve(), asked = 0, last = 0, feeding = false, closed = false;
      let splice = 'overwrite';                         // falls back to 'remove' if an overlapping append leaves a hole
      c.silenceFed = 0;
      const tail = () => { const b = sb.buffered; return b.length ? b.end(b.length - 1) : 0; };
      const append = (buf) => new Promise((ok) => {
        const tryIt = (again) => {
          try { sb.appendBuffer(buf); sb.addEventListener('updateend', () => ok(true), {once: true}); }
          catch (err) {
            if (err.name === 'QuotaExceededError' && again && a.currentTime > 3) {
              sb.addEventListener('updateend', () => tryIt(false), {once: true}); sb.remove(0, a.currentTime - 2); return; }
            log('error', {why: `append: ${err.name}`}); ok(false);
          } };
        tryIt(true); });
      // every append waits its turn, so a clip that arrives goes in after the silence already there
      const queue = (job) => (chain = chain.then(() => (c.stopped ? null : job())).catch(() => {}));
      const feed = () => {
        if (!sb || !quiet || feeding || closed || c.stopped || last >= COUNT || tail() - a.currentTime >= LEAD) return;
        feeding = true;
        queue(async () => { const from = tail(); if (await append(quiet)) c.silenceFed += tail() - from; })
          .then(() => { feeding = false; feed(); });
      };
      const ask = (n) => { asked = n;
        fetch(url(c.key, n, true)).then(counted(c, (r) => r.arrayBuffer())).then(counted(c, (buf) => queue(async () => {
          const at = Math.min(a.currentTime + 0.3, tail()), plays = lengths[n - 1] || 0;
          if (splice === 'remove' && tail() > at) await new Promise((ok) => {
            sb.addEventListener('updateend', ok, {once: true}); sb.remove(at, Infinity); });
          sb.timestampOffset = at;                      // in sequence mode, where the next sound goes
          if (await append(buf)) {
            starts[n] = at; ends[n] = plays ? at + plays : tail(); last = n;
            const b = sb.buffered, whole = b.length === 1 && b.start(0) <= a.currentTime + 0.05 && b.end(0) >= ends[n] - 0.05;
            log('splice', {clip: n, why: `${splice}${whole ? '' : ', left a hole'}`});
            if (!whole && splice === 'overwrite') splice = 'remove';
          }
          if (n === COUNT) { try { src.endOfStream(); } catch (e) {} closed = true; }
        }))).catch((err) => log('error', {why: `fetch: ${err}`}));
      };
      src.addEventListener('sourceopen', () => { sb = count(c, src.addSourceBuffer(type)); sb.mode = 'sequence';
        fetch('/lab/lockplay/silence').then(counted(c, (r) => r.arrayBuffer())).then(counted(c, (buf) => {
          quiet = buf; ask(1); feed(); })).catch((err) => log('error', {why: `silence: ${err}`})); }, {once: true});
      src.addEventListener('startstreaming', feed);
      a.addEventListener('waiting', feed);
      a.addEventListener('timeupdate', () => {
        const t = a.currentTime;
        for (let n = 1; n <= last; n++) if (t >= starts[n] && t < ends[n]) { reached(c, n); break; }
        if (last && t >= ends[last] && !c.ends.has(last)) { ended(c, last); if (last < COUNT && asked === last) ask(last + 1); }
        feed(); });
      a.addEventListener('playing', () => handlers(c, false));
      a.onended = () => ended(c, COUNT);
      c.clip = 1; a.src = URL.createObjectURL(src); go(c, a); },
  };

  const buttons = [...document.querySelectorAll('[data-way]')];
  const stopped = () => { buttons.forEach((b) => b.classList.remove('on')); $('stop').disabled = true; };
  const stop = () => { if (!cur) return; if (!cur.done) log('stopped by tap'); if (cur.stop) cur.stop(); cur = null; stopped(); show('Nothing playing.'); };
  const start = (key) => {
    stop();
    $('silentnote').textContent = '';
    const kind = key === 'E' ? 'playback' : 'auto';
    session(kind);
    try { performance.clearResourceTimings(); performance.setResourceTimingBufferSize(2000); } catch (e) {}
    const ramp = $('ramp').checked;
    cur = {key, ramp, run: `${key}-${ramp ? 'ramp-' : ''}${Date.now().toString(36)}`, clip: 0, heard: 0, ends: new Set(), stopped: false, done: false,
      calls: 0, callsHidden: 0};
    log('run start', {session: navigator.audioSession ? kind : 'none'});
    buttons.forEach((b) => b.classList.toggle('on', b.dataset.way === key)); $('stop').disabled = false;
    show(`Starting ${NAME[key]}${ramp ? ', with the ramp' : ''}…`); meta(cur, 1); handlers(cur, false);
    ways[key](cur);
  };
  buttons.forEach((b) => b.addEventListener('click', () => start(b.dataset.way)));
  $('stop').addEventListener('click', stop);

  // what this browser offers, so a way that cannot run here says so
  const MMS = window.ManagedMediaSource, MSE = window.MediaSource, yes = (v) => v ? 'yes' : 'no';
  const takes = (M, t) => { try { return !!(M && M.isTypeSupported(t)); } catch (e) { return false; } };
  const M = MMS || MSE;
  $('support').innerHTML = [
    `ManagedMediaSource: ${yes(MMS)}; MediaSource: ${yes(MSE)}.`,
    `${MMS ? 'ManagedMediaSource' : 'MediaSource'} takes audio/mpeg: ${yes(takes(M, 'audio/mpeg'))}; ` +
      `audio/mp4 (AAC): ${yes(takes(M, 'audio/mp4; codecs="mp4a.40.2"'))}.` +
      (M && !takes(M, 'audio/mpeg') ? ' C uses audio/mpeg, so C cannot run in this browser.' : ''),
    `navigator.audioSession: ${navigator.audioSession ? 'yes' : 'no (E is then the same as B)'}.`,
  ].join('<br>');

  const esc = (s) => String(s).replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`);
  const clock = (iso) => { if (!iso) return ''; const d = new Date(iso.length <= 19 ? iso.replace(' ', 'T') : iso);
    return isNaN(d) ? esc(iso) : d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'}); };
  const results = () => fetch('/lab/lockplay/results', {cache: 'no-store'}).then((r) => r.json()).then((runs) => {
    if (!runs.length) { $('results').textContent = 'No runs yet.'; return; }
    const kb = (n) => n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`;
    $('results').innerHTML = '<table><tr><th>Way</th><th>Started</th><th>Clips reached</th><th>While hidden</th><th>Stopped</th>' +
      '<th>Bytes (page / board)</th><th>Requests (page / board)</th><th>Code ran (all / hidden)</th><th>Playing at once</th><th>Held open</th><th>Silence fed</th><th>Longest gap survived while hidden</th></tr>' +
      runs.map((r) => `<tr><td>${esc(r.approach)}. ${esc(r.name)}${r.ramp ? ' <b>ramp</b>' : ''}</td><td>${clock(r.began)}</td><td>${r.reached} of ${COUNT}</td>` +
        `<td>${r.hidden}</td><td>${r.finished ? 'finished, ' + clock(r.last) : r.reached ? 'after clip ' + r.reached + ', ' + clock(r.last) : 'never started'}` +
        `${r.approach === 'D' ? ` <span class="muted">(board sent ${r.sent})</span>` : ''}` +
        `${r.errors ? ` <span class="muted">${r.errors} error${r.errors > 1 ? 's' : ''}</span>` : ''}` +
        `${r.stalls ? ` <span class="muted">${r.stalls} stall${r.stalls > 1 ? 's' : ''}</span>` : ''}` +
        `${r.approach === 'G' ? ` <span class="muted">(silent element ${r.silent_paused ? 'got paused' : 'kept playing'})</span>` : ''}</td>` +
        `<td>${kb(r.bytes)} / ${kb(r.server_bytes)}</td><td>${r.requests} / ${r.server_requests}</td>` +
        `<td>${r.calls} / ${r.hidden_calls}</td><td>${r.playing}</td><td>${r.held === null ? '' : r.held + ' s'}</td><td>${r.silence ? r.silence + ' s' : ''}</td>` +
        `<td>${r.longest_gap_hidden === null ? '' : r.longest_gap_hidden + ' s'}</td></tr>`).join('') + '</table>';
  }).catch(() => {});

  const making = () => fetch('/lab/lockplay/status', {cache: 'no-store'}).then((r) => r.json()).then((s) => {
    status = s;
    buttons.forEach((b) => { b.disabled = s.ready[b.dataset.way] < COUNT; });
    document.querySelectorAll('[data-made]').forEach((m) => { const n = s.ready[m.dataset.made];
      m.textContent = n < COUNT ? `(clips: ${n} of ${COUNT})` : ''; });
    $('making').textContent = s.error ? s.error : s.made < s.total
      ? `Making the clips in colony's voice: ${s.made} of ${s.total}. A way's button works once its twelve are made.` : 'All clips ready.';
    if (s.made < s.total && !s.error) setTimeout(making, 2000);
  }).catch(() => setTimeout(making, 5000));

  document.addEventListener('visibilitychange', () => {
    log('visibility'); if (document.visibilityState === 'visible') { flush(); results(); } });
  window.addEventListener('pagehide', flush);
  window.addEventListener('pageshow', flush);
  setInterval(() => { if (cur) counted(cur, () => {})(); if (document.visibilityState === 'visible') results(); }, 5000);
  flush(); making(); results();
})();
"""
