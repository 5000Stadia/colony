"""Warning signs (R146): whether colony's changes are making the projects' work better or worse, read over months from
what colony already records. Not a quality score: three views kept apart, never added up.

- Outcomes: items written, started, finished and still open each week, the checks they carried, the person's sign-offs.
- Friction: items reopened ([x] back to [ ] or [~]), reverts, the person's explicit corrections, dev reports (where
  colony's shape got in a project's way), and the ambiguous cases a cheap model labelled as a missed requirement or a
  defect. A change of intent is shown, never counted against the work.
- Cost: tokens each week from the programs' own transcripts, weighed as their limits weigh them, with the part spent
  in conversations no commit came out of (abandoned work, or talk).

Every signal carries its evidence (the commit, roadmap line, note, message or mail it was read from). Exposure marks,
per item, which colony mechanisms it actually ran under: the guidance version (GUIDE.md and guides/ by commit), topic
closing (R144) where its project's conversation shows it, and research library entries written for it. A week with
little in it says "too little evidence"; nothing here is ever shown as good, only as no warning sign. A concern is
raised only past the explicit, conservative thresholds below, and nothing found is ever turned into a rule.

Part B, labels: code finds the explicit cases; a small batch on the project's program at its rote tier labels only the
ambiguous ones, from short excerpts and the diff, plus a random sample of items that look clean (to find misses). Each
label keeps its rubric version, packet hash and the resolved model; old labels are never relabelled. Before a new
labeller or rubric labels anything, it rereads the saved anchor cases, and a judgement that moves is recorded as a
break. Batches run only when new ambiguous cases arrived since the baseline, at most one a day.

Everything read is read only; everything kept is under board.home()/warnings, never in a project.
"""
import hashlib
import json
import os
import random
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import board

VERSION = 1
RUBRIC_VERSION = "r1"
LABELS = ("missed_requirement", "defect_regression", "changed_intent", "no_problem", "unknown")
PROBLEM = ("missed_requirement", "defect_regression")        # what a label counts as friction
RUBRIC = (
    "You label cases from a software project's history for a warning system that watches whether the work is getting "
    "better or worse. Each case is a short excerpt (a commit, a roadmap line, the person's message) and sometimes a "
    "diff. Decide what it shows about the work done before it:\n"
    "- missed_requirement: the earlier work missed something the person or the item had asked for;\n"
    "- defect_regression: the earlier work had a bug, or broke something that worked;\n"
    "- changed_intent: the person changed or widened what they wanted; the earlier work did what was asked then;\n"
    "- no_problem: ordinary progress; nothing shows the earlier work falling short;\n"
    "- unknown: the excerpt can't tell.\n"
    "Judge only from what is shown. Reply with JSON only, a list: "
    '[{"case": "<id>", "label": "<one of the five>", "why": "<at most 15 words>"}]')

# When a week, or a comparison, says anything but "too little evidence"; each threshold is shown with its reading.
MIN_EVENTS = 3          # item events (written, started, finished, reopened) in a week before it is read at all
WINDOW = 4              # recent weeks compared with the project's earlier ones
MIN_BASE = 6            # earlier weeks with evidence before any comparison is made
MIN_FRICTION = 4        # counted friction signals in the window before a friction concern
RATIO = 2.0             # the window's rate must be at least this many times the baseline's
MARGIN = 0.25           # and at least this many signals per item event above it
MIN_FINISHED = 3        # finished items in the window before a cost concern
REFRESH = 6 * 3600      # how old the report may be before the board's watcher reads the records again
BATCH_MAX = 20          # cases one labelling batch reads
BATCH_EVERY = 24 * 3600
ANCHORS = 8             # saved anchor cases a new labeller rereads first
EXCERPT, DIFF = 600, 1200

ITEM_LINE = re.compile(r"^\s*-\s*\[( |x|X|~|\?)\]\s*(R\d+)\b\s*(.*)$")
STATE = {" ": "todo", "~": "doing", "?": "verify", "x": "done", "X": "done"}
REF = re.compile(r"(?<![\w/#=&?-])(R\d+)\b(?!-)")
CHURN = re.compile(r"\b(fix(e[sd])?|redo(ne)?|rework(ed)?|rebuil[dt]|rewr[io]te|replac(e|ed|es)|again|wrong|broke|broken|"
                   r"regress\w*|restor(e|ed|es)|instead|undo|back(ed)? out|roll(ed)? back|scrap(ped)?|rethink|redesign\w*)\b", re.I)
REVERT = re.compile(r"^(Revert\b|Reverts?\b)|\b(revert(s|ed)?|roll(ed|s)? back|back(ed|s)? out)\b", re.I)
BUILT = re.compile(r"\b(built|done|shipped|ships|finished)\b", re.I)
SAID = re.compile(r"\bThe person(?:,[^:\"“]{0,60})?:\s*[\"“]")
# the person's words quoted where the work is recorded: "<someone>, 2026-10-06: '...'" in a commit or a roadmap line
QUOTED = re.compile(r"\b(?:you|the person|[A-Z][a-z]+)(?:,? (?:on )?(\d{4}-\d{2}-\d{2})[^:\"'“‘]{0,40})?:\s*"
                    r"(?:[\"“]([^\"”]{6,400})|['‘]([^'’]{6,400}))")
DATED = re.compile(r"(\d{4}-\d{2}-\d{2})")
# work redone while the item was open, or anywhere when the words are strong
REVISED = re.compile(r"\b(revis(e|ed|es|ion)|round two|second (round|pass)|corrected|instead|replac(e|ed|es|ing)|"
                     r"rewr[io]te|rewritten|rework(ed)?|redo(ne)?|reshap(e|ed)|no longer|rethink|redesign\w*|scrap(ped)?|"
                     r"root[- ]causes?)\b", re.I)
