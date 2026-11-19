"""R171: a project's conversation history, kept by section, with a one-line ledger.

Each conversation is cut where its program compacted it. A closed section's prose (the messages to the agent, its
replies, and one line for each command it ran) is stored in colony's home, never in the project (several projects keep
.board/ in their own git), and a light model titles it in one line, from a digest. The ledger is those lines; an agent
lists, opens and searches it with `colony history`, and is told so after each compaction. The section stays whole
beside its title: on the person's own history, a plain search of the whole recalled 88% where a compacted ledger
recalled 65% (a project's recall test, Oct 28-29), so the title is an index, never a replacement.

Before a program's cleanup reaches a transcript (PROVIDER: providers' forgets_after), colony keeps a compressed copy,
and keeps the project's current conversation fresh instead, so Chat and resume go on working.
"""
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import board, providers

DIGEST = 24000          # characters of a section its title is written from
TITLE_BRIEF = ("Below is one span of a project's conversation: messages to the project's agent (from the person, or "
               "colony's notices), the agent's replies, and the summary its program wrote when it compacted the span, "
               "if there is one. Write the span's title for a ledger an agent will scan to find it later: one line, at "
               "most 16 words, naming what was worked on and settled, with roadmap item numbers (R12) where they "
               "appear. Write only the line. Read no files: everything you need is below.\n\n")


def folder(root):
    """This project's history in colony's home: by its identity, so a move keeps it."""
    key = board.ident(root, make=False) or hashlib.sha1(str(Path(root).resolve()).encode()).hexdigest()[:12]
    return board.home() / "history" / key


def _load(root):
    try:
        return json.loads((folder(root) / "ledger.json").read_text())
    except (OSError, ValueError):
        return {"sections": {}, "scans": {}}


def _save(root, led):
    board.save_json(folder(root) / "ledger.json", led)


