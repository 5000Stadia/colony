"""Who runs a project's agent. Each provider says how to start its CLI, how to wire a project so the agent
knows the colony and gets its notes and mail, and how to read its screen. Only Claude Code is here; another
CLI joins by adding an entry to PROVIDERS, and the board, settings and forms offer it from then on.

PROVIDER: for an agent adding another CLI (Codex or any other). Everything outside this file that still
assumes Claude Code is marked with a `PROVIDER:` comment; `grep -rn "PROVIDER:" .` lists them, each saying
what it assumes and what a second provider needs there. What a provider supplies here:
  program, site            the program it runs and where to get it; colony checks it is installed before
                           offering the provider, starting a console, or making it the default
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
  instructions             the file in a project's folder it reads instructions from; two projects sharing a
                           folder must read different ones
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
  choose(screen, text)     the keys that pick the option matching `text`; `colony choose` and the buttons use it;
                           with choice(), it also lets the watcher answer a session's start-up questions (starting())
  enter_after              seconds to wait between typing a message and pressing Enter (optional; 0 if not set)
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

    @staticmethod
    def config_home():
        """Where Claude Code keeps its files: CLAUDE_CONFIG_DIR if set, else .claude in the home folder (on Linux,
        macOS and Windows alike)."""
        import os
        return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    program, site = "claude", "https://claude.com/claude-code"     # its program, and where to get it
    # Exact models by full ID, so a project keeps the model it was given; an alias ("opus") moves to whatever
    # is newest. The models colony offers are discovered (discover()); these are the ones it knows by name, and
    # are confirmed with the rest.
    models = [("claude-fable-5-1", "Fable 5.1"), ("claude-opus-5-5", "Opus 5.5"), ("claude-sonnet-5", "Sonnet 5"),
              ("claude-haiku-4-5-20251001", "Haiku 4.5")]
    aliases = {"fable": "Fable 5.1", "opus": "Opus 5.5", "sonnet": "Sonnet 5", "haiku": "Haiku 4.5"}
    efforts = ["low", "medium", "high", "xhigh", "max"]
    mark = "❯"                                  # how it marks the highlighted option of a choice
    instructions = "CLAUDE.md"                  # the file it reads a project's instructions from
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

    def version(self):
        return _run([self.program, "--version"])

    def catalog(self):
        """The model menu Claude Code keeps for this account (~/.claude/cache/model-catalog/*-cc.json), refreshed
        by Claude Code itself: each model with its effort levels. Its main section only; older models it keeps
        under "more models" are left out. None if there's no such file or it can't be read. PROVIDER: Claude
        Code's own cache, undocumented; discover() falls back to asking when it isn't there."""
        files = sorted((self.config_home() / "cache" / "model-catalog").glob("*-cc.json"),
                       key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files:
            try:
                models = json.loads(f.read_text())["catalog"]["config"]["models"]
                return [(m["id"], m.get("name") or _label(m["id"]),
                         [o["id"] for o in ((m.get("thinking") or {}).get("effort_options") or [])])
                        for m in models if m.get("section", "main") == "main" and m.get("id")]
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None

    def discover(self, run=None):
        """The models this account can run: Claude Code's own model menu when it's there (free, exact). Otherwise
        it's asked once for the IDs it knows, the ones colony knows are added, and each is started with a trivial
        request whose report names the model that answered; only those confirmed are kept (a few tiny calls)."""
        import re
        import tempfile
        if run is None and (menu := self.catalog()):
            return menu
        run = run or _run
        here = tempfile.mkdtemp(prefix="colony-models-")      # no project's settings or hooks
        base = [self.program, "-p", "--setting-sources", ""]
        said = run(base + ["List the exact model IDs that Claude Code can be started with through --model on this "
                           "installation, one per line, nothing else."], cwd=here)
        asked = re.findall(r"\bclaude-[a-z0-9][a-z0-9.-]*[a-z0-9]\b", said or "")
        found = []
        for mid in dict.fromkeys(asked + [m for m, _ in self.models]):
            out = run(base + ["--model", mid, "--output-format", "json", "Reply with the single word: ok"], cwd=here)
            try:
                used = list((json.loads(out).get("modelUsage") or {}).keys())
            except (ValueError, TypeError, AttributeError):
                used = []
            if any(u == mid or u.startswith(mid) for u in used):
                found.append((mid, self.model_name(mid) if mid in dict(self.models) else _label(mid),
                              [e for e in self.efforts]))
        return found

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
        from .board import workdir
        folder = self.config_home() / "projects" / re.sub(r"[^A-Za-z0-9-]", "-", str(workdir(root).resolve()))
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
        from .board import workdir
        folder = self.config_home() / "projects" / re.sub(r"[^A-Za-z0-9-]", "-", str(workdir(root).resolve()))
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
        path = self.config_home() / "settings.json"
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
        # a rule is a line of ─ (Claude Code may label it with the session's name: "──── Holo-emitter ─")
        rule = lambda l: bool(re.match(r"^\s*[─━]{4,}", l)) and l.count("─") + l.count("━") >= len(l.strip()) / 2
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
            if out and re.match(r"^\s*[─━]{4,}", plain(l)):
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
        lines, mark = screen.splitlines(), self.mark
        # the choice on screen is the lowest one, and a numbered option beats an earlier prompt in the history
        marked = [i for i, l in enumerate(lines) if re.match(rf"^\s*{mark} \S", l)]
        cur = next((i for i in reversed(marked) if re.match(rf"^\s*{mark} \d+\.", lines[i])), marked[-1] if marked else None)
        if cur is None:
            return None
        col = lines[cur].index(mark) + 2
        numbered = re.match(r"\d+\.", lines[cur][col:])      # then only numbered lines are options
        is_option = lambda l: (len(l) > col and l[col] != " " and l[:col].strip() in ("", mark)
                               and (not numbered or re.match(r"\d+\.", l[col:])))
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
        here = next(k for k, l in enumerate(block) if l.lstrip().startswith(mark))
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

class Codex:
    """OpenAI's Codex CLI: AGENTS.md and lifecycle hooks (Codex 0.154).
    Hooks travel with the console command and need review in /hooks before Codex runs them.
    See https://developers.openai.com/codex/hooks for the payload and output contracts."""
    label = "Codex"
    program, site = "codex", "https://developers.openai.com/codex"
    aliases = {}
    efforts = ["low", "medium", "high", "xhigh", "max", ("ultra", "Ultra — delegates to subagents")]
    efforts_for = {"gpt-5.5": efforts[:4], "gpt-5.6-luna": efforts[:5], "gpt-6-luna": efforts[:5]}
    recommendation = "Suggested for a new Codex project: GPT-5.6 Sol, medium effort. Reserve Astra for hard decisions and failures."

    @staticmethod
    def config_home():
        import os
        return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")

    def version(self):
        return _run([self.program, "--version"])

    def catalog(self):
        return self.discover()

    def discover(self, run=None):
        """The models this account can run, from Codex's own catalog of them, with each one's effort levels;
        what it keeps hidden (internal models) is left out. Nothing is called."""
        try:
            catalog = json.loads((self.config_home() / "models_cache.json").read_text())
        except (OSError, ValueError):
            return []
        import re
        known = dict(self.models)
        name = lambda m: known.get(m["slug"]) or re.sub(r"-(?=[A-Za-z])", " ", m.get("display_name") or m["slug"])
        return [(m["slug"], name(m),
                 [x["effort"] if isinstance(x, dict) else x for x in m.get("supported_reasoning_levels") or []] or list(self.efforts))
                for m in catalog.get("models", []) if m.get("visibility") == "list" and m.get("slug")]

    @property
    def models(self):
        """Stable suggestions plus the new generation only once this installation advertises it."""
        models = [("gpt-6-astra", "GPT-6 Astra"), ("gpt-5.6-sol", "GPT-5.6 Sol"),
                  ("gpt-5.6-terra", "GPT-5.6 Terra"), ("gpt-5.6-luna", "GPT-5.6 Luna")]
        try:
            catalog = json.loads((self.config_home() / "models_cache.json").read_text())
            visible = {m.get("slug") for m in catalog.get("models", []) if m.get("visibility") == "list"}
        except (OSError, ValueError, AttributeError, TypeError):
            visible = set()
        return models + [(slug, label) for slug, label in (("gpt-6-sol", "GPT-6 Sol"), ("gpt-6-luna", "GPT-6 Luna"))
                         if slug in visible]
    mark = "›"
    instructions = "AGENTS.md"
    enter_after = 0.6       # typed text arriving at once reads to it as a paste, which swallows an Enter right after
    scrolled_marker = ""
    legacy_hooks = {"SessionStart": "colony notes --deliver --session", "UserPromptSubmit": "colony notes --deliver",
                    "Stop": "colony turn && printf '{}\\n'"}
    hooks = {"SessionStart": "colony notes --deliver --session --console codex",
             "UserPromptSubmit": "colony notes --deliver --console codex",
             "Stop": "colony turn --console codex && printf '{}\\n'"}  # Stop requires JSON output
    # colony's permission choices, in Codex's terms
    permissions = {"ask": ["-a", "on-request", "-s", "workspace-write"], "edits": ["-a", "never", "-s", "workspace-write"],
                   "all": ["--dangerously-bypass-approvals-and-sandbox"], "plan": ["-a", "on-request", "-s", "read-only"]}
    DELIVERY = ("\n- Colony's Codex hooks deliver notes and mail at session start and before each prompt, and record "
                "questions when a turn ends. The board's launch command supplies them; review them in `/hooks`. If hooks have not "
                "delivered notes at session start or when a `[colony]` line arrives, run "
                "`colony notes --deliver --console codex` and act on what it prints. Outside the matching board "
                "console, explicitly choose the intended project before manual delivery; do not infer it from a shared folder.\n")

    def command(self, label, s, resume=None):
        """The person's own `codex`: inline, so its console keeps scrollback, without the update question at
        start; the project's permissions, model and effort. Remote Control has no Codex equivalent here."""
        from .board import home
        parts = ["codex", "--no-alt-screen", "-c", "check_for_update_on_startup=false"]
        perms = self.permissions.get(s.get("permissions") or "ask", [])
        parts += perms
        if "workspace-write" in perms:
            parts += ["--add-dir", shlex.quote(str(home()))]    # colony keeps its records there: its commands must write
        if s.get("model"):
            parts += ["-m", shlex.quote(s["model"])]
        if s.get("effort"):
            parts += ["-c", shlex.quote(f"model_reasoning_effort={s['effort']}")]
        # Codex 0.154 discovers project hook files in the main checkout even when cwd is a linked worktree.
        # Session flags work for both fresh and resumed consoles, without editing another checkout or user config.
        for event, command in self.hooks.items():
            value = f'hooks.{event}=[{{hooks=[{{type="command",command={json.dumps(command)}}}]}}]'
            parts += ["-c", shlex.quote(value)]
        if resume:
            parts += ["resume", shlex.quote(resume)]
        return " ".join(parts)

    def wire(self, root, protocol):
        """Install instructions and retire our old file hooks; launch flags now supply them in every checkout."""
        path = root / "AGENTS.md"
        have = path.read_text() if path.exists() else ""
        block = protocol.lstrip("\n").rstrip("\n") + self.DELIVERY
        marker = "## This project is part of a colony"
        if marker in have:
            start = have.index(marker)
            end = have.find("\n## ", start + 5)
            path.write_text(have[:start] + block + (have[end + 1:] if end != -1 else ""))
        else:
            path.write_text(have + ("\n" if have and not have.endswith("\n") else "") + block)
        settings = root / ".codex" / "hooks.json"
        if settings.exists():
            cfg = json.loads(settings.read_text())
            for event, entries in cfg.get("hooks", {}).items():
                if event not in self.hooks:
                    continue
                for entry in entries:
                    entry["hooks"] = [h for h in entry.get("hooks", []) if not self._owned_hook(event, h)]
                cfg["hooks"][event] = [entry for entry in entries if entry.get("hooks")]
            settings.write_text(json.dumps(cfg, indent=2) + "\n")

    def _owned_hook(self, event, hook):
        return (hook.get("type") == "command"
                and hook.get("command") in (self.hooks.get(event), self.legacy_hooks.get(event)))

    def wired(self, root):
        path = root / "AGENTS.md"
        settings = root / ".codex" / "hooks.json"
        try:
            hooks = json.loads(settings.read_text()).get("hooks", {}) if settings.exists() else {}
        except (OSError, ValueError):
            return False
        return ("## This project is part of a colony" in (path.read_text() if path.exists() else "")
                and not any(self._owned_hook(event, h) for event in self.hooks
                            for entry in hooks.get(event, []) for h in entry.get("hooks", [])))

    def model_name(self, value):
        return dict(self.models).get(value, value)

    def own_defaults(self):
        """The model and effort in the person's ~/.codex/config.toml, if it names them."""
        try:
            import tomllib
            cfg = tomllib.loads((self.config_home() / "config.toml").read_text())
        except (ImportError, OSError, ValueError):
            cfg = {}
        return {"model": cfg.get("model") or None, "effort": cfg.get("model_reasoning_effort") or None}

    def turn_text(self, payload):
        """The Stop hook supplies the final reply directly, including on sessions with no transcript."""
        return payload.get("turn_id"), payload.get("last_assistant_message") or ""

    def conversation(self, payload):
        return payload.get("session_id"), payload.get("transcript_path")

    def history_text(self, root, limit=200_000):
        return None

    def classify(self, screen):
        """Codex shows "• Working (3s • esc to interrupt)" while it works, and a numbered choice marked `›`
        (a trust question, an approval) when it needs the person; otherwise it waits at its `›` prompt."""
        import re
        if "esc to interrupt" in screen.lower() or re.search(r"^\s*• Working \(", screen, re.M):
            return "working"
        if re.search(r"^\s*› \d+\. ", screen, re.M):
            return "needs you"
        return "idle"

    def activity(self, screen):
        import re
        m = None
        for m in re.finditer(r"^\s*• (Working \([^)]*\))", screen, re.M):
            pass
        return {"line": m.group(1) if m else None, "agents": []}

    def draft(self, screen):
        """What is typed at its prompt (the last `›` line that isn't an option) and not sent; its greyed hint in
        an empty prompt is dim, so it isn't a draft. None with no prompt on screen."""
        import re
        plain = lambda l: re.sub(r"\x1b\[[0-9;]*m", "", l)
        lines = screen.splitlines()
        at = next((i for i in range(len(lines) - 1, -1, -1) if re.match(r"^\s*›(?!\s*\d+\.)", plain(lines[i]))), None)
        if at is None:
            return None
        return plain(re.sub(r"\x1b\[2m.*?(\x1b\[(0|22)?m|$)", "", lines[at])).strip().lstrip("›").strip()

    choice = ClaudeCode.choice
    choose = ClaudeCode.choose


# The questions a session asks as it starts, before any work: trusting its folder, confirming the permission
# mode it was started with, Remote Control, its hooks. Mid-work prompts (run this command?) are not these.
STARTUP = ("trust", "bypass permissions", "remote control", "hook", "approval mode", "full access")


def starting(provider, screen, fresh=True):
    """The keys that answer a start-up question on screen so the session runs as it was set up: the option that
    begins with yes (or accept, allow, enable, continue, trust), never one that exits. Its folder's trust is
    answered whenever it's asked; the others only while the session is fresh (just started)."""
    import re
    found = provider.choice(screen)
    if not found:
        return None
    about = (" ".join(found[0]) + " " + " ".join(found[1])).lower()
    if not ("trust" in about or (fresh and any(k in about for k in STARTUP))):
        return None
    yes = next((o for o in found[1] if re.match(r"(yes|accept|allow|enable|continue|trust)\b", o.lower())), None)
    return provider.choose(screen, yes) if yes else None


PROVIDERS = {"claude": ClaudeCode(), "codex": Codex()}


def _run(cmd, cwd=None, timeout=120):
    """A program's output, or "" if it fails or isn't there."""
    import subprocess
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _label(mid):
    """A model ID as its name: claude-sonnet-5-5 → Sonnet 5.5, claude-haiku-4-5-20251001 → Haiku 4.5."""
    import re
    parts = [p for p in mid.split("-")[1:] if not re.fullmatch(r"\d{8}", p)]
    words = [p for p in parts if not p.isdigit()]
    nums = [p for p in parts if p.isdigit()]
    return " ".join(w.capitalize() for w in words) + (" " + ".".join(nums) if nums else "")


def available(provider):
    """The models colony offers for a provider: those discovered on this machine, else those it knows of."""
    from . import board
    found = _discovered().get(key(provider))
    return [(m, label) for m, label, _ in found["models"]] if found and found["models"] else list(provider.models)


def efforts_of(provider, model):
    from . import board
    found = _discovered().get(key(provider)) or {}
    for m, _, efforts in found.get("models", []):
        if m == model:
            return efforts
    return [e[0] if isinstance(e, tuple) else e for e in getattr(provider, "efforts_for", {}).get(model, provider.efforts)]


def _discovered():
    from . import board
    path = board.home() / "bench" / "available.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def discover(force=False, run=None, calls=True):
    """Find the models each ticked, installed provider can run and keep them. calls=False reads only what each
    program keeps on disk (the daily check: no tokens); a provider whose list isn't on disk is then left as it
    was. Returns the providers that were looked at."""
    from . import board
    have, looked = _discovered(), []
    for k, p in PROVIDERS.items():
        if not usable(p) or not hasattr(p, "discover"):
            continue
        ver = p.version() if run is None else "test"
        if run is None and not calls:
            found = p.catalog() if hasattr(p, "catalog") else p.discover()
            if not found:
                continue
        else:
            found = p.discover(run) if run else p.discover()
        if found or k not in have:
            have[k] = {"version": ver, "at": board.now(), "models": found}
        looked.append(k)
    path = board.home() / "bench" / "available.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(have, indent=1))
    return looked


def installed(provider):
    """Whether its program is on this machine. A stand-in console command (tests, demos) stands for every one."""
    import os
    import shutil
    return bool(os.environ.get("COLONY_CONSOLE_CMD")) or shutil.which(provider.program) is not None


def missing(provider):
    """What to tell the person when a provider's program isn't here."""
    return f"{provider.label} isn't installed on this machine ({provider.site})"


def key(provider):
    return next(k for k, p in PROVIDERS.items() if p is provider)


def enabled(provider):
    """Whether the person has it on in Settings (all are, until they turn one off)."""
    from . import board
    on = board.registry()["settings"].get("providers")
    return on is None or key(provider) in on


def usable(provider):
    """On in Settings and installed: what colony offers for new projects and its default."""
    return enabled(provider) and installed(provider)


def unusable(provider):
    """Why a provider can't be chosen, in a line."""
    return missing(provider) if not installed(provider) else f"{provider.label} is off in colony's Settings"
DEFAULT = "claude"


def get(name):
    return PROVIDERS.get(name or DEFAULT, PROVIDERS[DEFAULT])


def of(root):
    """The provider a project's settings name, else the global one."""
    from . import board
    return get(board.project_settings(root)[0].get("provider") if root else board.registry()["settings"]["provider"])
