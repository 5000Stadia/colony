"""Chat: a console's conversation as the person and the agent had it, each turn's work folded under it. Read from the
transcript the session's hooks recorded, so it costs nothing and the console itself is untouched; what the person sends
goes into the console as Message does."""
import json
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
            if e.get("operation") == "enqueue" and isinstance(e.get("content"), str) and not e["content"].lstrip().startswith("<"):
                queued.append(e["content"])
            elif e.get("operation") == "remove" and e.get("content") in queued:
                queued.remove(e["content"])
            elif e.get("operation") == "dequeue" and queued:
                queued.pop(0)                             # the oldest, taken up as the next turn
            elif e.get("operation") == "popAll":
                queued.clear()                            # taken back into the box (to edit, or reorder)
            continue
        if e.get("type") == "attachment":
            a = e.get("attachment") or {}
            if a.get("type") == "queued_command" and isinstance(a.get("prompt"), str) and a["prompt"].strip() \
                    and not a["prompt"].lstrip().startswith("<"):
                out.append((_who(a["prompt"]), a["prompt"].strip(), at))
            continue
        if e.get("isCompactSummary"):
            out.append(("compact", "", at))
            continue
        if e.get("type") == "user":
            text = content if isinstance(content, str) else "\n".join(
                c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")
            text = text.strip()
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
                elif c.get("type") == "tool_use":
                    inp = c.get("input") or {}
                    arg = inp.get("description") or inp.get("command") or inp.get("file_path") or inp.get("pattern") \
                        or inp.get("prompt") or next((v for v in inp.values() if isinstance(v, str)), "")
                    out.append(("step", _step(c.get("name", "tool"), arg), at))
    return out + [("queued", q.strip(), "") for q in queued if q.strip()]


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
                             if isinstance(c, dict) and c.get("type") in ("input_text", "output_text")).strip()
            if not text:
                continue
            if p["role"] == "assistant":
                out.append(("say", text, at))
            elif not text.startswith("<") and not text.startswith("# AGENTS.md"):
                out.append((_who(text), text, at))
        elif kind in ("custom_tool_call", "function_call"):
            raw = p.get("input") or p.get("arguments") or ""
            cmd = re.search(r'cmd:\s*"((?:[^"\\]|\\.)*)"', raw) or re.search(r'"cmd"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
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


def running(lines):
    """What runs in the background while the agent waits, off its console's footer (Claude Code: "· 1 shell",
    "2 background tasks", "1 local agent"): a few words, or "". PROVIDER: Claude Code's footer wording."""
    found = []
    for line in lines:
        for n, what in re.findall(r"\b(\d+) (shells?|background tasks?|local agents?|running tasks?)\b", line):
            found.append(f"{n} {what}")
    return ", ".join(dict.fromkeys(found)) + " running" if found else ""


def background(path):
    """What the agent started in the background, and how each ended: [{id, kind, what, at, state, ended, exit}], oldest
    first. state is running until its notice comes (completed, failed, killed). PROVIDER: Claude Code's transcript:
    a tool run with run_in_background, and the <task-notification> that names its tool-use-id when it stops."""
    started, notices = {}, {}
    for e in _tail(path) if path and Path(path).exists() else []:
        at = e.get("timestamp", "")
        content = (e.get("message") or {}).get("content")
        if e.get("type") == "assistant" and isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    inp = c.get("input") or {}
                    started[c.get("id")] = {"id": c.get("id"), "kind": "Shell" if c.get("name") == "Bash" else c.get("name", "Task"),
                                            "what": (inp.get("description") or inp.get("command") or inp.get("prompt") or "").strip(),
                                            "at": at, "bg": bool(inp.get("run_in_background"))}
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
