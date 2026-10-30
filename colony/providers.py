"""Who runs a project's agent. Each program colony can run (Claude Code, Codex) is a Provider: how to start its CLI in
tmux, how to wire a project so the agent knows the colony and gets its notes and mail, and how to read its screen and
records. Provider below is the one place this is written down: each shape colony asks of a program is a member there,
saying what it is, with what colony does when a program doesn't supply it. A program joins by subclassing it, filling
in the required members (REQUIRED) and what it does its own way, and adding an entry to PROVIDERS; the board, settings
and forms offer it from then on, and `colony doctor` lists each shape as its own, colony's default, or missing.

Code outside this file asks a program for a shape by calling it, never by checking which program it is or whether it
has the member; what only one program can do is marked with a `PROVIDER:` comment where it is used. The colony's own
mechanisms (notes, gates, mail, the roadmap, the watcher) are provider-agnostic: files in .board/, the `colony`
command, and text typed into a tmux session. Keep new ones that way.
"""
import json
import os
import re
import sys
import shlex
from pathlib import Path

from .jsonl import read_line

SOURCE = Path(__file__).resolve().parent.parent     # colony's own source folder, wherever it is now


def _signin_environment():
    """A browser login chooses native account credentials, rather than an inherited API/token override."""
    env = dict(os.environ)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_API_KEY",
                 "CLAUDE_CODE_OAUTH_REFRESH_TOKEN", "CLAUDE_CODE_OAUTH_SCOPES",
                 "OPENAI_API_KEY", "OPENAI_ACCESS_TOKEN", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN",
                 "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
        env.pop(name, None)
    return env


def _signin_url(text, endpoints):
    """Only the native provider's authorization endpoint can become a link; never terminal escape codes or userinfo."""
    from urllib.parse import urlsplit
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    for value in re.findall(r"(?<![A-Za-z0-9_:/.-])https://[^\s<>\"']+", plain):
        try:
            url = urlsplit(value)
            if (url.hostname, url.path) in endpoints and url.scheme == "https" and not url.username and \
                    not url.password and url.port in (None, 443) and not url.fragment and "\\" not in value:
                return value
        except ValueError:
            pass
    return None


def signin_eligible(key_, provider):
    """Only an installed program that is enabled or still serves a project needs account checks."""
    if not installed(provider):
        return False
    if enabled(provider):
        return True
    from . import board
    return any(root.exists() and of(root) is provider for root in board.projects())


def _newest_first(path, most=1 << 22, block=1 << 16):
    """A file's lines from its end backwards, at most `most` bytes back: a look back that stops early reads little
    of a transcript that has grown large."""
    with open(path, "rb") as fh:
        pos, rest = fh.seek(0, 2), b""
        while pos > 0 and most > 0:
            step = min(block, pos, most)
            pos, most = pos - step, most - step
            fh.seek(pos)
            lines = (fh.read(step) + rest).split(b"\n")
            rest = lines.pop(0)                  # it may start mid-line: read again with the block before it
            yield from (line for line in reversed(lines) if line.strip())
        if pos == 0 and rest.strip():
            yield rest


def _turn_marks(path, start, started):
    """A Codex rollout read from `start` (a line cut there skipped) to its last whole line: (where that ends, when
    its open turn started or None, whether any turn's start or end was read)."""
    seen = False
    with open(path, "rb") as f:
        f.seek(max(0, start - 1))
        if start and f.read(1) != b"\n":                # begun mid-line: from the next one
            start += len(f.readline())
        at = start
        for line in f:
            if not line.endswith(b"\n"):
                break
            at += len(line)
            if b'"event_msg"' in line and (b'"task_started"' in line or b'"task_complete"' in line or b'"turn_aborted"' in line):
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                kind = (e.get("payload") or {}).get("type")
                if kind == "task_started":
                    started, seen = _stamp(e), True
                elif kind in ("task_complete", "turn_aborted"):
                    started, seen = None, True
    return at, started, seen


def _stamp(entry):
    """A transcript entry's time, as a timestamp (0 without one)."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(entry.get("timestamp")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _mail_diagnostic(text, prompt, error, assistant, stopped):
    """The last output row before the current input, never the input or an earlier successful reply."""
    lines = re.sub(r"\x1b\[[0-9;]*m", "", text).splitlines()
    inputs = [i for i, line in enumerate(lines) if re.match(prompt, line)]
    if inputs:
        lines = lines[:inputs[-1]]
    elif not stopped:
        return None
    rows = [(i, line) for i, line in enumerate(lines)
            if line.strip() and not re.match(r"^\s*[─━]{4,}", line)]
    if not rows:
        return None
    at, line = rows[-1]
    found = re.fullmatch(error, line)
    if not found:
        return None
    # An assistant's quoted example or tool output is not the program's own diagnostic. A later input starts
    # another turn; within a reply, conservatively leave the reason unknown rather than guessing from its prose.
    last_input = max((i for i, line in enumerate(lines[:at]) if re.match(prompt, line)), default=-1)
    if any(re.match(assistant, line) or line.lstrip().startswith("```") for line in lines[last_input + 1:at]):
        return None
    return found.group("error")


def _mail_reason(provider, diagnostic):
    """A known cause without any provider error body, which may contain tokens, URLs or project material."""
    if diagnostic is None:
        return None
    body = re.sub(r"^(?:API Error:|Error:)\s*", "", diagnostic, flags=re.I)
    http = re.match(r"(?:(?:unexpected status|HTTP(?: status)?)[ :]+)?([45]\d\d)\b", body, re.I)
    status = int(http[1]) if http else None
    if status == 401:
        code, why = 'authentication', 'authentication failed (HTTP 401)'
    elif status == 403:
        code, why = 'authorization', 'access refused (HTTP 403)'
    elif status == 429:
        code, why = 'usage-limit', 'request rate limit reached (HTTP 429)'
    elif status and 500 <= status <= 599:
        code, why = 'server-error', f'provider request failed (HTTP {status})'
    elif re.match(r"(?:Invalid (?:authentication |API )?(?:token|key|credentials)|authentication failed|not logged in|"
                  r"Login expired|(?:Your )?OAuth (?:token|credentials?) (?:has |have )?(?:expired|been revoked)|"
                  r"(?:Your )?refresh token (?:has |was )?(?:expired|revoked|already been used))\b", body, re.I):
        code, why = 'authentication', 'authentication failed'
    elif hit := re.match(r"You've hit your (?:(session|usage|weekly|monthly spend|rate) )?limit\b", body):
        code, why = 'usage-limit', (hit[1] + ' ' if hit[1] else 'usage ') + 'limit reached'
    elif provider.NETWORK.match(body):
        code, why = 'network', 'provider connection failed'
    else:
        return None
    return {'code': code, 'reason': f'{provider.label} {why}.'}


TURN_WAIT = 2.0             # seconds an end-of-turn hook reads the transcript again while the turn shows no words yet


def turn_prose(provider, path, wait=0):
    """A turn's prose from its program's transcript (Provider.events): what the agent said since the last message into
    it, read again for up to `wait` seconds while there is none yet, since a program can still be writing it as its
    end-of-turn hook runs."""
    import time
    until = time.time() + wait
    while True:
        there = bool(path) and Path(path).exists()
        evs = provider.events(path) if there else []
        start = max((i for i, (kind, _, _) in enumerate(evs) if kind in ("you", "colony", "compact")), default=-1)
        said = [text for kind, text, _ in evs[start + 1:] if kind == "say"]
        if said or not there or time.time() >= until:
            return "\n\n".join(said)
        time.sleep(0.2)


def _keep(path, text):
    """A file colony writes, rewritten only when it differs and removed when there is nothing to write: what changed."""
    if not text:
        if path.exists():
            path.unlink()
            return [path]
        return []
    if path.exists() and path.read_text() == text:
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return [path]


def _block(have, headings):
    """Where colony's section sits in an instructions file: from its heading, or an old one, as a whole line ("## The
    board is yours to keep healthy", the steward's own section, is not "## The board", and was cut out at every start
    when it was taken for it), to the next section or the end. None when it isn't there."""
    found = [re.search(rf"(?m)^{re.escape(m)}[ \t]*$", have) for m in headings]
    marker = next((f for f in found if f), None)
    if not marker:
        return None
    end = have.find("\n## ", marker.start() + 5)
    return marker.start(), (end + 1 if end != -1 else len(have))


def _place(path, block, headings):
    """Colony's section in an instructions file: an older one brought up to date in place, else added at the end."""
    have = path.read_text() if path.exists() else ""
    span = _block(have, headings)
    if span:
        return _keep(path, have[:span[0]] + block + have[span[1]:])
    return _keep(path, have + ("\n" if have and not have.endswith("\n") else "") + block)


def _cut(path, headings):
    """Colony's section taken out of an instructions file, the rest left as it was: for a private project, whose
    tracked file must not carry it. A file that held nothing else goes."""
    have = path.read_text() if path.exists() else ""
    span = _block(have, headings)
    return _keep(path, have[:span[0]] + have[span[1]:]) if span else []


