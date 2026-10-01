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
  consult(brief, model, effort, project)
                           one fresh, stateless consultation: an empty folder, no settings or hooks, read-only, a
                           no cap: it takes what it takes; returns {"text", "cost", "usage", "error"} (colony consult uses it)
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
import sys
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
             "Stop": "colony turn", "PreCompact": "colony context --hook before --console claude"}

    def command(self, label, s, resume=None, root=None):
        """The person's own `claude` with the project's choices: permissions, Remote Control, model, effort;
        with resume, back in that conversation."""
        from .board import PERMISSIONS
        parts = ["claude"] + (["--resume", shlex.quote(resume)] if resume else [])
        if self.token():
            # the long-lived sign-in, read from its file as the console starts: never written into the command
            parts.insert(0, f'{self.TOKEN_ENV}="$(cat {shlex.quote(str(self.token_path()))})"')
        if PERMISSIONS.get(s.get("permissions") or "ask"):
            parts += ["--permission-mode", PERMISSIONS[s["permissions"]]]
        if s.get("remote"):
            parts += ["--remote-control", shlex.quote(label)]
        if s.get("model"):
            parts += ["--model", shlex.quote(s["model"])]
        if s.get("effort"):
            parts += ["--effort", shlex.quote(s["effort"])]
        if s.get("autocompact"):
            parts += ["--autocompact", shlex.quote(s["autocompact"])]     # how large its conversation may grow
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
        for event, wanted in hook_entries(self).items():
            entries = cfg.setdefault("hooks", {}).setdefault(event, [])
            for entry in wanted:
                command = entry["hooks"][0]["command"]
                if not any(h.get("command") == command for e in entries for h in e.get("hooks", [])):
                    entries.append(entry)
        # PROVIDER: Claude Code hands its usage limits only to its status line; colony's records them and shows
        # the person's own status line, if they set one. A project that set its own keeps it.
        cfg.setdefault("statusLine", {"type": "command", "command": "colony statusline"})
        settings.write_text(json.dumps(cfg, indent=2) + "\n")

    HELPER_CALL = "start one by its name (the Agent tool's subagent_type)"

    # PROVIDER: Claude Code's long-lived sign-in (claude setup-token, on a subscription), so its consoles aren't
    # signed out every week or so; given to each as it starts, from a file only the person can read.
    TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
    TOKEN_STEPS = [("In a terminal on this machine, run `claude setup-token` and sign in in the browser it opens", None),
                   ("Copy the token it prints", None),
                   ("Paste it below and save: colony checks it with one tiny call, keeps it on this machine only, and "
                    "starts each Claude Code console with it from then on (each moves over once it sits idle)", None)]

    @staticmethod
    def token_path():
        from .board import home
        return home() / "claude-token"

    def token(self):
        try:
            return self.token_path().read_text().strip() or None
        except OSError:
            return None

    def set_token(self, token):
        """Keep the long-lived token, readable by the person alone; empty removes it."""
        import os
        p = self.token_path()
        if not token.strip():
            p.unlink(missing_ok=True)
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(token.strip() + "\n")
        p.chmod(0o600)

    def check_token(self, token, run=None):
        """Whether a token signs in, by one tiny call on it (Claude Code's own status doesn't check). Returns
        (ok, why)."""
        import os
        import subprocess
        import tempfile
        env = dict(os.environ, **{self.TOKEN_ENV: token.strip()})
        try:
            from . import bench
            cheap = (bench.tiers_for("claude").get("chores") or {}).get("model")     # the cheapest worth its points
            r = (run or subprocess.run)([self.program, "-p", *(["--model", cheap] if cheap else []), "--setting-sources", "",
                                         "--output-format", "json"], input="Reply with: ok", capture_output=True, text=True,
                                        env=env, cwd=tempfile.mkdtemp(prefix="colony-token-"), timeout=120)
            d = json.loads(r.stdout or "{}")
        except (OSError, ValueError, subprocess.TimeoutExpired) as err:
            return False, f"the check couldn't run ({err.__class__.__name__})"
        if d.get("is_error") or not d.get("result"):
            return False, (d.get("result") or r.stderr or "the token was refused").strip()[:200]
        return True, "signed in"

    def signed_in(self, run=None):
        """Whether Claude Code is signed in here, by its own status (no tokens spent)."""
        out = (run or _run)([self.program, "auth", "status", "--json"])
        try:
            return bool(json.loads(out or "{}").get("loggedIn"))
        except ValueError:
            return None

    def latest_conversation(self, folder):
        """The conversation last active in a folder, from Claude Code's own records: for a console whose hooks
        don't record it (the monitor's)."""
        import re as _re
        d = self.config_home() / "projects" / _re.sub(r"[^A-Za-z0-9]", "-", str(folder))
        files = sorted(d.glob("*.jsonl"), key=lambda f: f.stat().st_mtime) if d.exists() else []
        return files[-1].stem if files else None

    def update(self):
        """Install the newest Claude Code beside the running one (its own updater)."""
        return _run([self.program, "update"], timeout=600)

    def startup_files(self, root):
        """What Claude Code reads only when it starts, among what colony writes: the tier helpers."""
        return [root / ".claude" / "agents" / f"{self.helper_name(t)}.md" for t in self.HELPER_BRIEF]
    HELPER_BRIEF = {"routine": "ordinary work: building, editing, looking things up across files",
                    "step-up": "work that has stalled, been retried or redone, or needs the strongest reasoning here",
                    "chores": "clear, mechanical tasks: small edits, running a named test, copying, simple lookups"}

    @staticmethod
    def helper_name(tier):
        return "colony-" + tier.replace("-", "")

    def write_helpers(self, root, tiers):
        """A helper definition per tier in .claude/agents, so a helper runs at exactly its tier's model and effort
        (the Agent tool picks a model only by alias, and no effort). Rewritten when a tier changes; a tier colony
        can't fill leaves no file. Returns the files it changed."""
        folder = root / ".claude" / "agents"
        changed = []
        for tier, brief in self.HELPER_BRIEF.items():
            path = folder / f"{self.helper_name(tier)}.md"
            t = tiers.get(tier)
            if not t:
                if path.exists():
                    path.unlink()
                    changed.append(path)
                continue
            text = (f"---\nname: {self.helper_name(tier)}\ndescription: Colony's {tier} tier: {brief}.\n"
                    f"model: {t['model']}\n" + (f"effort: {t['effort']}\n" if t.get("effort") else "")
                    + "---\n\nDo the task you are given within its brief, and hand in what you find and do.\n"
                    "<!-- written by colony from this project's tiers (colony models); edits here are replaced -->\n")
            if not path.exists() or path.read_text() != text:
                folder.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                changed.append(path)
        return changed

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

    def consult(self, brief, model, effort, project, run=None):
        """One fresh, stateless consultation: in an empty folder with none of the project's settings or hooks (so
        it can't take the person's notes or act as the project's agent), reading the project only by path.
        Returns the answer and what it cost."""
        import subprocess
        import tempfile
        here = tempfile.mkdtemp(prefix="colony-consult-")
        cmd = [self.program, "-p", "--model", model, "--effort", effort, "--setting-sources", "", "--output-format", "json",
               "--add-dir", str(project), "--allowedTools", "Read,Grep,Glob",
               "--disallowedTools", "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch,Agent"]
        try:
            import os
            env = dict(os.environ, **({self.TOKEN_ENV: self.token()} if self.token() else {}))
            out = (run or subprocess.run)(cmd, input=brief, capture_output=True, text=True, cwd=here, env=env, timeout=1800).stdout
            d = json.loads(out)
        except (ValueError, OSError, subprocess.TimeoutExpired) as err:
            return {"text": "", "cost": None, "error": f"{err.__class__.__name__}"}
        return {"text": d.get("result") or "", "cost": d.get("total_cost_usd"), "usage": d.get("usage"),
                "error": None if not d.get("is_error") else (d.get("subtype") or "error")}

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
                and all(h['command'] in hooks for entries in hook_entries(self).values() for e in entries for h in e['hooks']))

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
    reports_cost = False                      # codex exec reports tokens, not dollars
    aliases = {}
    efforts = ["low", "medium", "high", "xhigh", "max", ("ultra", "Ultra — delegates to subagents")]
    efforts_for = {"gpt-5.5": efforts[:4], "gpt-5.6-luna": efforts[:5], "gpt-6-luna": efforts[:5]}

    @staticmethod
    def config_home():
        import os
        return Path(os.environ.get("COLONY_CODEX_SOURCE_HOME") or os.environ.get("CODEX_HOME") or Path.home() / ".codex")

    def version(self):
        return _run([self.program, "--version"])

    def catalog(self):
        return self.discover()

    # PROVIDER: Codex sandboxes its commands with bubblewrap on Linux. Ubuntu 24.04 and later forbid the user
    # namespaces bubblewrap needs unless an AppArmor profile allows them: without one, a Codex agent or consultant
    # can't run a command, or even read a file, in its read-only or workspace sandbox.
    APPARMOR = ("abi <abi/4.0>,\ninclude <tunables/global>\n\nprofile codex-bwrap "
                "/{usr/bin/bwrap,home/*/.codex/packages/standalone/releases/*/codex-resources/bwrap} "
                "flags=(unconfined) {\n  userns,\n\n  include if exists <local/codex-bwrap>\n}\n")

    def sandbox_problem(self, run=None):
        """Why Codex's sandbox can't start on this machine, with the fix; None when it can (or isn't Linux's)."""
        import shutil
        import subprocess
        if not sys.platform.startswith("linux") or not shutil.which("bwrap"):
            return None
        try:
            r = (run or subprocess.run)(["bwrap", "--unshare-net", "--ro-bind", "/", "/", "true"], capture_output=True,
                                        text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if r.returncode == 0:
            return None
        return ("Codex's sandbox can't start here (" + (r.stderr.strip().splitlines() or ["bwrap failed"])[-1] + "), so "
                "Codex can't run commands or read files in it. On Ubuntu, allow bubblewrap its user namespaces: "
                "printf '" + self.APPARMOR.replace("\n", "\\n") + "' | sudo tee /etc/apparmor.d/codex-bwrap && "
                "sudo apparmor_parser -r /etc/apparmor.d/codex-bwrap")

    def consult(self, brief, model, effort, project, price=None, popen=None):
        """One fresh, stateless consultation in its read-only sandbox, in an empty folder, with hooks off. Codex
        reports tokens, not dollars: price (per 1M tokens in, out) turns its usage into what it cost."""
        import subprocess
        import tempfile
        here = tempfile.mkdtemp(prefix="colony-consult-")
        last = Path(here) / "answer.txt"
        cmd = [self.program, "exec", "-m", model, "-c", f"model_reasoning_effort={effort}", "-s", "read-only",
               "--skip-git-repo-check", "--ephemeral", "--disable", "hooks", "--json", "-o", str(last), "-C", here, "-"]
        pin, pout = price or (0, 0)
        cost = lambda u: ((u.get("input_tokens", 0) - u.get("cached_input_tokens", 0)) * pin
                          + u.get("cached_input_tokens", 0) * pin * 0.1 + u.get("output_tokens", 0) * pout) / 1e6
        usage = {}
        try:
            proc = (popen or subprocess.Popen)(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            proc.stdin.write(brief)
            proc.stdin.close()
            for line in proc.stdout:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                u = e.get("usage") or (e.get("msg") or {}).get("usage")
                if isinstance(u, dict) and u:
                    usage = u
            proc.wait(timeout=60)
        except (OSError, subprocess.TimeoutExpired) as err:
            return {"text": "", "cost": None, "error": err.__class__.__name__}
        return {"text": last.read_text() if last.exists() else "", "cost": cost(usage) if price else None,
                "usage": usage, "error": None if last.exists() else "no answer"}

    def discover(self, run=None):
        """The models this account can run, from Codex's own catalog of them, with each one's effort levels;
        what it keeps hidden (internal models) is left out. Nothing is called."""
        return self.catalog_snapshot()[0]

    def catalog_snapshot(self):
        """Read model rows and their actual writer version together, not the installed CLI's version."""
        from . import board
        import re
        source = self.config_home()
        paths = [source / 'models_cache.json']
        # Only hosts sharing this sign-in may supply this account's catalog. Prefer
        # the newer client, then its freshest snapshot; never union removed models.
        for host in (board.home() / 'codex-remote').glob('*'):
            auth = host / 'auth.json'
            if auth.is_symlink() and auth.resolve() == (source / 'auth.json').resolve():
                paths.append(host / 'models_cache.json')
        snapshots = []
        source_identity = None
        for path in paths:
            try:
                catalog = json.loads(path.read_text())
                if not isinstance(catalog.get('models'), list):
                    continue
                if path == paths[0]:
                    source_identity = catalog.get('identity')
                elif source_identity and catalog.get('identity') and catalog['identity'] != source_identity:
                    continue                   # a shared auth link can outlive an account change
                version = tuple(map(int, re.findall(r'\d+', catalog.get('client_version') or '')[:3]))
                snapshots.append((version, catalog.get('fetched_at') or '', str(path), catalog))
            except (OSError, ValueError, AttributeError, TypeError):
                continue
        if not snapshots:
            return [], {}
        _, _, path, catalog = max(snapshots, key=lambda x: x[:3])
        known = dict(self.models)
        name = lambda m: known.get(m["slug"]) or re.sub(r"-(?=[A-Za-z])", " ", m.get("display_name") or m["slug"])
        found = [(m["slug"], name(m),
                 [x["effort"] if isinstance(x, dict) else x for x in m.get("supported_reasoning_levels") or []] or list(self.efforts))
                for m in catalog.get("models", []) if m.get("visibility") == "list" and m.get("slug")]
        return found, dict(source=str(source.resolve()), path=path, identity=catalog.get('identity'), client_version=catalog.get('client_version'),
                           fetched_at=catalog.get('fetched_at'))

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
             "Stop": "colony turn --console codex && printf '{}\\n'",
             "PreCompact": "colony context --hook before --console codex"}  # Stop requires JSON output
    # colony's permission choices, in Codex's terms
    permissions = {"ask": ["-a", "on-request", "-s", "workspace-write"], "edits": ["-a", "never", "-s", "workspace-write"],
                   "all": ["--dangerously-bypass-approvals-and-sandbox"], "plan": ["-a", "on-request", "-s", "read-only"]}
    DELIVERY = ("\n- Colony's Codex hooks deliver notes and mail at session start and before each prompt, and record "
                "questions when a turn ends. The board's launch command supplies them; review them in `/hooks`. If hooks have not "
                "delivered notes at session start or when a `[colony]` line arrives, run "
                "`colony notes --deliver --console codex` and act on what it prints. Outside the matching board "
                "console, explicitly choose the intended project before manual delivery; do not infer it from a shared folder.\n")

    def signed_in(self, run=None):
        """Whether Codex is signed in here, by its own status (no tokens spent)."""
        import subprocess
        try:
            r = (run or subprocess.run)([self.program, "login", "status"], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None
        out = (r.stdout + r.stderr).strip().lower()       # it reports on stderr
        return None if not out else out.startswith("logged in")

    def update(self):
        """Install the newest Codex beside the running one (its own updater)."""
        return _run([self.program, "update"], timeout=600)

    def startup_files(self, root):
        """What Codex reads only when it starts, among what colony writes: the tier overlays."""
        return [root / ".codex" / "agents" / f"{self.helper_name(t)}.toml" for t in self.HELPER_BRIEF]

    HELPER_CALL = "spawn one with agent_type set to its name and fork_turns \"none\"; its model and effort come from it"
    HELPER_BRIEF = {"routine": "Colony's routine tier: ordinary work, building, editing, looking things up across files",
                    "step-up": "Colony's step-up tier: work that has stalled, been retried or redone, or needs the strongest reasoning here",
                    "chores": "Colony's chores tier: clear, mechanical tasks, small edits, running a named test, simple lookups"}

    @staticmethod
    def helper_name(tier):
        return "colony-" + tier.replace("-", "")

    def write_helpers(self, root, tiers):
        """A config overlay per tier in .codex/agents (model and reasoning effort), which the console command
        registers as a named agent; the agent spawns it by agent_type. Verified on Codex 0.154 by colony-codex
        (docs/codex-helper-tiers.md there). A new or changed tier takes hold when the console next starts."""
        folder = root / ".codex" / "agents"
        changed = []
        for tier in self.HELPER_BRIEF:
            path = folder / f"{self.helper_name(tier)}.toml"
            t = tiers.get(tier)
            if not t:
                if path.exists():
                    path.unlink()
                    changed.append(path)
                continue
            text = ("# written by colony from this project's tiers (colony models); edits here are replaced\n"
                    # Codex 0.159 also finds these by itself in .codex/agents, as roles that must carry a name
                    f"name = {json.dumps(self.helper_name(tier))}\n"
                    f"description = {json.dumps(self.HELPER_BRIEF[tier])}\n"
                    f"model = {json.dumps(t['model'])}\n" + (f"model_reasoning_effort = {json.dumps(t['effort'])}\n" if t.get("effort") else "")
                    + 'developer_instructions = "Do the task you are given within its brief, and hand in what you find and do."\n')
            if not path.exists() or path.read_text() != text:
                folder.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                changed.append(path)
        return changed

    def command(self, label, s, resume=None, root=None):
        if root:
            parts = [sys.executable, '-m', 'colony.codex_remote', 'launch', '--project', str(root),
                     '--settings', json.dumps(s, sort_keys=True)]
            if resume:
                parts += ['--resume', resume]
            return shlex.join(parts)
        return self.local_command(label, s, resume, root)

    def local_command(self, label, s, resume=None, root=None):
        """The person's own `codex`: inline, so its console keeps scrollback, without the update question at
        start; the project's permissions, model and effort. Remote lifecycle lives in codex_remote."""
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
        for event, entries in hook_entries(self).items():
            from .codex_remote import toml
            value = f'hooks.{event}={toml(entries)}'
            parts += ["-c", shlex.quote(value)]
        # the helper tiers, registered for this session (a project file would need the project trusted first);
        # each overlay holds its tier's model and effort (write_helpers)
        from .board import workdir
        for tier in (self.HELPER_BRIEF if root else ()):
            overlay = workdir(root) / ".codex" / "agents" / f"{self.helper_name(tier)}.toml"
            if overlay.exists():
                name = self.helper_name(tier)
                parts += ["-c", shlex.quote(f"agents.{name}.description={json.dumps(self.HELPER_BRIEF[tier])}"),
                          "-c", shlex.quote(f"agents.{name}.config_file={json.dumps(str(overlay))}")]
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


def discover(force=False, run=None, calls=True, only=None):
    """Find the models each ticked, installed provider can run and keep them. calls=False reads only what each
    program keeps on disk (the daily check: no tokens); a provider whose list isn't on disk is then left as it
    was. Returns the providers that were looked at."""
    from . import board
    have, looked = _discovered(), []
    for k, p in PROVIDERS.items():
        if only and k != only:
            continue
        if not usable(p) or not hasattr(p, "discover"):
            continue
        ver = p.version() if run is None else "test"
        catalog = {}
        if isinstance(p, Codex) and run is None:
            found, catalog = p.catalog_snapshot()
            previous = have.get(k) or {}
            prior = previous.get('catalog') or {}
            import re
            version = lambda value: tuple(map(int, re.findall(r'\d+', value or '')[:3]))
            writer = version(catalog.get('client_version'))
            prior_writer = version(prior.get('client_version') or previous.get('version'))
            same_source = not prior.get('source') or prior['source'] == catalog.get('source')
            same_identity = not prior.get('identity') or not catalog.get('identity') or prior['identity'] == catalog['identity']
            if same_source and same_identity and writer and prior_writer and writer < prior_writer:
                continue                 # an older still-running CLI overwrote the shared cache
            if not found:
                continue
        elif run is None and not calls:
            found = p.catalog() if hasattr(p, "catalog") else p.discover()
            if not found:
                continue
        else:
            found = p.discover(run) if run else p.discover()
        if found or k not in have:
            have[k] = {"version": ver, "at": board.now(), "models": found}
            if catalog:
                have[k]['catalog'] = catalog
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


def get(name, strict=False):
    """A provider by name; none named is the default. strict: an unknown name is an error, never quietly Claude Code
    (a consultation must know which family it's getting)."""
    if strict and name not in PROVIDERS:
        raise KeyError(name)
    return PROVIDERS.get(name or DEFAULT, PROVIDERS[DEFAULT])


def of(root):
    """The provider a project's settings name, else the global one."""
    from . import board
    return get(board.project_settings(root)[0].get("provider") if root else board.registry()["settings"]["provider"])


def hook_entries(provider):
    """Restoration has its own output budget, separate from notes and tier briefings."""
    result = {event: [{"hooks": [{"type": "command", "command": command}]}]
              for event, command in provider.hooks.items()}
    for event in ('SessionStart', 'UserPromptSubmit'):
        hook = dict(type='command', command=f'colony context --hook restore --console {key(provider)}')
        if key(provider) == 'codex':
            hook['additionalContextLimit'] = 12_000
        result[event].insert(0, dict(hooks=[hook]))
    return result
