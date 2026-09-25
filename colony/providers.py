"""Who runs a project's agent. Each provider says how to start its CLI, how to wire a project so the agent
knows the colony and gets its notes and mail, and how to read its screen. Only Claude Code is here; another
CLI joins by adding an entry to PROVIDERS, and the board, settings and forms offer it from then on.

PROVIDER: for an agent adding another CLI (Codex or any other). Everything outside this file that still
assumes Claude Code is marked with a `PROVIDER:` comment; `grep -rn "PROVIDER:" .` lists them, each saying
what it assumes and what a second provider needs there. What a provider supplies here:
  label, models, efforts   shown in the add/create forms and settings (suggestions; any value can be typed)
  command(label, s)        the shell command that starts its interactive agent with the settings in `s`:
                           provider, model, effort, permissions (ask|edits|all|plan) and remote (on|off).
                           Map each to the CLI's own flags, and ignore any it has no equivalent for.
  wire(root, protocol)     put the colony protocol where the CLI reads project instructions (AGENTS.md for
                           Codex) and arrange for `colony notes --deliver` output to reach the agent: at
                           session start (`--session`) and before each turn the person or the board types.
                           Without a hook system, the protocol can ask the agent to run it itself, weaker.
  wired(root)              whether wire() has been done (the doctor asks)
  classify(screen)         "working" | "needs you" | "idle" from its terminal screen; the watcher and the
                           monitor's wake-ups depend on this, so match the CLI's own busy and prompt markers.
The colony's own mechanisms (notes, gates, mail, the roadmap, the watcher) are provider-agnostic: files in
.board/, the `colony` command, and text typed into a tmux session. Keep new ones that way.
"""
import json
import shlex
from pathlib import Path


class ClaudeCode:
    label = "Claude Code"
    models = ["fable", "opus", "sonnet", "haiku"]          # aliases for the latest of each; full names work too
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

    def wired(self, root):
        claude_md = root / "CLAUDE.md"
        settings = root / ".claude" / "settings.json"
        hooks = json.dumps(json.loads(settings.read_text()).get("hooks", {})) if settings.exists() else ""
        return ("## This project is part of a colony" in (claude_md.read_text() if claude_md.exists() else "")
                and all(c in hooks for c in self.hooks.values()))

    def classify(self, screen):
        """Claude Code shows "esc to interrupt" while it works and a numbered choice when it asks permission;
        anything else is waiting for the person to type."""
        low = screen.lower()
        if "esc to interrupt" in low:
            return "working"
        if any(k in low for k in ("do you want", "❯ 1.", "trust this folder", "yes, proceed")):
            return "needs you"
        return "idle"


PROVIDERS = {"claude": ClaudeCode()}
DEFAULT = "claude"


def get(name):
    return PROVIDERS.get(name or DEFAULT, PROVIDERS[DEFAULT])


def of(root):
    """The provider a project's settings name, else the global one."""
    from . import board
    return get(board.project_settings(root)[0].get("provider") if root else board.registry()["settings"]["provider"])