def _stamp(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _name(path, n):
    """A section's id: its conversation's own id, short, then its place in it (PROVIDER: Codex names a file
    rollout-<time>-<uuid>, whose uuid ends in its random part; Claude Code names it by a random uuid)."""
    stem = Path(path).stem
    return f"{stem[-8:] if stem.startswith('rollout-') else stem[:8]}.{n}"


def _records(path, start):
    """Each record of a transcript from byte offset start: (offset, offset after it, record)."""
    with open(path, "rb") as fh:
        fh.seek(start)
        at = start
        for line in fh:
            after = at + len(line)
            if line.endswith(b"\n"):                       # a line still being written waits for the next look
                try:
                    yield at, after, json.loads(line)
                except ValueError:
                    pass
            at = after


def _when(t):
    return time.strftime("%b %-d %H:%M", time.localtime(t)) if t else ""


def _block(t, kind, text):
    return f"— {_when(t)} {kind}\n{text.strip()}\n"


def _walk(prov, path, start, n):
    """The sections from offset start (the n-th of the file onward): ([closed], open), each
    {start, end, from, to, summary, prose}; a summary after a boundary belongs to the section it closed."""
    closed, cur = [], dict(start=start, end=None, **{"from": None}, to=None, summary="", prose=[])
    for at, after, rec in _records(path, start):
        t = _stamp(rec.get("timestamp"))
        for kind, text in prov.history_events(rec):
            if kind == "boundary":
                if cur["prose"]:
                    closed.append(dict(cur, end=at))
                cur = dict(start=after, end=None, **{"from": None}, to=None, summary="", prose=[])
            elif kind == "summary":
                if not cur["prose"] and closed:
                    closed[-1]["summary"] = text
                elif not cur["prose"]:
                    cur["summary_before"] = text           # it closed a section read before this look
            else:
                cur["prose"].append(_block(t, kind, text))
                cur["from"] = cur["from"] or t
                cur["to"] = t or cur["to"]
    return closed, cur


def _sources(root):
    """Every program's transcripts of this project: [(program key, provider, path)]. A seat working in another
    project's folder has only its own program's: that folder's other conversations are the other project's."""
    out, mine = [], _seat_program(root)
    for key, prov in providers.PROVIDERS.items():
        if mine and key != mine:
            continue
        try:
            out += [(key, prov, p) for p in prov.transcripts(root)]
        except OSError:
            continue
    return out


def _seat_program(root):
    """The program of a seat working in another project's folder, else None."""
    return providers.key(providers.of(root)) if board.workdir(root) != Path(root) else None


def _forget_others(root, led, home):
    """Files recorded that are still there but, by a rule made since, are not this project's conversations (a
    one-shot run, a helper's thread, another project's): their sections and copies go. A file its program deleted
    keeps its sections; keeping them is what the history is for."""
    mine = _seat_program(root)
    for path, seen in list(led["scans"].items()):
        prov = providers.get(seen.get("program"))
        foreign = (mine and seen.get("program") != mine) or (prov and not prov.conversation_file(path))
        if not foreign or not Path(path).exists():
            continue
        del led["scans"][path]
        for sid in [s for s, sec in led["sections"].items() if sec["path"] == path]:
            (home / f"{sid}.txt").unlink(missing_ok=True)
            del led["sections"][sid]
        (home / "raw" / (Path(path).name + ".gz")).unlink(missing_ok=True)


def scan(root):
    """Bring the ledger up to date: each closed section recorded and its prose stored; each file's open section
    noted. Reads only what a file gained since the last look."""
    led, home = _load(root), folder(root)
    home.mkdir(parents=True, exist_ok=True)
    _forget_others(root, led, home)
    for key, prov, path in _sources(root):
        try:
            st = path.stat()
        except OSError:
            continue
        seen = led["scans"].get(str(path)) or {}
        if seen.get("size") == st.st_size and seen.get("inode") == st.st_ino:
            continue
        again = seen.get("inode") == st.st_ino and st.st_size >= seen.get("size", 0)
        start, n = (seen.get("open", 0), seen.get("n", 0)) if again else (0, 0)
        closed, cur = _walk(prov, path, start, n)
        if cur.get("summary_before") and n:
            prior = led["sections"].get(_name(path, n - 1))
            if prior and not prior.get("summary"):
                prior["summary"] = cur["summary_before"]
        for sec in closed:
            sid = _name(path, n)
            _store(home, sid, sec)
            led["sections"][sid] = dict(id=sid, program=key, path=str(path), start=sec["start"], end=sec["end"],
                                        **{"from": sec["from"]}, to=sec["to"], summary=sec["summary"][:20000],
                                        title=(led["sections"].get(sid) or {}).get("title", ""))
            n += 1
        led["scans"][str(path)] = dict(program=key, inode=st.st_ino, size=st.st_size, open=cur["start"], n=n,
                                       **{"from": cur["from"]}, to=cur["to"])
    _save(root, led)
    return led


def _store(home, sid, sec):
    (home / f"{sid}.txt").write_text("".join(sec["prose"]))


def close_open(root, path):
    """A file no longer written to (kept, before its program forgets it): its open section is closed as it stands."""
    led = _load(root)
    seen = led["scans"].get(str(path))
    if not seen or seen.get("from") is None:
        return
    prov = providers.get(seen["program"])
    _, cur = _walk(prov, path, seen["open"], seen["n"])
    if cur["prose"]:
        sid = _name(path, seen["n"])
        _store(folder(root), sid, cur)
        led["sections"][sid] = dict(id=sid, program=seen["program"], path=str(path), start=cur["start"],
                                    end=Path(path).stat().st_size, **{"from": cur["from"]}, to=cur["to"], summary="",
                                    title="", ended=True)
        seen.update(n=seen["n"] + 1, open=Path(path).stat().st_size, **{"from": None}, to=None)
        _save(root, led)


def keep(root, now=None):
    """Before a program forgets a transcript (PROVIDER: forgets_after), keep it: the project's current conversation
    is touched, so resume and Chat keep it; any other is copied, compressed, into colony's home, and its open
    section closed. Returns what was done: [(action, path)]."""
    from . import console, context
    now, done = now or time.time(), []
    current = set()
    try:
        s = context.session(root)
    except (OSError, ValueError, KeyError, TypeError):
        s = None
    if s:
        current.add(str(Path(s["path"]).resolve()))
    for key in providers.PROVIDERS:
        saved = console.saved_conversation(root, key)
        if saved:
            current.add(str(Path(saved["transcript"]).resolve()))
    for key, prov, path in _sources(root):
        limit = getattr(prov, "forgets_after", None)
        try:
            idle = now - path.stat().st_mtime
        except OSError:
            continue
        if not limit or idle < limit * 2 / 3:             # a third of its time left as a margin: a machine left off
            continue
        if str(path.resolve()) in current:
            os.utime(path)
            done.append(("touched", path))
            continue
        raw = folder(root) / "raw" / (path.name + ".gz")
        if raw.exists() and raw.stat().st_mtime >= path.stat().st_mtime:
            continue
        raw.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "rb") as src, gzip.open(str(raw) + ".part", "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(str(raw) + ".part", raw)
        close_open(root, path)
        done.append(("kept", path))
    return done


def digest(root, sec):
    """What a section's title is written from: its summary, then its messages and replies in order, each cut short,
    evenly thinned to the budget."""
    text = (folder(root) / f"{sec['id']}.txt").read_text(errors="replace")
    blocks = [b for b in re.split(r"(?m)^(?=— )", text) if b.strip() and " ran\n" not in b.split("\n", 1)[0] + "\n"]
    blocks = [b if len(b) <= 420 else b[:400] + " …\n" for b in blocks]
    head = ("Summary written at its compaction:\n" + sec["summary"][:6000] + "\n\n") if sec.get("summary") else ""
    room = max(2000, DIGEST - len(head))
    while blocks and sum(map(len, blocks)) > room:
        blocks = blocks[::2] if len(blocks) > 2 else blocks[:1]
    return head + "The span, in order:\n" + "".join(blocks)


def clean_title(text):
    """The model's first line, without the label or marks some models put around a title."""
    line = " ".join(text.strip().splitlines()[:1]).strip(" \"'*#")
    return re.sub(r"^title\s*:\s*", "", line, flags=re.I).strip(" \"'*#")


def title_pending(root, limit=6, ask=None):
    """Titles up to limit closed sections, newest first, each by the rote helper of the program it ran on; a
    section whose program can't be asked now keeps waiting. Returns how many were titled."""
    from . import selection, usage
    led = scan(root)
    if str(root) in usage.paused():
        return 0
    todo = sorted((s for s in led["sections"].values() if not s.get("title")), key=lambda s: s.get("to") or 0,
                  reverse=True)[:limit]
    done = 0
    for sec in todo:
        prov = providers.get(sec["program"])
        if not providers.usable(prov):
            continue
        try:
            pick = selection.resolve(sec["program"], "rote", selection.pin_of(root, "rote")
                                     if sec["program"] == providers.key(providers.of(root)) else None, root)
        except Exception:
            continue
        said = (ask or prov.consult)(TITLE_BRIEF + digest(root, sec), pick["model"], pick.get("effort") or "low",
                                     board.workdir(root))
        line = clean_title(said.get("text") or "")
        if said.get("error") or not line:
            continue
        led = _load(root)
        led["sections"][sec["id"]]["title"] = line[:200]
        led["sections"][sec["id"]]["titled_by"] = pick["model"]
        _save(root, led)
        done += 1
    return done


def build(root, limit=6):
    """One pass for a project: record new sections, keep what a program would forget (closing its open span),
    title a few."""
    scan(root)
    kept = keep(root)
    titled = title_pending(root, limit)
    return kept, titled


def kick():
    """The watcher's call: every project's history brought up to date in a process of its own, never two at once
    (the titles take a model call each)."""
    import sys
    out = board.home() / "history"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "build.log", "ab") as log:
        subprocess.Popen([sys.executable, "-m", "colony", "history", "--build", "--all"], stdout=log, stderr=log,
                         stdin=subprocess.DEVNULL, start_new_session=True, cwd=str(Path(__file__).resolve().parent.parent))