STRONG = re.compile(r"\b(rewr[io]te|rewritten|rework(ed)?|redo(ne)?|redesign\w*|scrap(ped)?|rebuil[dt]|no longer|"
                    r"was the problem|threw (it )?out|root[- ]causes?)\b", re.I)
FAILED = re.compile(r"\bfail(ed|s|ing)?\b[^.;]{0,40}\b(checks?|tests?|rehearsals?|reviews?|runs?)\b|"
                    r"\b(checks?|tests?|rehearsals?|reviews?) (failed|fail)\b", re.I)
BULK = 3                # items reopened by one commit: a reformat as likely as a reopening, so the labeller tells
# the person's corrections: these by code; the softer ones go to the labeller
EXPLICIT = re.compile(r"\b(I said|I told you|as I said|I already (said|asked|told)|not what I (asked|meant|wanted|said)|"
                      r"you forgot|you missed|that'?s not (it|right|what I)|that is not what|still (broken|not working|"
                      r"doesn'?t work)|(doesn'?t|didn'?t|isn'?t) work(ing)?|not working)\b|^\s*(false|wrong|no)\b[,.!]", re.I)
SOFT = re.compile(r"\b(actually|instead|I meant|rather than|why did|why is|why does|wrong|again|hmm|not quite|broke|"
                  r"missing|forgot|should(n'?t)? have|should'?ve|jumps|shaky|don'?t like|isn'?t right)\b", re.I)
GUIDANCE = ("GUIDE.md", "guides")      # the guidance colony deploys, by commit of its own repository

CANNOT_SHOW = [
    "which guides an agent actually opened, or what the steward's notes changed: neither is recorded per item",
    "edits to the always-loaded protocol inside colony/board.py: they share commits with the rest of board.py",
    "which model or tier worked an item: transcripts are not tied to items",
    "Q Branch briefs handed at an item's start: not recorded yet; research library entries naming an item stand in",
    "the person's words typed straight into a console: only those sent through the board (messages, notes, answers) "
    "are read, since a transcript doesn't tell the person's typing from colony's",
    "a project whose roadmap isn't in git: its items have no history to read",
    "getting better: fewer corrections can mean less inspection, and less rework less ambitious work",
]

_lock = threading.Lock()
_running = False


# ------------------------------------------------------------------------------------------------- small helpers

def folder():
    return board.home() / "warnings"


def git(root, *args):
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, errors="replace", timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout if r.returncode == 0 else ""


def stamp(at):
    """A time as seconds: an ISO date with any offset, a board time, or seconds already."""
    if isinstance(at, (int, float)):
        return float(at)
    try:
        return datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def week(at):
    """The Monday (UTC) of the week a time falls in, as YYYY-MM-DD."""
    t = stamp(at)
    if t is None:
        return None
    d = datetime.fromtimestamp(t, timezone.utc).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def short(text, n):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def sid(*parts):
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12]


def signal(root, view, kind, at, text, evidence, item=None, explicit=True):
    return {"id": sid(root, kind, evidence.get("ref"), item, short(text, 80)), "project": str(root),
            "name": Path(root).name, "view": view, "kind": kind, "at": iso(stamp(at)), "week": week(at),
            "item": item, "explicit": explicit, "text": short(text, 300), "evidence": evidence}


