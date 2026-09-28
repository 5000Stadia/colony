"""Who runs a project's agent. Each provider says how to start its CLI, how to wire a project so the agent
knows the colony and gets its notes and mail, and how to read its screen. Only Claude Code is here; another
CLI joins by adding an entry to PROVIDERS, and the board, settings and forms offer it from then on.

PROVIDER: for an agent adding another CLI (Codex or any other). Everything outside this file that still
assumes Claude Code is marked with a `PROVIDER:` comment; `grep -rn "PROVIDER:" .` lists them, each saying
what it assumes and what a second provider needs there. What a provider supplies here:
  label, models, efforts   shown in the add/create forms and settings (suggestions; any value can be typed);
                           models as (id, name) pairs
  model_name(value)        a model ID or alias as the model's own name, for showing "Default (Opus 5.5)"
  command(label, s)        the shell command that starts its interactive agent with the settings in `s`:
                           provider, model, effort, permissions (ask|edits|all|plan) and remote (on|off).
                           Map each to the CLI's own flags, and ignore any it has no equivalent for.
  wire(root, protocol)     put the colony protocol where the CLI reads project instructions (AGENTS.md for
                           Codex) and arrange for `colony notes --deliver` output to reach the agent: at
                           session start (`--session`) and before each turn the person or the board types.
                           Without a hook system, the protocol can ask the agent to run it itself, weaker.
  wired(root)              whether wire() has been done (the doctor asks)
  own_defaults()           the model and effort the CLI uses when colony names none, or None where it
                           decides itself; the forms show them as "Default (...)"
  history_text(root)       the session's conversation as plain text, for Select on a phone (or None)
  active_seconds(root)     how long its agent has spent at work in the project, all told: turns only, not the
                           time it sat waiting for the person (None if it can't tell)
  activity(screen)         what it's doing now, off its screen: {"line": the current activity or None,
                           "agents": [{"name", "detail", "current"}]}, shown as the project's status
  scrolled_marker          text on screen while the view is scrolled up from the latest ("" if none); the
                           phone console's ↓ shows while it's there
  turn_text(payload)       what the agent wrote in the turn that just ended, and a key for that turn, so a
                           turn that asks the person something shows under "Waiting on you"
  classify(screen)         "working" | "needs you" | "idle" from its terminal screen; the watcher and the
                           monitor's wake-ups depend on this, so match the CLI's own busy and prompt markers.
  choice(screen)           the choice on screen (a trust question, a permission prompt): its question, its
                           options and which is highlighted, or None; the board shows it as buttons
  choose(screen, text)     the keys that pick the option matching `text`; `colony choose` and the buttons use it
  draft(screen)            what the person has half-typed in its input and not sent, from a screen captured with
                           its styles; "" if nothing, None if it can't tell. Nothing is typed into a session while
                           there is one: it would land on the draft and send it.
The colony's own mechanisms (notes, gates, mail, the roadmap, the watcher) are provider-agnostic: files in
.board/, the `colony` command, and text typed into a tmux session. Keep new ones that way.
"""
import json
import shlex
from pathlib import Path