def building():
    """The lock a build holds; None when another holds it."""
    import fcntl
    path = board.home() / "history" / "build.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


# --- reading it: `colony history`

def _day(t):
    return time.strftime("%b %-d" + (", %Y" if time.localtime(t).tm_year != time.localtime().tm_year else ""),
                         time.localtime(t)) if t else "?"


def _span(a, b):
    return _day(a) if _day(a) == _day(b) else f"{_day(a)} – {_day(b)}"


def _first_up(text):
    return text[:1].upper() + text[1:]


def _sorted(led):
    return sorted(led["sections"].values(), key=lambda s: s.get("from") or 0)


def ledger_lines(root):
    """The ledger, oldest first, with each conversation's open span last."""
    led = scan(root)
    out = [f"{s['id']:12} {_span(s.get('from'), s.get('to')):18} {s.get('title') or '(not titled yet)'}"
           for s in _sorted(led)]
    for path, seen in led["scans"].items():
        if seen.get("from") and Path(path).exists():
            sid, live = _name(path, seen["n"]), time.time() - Path(path).stat().st_mtime < 6 * 3600
            when = _day(seen["from"]) + " – now" if live else _span(seen["from"], seen.get("to"))
            out.append(f"{sid:12} {when:18} " + ("(open: the conversation's current span)" if live else
                                                  "(its conversation's last span, never compacted)"))
    return out