class Provider:
    """What colony asks of a program, each shape with what stands when the program doesn't supply it."""
    # --- what it is: shown in the forms and settings, and checked installed before it's offered (all required)
    program = site = label = instructions = None   # its command, where to get it, its name, the file it reads a
    #                                                 project's instructions from (two sharing a folder differ)
    models, efforts = (), ()           # suggestions, any value can be typed: models as (id, name) pairs
    aliases, efforts_for = {}, {}      # names it takes for models; the efforts a model takes, where they differ
    reports_cost = True                # whether consult() reports dollars, not only tokens

    def command(self, label, s, resume=None, root=None):
        """The shell command that starts its interactive agent in tmux with the settings in `s`: provider, model,
        effort, permissions (ask|edits|all|plan) and remote (on|off); for the steward, also autocompact. Each maps to
        the CLI's own flags; one it has no equivalent for is ignored. Required."""
        raise NotImplementedError

    def wire(self, root, protocol, private=False):
        """Put the colony protocol where the CLI reads project instructions, and arrange for `colony notes --deliver`
        output to reach the agent at session start (`--session`) and before each turn typed to it. Rewritten when it
        differs, at tracking, at board start and daily (board.deploy). Private (colony track --private), all of it
        goes in files of the CLI's that the project's history need never see, named in private_files. Required."""
        raise NotImplementedError

    def wired(self, root, private=False):
        """Whether wire() has been done (the doctor asks). Required."""
        raise NotImplementedError

    # What wire(private=True) and write_helpers put in a project, as patterns for its repository's local exclude
    # (relative to the project's folder); None where the program reads its instructions only from a file the project
    # may track, so a private project can't run on it.
    private_files = None

    def classify(self, screen):
        """"working" | "needs you" | "idle" from its terminal screen: the watcher and the steward's wake-ups depend
        on it, so it matches the CLI's own busy and prompt markers. Required."""
        raise NotImplementedError

    def mail_problem(self, text, *, stopped=False):
        """A current console diagnostic preventing a reply, {code, reason}, or None when unknown. Reasons contain
        only a known cause, never arbitrary console text. stopped permits diagnostics from a verified dead console."""
        return None

    # --- its models
    def model_name(self, value):
        """A model ID or alias as the model's own name, for "Default (Opus 5.5)"."""
        return value

    def own_defaults(self):
        """The model and effort the CLI uses when colony names none ({"model", "effort"}), shown as "Default (...)"."""
        return {}

    def discover(self, run=None):
        """The models it can run here, [(id, name, efforts)], asking the program and confirming each with a trivial
        call where it has no command that lists them. Without, the cards come from `models`."""
        return None

    def catalog(self):
        """discover() from the files it keeps alone, calling nothing (the daily check)."""
        return self.discover()

    def version(self):
        """Its installed version, as it prints it (None: unknown)."""
        return None

    def update(self):
        """Install its newer version beside the running one; consoles move over when idle (auto update)."""

    def signed_in(self, run=None):
        """False when it is signed out here, so its consoles can't work (None: can't tell)."""
        return None

    def signin_command(self):
        """Its native, non-model browser login argv, or None when this program has no board login yet."""
        return None

    def signin_environment(self):
        """The native login's environment; credential exchange and storage stay with the program."""
        return dict(os.environ)

    def signin_prompt(self, text):
        """Only a validated authorization URL, device code and whether stdin takes a browser code. Never raw output."""
        return {}

    def signin_input(self, code):
        """One browser authorization code for its waiting login, never a command."""
        raise ValueError("This sign-in does not accept a code on the board.")

    def signin_verified(self):
        """Whether the just-completed native login stored a sign-in, before retiring a previous source."""
        return self.signed_in()

    def signin_complete(self):
        """After verified native login, retire an old Colony override if it would shadow the new sign-in."""

    def signin_refresh(self, root):
        """Let an idle console reload the new credentials; False when background work still needs its process."""
        return True

    signin_help = "Sign in in your provider's browser, then return here."

    def sandbox_problem(self, run=None):
        """Why its sandbox can't run here, in a line (None: it can, or it has none)."""
        return None

    def catalog_freshness(self, now):
        """How fresh its own model catalog is, for Settings, calling nothing: {"fields": [(name, value)], "warnings"}."""
        return {"fields": [], "warnings": []}

    app = None                         # the app its consoles are reached from besides the board (None: none)
    reach = None                       # the setting that makes a console reachable there, as Settings names it

    # --- its console in tmux
    scrolled_marker = ""               # on screen while its view is scrolled up from the latest; the phone's ↓ follows it
    scrolls_in_tmux = False            # it draws inline and takes no mouse: its history is tmux's, a swipe scrolls that
    queue_keys = None                  # its queue of messages sent mid-turn: {"now", "back", "clear"} keys (None: no queue)
    dismiss_keys = None                # keys that put away a choice on its screen, so a message can go instead (None: none)
    enter_after = 0.3                  # seconds between typing a message and pressing Enter

    def startup_files(self, root):
        """Files its console's start reads, so a change to one restarts an idle console."""
        return []

    def stale(self, root):
        """Why its running console no longer matches how it should run, only its program knowing (None: it does)."""
        return None

    def latest_conversation(self, folder):
        """Its newest conversation in a folder, to resume when colony recorded none (None: start afresh)."""
        return None

    def draft(self, screen):
        """What the person half-typed in its box and didn't send, from a screen captured with its styles: "" if
        nothing, None if it can't tell. Nothing is typed into a session while there is one."""
        return ""

    def pending_input(self, text, left):
        """Whether a visible nonempty draft is a prefix of the input we just wrote.

        Providers that compress pastes may recognize their own preview as well.
        An unrelated changed draft is never an acknowledgement of delivery.
        """
        return bool(left) and text.startswith(left)

    def choice(self, screen):
        """The choice on screen (a trust question, a permission prompt): (question lines, options, highlighted), or
        None; the board shows it as buttons."""
        return None

    def choose(self, screen, text):
        """The keys that pick the option of choice() matching `text` (`colony choose`, the buttons, start-up)."""
        return None

    def activity(self, screen):
        """What it's doing now, off its screen: {"line": the current activity or None, "agents": [{"name", "detail",
        "current"}]}, the project's status."""
        return {"line": None, "agents": []}

    def elapsed(self, screen):
        """The timer its live working line shows ("3m 12s"), or None: one reading the same for 15 minutes on the
        same process means it froze, and the watcher restarts it (recovery.py)."""
        return None

    def unfreeze(self, root):
        """Before a frozen console restarts, stop what runs its turn outside the console's own process if that froze
        too; False when it still answers and was kept, so the turn goes on."""
        return True

    def busy(self, root, name):
        """Whether it is at work where its screen doesn't show it (background, a turn run elsewhere), or None."""
        return None

    def at_rest(self, root):
        """Whether nothing of its own runs for the project beyond what its screen shows (`colony close` waits for
        it); False when that can't be told."""
        return True

    def commands(self, pane):
        """The commands its agent left running from the console in `pane` (lingering.py)."""
        return []

    def let_go(self):
        """Stop what it keeps running for a project that no longer runs on it (moved to another program, or off the
        board), once that is at rest; the watcher asks every few minutes. The projects let go."""
        return []

    def keeps(self):
        """The processes it keeps running on purpose past their console's end, for projects still on it: never
        named as left behind (lingering.left)."""
        return set()

    # --- installation repair access, independent of registered projects
    def repair_status(self, home):
        """A live native repair host's connection state; unsupported programs provide no repair host."""
        raise NotImplementedError

    def repair_start(self, home, settings):
        """Ensure one detached native host, without creating a conversation or submitting work."""
        raise NotImplementedError

    def repair_pair(self, home, manual_code=None):
        """Explicitly generate/check a pairing code on the existing repair host; never persist the code."""
        raise NotImplementedError

    def repair_stop(self, home):
        """Gracefully let an idle repair host go; False preserves busy or unreachable work."""
        return True

    def repair_keeps(self, home):
        """Owned live host PIDs, checked against their recorded process start time, without native probes."""
        return set()

    def helpers(self, root):
        """Its helpers at work, [{name, what, stats}], for a program that doesn't list them on its screen (None:
        its screen says them)."""
        return None

    # --- its records
    def conversation(self, payload):
        """Which conversation a hook ran in, and where it is kept, (id, path): for a console restarted to resume
        it (None: it can't say)."""
        return None

    def turn_text(self, payload):
        """What the agent wrote in the turn that just ended, and a key for it, (key, text): a turn that asks the
        person something shows under Waiting on you."""
        return None, ""

    def events(self, path):
        """Its conversation, from its transcript, as Chat shows it: the person's messages, the agent's prose, one
        step per tool it ran (chat.turns reads them)."""
        return []

    def history_text(self, root):
        """The session's conversation as plain text, for Select on a phone (None: tmux's own scrollback)."""
        return None

    def background(self, path):
        """What the agent left running in the background, and how each ended, from its transcript: [{id, kind,
        what, at, state, ended, exit}], state running until its end is read; Chat's task list."""
        return []

    def active_seconds(self, root):
        """How long its agent has spent at work in the project, all told: turns only, not time waiting for the
        person (None: it can't tell)."""
        return None

    api_host = None                    # a host its requests go to: when it takes a connection again, the network is back

    def network_stop(self, path):
        """What a network drop stopped, from its transcript: when its last turn ended on a network error its retries
        gave up on, {"key", "at", "error", "helpers", "ok_at"}; else None. The watcher types one nudge once
        api_host answers again (recovery.py)."""
        return None

    # --- its helpers, goals and fresh views
    def helper_name(self, tier):
        """The name a helper tier is started by in it (None: it has no named helpers)."""
        return None

    def write_helpers(self, root, tiers):
        """Keep its helper definitions at the project's tiers; returns the files changed."""
        return []

    def goal_client(self, root, timeout=15):
        """A client for its own goal per conversation, which bounded continuation pauses and resumes; a program
        without one is continued by a wake typed into its idle console (continuation.py)."""
        raise NotImplementedError

    def consult(self, brief, model, effort, project):
        """One fresh, stateless consultation: an empty folder, no settings or hooks, read-only; {"text", "cost",
        "usage", "error"} (colony consult). A program without it isn't offered as a consultant."""
        raise NotImplementedError

    def scout(self, brief, model, effort, run=None):
        """One fresh, stateless scout for the steward: only searching the web and reading pages, held there by the
        program itself, so strangers' text never reaches the session that speaks for the person; {"text", "error"}."""
        raise NotImplementedError


REQUIRED = ("program", "site", "label", "instructions", "models", "efforts", "command", "wire", "wired", "classify")


def supplies(provider, shape):
    """Whether a program does a shape its own way, rather than leaving it to colony's default."""
    return getattr(type(provider), shape, None) is not getattr(Provider, shape, None)


def shapes(provider):
    """Each shape colony asks of a program: (name, "its own" | "colony's default" | "missing")."""
    return [(n, "its own" if supplies(provider, n) else "missing" if n in REQUIRED else "colony's default")
            for n in Provider.__dict__ if not n.startswith("_")]