class ClaudeCode:
    label = "Claude Code"
    # Exact models by full ID, so a project keeps the model it was given; an alias ("opus") moves to whatever
    # is newest. PROVIDER: Claude Code's current models; add new ones here as they ship.
    models = [("claude-fable-5-1", "Fable 5.1"), ("claude-opus-5-5", "Opus 5.5"), ("claude-sonnet-5", "Sonnet 5"),
              ("claude-haiku-4-5-20251001", "Haiku 4.5")]
    aliases = {"fable": "Fable 5.1", "opus": "Opus 5.5", "sonnet": "Sonnet 5", "haiku": "Haiku 4.5"}
    efforts = ["low", "medium", "high", "xhigh", "max"]
    # Claude Code runs these and puts what they print in the agent's context: delivery needs no memory.
    # SessionStart gives the backlog at start; UserPromptSubmit gives what is new before every turn.
    # Stop runs when a turn ends: `colony turn` records the turn if it asks the person something.
    # Shown on screen while its view is scrolled up from the latest; the console's ↓ follows it.
    scrolled_marker = "Jump to bottom"
    hooks = {"SessionStart": "colony notes --deliver --session", "UserPromptSubmit": "colony notes --deliver",
             "Stop": "colony turn"}

    def command(self, label, s, resume=None):
        """The person's own `claude` with the project's choices: permissions, Remote Control, model, effort;
        with resume, back in that conversation."""
        from .board import PERMISSIONS
        parts = ["claude"] + (["--resume", shlex.quote(resume)] if resume else [])
        if PERMISSIONS.get(s.get("permissions") or "ask"):
            parts += ["--permission-mode", PERMISSIONS[s["permissions"]]]
        if s.get("remote"):
            parts += ["--remote-control", shlex.quote(label)]
        if s.get("model"):
            parts += ["--model", shlex.quote(s["model"])]
        if s.get("effort"):
            parts += ["--effort", shlex.quote(s["effort"])]
        return " ".join(parts)

    def wire(self, root, protocol):
        """The colony protocol in CLAUDE.md (an older block brought up to date in place) and the delivery hooks
        in .claude/settings.json, added to whatever the project already has."""
        claude_md = root / "CLAUDE.md"
        have = claude_md.read_text() if claude_md.exists() else ""
        block = protocol.lstrip("\n")
        marker = next((m for m in ("## This project is part of a colony", "## The board") if m in have), None)
        if marker:
            start = have.index(marker)
            end = have.find("\n## ", start + 5)
            claude_md.write_text(have[:start] + block + (have[end + 1:] if end != -1 else ""))
        else:
            claude_md.write_text(have + ("\n" if have and not have.endswith("\n") else "") + block)
        settings = root / ".claude" / "settings.json"
        settings.parent.mkdir(exist_ok=True)
        cfg = json.loads(settings.read_text()) if settings.exists() else {}
        for event, command in self.hooks.items():
            entries = cfg.setdefault("hooks", {}).setdefault(event, [])
            if not any(h.get("command") == command for e in entries for h in e.get("hooks", [])):
                entries.append({"hooks": [{"type": "command", "command": command}]})
        settings.write_text(json.dumps(cfg, indent=2) + "\n")

    def conversation(self, payload):
        """Which conversation a hook ran in, and where it is kept: for a console that must be restarted."""
        return payload.get("session_id"), payload.get("transcript_path")

    def model_name(self, value):
        """The model a value means, by name: an ID or alias; anything else as typed."""
        return dict(self.models).get(value) or self.aliases.get(str(value).lower()) or value

    def turn_text(self, payload):
        """What the agent wrote in the turn that just ended, from the Stop hook's payload: every piece of prose
        since the person's last message, and a key naming that turn. PROVIDER: reads Claude Code's own
        transcript file; another CLI reports its turn however it can, as the same (key, text)."""
        path = payload.get("transcript_path")
        if not path or not Path(path).exists():
            return None, ""
        prose, key = [], None
        for line in Path(path).read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            m = e.get("message") or {}
            content = m.get("content")
            if e.get("type") == "user" and (isinstance(content, str) or any(
                    isinstance(c, dict) and c.get("type") == "text" for c in content or [])):
                prose, key = [], None                          # the person spoke: a new turn starts
            elif e.get("type") == "assistant" and isinstance(content, list):
                text = "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text").strip()
                if text:
                    prose.append(text)
                    key = e.get("uuid") or key
        return key, "\n\n".join(prose)

    def active_seconds(self, root):
        """Time at work in this folder, all told: Claude Code closes each turn with its length (turn_duration) in
        the transcript; a turn still open counts from its prompt to the latest thing written in it. Transcripts
        only grow, so each is read on from where the last look stopped. PROVIDER: reads Claude Code's own
        transcripts."""
        import re
        from datetime import datetime
        stamp = lambda e: datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
        folder = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9-]", "-", str(Path(root).resolve()))
        total = 0.0
        for f in folder.glob("*.jsonl"):
            at, ms, opened, last = self._turns.get(f, (0, 0, None, None))
            size = f.stat().st_size
            if size < at:
                at, ms, opened, last = 0, 0, None, None           # rewritten: read it afresh
            if size > at:
                with f.open("rb") as fh:
                    fh.seek(at)
                    chunk = fh.read()
                whole = chunk[:chunk.rfind(b"\n") + 1]               # a line still being written waits
                for line in whole.splitlines():
                    try:
                        if b'"turn_duration"' in line:
                            ms, opened = ms + json.loads(line).get("durationMs", 0), None
                        elif opened is None and re.search(rb'"type":\s*"user"', line):
                            opened = stamp(json.loads(line))          # a turn begins
                        if opened is not None and b'"timestamp"' in line:
                            last = line                               # parsed once, below
                    except (ValueError, KeyError):
                        pass
                if isinstance(last, bytes):
                    try:
                        last = stamp(json.loads(last))
                    except (ValueError, KeyError):
                        last = None
                at += len(whole)
                self._turns[f] = (at, ms, opened, last)
            total += ms / 1000 + (max(0, last - opened) if opened and last else 0)
        return total

    _turns = {}                                   # transcript -> (bytes read, ms of turns ended, open turn's start, its latest)

    def history_text(self, root, limit=200_000):
        """The session's conversation as plain text, for reading and copying on a phone: the person's
        messages, the agent's replies, one line per tool it ran. Claude Code draws in the alternate screen,
        so the terminal keeps no history of its own. PROVIDER: reads the newest transcript Claude Code keeps
        for this folder; None if there is none (the board then shows the screen)."""
        import re
        folder = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9-]", "-", str(Path(root).resolve()))
        files = sorted(folder.glob("*.jsonl"), key=lambda f: f.stat().st_mtime)
        if not files:
            return None
        out = []
        for line in files[-1].read_text(errors="replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("isMeta"):
                continue
            content = (e.get("message") or {}).get("content")
            parts = [{"type": "text", "text": content}] if isinstance(content, str) else content or []
            for c in parts:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text" and c.get("text", "").strip() and not c["text"].startswith("<"):
                    out.append(("❯ " if e.get("type") == "user" else "") + c["text"].strip())
                elif c.get("type") == "tool_use":
                    arg = next((str(v) for v in (c.get("input") or {}).values() if isinstance(v, str)), "")
                    out.append(f"● {c.get('name')}({arg.splitlines()[0][:100] if arg else ''})")
        text = "\n\n".join(out)
        return text[-limit:] if text else None

    def own_defaults(self):
        """What Claude Code uses when colony names nothing: its own settings file, where the person may have
        pinned a model or effort. None where it's left to Claude Code, which picks by plan and version and
        records the choice nowhere stable."""
        path = Path.home() / ".claude" / "settings.json"
        try:
            s = json.loads(path.read_text())
        except (OSError, ValueError):
            s = {}
        return {"model": s.get("model") or None, "effort": s.get("effortLevel") or None}

    def wired(self, root):
        claude_md = root / "CLAUDE.md"
        settings = root / ".claude" / "settings.json"
        hooks = json.dumps(json.loads(settings.read_text()).get("hooks", {})) if settings.exists() else ""
        return ("## This project is part of a colony" in (claude_md.read_text() if claude_md.exists() else "")
                and all(c in hooks for c in self.hooks.values()))

    def classify(self, screen):
        """Claude Code shows "esc to interrupt" while it works and a numbered choice when it asks permission;
        anything else is waiting for the person to type. On a narrow window (a phone attached) the status line
        is cut short, so its spinner counts too: "✻ Calculating…" while working, "✻ Worked for 3s" once done."""
        import re
        low = screen.lower()
        if "esc to interrupt" in low or re.search(r"^\s*[✻✶✳✢✽·*+] \S[^\n]*?…", screen, re.M) \
                or re.search(r"waiting for \d+ background", low):     # its background agents are still at work
            return "working"
        # A choice takes the typing box's place. While the box is there, "Do you want…" above it is the agent's
        # own prose, a question for the person to answer by typing, so only what's below the box counts.
        lines = screen.splitlines()
        box = self._box(lines)
        low = "\n".join(lines[box + 1:] if box is not None else lines).lower()
        if any(k in low for k in ("do you want", "❯ 1.", "trust this folder", "yes, proceed")):
            return "needs you"
        return "idle"

    @staticmethod
    def _box(lines):
        """The line its typing box starts on (`❯` just under a rule), or None while a choice has taken its place."""
        import re
        rule = lambda l: len(l.strip()) > 8 and set(l.strip()) <= set("─━")
        return next((i for i in range(len(lines) - 1, 0, -1)
                     if re.match(r"^\s*❯(?!\s*\d+\.)", lines[i]) and rule(lines[i - 1])), None)

    def draft(self, screen):
        """What is typed in its box and not yet sent, read off a screen captured with its styles (tmux -e); ""
        when the box is empty (the greyed hint a fresh session shows there is dim, not a draft); None with no box."""
        import re
        plain = lambda l: re.sub(r"\x1b\[[0-9;]*m", "", l)
        lines = screen.splitlines()
        box = self._box([plain(l) for l in lines])
        if box is None:
            return None
        out = []
        for l in lines[box:]:
            if out and len(plain(l).strip()) > 8 and set(plain(l).strip()) <= set("─━"):
                break                                                    # the rule under the box
            out.append(plain(re.sub(r"\x1b\[2m.*?(\x1b\[(0|22)?m|$)", "", l)))
        return "\n".join(out).strip().lstrip("❯").strip()

    def activity(self, screen):
        """What the session is doing, as Claude Code shows it: its spinner line ("✻ Waiting for 4 background
        agents to finish", "✻ Worked for 3s") and its background agents with their time and tokens."""
        import re
        line, agents = None, []
        for l in screen.splitlines():
            if m := re.match(r"^\s*[✻✶✳✢✽·*+]\s+(\S.*?)\s*$", l):
                line = m.group(1)
            elif m := re.match(r"^\s*([●○◯◉])\s+([\w.:-]+)(?:\s{2,}(\S.*?))?\s*$", l):
                agents.append({"name": m.group(2), "detail": m.group(3) or "", "current": m.group(1) in "●◉"})
        return {"line": line, "agents": agents if len(agents) > 1 else []}

    def choice(self, screen):
        """The choice on screen, if any: (question lines, options, index of the highlighted one). Claude Code
        draws a choice as a column of options, the highlighted one marked `❯`, under its question."""
        import re
        lines = screen.splitlines()
        cur = next((i for i, l in enumerate(lines) if re.match(r"^\s*❯ \S", l)), None)
        if cur is None:
            return None
        col = lines[cur].index("❯") + 2
        is_option = lambda l: len(l) > col and l[col] != " " and l[:col].strip() in ("", "❯")
        i = cur
        while i > 0 and (is_option(lines[i - 1]) or lines[i - 1][:col + 1].strip() == ""):
            i -= 1                                                       # up to the first option
        block = []
        for l in lines[i:]:
            if is_option(l):
                block.append(l)
            elif l[:col + 1].strip():                                    # shallower text: the list has ended
                break
        if len(block) < 2:
            return None                                                  # the input prompt, not a choice
        label = lambda l: re.sub(r"^\d+\.\s*", "", l[col:].strip())
        question = [l.strip() for l in lines[max(0, i - 8):i] if l.strip()][-4:]
        here = next(k for k, l in enumerate(block) if l.lstrip().startswith("❯"))
        return question, [label(l) for l in block], here

    def choose(self, screen, text):
        """The keys that pick the on-screen option containing `text`: arrows to it, then Enter; or None."""
        found = self.choice(screen)
        if not found:
            return None
        _, options, here = found
        want = text.lower().strip()
        hit = next((k for k, o in enumerate(options) if want in o.lower()), None)
        if hit is None:
            return None
        return (["Down"] * (hit - here) if hit > here else ["Up"] * (here - hit)) + ["Enter"]

PROVIDERS = {"claude": ClaudeCode()}
DEFAULT = "claude"


def get(name):
    return PROVIDERS.get(name or DEFAULT, PROVIDERS[DEFAULT])


def of(root):
    """The provider a project's settings name, else the global one."""
    from . import board
    return get(board.project_settings(root)[0].get("provider") if root else board.registry()["settings"]["provider"])
