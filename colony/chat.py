"""Chat: a console's conversation as the person and the agent had it, each turn's work folded under it. Read from the
transcript the session's hooks recorded, so it costs nothing and the console itself is untouched; what the person sends
goes into the console as Message does."""
import json
import time
import os
import re
import threading
from pathlib import Path

TAIL = 40_000_000          # bytes from the transcript's end, first read: many turns, never a whole long session
BIG = 300_000              # a line this long is a picture or a long tool output, never shown: not read
KEEP = 30_000              # entries kept
_seen, _lock = {}, threading.Lock()


def _tail(path, limit=TAIL):
    """The transcript's last entries, whole lines only. Read once, then only what was added since (a page asks every
    few seconds); a transcript started again, or rewritten, is read afresh."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    with _lock:
        c = _seen.get(str(path))
        try:
            with open(path, "rb") as fh:
                if c and size >= c["end"] and c["end"] >= len(c["mark"]):
                    fh.seek(c["end"] - len(c["mark"]))
                    if fh.read(len(c["mark"])) != c["mark"]:
                        c = None                      # not the file it was: read it afresh
                elif c:
                    c = None
                if not c:
                    c = {"end": max(0, size - limit), "mark": b"", "entries": [], "cut": size > limit}
                fh.seek(c["end"])
                chunk = fh.read(size - c["end"])
        except OSError:
            return []
        last = chunk.rfind(b"\n")
        if last >= 0:                                 # what is still being written waits for its line's end
            lines = chunk[:last].split(b"\n")
            if c.pop("cut", False):
                lines = lines[1:]                     # the first is cut mid-line
            for line in lines:
                if len(line) > BIG:
                    continue
                try:
                    c["entries"].append(json.loads(line))
                except ValueError:
                    pass
            del c["entries"][:-KEEP]
            c["end"] += last + 1
            c["mark"] = chunk[max(0, last + 1 - 64):last + 1]
        _seen[str(path)] = c
        rest = chunk[last + 1:].strip()               # a whole last line with no line end yet: read, not kept
        try:
            return list(c["entries"]) + ([json.loads(rest)] if rest and len(rest) <= BIG else [])
        except ValueError:
            return list(c["entries"])


# PROVIDER: Claude Code takes a long message typed into it as a paste, and keeps it wrapped in its transcript.
PASTED = re.compile(r'<pasted_content id="[^"]*">\n?(.*?)\n?</pasted_content(?: id="[^"]*")?>', re.S)


def _typed(text):
    """What was typed or sent, unwrapped from the program's paste marker: the person's words, not a notice of its own
    (notices start "<", and so did a long message, which the chat then never showed and left queued)."""
    return PASTED.sub(lambda m: m.group(1), text) if isinstance(text, str) else text


def _who(text):
    """Who a message into the console came from: colony's own notices and the steward's relays start [colony]."""
    return "colony" if text.lstrip().startswith("[colony]") else "you"


def _step(name, arg):
    arg = (arg or "").strip().splitlines()[0][:120] if (arg or "").strip() else ""
    return f"{name}({arg})" if arg else name


def claude_events(path):
    """PROVIDER: Claude Code's transcript: the person's messages, the agent's prose, one step per tool it ran."""
    out, queued = [], []
    for e in _tail(path):
        if e.get("isMeta") or e.get("isSidechain"):
            continue
        at, content = e.get("timestamp", ""), (e.get("message") or {}).get("content")
        # sent mid-turn: queued at once, then taken up within the turn (or as the next one, a user entry then)
        if e.get("type") == "queue-operation":
            sent = _typed(e.get("content"))
            if e.get("operation") == "enqueue" and isinstance(sent, str) and not sent.lstrip().startswith("<"):
                queued.append(sent)
            elif e.get("operation") == "remove" and sent in queued:
                queued.remove(sent)
            elif e.get("operation") == "dequeue" and queued:
                queued.pop(0)                             # the oldest, taken up as the next turn
            elif e.get("operation") == "popAll":
                queued.clear()                            # taken back into the box (to edit, or reorder)
            continue
        if e.get("type") == "attachment":
            a = e.get("attachment") or {}
            sent = _typed(a.get("prompt"))
            if a.get("type") == "queued_command" and isinstance(sent, str) and sent.strip() \
                    and not sent.lstrip().startswith("<"):
                out.append((_who(sent), sent.strip(), at))
            continue
        if e.get("isCompactSummary"):
            out.append(("compact", "", at))
            continue
        if e.get("type") == "user":
            text = content if isinstance(content, str) else "\n".join(
                c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")
            text = _typed(text).strip()
            if text and not text.startswith("<") and not text.startswith("Caveat:"):
                out.append((_who(text), text, at))
        elif e.get("type") == "assistant" and isinstance(content, list):
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text" and c.get("text", "").strip():
                    out.append(("say", c["text"].strip(), at))
                # PROVIDER: Claude Code shows the person updates on its work in the console (thinkingDisplay "updates"),
                # kept as thinking: a response may hold only these, so they are what was said to the person
                elif c.get("type") == "thinking" and e.get("thinkingDisplay") == "updates" and (c.get("thinking") or "").strip():
                    out.append(("say", c["thinking"].strip(), at))
                elif c.get("type") == "tool_use" and not NEEDS_YOU.match(str((c.get("input") or {}).get("command") or "")):
                    inp = c.get("input") or {}
                    arg = inp.get("description") or inp.get("command") or inp.get("file_path") or inp.get("pattern") \
                        or inp.get("prompt") or next((v for v in inp.values() if isinstance(v, str)), "")
                    out.append(("step", _step(c.get("name", "tool"), arg), at))
    return out + [("queued", q.strip(), "") for q in queued if q.strip()]


# `colony needs-you`: the agent telling the board its turn asks the person; plumbing, never shown as work
NEEDS_YOU = re.compile(r"^\s*(?:\S*/)?colony\s+needs-you\s*$")


def codex_events(path):
    """PROVIDER: a Codex rollout: the person's messages, the agent's prose, one step per tool call, its helpers' reports."""
    out = []
    for e in _tail(path):
        p = e.get("payload") if isinstance(e.get("payload"), dict) else {}
        at, kind = e.get("timestamp", ""), p.get("type")
        if e.get("type") == "compacted":
            out.append(("compact", "", at))
        if e.get("type") != "response_item":
            continue
        if kind == "message" and p.get("role") in ("user", "assistant"):
            text = "\n".join(c.get("text", "") for c in p.get("content") or []
                             if isinstance(c, dict) and c.get("type") in ("input_text", "output_text"))
            text = _typed(text).strip()
            if not text:
                continue
            if p["role"] == "assistant":
                out.append(("say", text, at))
            elif not text.startswith("<") and not text.startswith("# AGENTS.md"):
                out.append((_who(text), text, at))
        elif kind in ("custom_tool_call", "function_call"):
            raw = p.get("input") or p.get("arguments") or ""
            cmd = re.search(r'cmd:\s*"((?:[^"\\]|\\.)*)"', raw) or re.search(r'"cmd"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
            if cmd and NEEDS_YOU.match(cmd.group(1)):
                continue
            out.append(("step", _step(p.get("name", "tool"), cmd.group(1).replace("\\n", " ") if cmd else raw), at))
        elif kind == "agent_message":
            text = "\n".join(c.get("text", "") for c in p.get("content") or [] if isinstance(c, dict))
            sender = re.search(r"Sender: (\S+)", text)
            if "FINAL_ANSWER" in text:
                out.append(("step", f"helper {sender.group(1) if sender else ''} reported", at))
    return out


def events(provider_key, path):
    if not path or not Path(path).exists():
        return []
    return (codex_events if provider_key == "codex" else claude_events)(path)


def turns(evs):
    """The conversation as turns: what started each (the person's message, colony's, or a compaction), then what the
    agent did, in order: its prose, and its steps between."""
    out = []
    for kind, text, at in evs:
        if kind in ("you", "colony", "compact", "queued"):
            out.append({"from": kind, "text": text, "at": at or f"q{len(out)}", "items": []})
        else:
            if not out:
                out.append({"from": None, "text": "", "at": at, "items": []})
            out[-1]["items"].append((kind, text))
            out[-1].setdefault("times", []).append(at or out[-1].get("last") or out[-1]["at"])   # each piece's own time
            if at:
                out[-1]["last"] = at                    # when the agent last wrote in it: the reply's own time
    return out


def worked(steps):
    """A run of steps in a line, as the app says it: Ran 6 commands, edited 2 files."""
    kinds = {"command": 0, "read": 0, "edit": 0, "search": 0, "other": 0}
    for s in steps:
        name = s.split("(")[0].lower()
        kinds["command" if name in ("bash", "exec", "exec_command", "shell") else "read" if name == "read"
              else "edit" if name in ("edit", "write", "multiedit", "notebookedit", "apply_patch") else
              "search" if name in ("grep", "glob", "websearch", "webfetch", "toolsearch") else "other"] += 1
    words = {"command": ("ran", "command"), "read": ("read", "file"), "edit": ("edited", "file"),
             "search": ("searched", "time"), "other": ("used", "tool")}
    parts = [f"{words[k][0]} {n} {words[k][1]}{'s' if n != 1 else ''}" for k, n in kinds.items() if n]
    line = ", ".join(parts)
    return line[:1].upper() + line[1:]


def helpers(snap):
    """The helpers a console has at work, off the rows its program lists under the box (Claude Code: "◯ colony-rote
    Check the menu   2m 5s · ↓ 41k tokens", gone once it ends), or as its program says them otherwise (Codex, from
    its thread store): [{name, what, stats}]. PROVIDER: Claude Code's rows."""
    if "helpers" in snap:
        return snap["helpers"]
    out = []
    for a in (snap.get("activity") or {}).get("agents") or []:
        if a["name"] != "main":
            parts = re.split(r"\s{2,}", a["detail"].strip())
            stats = parts.pop() if parts and re.match(r"\d+[hms]\b", parts[-1]) else ""
            out.append({"name": a["name"], "what": " ".join(parts), "stats": stats})
    return out


def running(lines, helping=()):
    """What runs in the background while the agent waits, off its console's footer (Claude Code: "· 1 shell",
    "2 background tasks", "1 local agent"; Codex: "2 background terminals running") and its helpers: a few words, or "".
    PROVIDER: Claude Code's and Codex's footer wording."""
    found = [f"{len(helping)} helper{'s' if len(helping) > 1 else ''}"] if helping else []
    for line in lines:
        for n, what in re.findall(r"\b(\d+) (shells?|background tasks?|background terminals?|local agents?|running tasks?)\b", line):
            found.append(f"{n} {what}")
    return ", ".join(dict.fromkeys(found)) + " running" if found else ""


def behind(snap):
    """What runs behind a console, off its footer and its helpers' rows, in a few words, or ""."""
    return running((snap.get("lines") or []) + [(snap.get("activity") or {}).get("foot") or ""], helpers(snap))


def background(path):
    """What the agent started in the background, and how each ended: [{id, kind, what, at, state, ended, exit}], oldest
    first. state is running until its notice comes (completed, failed, killed). PROVIDER: Claude Code's transcript:
    a tool run with run_in_background, and the <task-notification> that names its tool-use-id when it stops."""
    started, notices = {}, {}
    for e in _tail(path) if path and Path(path).exists() else []:
        at = e.get("timestamp", "")
        content = (e.get("message") or {}).get("content")
        if e.get("type") in ("assistant", "user") and isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    inp = c.get("input") or {}
                    started[c.get("id")] = {"id": c.get("id"), "kind": {"Bash": "Shell", "Agent": "Helper"}.get(c.get("name"), c.get("name", "Task")),
                                            "what": (inp.get("description") or inp.get("command") or inp.get("prompt") or "").strip(),
                                            "at": at, "bg": bool(inp.get("run_in_background"))}
                elif isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in started:
                    said = c.get("content")
                    said = said if isinstance(said, str) else " ".join(x.get("text", "") for x in said or [] if isinstance(x, dict))
                    if said.startswith("Async agent launched"):          # a helper is in the background by default
                        started[c["tool_use_id"]]["bg"] = True
        texts = [e.get("content")] if e.get("type") == "queue-operation" else \
            [(e.get("attachment") or {}).get("prompt")] if e.get("type") == "attachment" else \
            [content] if isinstance(content, str) else []
        for text in texts:
            if isinstance(text, str) and "<task-notification>" in text:
                use = re.search(r"<tool-use-id>([^<]+)</tool-use-id>", text)
                state = re.search(r"<status>([^<]+)</status>", text)
                code = re.search(r"exit code (\d+)", text)
                if use and use.group(1) not in notices:
                    notices[use.group(1)] = {"state": state.group(1) if state else "completed", "ended": at,
                                             "exit": int(code.group(1)) if code else None}
    out = []
    for uid, s in started.items():
        if s["bg"] or uid in notices:
            n = notices.get(uid) or {"state": "running", "ended": "", "exit": None}
            out.append(dict(s, **n))
    return out



def codex_background(path):
    """What a Codex agent left running in the background, and how each ended, in background()'s form. PROVIDER: Codex's
    rollout, as colony-codex captured it on 0.165. Its end is what counts: the structured item_completed
    CommandExecution, with the command, its outcome and how long it ran, read whether or not its start was seen (a turn
    interrupted before the cell printed anything). A start shows one still running: a tool result (from a code cell, a
    later wait on that cell, or a direct exec_command call) giving a session id and no exit code, the session id being
    the end's process_id. A command that ended without its start being seen is shown when it ran LONG or more: Codex
    gives no sign of which of its commands were left to run, and one that long was work worth seeing."""
    calls, cells, started, ended = {}, {}, {}, {}

    def results(out):                                 # the tool results a call returned, never the commands' own output
        blocks = [b.get("text", "") for b in out if isinstance(b, dict)] if isinstance(out, list) else [str(out or "")]
        for b in blocks:
            try:
                r = json.loads(b)
            except ValueError:
                continue
            for x in (r if isinstance(r, list) else [r]):
                x = x.get("value", x) if isinstance(x, dict) and "status" in x and "value" in x else x   # Promise.allSettled
                if isinstance(x, dict) and ("chunk_id" in x or "wall_time_seconds" in x):
                    yield x

    for e in _tail(path) if path and Path(path).exists() else []:
        at, pl = e.get("timestamp", ""), e.get("payload") or {}
        kind = pl.get("type")
        if kind in ("custom_tool_call", "function_call"):
            said = pl.get("input") or pl.get("arguments") or ""
            if pl.get("name") == "exec_command":          # a direct call: its name says it, its arguments don't
                said = "exec_command " + said
            if pl.get("name") == "wait":                  # waiting on a cell that went on running: that cell's call
                m = re.search(r'"cell_id"\s*:\s*"?(\w+)', said)
                said, at = cells.get(m.group(1), (at, ""))[::-1] if m else (said, at)
            calls[pl.get("call_id")] = (at, said)
        elif kind in ("custom_tool_call_output", "function_call_output") and pl.get("call_id") in calls:
            when, said = calls[pl["call_id"]]
            out = pl.get("output")
            m = re.match(r"Script running with cell ID (\w+)", out if isinstance(out, str) else
                         next((b.get("text", "") for b in out or [] if isinstance(b, dict)), ""))
            if m:
                cells[m.group(1)] = (when, said)
            # each command this call started, in order, against each session it returned that isn't one it polled
            cmds = [] if "exec_command" not in said else re.findall(r'exec_command\s*\(\s*\{[^}]*?\bcmd"?\s*:\s*("(?:[^"\\]|\\.)*")', said) \
                or re.findall(r'\bcmd"?\s*:\s*("(?:[^"\\]|\\.)*")', said)
            polled = set(re.findall(r"write_stdin\s*\(\s*\{[^}]*?session_id\s*:\s*(\d+)", said))
            fresh = [str(r["session_id"]) for r in results(out) if r.get("session_id") is not None and r.get("exit_code") is None
                     and str(r["session_id"]) not in polled and str(r["session_id"]) not in started]
            starts = said.count("exec_command")
            for n, sid in enumerate(fresh[:starts]):
                try:
                    what = json.loads(cmds[n]) if len(cmds) == starts else ""
                except ValueError:
                    what = ""
                started[sid] = {"id": "codex-" + sid, "kind": "Shell", "what": what.strip(), "at": when, "bg": True}
        elif kind == "item_completed" and (pl.get("item") or {}).get("type") == "CommandExecution":
            item = pl["item"]
            sid = str(item.get("process_id") or "")
            took = (item.get("duration") or {}).get("secs", 0)
            cmd = item.get("command") or []
            began = pl.get("started_at_ms")
            ended.setdefault(sid, {"state": "completed" if item.get("status") == "completed" else "failed", "ended": at,
                                   "exit": item.get("exit_code"), "what": (cmd[-1] if cmd else "").strip(), "took": took,
                                   "began": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(began / 1000)) if began else at})
    out = []
    for sid in dict.fromkeys([*started, *ended]):
        s, n = started.get(sid), ended.get(sid)
        if s is None and (n["took"] < LONG):
            continue                                       # ended at once, its start unseen: not left running
        row = dict(s or {"id": "codex-" + sid, "kind": "Shell", "what": "", "at": n["began"], "bg": True})
        if n:
            row.update(state=n["state"], ended=n["ended"], exit=n["exit"], what=n["what"] or row["what"])
        else:
            row.update(state="running", ended="", exit=None)
        out.append(row)
    return sorted(out, key=lambda r: r["at"])


LONG = 30                     # seconds: a command that ran this long, its start unseen, is shown as one left running