class ClaudeCode(Provider):
    label = "Claude Code"
    # Its queue, from its documentation (interactive mode, v2.1.281 on): Ctrl+X Ctrl+S sends what is queued now
    # (work that can go to the background goes on there), Up on the box's first line takes it back into the box.
    queue_keys = {"now": ["C-x", "C-s"], "back": ["Up"], "clear": "C-u"}
    # PROVIDER: Esc cancels a choice (its "Esc to cancel"): a question it asked ends its turn unanswered, a permission
    # is refused, and its box is back within a second for the person's message
    dismiss_keys = ["Escape"]

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
    app, reach = "the Claude app", "Remote Control"
    hooks = {"SessionStart": "colony notes --deliver --session", "UserPromptSubmit": "colony notes --deliver",
             "Stop": "colony turn", "PreCompact": "colony context --hook before --console claude"}

    def command(self, label, s, resume=None, root=None):
        """The person's own `claude` with the project's choices: permissions, Remote Control, model, effort;
        with resume, back in that conversation."""
        from .board import PERMISSIONS
        parts = ["claude"] + (["--resume", shlex.quote(resume)] if resume else [])
        if root:
            from . import lead
            if str(Path(root).resolve()) == lead.info(root)['lead'] and lead.plan_root(root) != Path(root):
                parts += ['--add-dir', shlex.quote(str(lead.plan_root(root)))]
            for workspace in lead.workspaces(root):
                parts += ['--add-dir', shlex.quote(str(workspace))]
        if self.token():
            # the long-lived sign-in, read from its file as the console starts: never written into the command
            parts.insert(0, f'{self.TOKEN_ENV}="$(cat {shlex.quote(str(self.token_path()))})"')
        if PERMISSIONS.get(s.get("permissions") or "ask"):
            parts += ["--permission-mode", PERMISSIONS[s["permissions"]]]
        if s.get("remote"):
            parts += ["--remote-control", shlex.quote(label.lstrip("- ") or "colony")]   # "-x" would read as an option
        if s.get("model"):
            parts += ["--model", shlex.quote(s["model"])]
        if s.get("effort"):
            parts += ["--effort", shlex.quote(s["effort"])]
        if s.get("autocompact"):
            parts += ["--autocompact", shlex.quote(s["autocompact"])]     # how large its conversation may grow
        from . import connections                       # the outside services granted it, the person's own denied
        parts += [shlex.quote(x) for x in connections.claude_args(root)]
        return " ".join(parts)

    def wire(self, root, protocol, private=False):
        """The colony protocol in CLAUDE.md (an older block brought up to date in place) and the delivery hooks
        in .claude/settings.json, added to whatever the project already has. Private, they go in CLAUDE.local.md and
        .claude/settings.local.json, which Claude Code reads beside the shared ones, and come out of the shared ones
        if an earlier wiring put them there."""
        block = protocol.lstrip("\n")
        if block:                                  # nothing to deploy (the steward, whose brief is its own): left as it is
            if private:
                _cut(root / "CLAUDE.md", self.HEADINGS)
            _place(root / ("CLAUDE.local.md" if private else "CLAUDE.md"), block, self.HEADINGS)
        shared = root / ".claude" / "settings.json"
        settings = root / ".claude" / "settings.local.json" if private else shared
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
        own = json.loads(shared.read_text()).get("statusLine") if private and shared.exists() else None
        if not own or own.get("command") == "colony statusline":
            cfg.setdefault("statusLine", {"type": "command", "command": "colony statusline"})
        # PROVIDER: Claude Code also loads the CLAUDE.md of every folder above the project, so one inside colony's
        # source folder (projects/, where new ones go) would read colony's own development rules and the protocol a
        # second time on every request. Its settings skip that one file, at colony's path now; colony keeps its own.
        colony_md, excluded = str(SOURCE / "CLAUDE.md"), cfg.get("claudeMdExcludes") or []
        inside = SOURCE in Path(root).resolve().parents
        if inside and colony_md not in excluded:
            cfg["claudeMdExcludes"] = excluded + [colony_md]
        elif not inside and colony_md in excluded:             # moved out of colony's folder, or colony itself
            cfg["claudeMdExcludes"] = [p for p in excluded if p != colony_md]
            if not cfg["claudeMdExcludes"]:
                del cfg["claudeMdExcludes"]
        _keep(settings, json.dumps(cfg, indent=2) + "\n")
        if private and shared.exists():
            self._unhook(shared)

    HEADINGS = ("## This project is part of a colony", "## The board")
    private_files = ("CLAUDE.local.md", ".claude/settings.local.json", ".claude/agents/colony-*.md")

    def _unhook(self, settings):
        """Colony's hooks and status line taken out of a shared settings file, the rest left; rewritten only if
        they were there."""
        cfg = json.loads(settings.read_text())
        ours = {e["hooks"][0]["command"] for entries in hook_entries(self).values() for e in entries}
        hooks, found = cfg.get("hooks", {}), False
        for event in list(hooks):
            for entry in hooks[event]:
                kept = [h for h in entry.get("hooks", []) if h.get("command") not in ours]
                found |= len(kept) != len(entry.get("hooks", []))
                entry["hooks"] = kept
            hooks[event] = [e for e in hooks[event] if e.get("hooks")]
            if not hooks[event]:
                del hooks[event]
        if "hooks" in cfg and not hooks:
            del cfg["hooks"]
        if (cfg.get("statusLine") or {}).get("command") == "colony statusline":
            del cfg["statusLine"]
            found = True
        if found:
            _keep(settings, json.dumps(cfg, indent=2) + "\n" if cfg else "")

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
            cheap = (bench.tiers_for("claude").get("rote") or {}).get("model")     # the cheapest worth its points
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
        env = dict(os.environ)
        if token := self.token():
            env[self.TOKEN_ENV] = token
        return self._signin_status(env, run)

    def _signin_status(self, env, run=None):
        import subprocess
        try:
            result = (run or subprocess.run)([self.program, "auth", "status", "--json"],
                                            env=env, capture_output=True, text=True, timeout=15)
            data = json.loads(result.stdout or "{}")
            value = data.get("loggedIn") if isinstance(data, dict) else None
            return value if isinstance(value, bool) else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None

    # PROVIDER: Claude's own login supports a remote browser and an authorization code pasted into stdin.
    def signin_command(self):
        return [self.program, "auth", "login", "--claudeai"]

    def signin_environment(self):
        env = _signin_environment()
        env["CLAUDE_CONFIG_DIR"] = str(self.config_home())
        env["BROWSER"] = "true"                 # the board shows the URL; never open a browser on the server
        return env

    signin_help = "Open the Claude sign-in link on this phone. Copy the code the provider shows, return here and paste it below."

    def signin_prompt(self, text):
        url = _signin_url(text, {("claude.com", "/cai/oauth/authorize"),
                                 ("platform.claude.com", "/oauth/authorize")})
        return {"url": url, "accept_code": True} if url else {}

    def signin_input(self, code):
        code = code.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.~+/=-]{8,2048}#[A-Za-z0-9_.~+/=-]{8,2048}", code):
            raise ValueError("Paste the one-time code from the Claude sign-in page.")
        return code + "\n"

    def signin_verified(self):
        return self._signin_status(self.signin_environment())

    def signin_complete(self):
        self.set_token("")                       # only after the native login has succeeded and been verified

    def latest_conversation(self, folder):
        """The conversation last active in a folder, from Claude Code's own records: for a console whose hooks
        don't record it (the steward's)."""
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
                    "chores": "simple work that takes minor discernment: small edits, short clear instructions, light checks",
                    "rote": "clear, mechanical tasks: copying, renaming, running a named command or test, simple lookups"}

    @staticmethod
    def helper_name(tier):
        return "colony-" + tier.replace("-", "")

    def write_helpers(self, root, tiers):
        """A helper definition per tier in .claude/agents, so a helper runs at exactly its tier's model and effort
        (the Agent tool picks a model only by alias, and no effort). Rewritten when a tier changes; a tier colony
        can't fill leaves no file. Returns the files it changed."""
        changed = []
        for tier, brief in self.HELPER_BRIEF.items():
            t = tiers.get(tier)
            changed += _keep(root / ".claude" / "agents" / f"{self.helper_name(tier)}.md", t and (
                f"---\nname: {self.helper_name(tier)}\ndescription: Colony's {tier} tier: {brief}.\n"
                f"model: {t['model']}\n" + (f"effort: {t['effort']}\n" if t.get("effort") else "")
                + "---\n\nDo the task you are given within its brief, and hand in what you find and do.\n"
                "<!-- written by colony from this project's tiers (colony models); edits here are replaced -->\n"))
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

    # PROVIDER: Claude Code holds a process to the tools it's given. The scout's: search and fetch, and nothing else
    # (no shell, edits, local files, MCP servers or helpers); anything else that would ask is refused unasked.
    SCOUT_TOOLS = "WebSearch,WebFetch"

    def scout(self, brief, model, effort, run=None):
        """One fresh, stateless scout in an empty folder, with none of the person's settings, hooks, MCP servers or
        CLAUDE.md files: it can search the web and read pages, and nothing else (checked on Claude Code 2.1.288)."""
        import os
        import subprocess
        import tempfile
        cmd = [self.program, "-p", "--model", model, *(["--effort", effort] if effort else []), "--setting-sources", "",
               "--strict-mcp-config", "--permission-mode", "dontAsk", "--tools", self.SCOUT_TOOLS,
               "--allowedTools", self.SCOUT_TOOLS, "--disallowedTools", "Bash,Edit,Write,NotebookEdit,Read,Grep,Glob,Agent",
               "--no-session-persistence", "--output-format", "json"]
        with tempfile.TemporaryDirectory(prefix="colony-scout-") as here:
            try:
                env = dict(os.environ, **({self.TOKEN_ENV: self.token()} if self.token() else {}))
                d = json.loads((run or subprocess.run)(cmd, input=brief, capture_output=True, text=True, cwd=here, env=env,
                                                       timeout=1800).stdout)
            except (ValueError, OSError, subprocess.TimeoutExpired) as err:
                return {"text": "", "error": err.__class__.__name__}
        from . import tokens
        tokens.note("claude", d.get("usage"))           # a scout keeps no transcript: what it spent is noted here
        if d.get("is_error"):
            return {"text": "", "error": str(d.get("result") or d.get("subtype") or "error")[:300]}
        return {"text": d.get("result") or "", "error": None}

    def conversation(self, payload):
        """Which conversation a hook ran in, and where it is kept: for a console that must be restarted."""
        return payload.get("session_id"), payload.get("transcript_path")

    def model_name(self, value):
        """The model a value means, by name: an ID or alias; anything else as typed."""
        return dict(self.models).get(value) or self.aliases.get(str(value).lower()) or value

    def turn_text(self, payload, wait=None):
        """What the agent wrote in the turn that just ended, from the Stop hook's payload: every piece of prose
        since the person's last message, and a key naming that turn. PROVIDER: Claude Code hands its Stop hook the
        turn's last words (last_assistant_message) and keeps the rest in its transcript, which it may not have
        written yet as the hook runs: read 0.2 s after those words, a turn read empty, the agent's `colony
        needs-you` came to nothing, and its question never waited on the person. So its last words are taken as
        handed over, the transcript adds what came before them, and a build that hands over none has its transcript
        read again for a moment (TURN_WAIT) while the turn shows no words."""
        import hashlib
        import time
        last, path = (payload.get("last_assistant_message") or "").strip(), payload.get("transcript_path")
        until = time.time() + (TURN_WAIT if wait is None else wait)
        while True:
            key, prose = self._turn(path)
            if prose or last or not (path and Path(path).exists()) or time.time() >= until:
                break
            time.sleep(0.1)
        if last and " ".join(last.split()) not in " ".join(" ".join(prose).split()):
            prose.append(last)                                 # its last words, not in the transcript yet
            key = key or "said-" + hashlib.sha1(last.encode()).hexdigest()[:12]
        return key, "\n\n".join(prose)

    @staticmethod
    def _turn(path):
        """(key, prose) of the transcript's last turn: the uuid of its last words, and each piece of prose since the
        person's last message."""
        if not path or not Path(path).exists():
            return None, []
        prose, key = [], None
        with open(path, "rb") as fh:
            while True:
                line, complete = read_line(fh)
                if line == b"":
                    break
                if line is None:                              # an oversized tool result: keep scanning
                    if not complete:
                        break
                    continue
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
        return key, prose

    def active_seconds(self, root):
        """Time at work in this folder, all told: Claude Code closes each turn with its length (turn_duration) in
        the transcript. A turn not closed (still running, interrupted, only a /command) counts the time between its
        own lines, leaving out any gap over IDLE_GAP: a session resumed days later, not work. Transcripts only grow,
        so each is read on from where the last look stopped. PROVIDER: reads Claude Code's own transcripts."""
        import re
        from datetime import datetime
        stamp = lambda e: datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
        from .board import workdir
        folder = self.config_home() / "projects" / re.sub(r"[^A-Za-z0-9-]", "-", str(workdir(root).resolve()))
        total = 0.0
        for f in folder.glob("*.jsonl"):
            at, ms, open_, last = self._turns.get(f, (0, 0, 0.0, None))
            size = f.stat().st_size
            if size < at:
                at, ms, open_, last = 0, 0, 0.0, None              # rewritten: read it afresh
            if size > at:
                with f.open("rb") as fh:
                    fh.seek(at)
                    while True:
                        line, complete = read_line(fh, edges=512)
                        if not complete:                         # a line still being written waits
                            break
                        at = fh.tell()
                        if line is None:
                            continue
                        try:
                            if b'"turn_duration"' in line:
                                ms, open_, last = ms + json.loads(line).get("durationMs", 0), 0.0, None
                            elif re.search(rb'"type":\s*"(user|assistant)"', line):
                                found = re.findall(rb'"timestamp":\s*"([^"]+)"', line)   # the line's own is its last
                                t = stamp({"timestamp": found[-1].decode()} if found else json.loads(line))
                                if last is not None and 0 < t - last <= self.IDLE_GAP:
                                    open_ += t - last
                                last = t if last is None else max(last, t)
                        except (ValueError, KeyError):
                            pass
                self._turns[f] = (at, ms, open_, last)
            total += ms / 1000 + open_
        return total

    IDLE_GAP = 60 * 60          # a step can run long (a helper, a long test); a session resumed later is hours or days on
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

    # What Claude Code says when the network, not the work, ended a request its retries gave up on: the connection
    # errors it names, and a reply cut off mid-stream. A usage limit, a sign-in, a refusal or a server error is none.
    NETWORK = re.compile(
        r"Can't reach the API server|No internet route|Connection (?:dropped|refused|error|lost|closed before)|"
        r"Connection to the API was lost|Unable to connect to API(?!: SSL)|timed out|No response from (?:the )?API|"
        r"The response stopped arriving|Part of the response never arrived|socket hang up|fetch failed|"
        r"\b(?:ECONNRESET|ECONNREFUSED|ECONNABORTED|ETIMEDOUT|EAI_AGAIN|ENOTFOUND|ENETUNREACH|ENETDOWN|EHOSTUNREACH|EPIPE)\b")
    # A background helper's report that it stopped, as Claude Code hands it to the agent.
    LOST = re.compile(r"<task-id>(?P<id>[^<]+)</task-id>.*?<status>failed</status>\s*"
                      r"<summary>Agent \"(?P<name>.*?)\" failed: (?P<error>.*?)</summary>", re.S)
    api_host = "api.anthropic.com"

    def network_stop(self, path):
        """What a network drop stopped in this session, if its last turn ended on one (see the list at the top).
        Claude Code retries a request for about six minutes, then gives up: each helper is reported to the agent as
        failed ("Agent terminated early due to an API error: API Error: Can't reach the API server ..."), and the
        agent's own turn ends on that error, as does each turn those reports start while the network is still
        down. Read back from the transcript's end only as far as the agent's last real reply. PROVIDER: reads
        Claude Code's own transcript."""
        stop, helpers = None, []
        for line in _newest_first(path):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            content = (e.get("message") or {}).get("content")
            text = content if isinstance(content, str) else "\n".join(
                c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")
            if e.get("type") == "assistant":
                if not e.get("isApiErrorMessage"):
                    return dict(stop, helpers=helpers[::-1], ok_at=_stamp(e)) if stop else None   # it replied since
                if stop is None:
                    if not self.NETWORK.search(text):
                        return None                # its last turn ended on something else: a limit, a sign-in
                    stop = {"key": e.get("uuid") or e.get("timestamp"), "at": _stamp(e),
                            "error": text.removeprefix("API Error: ").strip()}
            elif e.get("type") == "user" and text.strip():
                if stop is None:
                    return None                    # a turn began after its last reply: under way, or just typed
                m = self.LOST.search(text)
                if m and "API Error" in m["error"] and self.NETWORK.search(m["error"]):
                    helpers.append({"key": f"{m['id']}@{e.get('timestamp')}", "id": m["id"], "name": m["name"],
                                    "at": _stamp(e), "error": m["error"]})
        return dict(stop, helpers=helpers[::-1], ok_at=0.0) if stop else None

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

    def wired(self, root, private=False):
        claude_md = root / ("CLAUDE.local.md" if private else "CLAUDE.md")
        settings = root / ".claude" / ("settings.local.json" if private else "settings.json")
        hooks = json.dumps(json.loads(settings.read_text()).get("hooks", {})) if settings.exists() else ""
        return ("## This project is part of a colony" in (claude_md.read_text() if claude_md.exists() else "")
                and all(h['command'] in hooks for entries in hook_entries(self).values() for e in entries for h in e['hooks']))

    def mail_problem(self, text, *, stopped=False):
        # PROVIDER: Claude Code's API-error/usage rows, before its current input box. Unmarked errors only count
        # when the caller verified that this console ended; ordinary assistant prose and drafts never count.
        marker = r"\s*⎿\s*"
        if stopped:
            marker = r"(?:\s*⎿\s*)?"
        diagnostic = _mail_diagnostic(text, r"^\s*❯(?!\s*\d+\.)|^>\s*$",
                                      marker + r"(?P<error>API Error: .+|You've hit your .+|Login expired.*|Not logged in.*)",
                                      r"^\s*●\s", stopped)
        problem = _mail_reason(self, diagnostic)
        if problem:
            return problem
        # PROVIDER: the signed-out footer is below the actual input box, never assistant prose above it.
        lines = text.splitlines()
        box = self._box(lines)
        if box is not None and any(re.fullmatch(r"\s*Not logged in\s*[·•]\s*Please run /login\s*", line, re.I)
                                   for line in lines[box + 1:]):
            return {'code': 'authentication', 'reason': 'Claude Code is signed out.'}
        return None

    def classify(self, screen):
        """Claude Code shows "esc to interrupt" while it works and a numbered choice when it asks permission;
        anything else is waiting for the person to type. On a narrow window (a phone attached) the status line
        is cut short, so its spinner counts too: "✻ Calculating…" while working, "✻ Worked for 3s" once done.
        Its spinner lines stay in the transcript once passed ("✻ Waiting for 2 background agents to finish"), so
        with the box on screen only the live one counts: the last unindented line above it."""
        import re
        lines = screen.splitlines()
        box = self._box(lines)
        live = screen
        if box is not None:
            above = [l for l in lines[:box - 1] if l.strip() and not l[:1].isspace()]
            live = above[-1] if above else ""
        if "esc to interrupt" in screen.lower() or re.search(r"^\s*[✻✶✳✢✽·*+] \S[^\n]*?…", live, re.M) \
                or re.search(r"waiting for \d+ background", live.lower()):     # its background agents are still at work
            return "working"
        # A choice takes the typing box's place. While the box is there, "Do you want…" above it is the agent's
        # own prose, a question for the person to answer by typing, so only what's below the box counts.
        low = "\n".join(lines[box + 1:] if box is not None else lines).lower()
        if any(k in low for k in ("do you want", "trust this folder", "yes, proceed", "enter to select")) \
                or re.search(r"^\s*❯ \d+\.", low, re.M):
            return "needs you"
        return "idle"

    def busy(self, root, name):
        """A turn can end with work still at work: a command it put in the background, which Claude Code runs through
        its shell snapshot, or a helper agent, which its footer counts under the box ("· 1 shell", "· 2 agents")."""
        import re
        import subprocess
        from . import console
        pane = subprocess.run(["tmux", "display", "-p", "-t", name, "#{pane_pid}"], capture_output=True, text=True).stdout.strip()
        for pid in console._descendants(int(pane)) if pane.isdigit() else []:
            try:
                cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="replace")
            except OSError:
                continue
            if "/shell-snapshots/snapshot-" in cmd:
                return "a command it started is still running"
        lines = console.screen(name).splitlines()
        box = self._box(lines)
        foot = "\n".join(lines[box + 1:]) if box is not None else ""
        m = re.search(r"\b(\d+) (?:background )?(agents?|tasks?|shells?|monitors?)\b", foot)
        return f"{m.group(1)} {m.group(2)} still at work" if m else None

    def commands(self, pane):
        from . import lingering
        return lingering.claude_commands(pane)

    def background(self, path):
        from . import chat
        return chat.background(path)

    def catalog_freshness(self, now):
        from . import catalog_freshness
        return catalog_freshness.claude(self, now)

    def events(self, path):
        from . import chat
        return chat.claude_events(path)

    def elapsed(self, screen):
        """The timer on its live spinner line: "✶ Flowing… (3m 12s · ↓ 1.7k tokens)" gives "3m 12s"."""
        import re
        lines = screen.splitlines()
        box = self._box(lines)
        if box is None:
            return None
        above = [l for l in lines[:box - 1] if l.strip() and not l[:1].isspace()]
        m = re.match(r"\s*[✻✶✳✢✽·*+] \S[^\n]*?… \(((?:\d+h )?(?:\d+m )?\d+s)\b", above[-1] if above else "")
        return m.group(1) if m else None

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

    def pending_input(self, text, left):
        # PROVIDER: Claude normalizes pasted text; physical wraps can split a
        # word or omit boundary whitespace and add continuation padding.
        import unicodedata
        if not left:
            return False
        actual = unicodedata.normalize('NFC', text.replace('\r\n', '\n').replace('\r', '\n').replace('\t', '    '))
        offset = len(actual) - len(actual.lstrip())
        for index, row in enumerate(left.splitlines()):
            if index:
                row = row.lstrip()
                while offset < len(actual) and actual[offset].isspace():
                    offset += 1
            if not actual.startswith(row, offset):
                break
            offset += len(row)
        else:
            return True
        # A pill gives only a newline count. Recognize a whole expected pill,
        # never infer ownership from arbitrary mixed or multiple hidden pastes.
        rows = [row.strip() for row in left.splitlines()]
        for visible in (''.join(rows), ' '.join(rows)):
            pill = re.fullmatch(r'\[Pasted text #[1-9][0-9]*(?: \+([1-9][0-9]*) lines)?\]', visible)
            if pill and int(pill[1] or 0) == actual.count('\n'):
                return True
        return False

    def activity(self, screen):
        """What the session is doing, as Claude Code shows it: its spinner line ("✻ Waiting for 4 background
        agents to finish", "✻ Worked for 3s"), its background agents with their time and tokens, and its footer
        ("· 2 shells"), which those agents' rows can push off the last lines."""
        import re
        line, agents, lines = None, [], screen.splitlines()
        box = self._box(lines)
        for l in lines:
            if m := re.match(r"^\s*[✻✶✳✢✽·*+]\s+(\S.*?)\s*$", l):
                line = m.group(1)
            elif m := re.match(r"^\s*([●○◯◉])\s+([\w.:-]+)(?:\s{2,}(\S.*?))?\s*$", l):
                agents.append({"name": m.group(2), "detail": m.group(3) or "", "current": m.group(1) in "●◉"})
        foot = " ".join(l.strip() for l in lines[box + 1:] if "·" in l) if box is not None else ""
        return {"line": line, "agents": agents if len(agents) > 1 else [], "foot": foot}

    def choice(self, screen):
        """The choice on screen, if any: (question lines, options, index of the highlighted one). Claude Code
        draws a choice as a column of options, the highlighted one marked `❯`, under its question."""
        import re
        lines, mark = screen.splitlines(), self.mark
        # the choice on screen is the lowest one, and a numbered option beats an earlier prompt in the history
        marked = [i for i, l in enumerate(lines) if re.match(rf"^\s*{mark}\s+\S", l)]
        cur = next((i for i in reversed(marked) if re.match(rf"^\s*{mark} \d+\.", lines[i])), marked[-1] if marked else None)
        if cur is None:
            return None
        # Physical wraps in the ruled typing box align like unnumbered options.
        # A real menu can also open below a rule, but keeps its navigation footer.
        box = self._box(lines) if mark == "❯" else None
        end = next((i for i in range(box + 1, len(lines)) if re.match(r"^\s*[─━]{4,}", lines[i])), None) \
            if box is not None else None
        if end is not None and cur < end and not re.search(
                r"Enter to (?:confirm|select)|↑/↓|Esc to cancel", "\n".join(lines[end + 1:]), re.I):
            return None
        col = lines[cur].index(mark) + 2
        if re.match(rf"^\s*{mark}\s+Submit\s*$", lines[cur]):          # a multi-select's Submit, highlighted once ticked:
            cur = next((k for k in range(cur - 1, max(-1, cur - 30), -1) if re.match(r"^\s*\d+\.", lines[k])), cur)
            col = len(lines[cur]) - len(lines[cur].lstrip()) if cur != marked[-1] else col   # its options are read from above it
        numbered = re.match(r"\d+\.", lines[cur][col:])      # then only numbered lines are options
        row = lambda l: len(l) > col and l[col] != " " and l[:col].strip() in ("", mark)
        is_option = lambda l: row(l) and (not numbered or re.match(r"\d+\.", l[col:]))
        i = cur
        while i > 0 and (is_option(lines[i - 1]) or lines[i - 1][:col + 1].strip() == ""):
            i -= 1                                                       # up to the first option
        block = []
        for k, l in enumerate(lines[i:], i):
            # a multi-select's Submit is an unnumbered row among the numbered ones, or set in just under them
            if is_option(l) or (row(l) and any(is_option(x) for x in lines[k + 1:k + 4])) \
                    or (block and re.match(rf"^\s*(?:{mark}\s+)?Submit\s*$", l)):
                block.append(l)
            elif l[:col + 1].strip():                                    # shallower text: the list has ended
                break
        if len(block) < 2:
            return None                                                  # the input prompt, not a choice
        def label(l):
            text = re.sub(r"^\d+\.\s*", "", l[col:].strip())
            box = re.match(r"\[([ ✔✓xX])\]\s*", text)                  # a multi-select's checkbox, ticked or not
            return ("☑ " if box.group(1).strip() else "☐ ") + text[box.end():] if box else text
        # its question, up to the rule the menu opens with: not its tabs ("←  ☐ Fruit  ✔ Submit  →"), its header chip,
        # nor what was on screen above it
        question = []
        for l in reversed(lines[max(0, i - 8):i]):
            if re.match(r"^\s*[─━═]{3,}", l):
                break
            if l.strip() and not re.match(r"^\s*←.*→\s*$", l) and not re.match(r"^\s*[☐☒]\s+\S[^.?!]{0,30}$", l):
                question.insert(0, l.strip())
        here = next(k for k, l in enumerate(block) if l.lstrip().startswith(mark))
        return question[-4:], [label(l) for l in block], here

    def choose(self, screen, text):
        """The keys that pick the on-screen option containing `text`: arrows to it, then Enter; or None."""
        found = self.choice(screen)
        if not found:
            return None
        _, options, here = found
        want = text.lower().strip().lstrip("☐☑ ")             # an option by its words, ticked or not
        hit = next((k for k, o in enumerate(options) if want in o.lower()), None)
        if hit is None:
            return None
        return (["Down"] * (hit - here) if hit > here else ["Up"] * (here - hit)) + ["Enter"]