def _prose(root, led, sid):
    """A section's prose: stored when it closed; an open one read from its transcript."""
    stored = folder(root) / f"{sid}.txt"
    if stored.exists():
        return stored.read_text(errors="replace")
    for path, seen in led["scans"].items():
        if _name(path, seen["n"]) == sid and Path(path).exists():
            return "".join(_walk(providers.get(seen["program"]), path, seen["open"], seen["n"])[1]["prose"])
    return None


def show(root, sid, raw=False):
    led = scan(root)
    sec = led["sections"].get(sid)
    text = _prose(root, led, sid)
    if text is None:
        raise KeyError(sid)
    if raw:
        return text
    head = (f"{sid} · {_span(sec.get('from'), sec.get('to'))} · {sec.get('title') or '(not titled yet)'}"
            if sec else f"{sid} · open: the conversation's current span")
    said = [b for b in re.split(r"(?m)^(?=— )", text) if b.startswith("— ") and b.split("\n", 1)[0].endswith(" message")]
    said = [b if len(b) <= 620 else b[:600] + " …\n" for b in said]
    return "\n".join([head, ""] + ([f"Summary written when it was compacted:\n{sec['summary']}", ""] if sec and sec.get("summary") else [])
                     + [f"The {len(said)} messages to the agent in it:", "".join(said),
                        f"All its prose: colony history {sid} --raw ({len(text) // 1000}k characters)."])


def search(root, words, limit=12):
    """Blocks of prose holding every word (else the most of them), newest first: [(section, title, when, snippet)]."""
    led = scan(root)
    terms = [w.lower() for w in words.split() if w.strip()]
    if not terms:
        return []
    ids = [s["id"] for s in _sorted(led)] + [_name(p, s["n"]) for p, s in led["scans"].items() if s.get("from")]
    hits = []
    for order, sid in enumerate(ids):
        text = _prose(root, led, sid) or ""
        for block in re.split(r"(?m)^(?=— )", text):
            low = block.lower()
            got = sum(1 for t in terms if t in low)
            if got:
                at = min(low.find(t) for t in terms if t in low)
                snip = " ".join(block[max(0, at - 120): at + 200].split())
                hits.append((got, order, sid, block.split("\n", 1)[0][2:], snip))
    best = max((h[0] for h in hits), default=0)
    hits = sorted((h for h in hits if h[0] == best), key=lambda h: h[1], reverse=True)[:limit]
    titles = {s["id"]: s.get("title") or "" for s in led["sections"].values()}
    return [(sid, titles.get(sid, "(open)"), when, snip) for _, _, sid, when, snip in hits]


def item(root, iid):
    """What an agent needs to place an item far back: its dates, the sections from its time or naming it, and the
    commits naming it."""
    rec = board.item_times(root).get(iid)
    road = board.items(board.roadmap(root)).get(iid)
    if not rec and not road:
        raise KeyError(iid)
    led = scan(root)
    lo = (rec or {}).get("created") or (rec or {}).get("first") or 0
    hi = (rec or {}).get("at") if (rec or {}).get("state") == "done" else time.time()
    word = re.compile(rf"\b{re.escape(iid)}\b")
    named = {}
    for s in _sorted(led):
        try:
            named[s["id"]] = len(word.findall((folder(root) / f"{s['id']}.txt").read_text(errors="replace")))
        except OSError:
            named[s["id"]] = 0
    near = [s for s in _sorted(led) if named.get(s["id"]) or (lo and (s.get("to") or 0) >= lo and (s.get("from") or 0) <= (hi or 0))]
    near = sorted(near, key=lambda s: -named.get(s["id"], 0))[:12]
    near = sorted(near, key=lambda s: s.get("from") or 0)
    commits = subprocess.run(["git", "-C", str(root), "log", "-E", f"--grep=(^|[^0-9A-Za-z]){iid}([^0-9]|$)", "--date=short",
                              "--format=%h %ad %s", "-n", "15"], capture_output=True, text=True).stdout.strip()
    out = [f"{iid}: {road['text'] if road else '(no longer on the roadmap)'}",
           _first_up(board.item_dates(rec) or "no dates recorded") + (f" (now {rec['state']})" if rec else ""), ""]
    out += ["Sections from its time or naming it:"] + [
        f"  {s['id']:12} {_span(s.get('from'), s.get('to')):18} {s.get('title') or '(not titled yet)'}"
        + (f"  [{named[s['id']]}× {iid}]" if named.get(s["id"]) else "") for s in near] if near else ["No sections from its time."]
    out += ["", "Commits naming it:", commits or "  none"]
    out += ["", f"Open a section: colony history ID; search them all: colony history --search WORDS"]
    return "\n".join(out)
