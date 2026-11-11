"""The check before each shell command, for every agent of both programs. It turns back two things.

Waiting on a clock (R137). Each time an agent wakes to check, it re-sends its whole conversation. A fixed sleep of
5 s or more (`sleep 60; tail log`) guesses a time and comes back to look, so it is turned back with that program's
free way to wait. A loop inside one command (`until curl …; do sleep 3; done`) waits on the condition itself in a
single step, and goes through, as do a short settling pause and anything already in the background.

A commit chained to a test run read through a pipe (R157). `run tests 2>&1 | tail -5 && git commit` commits on the
reader's exit status, not the tests', so it goes ahead over a failing run. Counted before it was built: 161 such
commands in 151,994, at least 11 of them over a failure.

Any doubt lets the command through: this runs before every shell command, so it imports almost nothing.
"""
import json
import re
import sys

LONGEST_PAUSE = 5                      # seconds: a settling pause under this goes through
UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
# `sleep` where a command starts: the line's start, after ; & | ( ) { } or a backquote, $( or a shell keyword.
_START = r"(?:^|[;&|(){}`\n]|\$\(|\b(?:do|then|else|exec|nohup|time)\b)\s*"
SLEEP = re.compile(_START + r"sleep((?:\s+[0-9.]+[smhd]?|\s+inf(?:inity)?)+)\b", re.M)
LOOP_BODY = re.compile(r"\bdo\b(?:(?!\bdo\b)[\s\S])*?\bdone\b")      # innermost first, so nested loops unwind
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n[\s\S]*?^\s*\2\s*$", re.M)
BACKGROUND = re.compile(r"(?<!&)&\s*(?:disown\s*)?$")
# A test runner; its arguments up to a pipe (not ||, not a >| redirect) into a reader; then, later in the same command,
# a git commit.
TESTS = (r"(?:\bpytest\b|\bunittest\b|\btests?/run\.py\b|\b(?:npm|yarn|pnpm|bun)\s+(?:run\s+)?test\b|\bcargo\s+test\b"
         r"|\bgo\s+test\b|\bvitest\b|\bjest\b)")
PIPED_TEST = re.compile(
    TESTS + r"(?P<run>(?:(?!\n|;|&&|\|\|).)*?)(?<![|>])\|(?!\|)&?\s*(?:tail|head|grep|egrep|rg|tee|sed|awk|wc)\b"
    r"[\s\S]*?(?:&&|\|\||;|\n|\bthen\b|\bdo\b)\s*(?:[^\n;&|]*\s)?git\s+(?:-C\s+\S+\s+)?commit\b")
UNREAD = ("This commit is chained to a test run read through a pipe. A pipe reports its reader's result, not the "
          "tests', so it would commit over a failing run. Run the tests alone and commit on their own exit status, or "
          "start the command with `set -o pipefail;`.")


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
    """True when a shell command waits on a clock: a sleep of LONGEST_PAUSE or more outside any loop."""
    command = HEREDOC.sub("", command)       # a heredoc's body is a file's text, not commands
    if BACKGROUND.search(command.strip()):
        return False                         # already in the background: waiting there costs nothing
    while True:                              # a loop waits on its condition in this one step: its sleeps are fine
        unwound = LOOP_BODY.sub(" :", command)
        if unwound == command:
            break
        command = unwound
    command = re.sub(r"\bdo\b[\s\S]*", "", command)     # a loop whose end this can't see: its sleeps go through too
    for match in SLEEP.finditer(command):
        try:
            if seconds(match.group(1)) >= LONGEST_PAUSE:
                return True
        except (AttributeError, ValueError):
            continue
    return False


def commits_unread(command):
    """True when a shell command commits after a test run whose output went through a pipe: the commit would follow
    the reader's exit status. With pipefail set, or PIPESTATUS read, the tests' own status is in play; a run that
    only collects tests has no result to lose."""
    if "commit" not in command or "|" not in command:
        return False
    command = HEREDOC.sub("", command)
    if "pipefail" in command or "PIPESTATUS" in command:
        return False
    return any(not re.search(r"--co(?:llect-only)?\b", match.group("run")) for match in PIPED_TEST.finditer(command))


def verdict(payload, shells, background_field=None):
    """The command a hook payload would run, judged: "wait" or "unread" to turn it back, "" to let it through.
    shells: the tool names that run a shell command; background_field: the tool_input field that says it already
    runs in the background (where waiting is free; a commit over an unread test is not)."""
    if not isinstance(payload, dict) or payload.get("tool_name") not in shells:
        return ""
    args = payload.get("tool_input")
    if isinstance(args, str):
        args = json.loads(args)
    if not isinstance(args, dict):
        return ""
    command = args.get("command") if "command" in args else args.get("cmd")
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)
    if not isinstance(command, str):
        return ""
    if commits_unread(command):
        return "unread"
    return "wait" if not (background_field and args.get(background_field) is True) and waits(command) else ""


def hook(console, stdin=None, stdout=None):
    """The PreToolUse hook: reads the program's payload, prints its deny when the command waits by sleeping or
    commits after a piped test run. Any error lets the command through."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    out = ""
    try:
        from .providers import get
        provider = get(console, strict=True)
        payload = json.loads(stdin.read() or "null")
        why = verdict(payload, provider.SHELL_TOOLS, provider.SHELL_BACKGROUND)
        if why:
            out = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                     "permissionDecisionReason": UNREAD if why == "unread"
                                                     else provider.WAIT_FREE}})
        elif provider.HOOK_NEEDS_JSON:
            out = "{}"
    except Exception:
        out = "{}" if console == "codex" else ""     # PROVIDER: Codex's hooks answer in JSON, as its others do
    if out:
        print(out, file=stdout, flush=True)
    return 0