def jsonl(path):
    out = []
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def append(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def projects():
    """Every project colony knows, the archived ones too (their history is part of the baseline). Read only."""
    reg = board.registry()
    out = [Path(p) for p in board.projects(reg, adopt=False)]
    out += [Path(p) for p in board._archived() if Path(p) not in out]
    return [p for p in out if p.is_dir()]


# ------------------------------------------------------------------------------------- a project's plan, from git

def roadmap(root):
    """Each item's history from the commits of ROADMAP.md, and the signals it shows: {items, signals, commits}.
    items: {id: {created, started, done, reopened, state, text, events: [(at, state, commit)]}}."""
    out = git(root, "log", "--reverse", "--no-color", "--unified=0", "--format=%x1e%H%x1f%aI%x1f%s", "-p", "--",
              "ROADMAP.md")
    items, sigs, n = {}, [], 0
    for chunk in out.split("\x1e")[1:]:
        head, _, body = chunk.partition("\n")
        try:
            h, at, subj = head.split("\x1f", 2)
        except ValueError:
            continue
        n += 1
        gone, came = {}, {}
        for line in body.splitlines():
            if line.startswith(("---", "+++")):
                continue
            if line[:1] in "-+" and (m := ITEM_LINE.match(line[1:])):
                (gone if line[0] == "-" else came)[m[2]] = (STATE[m[1]], m[3].strip(), line[1:].strip())
        bulk = sum(1 for r, (st_, _, _) in came.items() if st_ in ("todo", "doing")
                   and (items.get(r) or {}).get("state") == "done") >= BULK
        for rid, (state, text, raw) in came.items():
            it = items.get(rid)
            ev = {"type": "roadmap", "ref": h[:12], "line": short(raw, 300), "subject": short(subj, 200)}
            if it is None or it.get("dropped"):
                items[rid] = it = {"id": rid, "created": at, "state": state, "text": text, "events": [], "reopened": 0}
            old = it["state"]
            if state != old or not it["events"]:
                it["events"].append((at, state, h[:12]))
            if state == "doing" and not it.get("started"):
                it["started"] = at
            if state == "done" and old != "done":
                it["done"], it["done_commit"] = at, h[:12]
                it.setdefault("started", at)
            if old == "done" and state in ("todo", "doing"):
                it["reopened"] += 1
                it.pop("done", None)
                sigs.append(signal(root, "friction", "reopened" if not bulk else "reopened with others in one commit",
                                   at, f"{rid} reopened: [x] back to {'[~]' if state == 'doing' else '[ ]'} ({subj})",
                                   ev, rid, explicit=not bulk))
            elif old == "done" and state == "done" and gone.get(rid):
                new = set(CHURN.findall(text)) - set(CHURN.findall(gone[rid][1]))
                if new and len(text) > len(gone[rid][1]):
                    sigs.append(signal(root, "friction", "finished item rewritten", at,
                                       f"{rid}, finished, rewritten: {subj}", dict(ev, before=short(gone[rid][2], 300)),
                                       rid, explicit=False))
            if it["events"] and len(it["events"]) == 1 and it["created"] == at and STRONG.search(text):
                sigs.append(signal(root, "friction", "new item redoes earlier work", at, f"{rid}: {short(text, 240)}",
                                   ev, rid, explicit=False))
            if rid not in gone:                    # a new line: does it name a finished item it reworks?
                for ref in set(REF.findall(text)) - {rid}:
                    if items.get(ref, {}).get("state") == "done" and CHURN.search(text):
                        sigs.append(signal(root, "friction", "later item names a finished one", at,
                                           f"{rid} names finished {ref}: {short(text, 200)}", ev, ref, explicit=False))
            old_quotes = {a or b for _, a, b in QUOTED.findall(gone[rid][1])} if rid in gone else set()
            for day, a, b in QUOTED.findall(text):
                q = a or b
                if q in old_quotes or (day and stamp(day) is not None and stamp(day) < stamp(at) - 14 * 86400):
                    continue
                if EXPLICIT.search(q) or SOFT.search(q):
                    sigs.append(signal(root, "friction", "the person's words in the plan", at, f"{rid}: \"{q}\"",
                                       dict(ev, line=short(q, EXCERPT)), rid, explicit=False))
            it["state"], it["text"] = state, text
        for rid in set(gone) - set(came):
            if rid in items:
                items[rid]["dropped"] = at
    return {"items": items, "signals": sigs, "commits": n}


def details(root, items):
    """The person's words quoted into an item's details file (roadmap/R#.md) after the item was built: a change of
    intent, a missed requirement or a defect, for the labeller to tell."""
    out = git(root, "log", "--reverse", "--no-color", "--unified=0", "--format=%x1e%H%x1f%aI%x1f%s", "-p", "--",
              "roadmap")
    sigs = []
    for chunk in out.split("\x1e")[1:]:
        head, _, body = chunk.partition("\n")
        try:
            h, at, subj = head.split("\x1f", 2)
        except ValueError:
            continue
        rid, new = None, False
        for line in body.splitlines():
            if line.startswith("diff --git"):
                rid, new = None, False
            elif line.startswith("--- "):
                new = line.strip() == "--- /dev/null"     # a details file made in this commit: moved, not said
            elif line.startswith("+++ "):
                m = re.search(r"roadmap/(R\d+)\.md$", line)
                rid = m[1] if m else None
            elif rid and not new and line.startswith("+") and SAID.search(line):
                built = (items.get(rid) or {}).get("built")
                day = DATED.search(line[SAID.search(line).start():][:80])
                if built and day and stamp(day[1]) is not None and stamp(day[1]) < stamp(built) - 86400:
                    continue                            # the person's earlier words, carried into the file
                if built and stamp(at) > stamp(built):
                    sigs.append(signal(root, "friction", "the person's word after it was built", at,
                                       f"{rid}: {short(line[1:], 260)}",
                                       {"type": "detail", "ref": h[:12], "file": f"roadmap/{rid}.md",
                                        "line": short(line[1:], EXCERPT), "subject": short(subj, 200)}, rid, explicit=False))
    return sigs


def commits(root, items):
    """Commits on the branch: reverts (explicit), commits naming a finished item with words of rework (ambiguous), and
    when each item was first said to be built. Returns (signals, commit times)."""
    out = git(root, "log", "--reverse", "--no-color", "--format=%H%x1f%aI%x1f%s")
    sigs, times = [], []
    for line in out.splitlines():
        try:
            h, at, subj = line.split("\x1f", 2)
        except ValueError:
            continue
        t = stamp(at)
        times.append(t)
        ev = {"type": "commit", "ref": h[:12], "line": short(subj, 300)}
        refs = [r for r in REF.findall(subj) if r in items]
        for r in refs:
            if BUILT.search(subj) and not items[r].get("built"):
                items[r]["built"] = at
        if REVERT.search(subj):
            sigs.append(signal(root, "friction", "revert", at, subj, ev, refs[0] if refs else None))
            continue
        first = refs[0] if refs else None
        if FAILED.search(subj):
            sigs.append(signal(root, "friction", "failed check", at, subj, ev, first, explicit=False))
            continue
        quotes = [a or b for _, a, b in QUOTED.findall(subj) if EXPLICIT.search(a or b) or SOFT.search(a or b)]
        if quotes:
            sigs.append(signal(root, "friction", "the person's words in a commit", at, subj,
                               dict(ev, quote=short(quotes[0], 300)), first, explicit=False))
            continue
        flagged = False
        for r in refs:
            done = items[r].get("done")
            if done and t > stamp(done) and h[:12] != items[r].get("done_commit") and CHURN.search(subj):
                sigs.append(signal(root, "friction", "finished item worked again", at, subj, ev, r, explicit=False))
                flagged = True
            elif ((not done or t < stamp(done)) and stamp(items[r].get("started") or items[r]["created"]) <= t
                  and REVISED.search(subj)):
                sigs.append(signal(root, "friction", "item reworked while open", at, subj, ev, r, explicit=False))
                flagged = True
        if not flagged and not refs and STRONG.search(subj):
            sigs.append(signal(root, "friction", "work redone", at, subj, ev, None, explicit=False))
    for it in items.values():
        it.setdefault("built", it.get("done"))
    return sigs, times


def words(root, items):
    """The person's words sent through the board (messages, notes, answers): explicit corrections by code, softer
    ones for the labeller. Also the sign-offs put to them."""
    b = Path(root) / ".board"
    sigs, signoffs = [], []
    said = []
    for m in jsonl(b / "messages.jsonl"):
        if m.get("type") == "message":
            said.append((m.get("at"), m.get("text") or "", {"type": "message", "ref": m.get("at")}))
    for n in jsonl(b / "notes.jsonl"):
        if n.get("type") == "note" and n.get("author", "person") == "person":
            item = (n.get("anchor") or {}).get("item") if isinstance(n.get("anchor"), dict) else None
            said.append((n.get("at"), n.get("text") or "", {"type": "note", "ref": n.get("id"), "item": item}))
    for g in jsonl(b / "gates.jsonl"):
        if g.get("type") == "answer":
            said.append((g.get("at"), g.get("text") or "", {"type": "answer", "ref": g.get("of")}))
    for at, text, ev in said:
        if not at or not text.strip() or text.lstrip().startswith("[colony]"):
            continue
        refs = [r for r in REF.findall(text) if r in items]
        item = ev.pop("item", None) or (refs[0] if refs else None)
        ev["line"] = short(text, EXCERPT)
        if EXPLICIT.search(text):
            sigs.append(signal(root, "friction", "the person's correction", at, text, ev, item))
        elif SOFT.search(text):
            sigs.append(signal(root, "friction", "the person's words, perhaps a correction", at, text, ev, item,
                               explicit=False))
    for r in jsonl(b / "ready.jsonl"):
        if r.get("type") == "ready":
            signoffs.append(r)
    return sigs, signoffs


def dev_reports(names):
    """Mail tagged dev (R148): where colony's shape got in a project's way, read from colony's own mailbox."""
    here = Path(__file__).resolve().parent.parent
    sigs = []
    for m in jsonl(here / ".board" / "mail.jsonl"):
        if m.get("type") == "message" and m.get("dev") and m.get("from") in names:
            root = names[m["from"]]
            sigs.append(signal(root, "friction", "dev report", m.get("at"), m.get("text") or "",
                               {"type": "mail", "ref": m.get("id"), "line": short(m.get("text"), EXCERPT)}))
    return sigs


# ----------------------------------------------------------------------------------------------------- exposure

def guidance():
    """[(time, commit)] of colony's own commits to the guidance it deploys, oldest first."""
    here = Path(__file__).resolve().parent.parent
    out = []
    for line in git(here, "log", "--reverse", "--format=%aI%x1f%h", "--", *GUIDANCE).splitlines():
        at, _, h = line.partition("\x1f")
        if stamp(at):
            out.append((stamp(at), h))
    return out


def topic_closes(root):
    """When this project's conversations closed a topic (R144), with the conversation: from its context history."""
    return [(stamp(r.get("at")), r.get("session")) for r in jsonl(Path(root) / ".board" / "context-history.jsonl")
            if r.get("status") == "topic closed" and stamp(r.get("at"))]


def research_for(root):
    """{item: [entry ids]} for research library entries that name this project and one of its items."""
    out = {}
    d = board.home() / "research"
    name = Path(root).name
    for f in d.glob("*.json") if d.is_dir() else []:
        try:
            e = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if e.get("by") != name:
            continue
        for r in set(REF.findall((e.get("text") or "")[:600] + " " + (e.get("title") or ""))):
            out.setdefault(r, []).append(e.get("id"))
    return out


def exposure(root, items, guides, closes, research, now):
    """Per item, the mechanisms it ran under while it was open: the guidance version it started on and any changes
    during it, the topic closings in its project's conversations then, and research written for it."""
    out = []
    for rid, it in items.items():
        start = stamp(it.get("started"))           # an item never begun ran under nothing
        end = stamp(it.get("done")) or now
        if start is None:
            continue
        at_start = [h for t, h in guides if t <= start]
        during = [h for t, h in guides if start < t <= end]
        tc = [s for t, s in closes if start <= t <= end]
        out.append({"item": rid, "start": iso(start), "end": iso(end) if it.get("done") else None,
                    "guidance": at_start[-1] if at_start else None, "guidance_changed": during,
                    "topic_closing": {"ran": bool(tc), "closes": len(tc), "conversations": sorted({s for s in tc if s})},
                    "research": research.get(rid, [])})
    return out


# --------------------------------------------------------------------------------------------------------- cost

def _sources():
    from . import providers as pv
    bases = [(Path(pv.get("claude").config_home()) / "projects", "claude"),
             (Path(pv.get("codex").config_home()) / "sessions", "codex")]
    bases += [(d / "sessions", "codex") for d in board.codex_homes().glob("*")]
    for base, prog in bases:
        if base.exists():
            for f in base.rglob("*.jsonl"):
                yield f, prog


def _read_transcript(f, prog, st):
    """Add what a transcript gained since last read to its per-week sums: [fresh, cache read, cache write, output]."""
    from . import tokens
    with open(f, "rb") as fh:
        fh.seek(st["at"])
        for raw in fh:
            if not raw.endswith(b"\n"):
                break
            st["at"] += len(raw)
            if prog == "claude":
                if b'"usage"' not in raw or b'"assistant"' not in raw:
                    if not st.get("cwd") and b'"cwd"' in raw:
                        try:
                            st["cwd"] = json.loads(raw).get("cwd") or ""
                        except ValueError:
                            pass
                    continue
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                m = d.get("message") or {}
                u, mid = m.get("usage"), m.get("id")
                st["cwd"] = d.get("cwd") or st.get("cwd") or ""
                if not u or not mid or mid in st.setdefault("seen", []) or m.get("model") == "<synthetic>":
                    continue
                st["seen"] = (st["seen"] + [mid])[-20:]
                nums = (u.get("input_tokens"), u.get("cache_read_input_tokens"), u.get("cache_creation_input_tokens"),
                        u.get("output_tokens"))
            else:
                if b'"session_meta"' in raw and not st.get("cwd"):
                    try:
                        st["cwd"] = (json.loads(raw).get("payload") or {}).get("cwd") or ""
                    except ValueError:
                        pass
                    continue
                if b'"token_count"' not in raw:
                    continue
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                u = ((d.get("payload") or {}).get("info") or {}).get("last_token_usage")
                if not u:
                    continue
                cached = u.get("cached_input_tokens") or 0
                nums = ((u.get("input_tokens") or 0) - cached, cached, u.get("cache_write_input_tokens"),
                        u.get("output_tokens"))
            t = stamp(d.get("timestamp"))
            if t is None:
                continue
            st["first"] = min(st.get("first") or t, t)
            st["last"] = max(st.get("last") or t, t)
            w = st.setdefault("weeks", {}).setdefault(week(t), [0, 0, 0, 0])
            for i, n in enumerate(nums):
                w[i] += int(n or 0)
    st["cwd_where"] = tokens._where(st.get("cwd"))


def scan_costs(sources=None, budget=None):
    """Bring the per-transcript week sums up to date (kept in warnings/cost.json); returns the state."""
    path = folder() / "cost.json"
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        state = {}
    end = time.time() + budget if budget else None
    for f, prog in (sources if sources is not None else _sources()):
        if end and time.time() > end:
            break
        st = state.setdefault(str(f), {"at": 0, "prog": prog})
        try:
            size = f.stat().st_size
        except OSError:
            continue
        if size < st["at"]:
            st.clear()
            st.update(at=0, prog=prog)
        if size > st["at"]:
            try:
                _read_transcript(f, prog, st)
            except OSError:
                continue
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(path)
    return state


def owner_of(cwd, roots):
    """The project a transcript's folder belongs to: the longest project folder holding it, a worktree's repository,
    or the project whose agent from the other program it is; None for anything else."""
    from . import tokens
    best = None
    for r in roots:
        s = str(r)
        if cwd == s or cwd.startswith(s + "/"):
            if not best or len(s) > len(str(best)):
                best = r
    if best is None and cwd and not cwd.startswith("colony:"):
        repo = tokens._home_repo(cwd)
        if repo:
            best = next((r for r in roots if str(r) == repo), None)
    if best is None:
        twin = re.match(re.escape(str(board.agents_home())) + r"/([^/]+?)(?:-codex)?(?:/|$)", cwd or "")
        if twin:
            best = next((r for r in roots if Path(r).name == twin[1]), None)
    return best


def costs(state, roots, landed):
    """{root: {week: {weighted, tokens, nothing_landed}}}: each transcript's tokens weighed as its program's limits
    weigh them, and the part spent in a conversation during which no commit landed in its project."""
    from . import tokens
    out = {}
    for f, st in state.items():
        root = owner_of(st.get("cwd_where") or st.get("cwd") or "", roots)
        if root is None or not st.get("weeks"):
            continue
        w8 = tokens.WEIGHTS.get(st.get("prog"), (1, 0.1, 1, 5))
        times = landed.get(str(root))
        a, b = (st.get("first") or 0) - 600, (st.get("last") or 0) + 3600
        nothing = times is not None and not any(a <= t <= b for t in times)
        for wk, v in st["weeks"].items():
            c = out.setdefault(str(root), {}).setdefault(wk, {"weighted": 0, "tokens": 0, "nothing_landed": 0,
                                                               "conversations": 0})
            weighted = sum(x * y for x, y in zip(v, w8))
            c["weighted"] += weighted
            c["tokens"] += sum(v)
            c["conversations"] += 1
            if nothing:
                c["nothing_landed"] += weighted
    return out


# ------------------------------------------------------------------------------------------------- the reading

def read_project(root, now=None, guides=None):
    """One project's signals, outcomes by week, exposure and coverage, from its own records (read only)."""
    now = now or time.time()
    road = roadmap(root)
    items = road["items"]
    csigs, times = commits(root, items)
    sigs = road["signals"] + csigs + details(root, items)
    wsigs, signoffs = words(root, items)
    sigs += wsigs
    weeks = {}

    def at(wk):
        return weeks.setdefault(wk, {"written": 0, "started": 0, "finished": 0, "reopened": 0, "checks": 0,
                                     "signoffs": 0, "open": 0})
    for it in items.values():
        for i, (t, state, _) in enumerate(it["events"]):
            wk = week(t)
            if i == 0:
                at(wk)["written"] += 1
            if state == "doing":
                at(wk)["started"] += 1
            if state == "done":
                at(wk)["finished"] += 1
                if re.search(r"\bcheck", it["text"], re.I):
                    at(wk)["checks"] += 1
    for s in sigs:
        if s["kind"] == "reopened":
            at(s["week"])["reopened"] += 1
    for r in signoffs:
        if week(r.get("at")):
            at(week(r["at"]))["signoffs"] += 1
    for wk in weeks:                       # unfinished items count: those open at the week's end
        end = stamp(wk) + 7 * 86400
        weeks[wk]["open"] = sum(1 for it in items.values() if stamp(it["created"]) < end
                                and not (it.get("dropped") and stamp(it["dropped"]) < end)
                                and ([s for t, s, _ in it["events"] if stamp(t) < end] or ["todo"])[-1] != "done")
    has_board = (Path(root) / ".board").is_dir()
    coverage = {"roadmap_commits": road["commits"], "items": len(items), "commits": len(times),
                "board_records": has_board, "weeks": sorted(weeks),
                "note": ("no roadmap history in git" if not road["commits"] else
                         "no board records: the person's words aren't read" if not has_board else "")}
    expo = exposure(root, items, guidance() if guides is None else guides, topic_closes(root), research_for(root), now)
    return {"root": str(root), "name": Path(root).name, "signals": sigs, "weeks": weeks, "coverage": coverage,
            "exposure": expo, "commit_times": times,
            "clean": [{"item": r, "text": short(it["text"], 240)} for r, it in sorted(items.items())
                      if it.get("done") and not any(s["item"] == r for s in sigs)]}


def evidence(w):
    return sum(w.get(k, 0) for k in ("written", "started", "finished", "reopened")) >= MIN_EVENTS


def counted(sig, labels):
    """Whether a signal counts as friction: explicit ones always (but the person's change of mind never), ambiguous
    ones only once labelled a missed requirement or a defect."""
    if sig["explicit"]:
        return True
    lab = labels.get(sig["id"])
    return bool(lab) and lab["label"] in PROBLEM


def concerns(p, labels, cost, today=None):
    """A concern only past the thresholds above: the last WINDOW weeks against the project's earlier weeks with
    evidence. Returns [{kind, text, signals}]."""
    weeks = p["weeks"]
    today = week(today or time.time())
    recent = [(datetime.fromisoformat(today) - timedelta(weeks=i)).date().isoformat() for i in range(WINDOW)]
    base = [wk for wk in sorted(weeks) if wk < recent[-1] and evidence(weeks[wk])]
    out = []
    if len(base) < MIN_BASE:
        return out
    fric = [s for s in p["signals"] if counted(s, labels)]

    def rate(wks):
        n = sum(1 for s in fric if s["week"] in wks)
        ev = sum(sum(weeks.get(w, {}).get(k, 0) for k in ("started", "finished")) for w in wks)
        return n, ev, (n / ev if ev else None)
    n, ev, r = rate(set(recent))
    _, _, r0 = rate(set(base))
    if n >= MIN_FRICTION and r is not None and r0 is not None and r >= RATIO * r0 and r - r0 >= MARGIN:
        out.append({"kind": "friction", "signals": [s["id"] for s in fric if s["week"] in recent],
                    "text": f"{n} repairs and corrections in the last {WINDOW} weeks over {ev} items started or "
                            f"finished ({r:.2f} each), against {r0:.2f} in its {len(base)} earlier weeks with evidence "
                            f"(a concern at {RATIO:g}x and {MARGIN:g} above, with at least {MIN_FRICTION})"})
    c = cost or {}
    fin = sum(weeks.get(w, {}).get("finished", 0) for w in recent)
    per = [c[w]["weighted"] / weeks[w]["finished"] for w in base if w in c and weeks[w].get("finished")]
    if fin >= MIN_FINISHED and len(per) >= MIN_BASE:
        now_per = sum(c.get(w, {}).get("weighted", 0) for w in recent) / fin
        mid = sorted(per)[len(per) // 2]
        if mid and now_per >= RATIO * mid:
            out.append({"kind": "cost", "signals": [],
                        "text": f"{now_per / 1e6:.1f}M weighted tokens per finished item in the last {WINDOW} weeks, "
                                f"against a median of {mid / 1e6:.1f}M in its earlier weeks (a concern at {RATIO:g}x, "
                                f"with at least {MIN_FINISHED} finished)"})
    return out


def build(roots=None, now=None, scan=True, budget=None):
    """The whole reading, kept as warnings/report.json: per project its weeks in the three views, its signals with
    their evidence, exposure, coverage and any concern. No model is called."""
    now = now or time.time()
    roots = [Path(r) for r in (roots if roots is not None else projects())]
    guides = guidance()
    labels = latest_labels()
    out = {"made": iso(now), "version": VERSION, "rubric": RUBRIC_VERSION, "projects": {}, "cannot_show": CANNOT_SHOW,
           "thresholds": {"min_events": MIN_EVENTS, "window": WINDOW, "min_base": MIN_BASE,
                          "min_friction": MIN_FRICTION, "ratio": RATIO, "margin": MARGIN, "min_finished": MIN_FINISHED},
           "guidance_versions": [h for _, h in guides]}
    read = {}
    for r in roots:
        try:
            read[str(r)] = read_project(r, now, guides)
        except Exception as err:             # one project's records unreadable: said, the rest go on
            read[str(r)] = {"root": str(r), "name": r.name, "error": err.__class__.__name__}
    names = {p["name"]: Path(k) for k, p in read.items()}
    for s in dev_reports(names):
        if "signals" in read.get(s["project"], {}):
            read[s["project"]]["signals"].append(s)
    cost = costs(scan_costs(budget=budget) if scan else {}, roots,
                 {k: p.get("commit_times") for k, p in read.items() if p.get("commit_times")})
    for k, p in read.items():
        if "error" in p:
            out["projects"][k] = p
            continue
        p["cost"] = cost.get(k, {})
        p["coverage"]["cost_weeks"] = sorted(p["cost"])
        p["concerns"] = concerns(p, labels, p["cost"], now)
        p.pop("commit_times", None)
        out["projects"][k] = p
    path = folder() / "report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out))
    tmp.replace(path)
    st = state()
    if not st.get("baseline"):
        st["baseline"] = out["made"]           # cases after this arrive new; those before are labelled only by sample
    st["built"] = out["made"]
    save_state(st)
    return out


_kept = {}


def report():
    """The last reading, read again only when it changed (the board shows it on every project page)."""
    path = folder() / "report.json"
    try:
        mtime = path.stat().st_mtime
        if _kept.get("key") != (str(path), mtime):
            _kept.update(key=(str(path), mtime), data=json.loads(path.read_text()))
        return _kept["data"]
    except (OSError, ValueError):
        return None


def state():
    try:
        return json.loads((folder() / "state.json").read_text())
    except (OSError, ValueError):
        return {}


def save_state(st):
    path = folder() / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(st))


# ------------------------------------------------------------------------------------------- part B: labels

def latest_labels():
    """{case: its label row}; a case is labelled once, and that label stands."""
    out = {}
    for row in jsonl(folder() / "labels.jsonl"):
        out.setdefault(row["case"], row)
    return out


def packet(sig):
    """What the labeller reads of a case: a short excerpt and, for a commit, its diff."""
    ev = sig["evidence"]
    p = {"case": sig["id"], "kind": sig["kind"], "item": sig.get("item"),
         "excerpt": short("\n".join(x for x in (ev.get("before") and "before: " + ev["before"],
                                                 ev.get("line"), ev.get("subject")) if x), EXCERPT)}
    if ev.get("type") in ("commit", "roadmap", "detail") and ev.get("ref"):
        diff = git(sig["project"], "show", "--no-color", "--stat=100", "--unified=1", "--format=%s", ev["ref"])
        p["diff"] = diff[:DIFF]
    return p


def clean_packet(root, rid, items_text):
    """An item that looks clean, sampled to find what code missed: its line and the commits naming it."""
    subj = git(root, "log", "--no-color", "--format=%h %s", "-n", "6", f"--grep=\\b{rid}\\b", "-E")
    case = sid(root, "clean sample", rid)
    return {"case": case, "kind": "clean sample", "item": rid,
            "excerpt": short(f"{rid} {items_text}\ncommits naming it:\n{subj}", EXCERPT + DIFF // 2)}


def phash(p):
    return hashlib.sha256(json.dumps(p, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def parse(text):
    """The labeller's answer: {case: (label, why)}; anything else is unknown."""
    a, b = text.find("["), text.rfind("]")
    try:
        rows = json.loads(text[a:b + 1]) if a >= 0 and b > a else []
    except ValueError:
        rows = []
    out = {}
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and r.get("case"):
            lab = r.get("label") if r.get("label") in LABELS else "unknown"
            out[str(r["case"])] = (lab, short(r.get("why"), 160))
    return out


def model_labeller(root):
    """PROVIDER: the project's program at its rote tier, through its existing headless consult, with one prompt for
    the batch. Returns a labeller: packets -> {labels, model (resolved), picked, cost, usage}."""
    from . import providers, selection
    pick = selection.helper(root, "rote")
    p = providers.of(root)
    family = providers.key(p)

    def label(packets):
        brief = RUBRIC + "\n\nCases:\n" + json.dumps(packets, ensure_ascii=False, indent=1)
        seen = {}

        def run(cmd, **kw):                    # the program's own report of the model it resolved to
            r = subprocess.run(cmd, **kw)
            try:
                seen.update(json.loads(r.stdout).get("modelUsage") or {})
            except (ValueError, AttributeError):
                pass
            return r
        import inspect
        params = inspect.signature(p.consult).parameters
        here = folder() / "labeller"
        here.mkdir(parents=True, exist_ok=True)
        said = (p.consult(brief, pick["model"], pick.get("effort") or "low", here, run=run) if "run" in params
                else p.consult(brief, pick["model"], pick.get("effort") or "low", here))
        resolved = max(seen, key=lambda k: (seen[k] or {}).get("outputTokens", 0)) if seen else pick["model"]
        return {"labels": parse(said.get("text") or ""), "model": resolved, "picked": pick["model"],
                "family": family, "cost": said.get("cost"), "usage": said.get("usage"), "error": said.get("error")}
    label.ruler = (RUBRIC_VERSION, family, pick["model"])
    return label


def anchors():
    try:
        return json.loads((folder() / "anchors.json").read_text())
    except (OSError, ValueError):
        return []


def rulers():
    return jsonl(folder() / "rulers.jsonl")


def check_ruler(labeller, now=None):
    """Before a labeller or rubric labels anything new, it rereads the anchor cases; a judgement that moves is a
    break, recorded with the cases that moved. Returns the ruler row, or None when the ruler is unchanged."""
    ruler = list(labeller.ruler)
    mine = [r for r in rulers() if r.get("family") == ruler[1]]
    if mine and [mine[-1]["rubric_version"], mine[-1]["family"], mine[-1]["picked"]] == ruler:
        return None
    saved = [a for a in anchors() if a.get("family") == ruler[1]]
    if not saved:
        return None                          # nothing to compare with yet: the first batch sets the anchors
    said = labeller([a["packet"] for a in saved])
    moved = [{"case": a["case"], "was": a["label"], "now": said["labels"].get(a["case"], ("unknown", ""))[0]}
             for a in saved if said["labels"].get(a["case"], ("unknown", ""))[0] != a["label"]]
    row = {"at": iso(now or time.time()), "rubric_version": ruler[0], "family": ruler[1], "picked": ruler[2],
           "model": said["model"], "anchors": len(saved), "moved": moved, "break": bool(moved),
           "cost": said.get("cost"), "usage": said.get("usage")}
    append(folder() / "rulers.jsonl", row)
    return row


def pending(rep, new_only=True):
    """Ambiguous cases not yet labelled, by project; only those after the baseline unless new_only is off."""
    done = latest_labels()
    since = stamp(state().get("baseline") or rep.get("made")) if new_only else None
    out = {}
    for k, p in rep["projects"].items():
        for s in p.get("signals", []):
            if not s["explicit"] and s["id"] not in done and (since is None or stamp(s["at"]) > since):
                out.setdefault(k, []).append(s)
    return out


def batch(rep=None, labeller_for=model_labeller, new_only=True, limit=BATCH_MAX, families=None, rng=None, now=None):
    """Label the ambiguous cases that arrived since the last run, with a random sample of clean items; nothing at all
    when no new case arrived. Returns the run's record."""
    rep = rep or report()
    now = now or time.time()
    if not rep:
        return {"skipped": "no reading yet"}
    waiting = pending(rep, new_only)
    if not waiting:
        return {"skipped": "nothing new"}
    rng = rng or random.Random(int(now))
    done = latest_labels()
    runs = []
    by_family = {}
    for k, sigs in waiting.items():
        lab = labeller_for(Path(k))
        if families and lab.ruler[1] not in families:
            continue
        by_family.setdefault(tuple(lab.ruler), (lab, []))[1].append((k, sigs))
    for ruler, (lab, groups) in by_family.items():
        cases = [(k, s) for k, sigs in groups for s in sigs]
        rng.shuffle(cases)
        cases = cases[:limit]
        if not cases:
            continue
        ruled = check_ruler(lab, now)
        packets, meta = [], {}
        for k, s in cases:
            pk = packet(s)
            packets.append(pk)
            meta[pk["case"]] = (k, False)
        # clean items, sampled from the same projects to find what code missed: about one for each four cases
        pool = [(k, c) for k in sorted({k for k, _ in cases}) for c in rep["projects"][k].get("clean", [])
                if sid(k, "clean sample", c["item"]) not in done]
        for k, c in rng.sample(pool, min(len(pool), max(1, len(cases) // 4))):
            pk = clean_packet(k, c["item"], c["text"])
            packets.append(pk)
            meta[pk["case"]] = (k, True)
        said = lab(packets)
        bid = sid(ruler, now)
        if said.get("error") and not said["labels"]:
            row = {"batch": bid, "at": iso(now), "family": ruler[1], "picked": ruler[2], "model": said.get("model"),
                   "cases": len(packets), "labelled": 0, "error": said["error"], "cost": said.get("cost"),
                   "usage": said.get("usage")}
            append(folder() / "runs.jsonl", row)
            runs.append(row)
            continue
        n = 0
        for pk in packets:
            lab_why = said["labels"].get(pk["case"])
            if not lab_why or pk["case"] in done:
                continue
            k, sample = meta[pk["case"]]
            append(folder() / "labels.jsonl", {
                "case": pk["case"], "project": k, "kind": pk["kind"], "item": pk.get("item"), "sample": sample,
                "label": lab_why[0], "why": lab_why[1], "rubric_version": ruler[0], "family": ruler[1],
                "picked": ruler[2], "model": said["model"], "packet_hash": phash(pk), "batch": bid, "at": iso(now)})
            n += 1
        if not [a for a in anchors() if a.get("family") == ruler[1]]:
            keep_anchors(packets, said, ruler, now)
        if ruled is None and not [r for r in rulers() if r.get("family") == ruler[1]]:
            append(folder() / "rulers.jsonl", {"at": iso(now), "rubric_version": ruler[0], "family": ruler[1],
                                               "picked": ruler[2], "model": said["model"], "first": True,
                                               "anchors": len(anchors()), "moved": [], "break": False})
        row = {"batch": bid, "at": iso(now), "family": ruler[1], "picked": ruler[2], "model": said["model"],
               "cases": len(packets), "labelled": n, "samples": sum(1 for v in meta.values() if v[1]),
               "cost": said.get("cost"), "usage": said.get("usage"), "ruler_check": ruled}
        append(folder() / "runs.jsonl", row)
        runs.append(row)
    st = state()
    st["batch_at"] = iso(now)
    save_state(st)
    return {"runs": runs}


def keep_anchors(packets, said, ruler, now):
    """The first labeller's cases become the anchors every later labeller rereads: up to ANCHORS, spread over labels."""
    by = {}
    for pk in packets:
        if pk["case"] in said["labels"]:
            by.setdefault(said["labels"][pk["case"]][0], []).append(pk)
    chosen = []
    while len(chosen) < ANCHORS and any(by.values()):
        for lab in list(by):
            if by[lab] and len(chosen) < ANCHORS:
                chosen.append((lab, by[lab].pop(0)))
    rows = anchors() + [{"case": pk["case"], "packet": pk, "label": lab, "family": ruler[1], "picked": ruler[2],
                         "model": said["model"], "rubric_version": ruler[0], "at": iso(now)} for lab, pk in chosen]
    path = folder() / "anchors.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False))


# --------------------------------------------------------------------------------------------- the board's watcher

def check(now=None):
    """Called from the board's watcher: read the records again when the reading is older than REFRESH, and label the
    new ambiguous cases at most once a day, in the background, one run at a time."""
    global _running
    if os.environ.get("COLONY_TEST_FORBIDS"):    # the test suite: a watcher's look never reads the machine's history
        return False
    now = now or time.time()
    st = state()
    due = not st.get("built") or now - stamp(st["built"]) > REFRESH
    if not due:
        return False
    with _lock:
        if _running:
            return False
        _running = True

    def work():
        global _running
        try:
            rep = build(now=now, budget=600)
            if pending(rep) and (not st.get("batch_at") or now - stamp(st["batch_at"]) > BATCH_EVERY):
                batch(rep, now=now)
        except Exception:
            pass
        finally:
            with _lock:
                _running = False
    threading.Thread(target=work, daemon=True).start()
    return True


def view(root, rep=None):
    """What the board shows for one project: its last weeks in the three views, its concerns and the cases behind
    them, coverage, labels and ruler breaks. None before the first reading."""
    rep = rep or report()
    if not rep:
        return None
    p = rep["projects"].get(str(Path(root)))
    if not p or "error" in p:
        return {"made": rep["made"], "missing": True, "error": (p or {}).get("error")}
    labels = latest_labels()
    weeks = sorted(set(p["weeks"]) | set(p.get("cost", {})))[-8:]
    rows = []
    for wk in reversed(weeks):
        w = p["weeks"].get(wk, {})
        sigs = [s for s in p["signals"] if s["week"] == wk]
        rows.append({"week": wk, "outcomes": w, "evidence": evidence(w),
                     "friction": [s for s in sigs if counted(s, labels)],
                     "intent": [s for s in sigs if labels.get(s["id"], {}).get("label") == "changed_intent"],
                     "awaiting": [s for s in sigs if not s["explicit"] and s["id"] not in labels],
                     "cost": p.get("cost", {}).get(wk)})
    breaks = [r for r in rulers() if r.get("break")]
    by_id = {s["id"]: s for s in p["signals"]}
    return {"made": rep["made"], "weeks": rows, "coverage": p["coverage"], "cannot_show": rep["cannot_show"],
            "concerns": [dict(c, cases=[by_id[i] for i in c["signals"] if i in by_id]) for c in p.get("concerns", [])],
            "labels": {i: labels[i] for i in by_id if i in labels}, "breaks": breaks,
            "thresholds": rep["thresholds"], "exposure": p.get("exposure", [])}