class Codex(Provider):
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

    # PROVIDER: Codex's detached app-server and native Remote Control repair connection.
    def repair_keeps(self, home):
        from . import codex_remote as r
        if not r.alive(home):
            return set()
        return {int(r.read_json(home / 'colony-daemon.json')['pid'])}

    def repair_status(self, home, timeout=3):
        import time
        from . import codex_remote as r
        from .codex_rpc import Client
        if not r.alive(home):
            return dict(state='offline', paired=False)
        deadline = time.monotonic() + timeout
        with Client(r.socket_for(home), timeout=timeout) as client:
            client.timeout = max(.01, deadline - time.monotonic())
            status = client.call('remoteControl/status/read')
            connected = status.get('status') == 'connected'
            environment = status.get('environmentId')
            client.timeout = max(.01, deadline - time.monotonic())
            paired = bool(connected and environment and client.call(
                'remoteControl/client/list', {'environmentId': environment}).get('data'))
        state = ('ready' if paired else 'unpaired' if connected and environment else
                 'connecting' if status.get('status') == 'connecting' else 'offline')
        return dict(state=state, paired=paired, server=status.get('serverName') or '')

    def repair_start(self, home, settings):
        import time
        from . import board, codex_remote as r
        from .codex_rpc import Client
        started = not r.alive(home)
        if started:
            if not r.supported_version(self.version()):
                return dict(state='unavailable', paired=False)
            source = self.config_home().resolve()
            if not (source / 'auth.json').is_file():
                return dict(state='needs-signin', paired=False)
            r.prepare_home(SOURCE, home, source)
            # Keep the canonical auth symlink, never clone rotating refresh credentials. Project hooks,
            # instructions and goals must not turn an installation repair host into an engaged project.
            for name in ('AGENTS.md', 'AGENTS.override.md', 'agents', 'rules'):
                path = home / name
                if path.is_symlink():
                    path.unlink()
            def clean(value):
                if isinstance(value, dict):
                    return {k: clean(v) for k, v in value.items()
                            if k not in ('hooks', 'notify', 'developer_instructions', 'model_instructions_file')
                            and 'goal' not in k.lower()}
                if isinstance(value, list):
                    return [clean(v) for v in value]
                return value
            inherited = clean(r.user_config(home))
            text = '\n'.join(json.dumps(k) + '=' + r.toml(v) for k, v in inherited.items()) + '\n'
            (home / 'config.toml').write_text(text)
            permission = settings.get('permissions') or 'ask'
            config = {'features.hooks': False, 'features.goals': False, 'project_doc_max_bytes': 0,
                      'check_for_update_on_startup': False, 'sqlite_home': str(home), 'notify': [],
                      'approval_policy': 'never' if permission in ('all', 'edits') else 'on-request',
                      'sandbox_mode': {'all': 'danger-full-access', 'plan': 'read-only'}.get(permission, 'workspace-write'),
                      'sandbox_workspace_write.writable_roots': [str(SOURCE), str(board.home())],
                      'developer_instructions': (
                          'You maintain this Colony installation when its owner asks. '
                          f'Colony source: {SOURCE}. Board home: {board.home()}. '
                          'Work on the repair the owner requests. Run the relevant tests before colony restart. '
                          'Do not start project goals, hooks, continuation, or roadmap work automatically.')}
            # start() owns the detached lifecycle; closing the RPC transport never stops its daemon.
            with r.start(root=SOURCE, home=home, source=source, config=config, cwd=SOURCE, project=False):
                pass
        with Client(r.socket_for(home), timeout=3) as client:
            remote = client.call('remoteControl/status/read')
            if remote.get('status') not in ('connected', 'connecting'):
                client.call('remoteControl/enable')
        deadline = time.monotonic() + 8
        status = self.repair_status(home)
        while started and status.get('state') == 'connecting' and time.monotonic() < deadline:
            time.sleep(.25)
            status = self.repair_status(home, timeout=min(1, max(.1, deadline - time.monotonic())))
        return status

    def repair_pair(self, home, manual_code=None):
        import time
        from . import codex_remote as r
        from .codex_rpc import Client
        if not r.alive(home):
            raise RuntimeError('The repair connection is not running. Enable it and try again.')
        with Client(r.socket_for(home), timeout=10) as client:
            if manual_code is not None:
                if not isinstance(manual_code, str) or not manual_code or len(manual_code) > 256:
                    raise ValueError('The pairing code is missing or invalid. Request a fresh code.')
                result = client.call('remoteControl/pairing/status', {'manualPairingCode': manual_code})
                return {'claimed': result.get('claimed') is True}
            result = client.call('remoteControl/pairing/start', {'manualCode': True})
            code, expiry = result.get('manualPairingCode'), result.get('expiresAt')
            if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,128}', code) or \
                    type(expiry) is not int or expiry <= time.time():
                raise RuntimeError('Codex did not return a usable pairing code. Try again.')
            return {'manualPairingCode': code, 'expiresAt': expiry}

    def repair_stop(self, home):
        import signal
        import time
        from . import codex_remote as r
        from .codex_rpc import Client
        if not r.alive(home):
            return True
        # No TERM/KILL fallback. An unresponsive host may still be running the owner's repair.
        record = r.read_json(home / 'colony-daemon.json')
        deadline, cursor = time.monotonic() + 5, None
        with Client(r.socket_for(home), timeout=2) as client:
            while time.monotonic() < deadline:
                result = client.call('thread/loaded/list', dict(cursor=cursor, limit=100))
                for tid in result['data']:
                    if time.monotonic() >= deadline:
                        return False
                    thread = client.call('thread/read', {'threadId': tid, 'includeTurns': False})['thread']
                    if thread['status']['type'] not in ('idle', 'notLoaded'):
                        return False
                cursor = result.get('nextCursor')
                if not cursor:
                    if r.alive(home) and r.read_json(home / 'colony-daemon.json') == record:
                        os.kill(record['pid'], signal.SIGHUP)
                    return not r.alive(home)
        return False

    def stale(self, root):
        """Why a running console would start differently now beyond its command: one that fell back to local on a
        Codex colony hadn't checked, now that this Codex is checked and the project wants app access."""
        from . import board, codex_remote
        status = codex_remote.read_json(codex_remote.home_for(root) / "colony-status.json")
        if (status.get("mode") == "local" and status.get("reason", "").startswith(codex_remote.UNTESTED)
                and board.project_settings(root)[0].get("remote", True)
                and codex_remote.supported_version(self.version())):
            return "app access is available again on this Codex"
        return None

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

    # PROVIDER: a consult keeps the shell (in its read-only sandbox, to read the project) and loses all else it could
    # reach out through or spend on: the person's apps, plugins and hooks, image making, goals, helpers of its own
    # (colony-codex's audit, Oct 27; checked on Codex 0.165: only its own built-in tools, none of the person's servers).
    CONSULT_OFF = ("hooks", "apps", "plugins", "image_generation", "goals", "multi_agent")

    def consult(self, brief, model, effort, project, price=None, popen=None):
        """One fresh, stateless consultation in its read-only sandbox, in an empty folder: none of the person's config
        (its MCP servers, plugins, connectors) or rules, hooks off, no web, nothing it could act through; it reads the
        project's files by path, as Claude Code's consult does. Codex reports tokens, not dollars: price (per 1M tokens
        in, out) turns its usage into what it cost."""
        import subprocess
        import tempfile
        here = tempfile.mkdtemp(prefix="colony-consult-")
        last = Path(here) / "answer.txt"
        cmd = [self.program, "exec", "-m", model, "-c", f"model_reasoning_effort={effort}", "-s", "read-only",
               "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules",
               *[a for f in self.CONSULT_OFF for a in ("--disable", f)], "-c", 'web_search="disabled"',
               "--json", "-o", str(last), "-C", here, "-"]
        pin, pout = price or (0, 0)
        cost = lambda u: ((u.get("input_tokens", 0) - u.get("cached_input_tokens", 0)) * pin
                          + u.get("cached_input_tokens", 0) * pin * 0.1 + u.get("output_tokens", 0) * pout) / 1e6
        usage = {}
        try:
            proc = (popen or subprocess.Popen)(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            import threading
            guard = threading.Timer(1800, proc.kill)    # a hung run never holds its caller (or a turbo slot) for good
            guard.daemon = True
            guard.start()
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
            guard.cancel()
        except (OSError, subprocess.TimeoutExpired) as err:
            return {"text": "", "cost": None, "error": err.__class__.__name__}
        return {"text": last.read_text() if last.exists() else "", "cost": cost(usage) if price else None,
                "usage": usage, "error": None if last.exists() else "no answer"}

    # PROVIDER: Codex can't list a process's tools, so the ones the scout doesn't need are turned off (the shell,
    # connectors, plugins, hooks, image tools, goals); what's left stays in its read-only sandbox, with none of the
    # person's config or rules (a command a rule allows runs outside the sandbox). Its search opens only what the
    # search index knows, so no address a page makes up can carry anything out. Checked on Codex 0.160.
    SCOUT_OFF = ("hooks", "shell_tool", "unified_exec", "apps", "plugins", "view_image", "image_generation", "goals")

    def scout(self, brief, model, effort, run=None):
        """One fresh, stateless scout in an empty folder, reading no AGENTS.md: it searches the web and reads what the
        search finds, and can act nowhere."""
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory(prefix="colony-scout-") as here:
            work, last = Path(here) / "work", Path(here) / "answer.txt"
            work.mkdir()
            cmd = [self.program, "exec", "-m", model, *(["-c", f"model_reasoning_effort={effort}"] if effort else []),
                   "-s", "read-only", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                   *[a for f in self.SCOUT_OFF for a in ("--disable", f)], "-c", 'web_search="indexed"',
                   "-c", "project_doc_max_bytes=0", "-o", str(last), "-C", str(work), "-"]
            try:
                r = (run or subprocess.run)(cmd, input=brief, capture_output=True, text=True, cwd=work, timeout=1800)
            except (OSError, subprocess.TimeoutExpired) as err:
                return {"text": "", "error": err.__class__.__name__}
            text = last.read_text() if last.exists() else ""
        used = re.search(r"tokens used\W+([\d,]+)", str(getattr(r, "stderr", "") or "") + str(getattr(r, "stdout", "") or ""), re.I)
        if used:                                          # a scout keeps no transcript: what it spent is noted here
            from . import tokens
            tokens.note("codex", {"input_tokens": int(used[1].replace(",", ""))})
        why = ((r.stderr or "").strip().splitlines() or ["no answer"])[-1][:300]
        return {"text": text, "error": None if text.strip() else why}

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
    scrolls_in_tmux = True  # inline (--no-alt-screen), no mouse of its own: its scrollback is tmux's
    app, reach = "ChatGPT", "Reach it from ChatGPT"
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
    # PROVIDER: only Codex has a native goal; colony progress sets and refreshes it (continuation.py).
    GOAL = ("- As lead, establish the checkpoint's native goal and keep it aligned with the next agreed pause: when "
            "you select the next bounded checkpoint, the goal refreshes automatically; do not wait for another /goal.\n")

    def signed_in(self, run=None):
        """Whether Codex is signed in here, by its own status (no tokens spent)."""
        import subprocess
        try:
            env = dict(os.environ, CODEX_HOME=str(self.config_home()))
            r = (run or subprocess.run)([self.program, "login", "status"], capture_output=True, text=True,
                                       timeout=15, env=env)
        except (OSError, subprocess.TimeoutExpired):
            return None
        out = (r.stdout + r.stderr).strip().lower()       # it reports on stderr
        if out.startswith("logged in"):
            return True
        return False if out.startswith("not logged in") else None

    # PROVIDER: Codex's device-code login avoids a localhost callback on the person's phone.
    def signin_command(self):
        return [self.program, "login", "--device-auth"]

    def signin_environment(self):
        return dict(_signin_environment(), CODEX_HOME=str(self.config_home()), BROWSER="true")

    signin_help = ("Open the OpenAI sign-in link and enter the device code shown here. Then return here; "
                   "the board confirms it automatically. Device-code login must be enabled in your ChatGPT security settings.")

    def signin_prompt(self, text):
        url = _signin_url(text, {("auth.openai.com", "/codex/device")})
        # Native Codex presents the device code on its own line, including when ANSI colour surrounds it.
        plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
        code = re.search(r"(?m)^\s*([A-Z0-9]{4}-[A-Z0-9]{4,5})\s*$", plain)
        return {"url": url, "code": code[1], "accept_code": False} if url and code else {}

    def signin_refresh(self, root):
        # PROVIDER: a remote app host outlives its console and caches authentication. Drain only while idle.
        from . import codex_remote
        return codex_remote.drain(codex_remote.home_for(root))

    def update(self):
        """Install the newest Codex beside the running one (its own updater)."""
        return _run([self.program, "update"], timeout=600)

    def startup_files(self, root):
        """What Codex reads only when it starts, among what colony writes: the tier overlays."""
        return [root / ".codex" / "agents" / f"{self.helper_name(t)}.toml" for t in self.HELPER_BRIEF]

    HELPER_CALL = "spawn one with agent_type set to its name and fork_turns \"none\"; its model and effort come from it"
    HELPER_BRIEF = {"routine": "Colony's routine tier: ordinary work, building, editing, looking things up across files",
                    "step-up": "Colony's step-up tier: work that has stalled, been retried or redone, or needs the strongest reasoning here",
                    "chores": "Colony's chores tier: simple work that takes minor discernment, small edits, short clear instructions, light checks",
                    "rote": "Colony's rote tier: clear, mechanical tasks, copying, renaming, running a named command or test, simple lookups"}

    @staticmethod
    def helper_name(tier):
        return "colony-" + tier.replace("-", "")

    def write_helpers(self, root, tiers):
        """A config overlay per tier in .codex/agents (model and reasoning effort), which the console command
        registers as a named agent; the agent spawns it by agent_type. Verified on Codex 0.154 by colony-codex
        (docs/codex-helper-tiers.md there). A new or changed tier takes hold when the console next starts."""
        changed = []
        for tier in self.HELPER_BRIEF:
            t = tiers.get(tier)
            changed += _keep(root / ".codex" / "agents" / f"{self.helper_name(tier)}.toml", t and (
                "# written by colony from this project's tiers (colony models); edits here are replaced\n"
                # Codex 0.159 also finds these by itself in .codex/agents, as roles that must carry a name
                f"name = {json.dumps(self.helper_name(tier))}\n"
                f"description = {json.dumps(self.HELPER_BRIEF[tier])}\n"
                f"model = {json.dumps(t['model'])}\n" + (f"model_reasoning_effort = {json.dumps(t['effort'])}\n" if t.get("effort") else "")
                + 'developer_instructions = "Do the task you are given within its brief, and hand in what you find and do."\n'))
        return changed

    def command(self, label, s, resume=None, root=None):
        if root:
            parts = own_module('colony.codex_remote') + ['launch', '--project', str(root),
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
        from . import connections                       # the outside services granted it
        parts += [shlex.quote(x) for x in connections.codex_args(root)]
        # Codex runs a hook only once it is trusted: these are colony's own, given in this very command, so with the
        # person's trust setting on (as the app path's trust_hooks requires) they run without a stored trust.
        from .board import registry
        if registry()["settings"].get("trust"):
            parts += ["--dangerously-bypass-hook-trust"]
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

    # PROVIDER: Codex reads AGENTS.override.md in a folder instead of its AGENTS.md (at most one file per folder:
    # learn.chatgpt.com/docs/agent-configuration/agents-md). A private project's instructions are that file: the
    # project's own AGENTS.md, then colony's section, refreshed at each wiring, so an edit to AGENTS.md reaches its
    # agent from the next one (board start, daily, colony track). The first line marks it as colony's.
    OVERRIDE_MARK = ("<!-- written by colony for a private project: this folder's AGENTS.md, then colony's section; "
                     "edit AGENTS.md, as this file is replaced -->\n")
    private_files = ("AGENTS.override.md", ".codex/agents/colony-*.toml")

    def wire(self, root, protocol, private=False):
        """Install instructions and retire our old file hooks; launch flags now supply them in every checkout.
        Private, the instructions go in AGENTS.override.md, and colony's section comes out of AGENTS.md."""
        if private:
            return self._wire_private(root, protocol)
        path = root / "AGENTS.md"
        have = path.read_text() if path.exists() else ""
        block = self._block(root, protocol)
        marker = "## This project is part of a colony"
        if marker in have:
            start = have.index(marker)
            end = have.find("\n## ", start + 5)
            _keep(path, have[:start] + block + (have[end + 1:] if end != -1 else ""))
        else:
            _keep(path, have + ("\n" if have and not have.endswith("\n") else "") + block)
        self._retire_hooks(root)

    def _wire_private(self, root, protocol):
        override = root / "AGENTS.override.md"
        if override.exists() and not override.read_text().startswith(self.OVERRIDE_MARK):
            raise ValueError(f"{override} is the project's own, and Codex reads only one instructions file per folder: "
                             "colony can't add its section privately here")
        _cut(root / "AGENTS.md", ("## This project is part of a colony",))
        own = (root / "AGENTS.md").read_text() if (root / "AGENTS.md").exists() else ""
        _keep(override, self.OVERRIDE_MARK + own + ("\n" if own and not own.endswith("\n") else "")
              + self._block(root, protocol))
        self._retire_hooks(root)

    def _block(self, root, protocol):
        block = protocol.lstrip("\n").rstrip("\n") + self.DELIVERY + self.GOAL
        memory = claude_memory(root)
        if memory:                                  # what its Claude Code agent learned stays with the project
            block += (f"- What this project's Claude Code agent learned working here is in `{memory}` and the files it "
                      "links: read it as background at the start of a session. The person's standing rules are not kept "
                      "there but by colony, which hands them to you as each session starts; record a new one with "
                      "`colony rule`.\n")
        return block

    def _retire_hooks(self, root):
        settings = root / ".codex" / "hooks.json"
        if settings.exists():
            cfg = json.loads(settings.read_text())
            for event, entries in cfg.get("hooks", {}).items():
                if event not in self.hooks:
                    continue
                for entry in entries:
                    entry["hooks"] = [h for h in entry.get("hooks", []) if not self._owned_hook(event, h)]
                cfg["hooks"][event] = [entry for entry in entries if entry.get("hooks")]
            _keep(settings, json.dumps(cfg, indent=2) + "\n")

    def _owned_hook(self, event, hook):
        return (hook.get("type") == "command"
                and hook.get("command") in (self.hooks.get(event), self.legacy_hooks.get(event)))

    def wired(self, root, private=False):
        path = root / ("AGENTS.override.md" if private else "AGENTS.md")
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

    def turn_text(self, payload, wait=None):
        """The Stop hook supplies the final reply directly, including on sessions with no transcript. PROVIDER: where
        it hands over none, the turn's prose is read from its rollout, again for a moment while Codex may still be
        writing it (turn_prose)."""
        last = (payload.get("last_assistant_message") or "").strip()
        if last:
            return payload.get("turn_id"), last
        return payload.get("turn_id"), turn_prose(self, payload.get("transcript_path"), TURN_WAIT if wait is None else wait)

    def conversation(self, payload):
        return payload.get("session_id"), payload.get("transcript_path")

    @staticmethod
    def goal_client(root, timeout=15):
        """Codex keeps one native goal per thread, read and set over its app-server control socket."""
        from . import codex_remote
        from .codex_rpc import Client
        socket = codex_remote.socket_for(codex_remote.home_for(root))
        if not socket.exists():
            raise FileNotFoundError("Native goal control is unavailable; bounded continuation is not active.")
        return Client(socket, timeout=timeout)

    def history_text(self, root, limit=200_000):
        return None

    # What Codex says when the network ended a turn or a helper after its own retries (a reply cut short for another
    # reason, "Incomplete response returned", is not one), and the kinds it gives a turn's error.
    NETWORK = re.compile(r"stream disconnected before completion(?!: Incomplete response)|error sending request|"
                         r"Connection failed|connection (?:reset|refused|closed)|Reconnecting|timed out|dns error|"
                         r"failed to lookup address|network (?:error|is unreachable)|broken pipe", re.I)
    NETWORK_KINDS = ("http_connection_failed", "response_stream_connection_failed", "response_stream_disconnected")
    api_host = "chatgpt.com"

    def network_stop(self, path):
        """As Claude Code's, from a Codex rollout: a turn that failed ends in a task_complete carrying its `error`
        (message and codex_error_info), and a helper's failure shows in the CollabAgentToolCall that saw it, as
        agents_states {thread: {"errored": message}}. PROVIDER: reads Codex's own rollout file."""
        def network(error):
            kind = error.get("codex_error_info")
            kind = next(iter(kind), None) if isinstance(kind, dict) else kind
            return kind in self.NETWORK_KINDS or (kind in (None, "other") and bool(self.NETWORK.search(error.get("message") or "")))
        stop, helpers = None, []
        for line in _newest_first(path):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            p = e.get("payload") if isinstance(e.get("payload"), dict) else {}
            kind = p.get("type")
            if e.get("type") == "response_item" and (kind in ("reasoning", "function_call", "custom_tool_call")
                                                     or (kind == "message" and p.get("role") == "assistant")):
                return dict(stop, helpers=helpers[::-1], ok_at=_stamp(e)) if stop else None       # it replied since
            if e.get("type") != "event_msg":
                continue
            if kind == "task_complete":
                failed = network(p["error"]) if p.get("error") else False
                if stop is None:
                    if not failed:
                        return None                # its last turn ended well, or on something else
                    stop = {"key": p.get("turn_id") or e.get("timestamp"), "at": _stamp(e),
                            "error": p["error"].get("message") or ""}
                elif not failed:
                    return dict(stop, helpers=helpers[::-1], ok_at=_stamp(e))          # the turn before the drop
            elif kind in ("task_started", "turn_aborted") and stop is None:
                return None                        # a turn under way, or one the person stopped
            elif kind == "item_completed" and stop and (p.get("item") or {}).get("type") == "CollabAgentToolCall":
                item = p["item"]
                names = {a.get("thread_id"): a.get("agent_nickname") or a.get("agent_role")
                         for a in item.get("receiver_agents") or []}
                for tid, state in (item.get("agents_states") or {}).items():
                    error = state.get("errored") if isinstance(state, dict) else None
                    if error and self.NETWORK.search(error) and all(h["id"] != tid for h in helpers):
                        helpers.append({"key": f"{tid}@{e.get('timestamp')}", "id": tid, "name": names.get(tid) or tid,
                                        "at": _stamp(e), "error": error})
        return dict(stop, helpers=helpers[::-1], ok_at=0.0) if stop else None

    def mail_problem(self, text, *, stopped=False):
        # PROVIDER: Codex draws request failures with ■; a dead console may instead leave its CLI's Error line.
        marker = r"\s*■\s*"
        if stopped:
            marker = r"(?:\s*■\s*)?"
        diagnostic = _mail_diagnostic(text, r"^\s*›(?!\s*\d+\.)",
                                      marker + r"(?P<error>.+)", r"^\s*•\s", stopped)
        return _mail_reason(self, diagnostic)

    def classify(self, screen):
        """Codex shows "• Working (3s • esc to interrupt)" while it works, and a numbered choice marked `›`
        (a trust question, an approval) when it needs the person; otherwise it waits at its `›` prompt."""
        import re
        if "esc to interrupt" in screen.lower() or re.search(r"^\s*• Working \(", screen, re.M):
            return "working"
        if re.search(r"^\s*› \d+\. ", screen, re.M) and not self.viewing(screen):
            return "needs you"
        return "idle"

    def _mine(self):
        """The folders of the projects that run on it, the steward's too where it does."""
        from . import board, monitor
        keep = {str(Path(p).resolve()) for p in board.projects() if of(p) is self}
        if monitor.provider() is self:
            keep.add(str(Path(monitor.home()).resolve()))
        return keep

    def let_go(self):
        """Its app hosts outlive their consoles by design (a restart reconnects to the turn), so one whose project no
        longer runs on Codex, or left the board, is let go here."""
        from . import codex_remote
        return codex_remote.let_go(self._mine())

    def keeps(self):
        """Its app hosts, for projects that still run on it: each lives on in the scope of the console that started it."""
        from . import codex_remote
        return codex_remote.hosts(self._mine())

    def unfreeze(self, root):
        """Its app host runs the turn in a session of its own, which a console restart leaves running and reconnects
        to. A host that still answers is running the turn, however quiet: it is kept, and only the console is
        restarted onto it (False). One that doesn't is stopped, so the restart starts a fresh one (True)."""
        from . import codex_remote
        if codex_remote.responsive(root):
            return False
        codex_remote.stop_host(root)
        return True

    def busy(self, root, name):
        """Its turn runs in its app host, whatever its console shows: busy until that host is at rest."""
        return None if self.at_rest(root) else "its app host is still running a turn"

    def at_rest(self, root):
        """Its app host runs turns, and carries on an active goal, whatever its console shows (one that has ended
        leaves them running): at rest only when no host is running, or every thread it has loaded is idle with no
        active goal. Anything it can't answer counts as not at rest."""
        from . import codex_remote
        from .codex_rpc import Client
        home = codex_remote.home_for(root)
        if not codex_remote.alive(home):
            return True
        try:
            with Client(codex_remote.socket_for(home)) as client:
                if not codex_remote.idle(client):
                    return False
                cursor = None
                while True:
                    result = client.call('thread/loaded/list', dict(cursor=cursor, limit=100))
                    for tid in result['data']:
                        goal = client.call('thread/goal/get', {'threadId': tid}).get('goal')
                        if goal and goal.get('status') == 'active':
                            return False
                    cursor = result.get('nextCursor')
                    if not cursor:
                        return True
        except Exception:
            return False

    def elapsed(self, screen):
        """The timer on its live working line, the one just above its `›` prompt: "• Working (5m 12s • esc to
        interrupt)" gives "5m 12s". The same words quoted in a reply or a command's output are indented, or stand
        above other lines, so they never count."""
        import re
        lines = [l for l in screen.splitlines() if l.strip()]
        prompt = max((i for i, l in enumerate(lines) if l.startswith("› ")), default=None)
        if not prompt:
            return None
        m = re.match(r"• Working \(((?:\d+h )?(?:\d+m )?\d+s) • esc to interrupt\)", lines[prompt - 1])
        return m.group(1) if m else None

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

    def pending_input(self, text, left):
        # PROVIDER: Codex collapses a rapid paste to this marker, sometimes only
        # for its first chunk, followed by ordinary typed text or another marker.
        # The visible remainder must match the corresponding sent-text suffix.
        if super().pending_input(text, left):
            return True
        if not left:
            return False
        marker = re.compile(r'\[Pasted Content ([1-9][0-9]*) chars\](?: #[1-9][0-9]*)?')
        offset, at, found = 0, 0, False
        for match in marker.finditer(left):
            literal = left[at:match.start()]
            if not text.startswith(literal, offset):
                return False
            offset += len(literal) + int(match[1])
            if offset > len(text):
                return False
            at, found = match.end(), True
        return found and text.startswith(left[at:], offset)

    _helping = {}

    def commands(self, pane):
        from . import lingering
        return lingering.codex_commands(pane)

    def background(self, path):
        from . import chat
        return chat.codex_background(path)

    def catalog_freshness(self, now):
        from . import catalog_freshness
        return catalog_freshness.codex(self, now)

    def events(self, path):
        from . import chat
        return chat.codex_events(path)

    def helpers(self, root, now=None):
        """Its helpers at work: Codex lists none under its box (its picker keeps finished ones too), so they come from
        its thread store, each spawned with its parent's id, and are at work while their own rollout's last turn has
        started and not ended. A helper's own helpers count too, though the one between has finished: the family is
        followed down from the session. [{name, what, stats, transcript}], kept ten seconds.
        PROVIDER: Codex's state_5.sqlite threads (source subagent.thread_spawn) and rollouts."""
        import sqlite3
        import time
        from . import context
        now = time.time() if now is None else now
        kept = self._helping.get(str(root))
        if kept and now - kept[0] < 10:
            return kept[1]
        out = []
        try:
            s = context.session(root)
            path = Path(s["path"]) if s and s.get("provider") == "codex" and s.get("path") else None
            home = next((d.parent for d in (path.parents if path else []) if d.name == "sessions"), None)
            if home and (home / "state_5.sqlite").exists():
                db = sqlite3.connect(f"file:{home / 'state_5.sqlite'}?mode=ro", uri=True, timeout=1)
                try:
                    rows = db.execute("SELECT id, source, updated_at, agent_nickname, agent_role, agent_path, rollout_path "
                                      "FROM threads WHERE source LIKE '%\"thread_spawn\"%'").fetchall()
                finally:
                    db.close()
                children = {}
                for row in rows:
                    try:
                        parent = json.loads(row[1])["subagent"]["thread_spawn"]["parent_thread_id"]
                    except (ValueError, KeyError, TypeError):
                        continue
                    children.setdefault(parent, []).append(row)
                family, at = [], [s["id"]]
                while at:
                    kin = [r for a in at for r in children.get(a, []) if r not in family]
                    family, at = family + kin, [r[0] for r in kin]
                for _, _, updated, nick, role, agent, rollout in family:
                    started = (updated or 0) > now - 2 * 86400 and self._turn_open(rollout)
                    if started:
                        mins = int(max(0, now - started) // 60)
                        out.append({"name": nick or role or "helper", "what": (agent or "").rsplit("/", 1)[-1].replace("_", " "),
                                    "stats": f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m", "transcript": rollout})
        except (OSError, sqlite3.Error, KeyError, TypeError):
            out = []
        self._helping[str(root)] = (now, out)
        return out

    def active_seconds(self, root):
        """Time at work in this folder, all told, as Claude Code's: each turn's own length as Codex closes it (task_complete
        or turn_aborted, duration_ms), and a turn not closed by the time between its own lines, leaving out any gap over
        IDLE_GAP. Its conversations here, not its helpers' (theirs run inside its turns), from each thread store's
        record of where each ran; each turn counted once by its own id, and each conversation's open turn once, since
        a conversation moved to colony's own host keeps a copy of its history in both (colony-codex's review). Rollouts
        only grow, so each is read on from where the last look stopped.
        PROVIDER: Codex's state_5.sqlite threads (cwd, source) and rollouts, in its own home and colony's hosts."""
        import sqlite3
        from . import board
        here = str(board.workdir(root).resolve())
        turns, open_ = {}, {}
        for home in [self.config_home(), *(board.home() / "codex-remote").glob("*")]:
            if not (home / "state_5.sqlite").exists():
                continue
            try:
                db = sqlite3.connect(f"file:{home / 'state_5.sqlite'}?mode=ro", uri=True, timeout=1)
                try:
                    rows = db.execute("SELECT id, rollout_path FROM threads WHERE cwd = ? AND source NOT LIKE '%\"thread_spawn\"%'",
                                      (here,)).fetchall()
                finally:
                    db.close()
            except sqlite3.Error:
                continue
            for thread, rollout in rows:
                ended, still = self._worked(rollout)
                turns.update(ended)
                open_.setdefault(thread, []).append(still)
        # a conversation's open turn once: its newest (of two copies of it, the one read further), and none that another
        # copy has seen close (colony-codex's review)
        live = [max((o for o in opens if o[0] not in turns), key=lambda o: (o[1], o[2]), default=(None, 0, 0.0))[2] for opens in open_.values()]
        return sum(turns.values()) / 1000 + sum(live)

    IDLE_GAP = ClaudeCode.IDLE_GAP
    _worked_in = {}       # rollout -> (bytes read, {turn: ms} of turns ended, open turn's time, its latest, (its id, its start))

    @classmethod
    def _worked(cls, rollout):
        import re
        try:
            size = os.path.getsize(rollout)
        except (OSError, TypeError):
            return {}, (None, 0, 0.0)
        at, ms, open_, last, opened = cls._worked_in.get(rollout, (0, {}, 0.0, None, (None, 0)))
        if size < at:
            at, ms, open_, last, opened = 0, {}, 0.0, None, (None, 0)   # rewritten: read it afresh
        if size > at:
            with open(rollout, "rb") as fh:
                fh.seek(at)
                while True:
                    line, complete = read_line(fh, edges=512)
                    if not complete:                             # a line still being written waits
                        break
                    at = fh.tell()
                    if line is None:
                        continue
                    try:
                        if b'"event_msg"' in line and (b'"task_complete"' in line or b'"turn_aborted"' in line):
                            e = (json.loads(line).get("payload") or {})
                            if e.get("type") in ("task_complete", "turn_aborted"):
                                took = e.get("duration_ms")
                                if took is None and e.get("started_at") and e.get("completed_at"):
                                    took = (e["completed_at"] - e["started_at"]) * 1000
                                ms[e.get("turn_id") or f"{rollout}:{len(ms)}"] = took or 0
                                open_, last, opened = 0.0, None, (None, 0)
                                continue
                        if b'"event_msg"' in line and b'"task_started"' in line:
                            e = json.loads(line)
                            open_, last = 0.0, _stamp(e)
                            opened = ((e.get("payload") or {}).get("turn_id"), last)
                            continue
                        if last is not None:                              # within a turn still open
                            found = re.match(rb'\{"timestamp":\s*"([^"]+)"', line)
                            t = _stamp({"timestamp": found[1].decode()}) if found else 0
                            if t and 0 < t - last <= cls.IDLE_GAP:
                                open_ += t - last
                            last = max(last, t) if t else last
                    except (ValueError, KeyError, TypeError):
                        pass
            cls._worked_in[rollout] = (at, ms, open_, last, opened)
        return ms, (opened[0], opened[1], open_) if last is not None else (None, 0, 0.0)

    _turns = {}

    @classmethod
    def _turn_open(cls, rollout):
        """When a rollout's last turn started, while it has not ended (task_complete, turn_aborted); else None. Read as
        it grows: its newest MiB first (all of it, only when that holds no turn's start or end: a helper's long tool
        output can push its start back that far), then only what was added since."""
        try:
            size = os.path.getsize(rollout)
            at, started = cls._turns.get(rollout, (0, None))
            if not at or at > size:
                at, started, seen = _turn_marks(rollout, max(0, size - (1 << 20)), None)
                if not seen and size > 1 << 20:
                    at, started, seen = _turn_marks(rollout, 0, None)
            elif at < size:
                at, started, _ = _turn_marks(rollout, at, started)
        except (OSError, TypeError):
            return None
        cls._turns[rollout] = (at, started)
        return started

    # Its own views that the agent may open (the subagent picker, "← for agents") are drawn as a choice, but they are
    # no question for the person: one is open while its heading is on screen with no prompt to type at below it.
    VIEWS = ("Select an agent to watch",)

    def viewing(self, screen):
        import re
        lines = screen.splitlines()
        at = max((i for i, l in enumerate(lines) if any(v in l for v in self.VIEWS)), default=None)
        return at is not None and not any(re.match(r"^\s*›(?!\s*\d+\.)", l) for l in lines[at + 1:])

    def choice(self, screen):
        return None if self.viewing(screen) else ClaudeCode.choice(self, screen)

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


def claude_memory(root):
    """The index of what Claude Code's agent keeps for a project between conversations (the person's standing rules
    and why), where there is one: its folder is named after the folder it runs in. None elsewhere."""
    import re
    from . import board, lead
    try:
        places = [board.workdir(root), Path(root), lead.plan_root(root)]
    except Exception:
        places = [Path(root)]
    for place in places:
        index = PROVIDERS["claude"].config_home() / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(place)) / "memory" / "MEMORY.md"
        if index.exists():
            return index
    return None


# A colony module run as a command inside a project folder: `python -m` would import that folder's own colony first,
# and a project can be a checkout of colony itself (colony-codex), running its older code.
RUN_OWN = ("import runpy, sys; sys.path[:1] = [] if sys.path and sys.path[0] == '' else sys.path[:1]; "
           "runpy.run_module(sys.argv.pop(1), run_name='__main__', alter_sys=True)")


def own_module(module):
    """The command start for one of colony's own modules, imported from where colony is installed, whatever the folder."""
    return [sys.executable, "-c", RUN_OWN, module]


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
    return [e[0] if isinstance(e, tuple) else e for e in provider.efforts_for.get(model, provider.efforts)]


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
        if not usable(p) or not supplies(p, "discover"):
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
            found = p.catalog()
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
