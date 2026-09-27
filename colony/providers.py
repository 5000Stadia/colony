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
  classify(screen)         "working" | "needs you" | "idle" from its terminal screen; the watcher and the
                           monitor's wake-ups depend on this, so match the CLI's own busy and prompt markers.
  choice(screen)           the choice on screen (a trust question, a permission prompt): its question, its
                           options and which is highlighted, or None; the board shows it as buttons
  choose(screen, text)     the keys that pick the option matching `text`; `colony choose` and the buttons use it
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
    hooks = {"SessionStart": "colony notes --deliver --session", "UserPromptSubmit": "colony notes --deliver"}

    def command(self, label, s):
        """The person's own `claude` with the project's choices: permissions, Remote Control, model, effort."""
        from .board import PERMISSIONS
        parts = ["claude"]
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

    def model_name(self, value):
        """The model a value means, by name: an ID or alias; anything else as typed."""
        return dict(self.models).get(value) or self.aliases.get(str(value).lower()) or value

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
        if "esc to interrupt" in low or re.search(r"^\s*[✻✶✳✢✽·*+] \S[^\n]*?…", screen, re.M):
            return "working"
        if any(k in low for k in ("do you want", "❯ 1.", "trust this folder", "yes, proceed")):
            return "needs you"
        return "idle"

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
