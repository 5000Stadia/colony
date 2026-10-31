"""Waiting costs nothing (R137): a check before each shell command that turns back a sleep-and-check.

Each check an agent makes while it waits re-sends its whole conversation; both programs already wake it for free
when its own work finishes. So a sleep of 5 s or more, or a loop that sleeps, is turned back with that program's free
way to wait; a short settling pause, and anything already in the background, goes through. Any doubt lets the
command through: this runs before every shell command of every agent, so it imports almost nothing.
"""
import json
import re
import sys

LONGEST_PAUSE = 5                      # seconds: a settling pause under this goes through
UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
# `sleep` where a command starts: the line's start, after ; & | ( ) { } or a backquote, $( or a shell keyword.
_START = r"(?:^|[;&|(){}`\n]|\$\(|\b(?:do|then|else|exec|nohup|time)\b)\s*"
SLEEP = re.compile(_START + r"sleep((?:\s+[0-9.]+[smhd]?|\s+inf(?:inity)?)+)\b", re.M)
SLEEPS = re.compile(_START + r"sleep\b", re.M)
LOOP = re.compile(r"(?:^|[;&|(){}\n]|\$\()\s*(?:while|until|for)\b[\s\S]*?\bdo\b([\s\S]*?)(?:\bdone\b|$)", re.M)
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n[\s\S]*?^\s*\2\s*$", re.M)
BACKGROUND = re.compile(r"(?<!&)&\s*(?:disown\s*)?$")


def seconds(args):
    """What `sleep ARGS` waits, in seconds (GNU sleep adds its arguments)."""
    total = 0.0
    for arg in args.split():
        if arg.startswith("inf"):
            return float("inf")
        number, unit = re.fullmatch(r"([0-9.]+)([smhd]?)", arg).groups()
        total += float(number) * UNITS[unit]
    return total


def waits(command):
    """True when a shell command waits by sleeping: a sleep of LONGEST_PAUSE or more, or a loop that sleeps."""
    command = HEREDOC.sub("", command)       # a heredoc's body is a file's text, not commands
    if BACKGROUND.search(command.strip()):
        return False                         # already in the background: waiting there costs nothing
    if any(SLEEPS.search(body) for body in LOOP.findall(command)):
        return True
    for match in SLEEP.finditer(command):
        try:
            if seconds(match.group(1)) >= LONGEST_PAUSE:
                return True
        except (AttributeError, ValueError):
            continue
    return False


def verdict(payload, shells, background_field=None):
    """The command a hook payload would run, judged: True to turn it back. shells: the tool names that run a shell
    command; background_field: the tool_input field that says it already runs in the background."""
    if not isinstance(payload, dict) or payload.get("tool_name") not in shells:
        return False
    args = payload.get("tool_input")
    if isinstance(args, str):
        args = json.loads(args)
    if not isinstance(args, dict) or (background_field and args.get(background_field) is True):
        return False
    command = args.get("command") if "command" in args else args.get("cmd")
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)
    return isinstance(command, str) and waits(command)


def hook(console, stdin=None, stdout=None):
    """The PreToolUse hook: reads the program's payload, prints its deny when the command waits by sleeping. Any
    error lets the command through."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    out = ""
    try:
        from .providers import get
        provider = get(console, strict=True)
        payload = json.loads(stdin.read() or "null")
        if verdict(payload, provider.SHELL_TOOLS, provider.SHELL_BACKGROUND):
            out = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                     "permissionDecisionReason": provider.WAIT_FREE}})
        elif provider.HOOK_NEEDS_JSON:
            out = "{}"
    except Exception:
        out = "{}" if console == "codex" else ""     # PROVIDER: Codex's hooks answer in JSON, as its others do
    if out:
        print(out, file=stdout, flush=True)
    return 0
