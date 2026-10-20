"""Chat: a console's conversation as the person and the agent had it, each turn's work folded under it. Read from the
transcript the session's hooks recorded, so it costs nothing and the console itself is untouched; what the person sends
goes into the console as Message does."""
import json
import re
from pathlib import Path

TAIL = 3_000_000           # bytes from the transcript's end: plenty of turns, never a whole long session


def _tail(path, limit=TAIL):
    """The transcript's last lines, whole ones only."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - limit))
            chunk = fh.read()
    except OSError:
        return []
    lines = chunk.split(b"\n")
    if size > limit:
        lines = lines[1:]                             # the first is cut mid-line
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


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
