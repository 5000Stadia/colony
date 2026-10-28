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


def _tail(path, keep, limit=TAIL):
    """The transcript's last entries, whole lines only, each as `keep` leaves it (what its program's readers read of
    it; None: not kept). Read once, then only what was added since (a page asks every few seconds); a transcript
    started again, or rewritten, is read afresh. Kept for every project for as long as the board runs, so kept lean,
    and read a line at a time: whole entries, 40 MB at a time, held over half a GB and took the board to 4 GB as every
    console reloaded at once (2026-10-28)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    with _lock:
        c, rest = _seen.get(str(path)), b""
        try:
            with open(path, "rb") as fh:
                if c and c["keep"] is keep and size >= c["end"] and c["end"] >= len(c["mark"]):
                    fh.seek(c["end"] - len(c["mark"]))
                    if fh.read(len(c["mark"])) != c["mark"]:
                        c = None                      # not the file it was: read it afresh
                else:
                    c = None
                if not c:
                    c = {"end": max(0, size - limit), "mark": b"", "entries": [], "keep": keep}
                    fh.seek(c["end"])
                    if c["end"]:
                        first = fh.readline()         # the first is cut mid-line
                        c["end"] += len(first) if first.endswith(b"\n") else 0
                fh.seek(c["end"])
                for line in fh:
                    if not line.endswith(b"\n"):
                        rest = line.strip()           # still being written: read, not kept, till its line ends
                        break
                    c["end"] += len(line)
                    c["mark"] = line[-64:]
                    if len(line) > BIG:
                        continue
                    try:
                        e = keep(json.loads(line))
                    except (ValueError, AttributeError, TypeError):
                        continue
                    if e is not None:
                        c["entries"].append(e)
                        if len(c["entries"]) > 2 * KEEP:
                            del c["entries"][:-KEEP]
        except OSError:
            return []
        del c["entries"][:-KEEP]
        _seen[str(path)] = c
        try:
            e = keep(json.loads(rest)) if rest and len(rest) <= BIG else None
        except (ValueError, AttributeError, TypeError):
            e = None
        return list(c["entries"]) + ([e] if e is not None else [])


def forget(path):
    """A file no longer read (the line's copy of the lines a transcript gained, read once): what was kept of it goes."""
    with _lock:
        _seen.pop(str(path), None)


SHORT = 400                # a tool's argument, kept this long: a step's line, a task's name


def _claude_keep(e):
    """PROVIDER: what of a Claude Code transcript entry claude_events and background read: the person's and agent's
    words, each tool's name, id and short arguments, the start of its result (a helper "Async agent launched"),
    queued and attached messages, and the flags they check. Not its request records, file snapshots, pictures, tool
    output or hidden thinking (only "updates" thinking is shown)."""
    kind = e.get("type")
    if kind not in ("user", "assistant", "queue-operation", "attachment") and not e.get("isCompactSummary"):
        return None
    out = {k: e[k] for k in ("type", "timestamp", "isMeta", "isSidechain", "isCompactSummary", "thinkingDisplay",
                             "operation", "content") if k in e}
    if kind == "attachment":
        a = e.get("attachment") or {}
        out["attachment"] = {k: a[k] for k in ("type", "prompt") if k in a}
    content = (e.get("message") or {}).get("content")
    if isinstance(content, list):
        kept = []
        for c in content:
            if not isinstance(c, dict):
                continue
            t = c.get("type")
            if t == "text":
                kept.append(c)
            elif t == "thinking":
                kept.append({"type": t, "thinking": c.get("thinking", "")} if e.get("thinkingDisplay") == "updates"
                            else {"type": t})
            elif t == "tool_use":
                inp = c.get("input") if isinstance(c.get("input"), dict) else {}
                kept.append({"type": t, "id": c.get("id"), "name": c.get("name"), "input": {
                    k: v[:SHORT] if isinstance(v, str) else v for k, v in inp.items() if isinstance(v, (str, bool, int, float))}})
            elif t == "tool_result":
                said = c.get("content")
                said = said if isinstance(said, str) else " ".join(x.get("text", "") for x in said or [] if isinstance(x, dict))
                kept.append({"type": t, "tool_use_id": c.get("tool_use_id"), "content": said[:64]})
        content = kept
    if "message" in e:
        out["message"] = {"content": content}
    return out


CODEX_READ = {"message", "custom_tool_call", "function_call", "custom_tool_call_output", "function_call_output",
              "agent_message", "item_completed"}


def _codex_keep(e):
    """PROVIDER: what of a Codex rollout entry codex_events and codex_background read: a compaction, the messages,
    tool calls and their results, helpers' reports and finished commands (without their output). Not its reasoning,
    token counts or the rest."""
    p = e.get("payload") if isinstance(e.get("payload"), dict) else {}
    if e.get("type") == "compacted":
        return {"type": "compacted", "timestamp": e.get("timestamp", "")}
    if p.get("type") not in CODEX_READ:
        return None
    if p["type"] == "item_completed":
        item = p.get("item") or {}
        p = {"type": p["type"], "started_at_ms": p.get("started_at_ms"), "item": {
            k: item[k] for k in ("type", "process_id", "duration", "command", "status", "exit_code") if k in item}}
    return {"type": e.get("type"), "timestamp": e.get("timestamp", ""), "payload": p}


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
    for e in _tail(path, _claude_keep):
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
    for e in _tail(path, _codex_keep):
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
    """A conversation as its own program records it (Provider.events); none from a program colony doesn't know."""
    from . import providers
    if not path or not Path(path).exists() or provider_key not in providers.PROVIDERS:
        return []
    return providers.PROVIDERS[provider_key].events(path)


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
    for e in _tail(path, _claude_keep) if path and Path(path).exists() else []:
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

    for e in _tail(path, _codex_keep) if path and Path(path).exists() else []:
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
            # the tools it called, in order, against the results it returned, in order: a session from an exec_command
            # is one it started, named by that call's own cmd; where the two don't pair up one for one, sessions it
            # didn't poll are still started, unnamed (colony-codex's review: a guess would name one by another's command)
            tools = ([("exec_command", said[len("exec_command "):])] if said.startswith("exec_command {") else
                     re.findall(r"\b(exec_command|write_stdin)\s*\(\s*(\{[^}]*\})?", said))
            res = [r for r in results(out)]
            polled = set(re.findall(r"""write_stdin\s*\(\s*\{[^}]*?["']?session_id["']?\s*:\s*(\d+)""", said))
            live = lambda r: r.get("session_id") is not None and r.get("exit_code") is None and str(r["session_id"]) not in started

            def named(args):
                m = re.search(r"""["']?\bcmd["']?\s*:\s*("(?:[^"\\]|\\.)*")""", args or "")
                try:
                    return json.loads(m.group(1)).strip() if m else ""
                except ValueError:
                    return ""
            if tools and len(tools) == len(res):
                pairs = [(named(a), r) for (t, a), r in zip(tools, res) if t == "exec_command" and live(r)]
            else:
                execs = [a for t, a in tools if t == "exec_command"]
                fresh = [r for r in res if live(r) and str(r["session_id"]) not in polled][:len(execs)]
                pairs = [(named(execs[0]) if len(execs) == len(fresh) == 1 else "", r) for r in fresh]
            for what, r in pairs:
                sid = str(r["session_id"])
                started[sid] = {"id": "codex-" + sid, "kind": "Shell", "what": what, "at": when, "bg": True}
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