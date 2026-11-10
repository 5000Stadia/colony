"""Colony's watcher: its own work, in the board's process, every few seconds and with no tokens.

It reads every project's screen and keeps the board's order; delivers mail and the person's notes, nudging an idle
agent once; keeps the safe and week pauses, winding projects down at a usage limit and waking them at the reset;
runs turbo; answers a new session's start-up questions and, where the person allows it, its permission prompts;
recovers work a network drop or a frozen console stopped; refreshes context; reloads a console onto a switched
program, a new version or changed settings at its first safe moment; makes status reports and reads warning signs on
the person's schedule; updates the programs and the model data once a day; and audits a connection an agent added.
What the person should hear of its own work it tells them in a notice on the board (board.notice).

The module keeps its old name (it once also ran the steward, retired in R152).
"""
import json
import os
import threading
import time
from pathlib import Path

from . import board, console, providers


STARTUP_WINDOW = 300            # seconds after a console starts in which its questions count as start-up ones
RELOAD_AFTER = 300              # seconds a stale console must sit idle, untouched, before it is reloaded
RENUDGE = 600                   # seconds before a safe pause's end, still not handed over, is nudged again


class Watcher:
    """Colony's own work, every few seconds and with no tokens: reads every project's screen, delivers mail and
    notes, keeps the safe and week pauses, wakes at resets, recovers, refreshes context, and keeps the board's order."""

    def __init__(self, interval=4.0):
        self.interval = interval
        self.states = {}
        self.close_idle = {}                 # a closing project's looks in a row at idle, its note handed over
        self.nudged, self.nudged_at = set(), {}
        self.woke_for = set()                # quiet notes a sleeping console is woken for (R142)
        self.mail_lock = threading.Lock()
        self.settling = {}                      # a stop not yet confirmed
        self.discovered = time.time() if os.environ.get("COLONY_CONSOLE_CMD") else 0   # tests look for nothing
        self.usage_checked = self.current_checked = self.discovered
        self.idle_since = {}
        self.signin_observed = self.discovered
        self.reads_kept = time.time()        # what sessions read, first kept two minutes in (cleanup.update)

    def events(self):
        try:
            from . import warning_signs
            warning_signs.check()               # R146: the records read again every few hours, in the background
        except Exception:
            pass
        try:
            from . import reports
            reports.check()                     # R150: a status report on the person's schedule, if they set one
        except Exception:
            pass
        for p in board.projects():
            if not p.exists():
                continue
            board.item_times(p)                 # when each roadmap item reached its state: the Roadmap tab orders by it
            try:
                from . import measures
                measures.check(p)               # its measures, taken by colony at each push and milestone finished
                from . import hints
                hints.check(p)                  # a milestone finished: the plan's shape hints, once, quietly
                # (colony no longer asks the person for a verdict at each milestone: stopped on their word,
                # 2026-11-10, until the need and the method are talked through with them, R146)
                from . import cleanup
                cleanup.check(p)                # an item finished: a cleanup pass that clearly pays, said once (R139)
                from . import weakened
                weakened.check(p)               # a commit that weakens its tests without saying why, put to its agent
                from . import qdept
                qdept.check(p)                  # R131: an item started or finished: Q Branch's research and guides
            except Exception:
                pass
            snap = console.snapshot(p, lines=4)
            if snap["state"] == "needs you":
                # the person put it on the board as it is set up: a new session's start-up questions (its folder's
                # trust, its permission mode, Remote Control, hooks) are answered so it runs that way; and where they
                # set its sessions to do everything unasked, a tool's permission question its program still asks
                name = console.session_name(p)
                screen = console.screen(name)
                keys = (providers.starting(providers.of(p), screen, fresh=console.age(name) < STARTUP_WINDOW)
                        if board.registry()["settings"]["trust"] else None) \
                    or (providers.approving(providers.of(p), screen)
                        if board.project_settings(p)[0].get("permissions") == "all" else None)
                if keys:
                    console.press(name, keys)
                    continue
            # A project not seen before counts as off, so one already waiting (a new folder's trust question,
            # a question left while the board was down) is reported, not taken as where it always was.
            before, now = self.states.get(str(p), "off"), snap["state"]
            if before == "working" and now != "working":
                # Scrolled up from the latest, a screen hides the spinner, and a read mid-redraw can miss it: a
                # session has stopped working only when its screen is at the latest and says so twice running.
                if snap.get("scrolled") or self.settling.get(str(p)) != now:
                    self.settling[str(p)] = None if snap.get("scrolled") else now
                    now = before
            else:
                self.settling.pop(str(p), None)
            self.states[str(p)] = now
            # it comes to the front of the board's list, and stays while working: at work as its dot shows it, helpers or
            # background commands running behind an idle screen included (an idle-looking one sat left of one at work)
            back = board.seen(snap).get("behind") if now != "working" else ""
            self.at_work(p, now == "working" or bool(back), behind=bool(back))

    def mail(self):
        with self.mail_lock:
            self._mail()

    def _mail(self):
        """Check delivery health before suppressing repeated nudges. Hooks alone confirm handoff."""
        from . import mail
        closing, woke_starting = board.closing(), False
        for p in board.projects():
            if not p.exists():
                continue
            try:
                mail.repair(p)
                woke_starting = self._mail_project(p, closing.get(str(p)), woke_starting)
            except Exception as err:
                # One broken console or inbox must not hold every other project's mail. Exception text can
                # contain launch commands, credentials or project material; only its category is safe to relay.
                try:
                    incoming = mail.inbox(p)
                    known = {'code': 'console-check',
                             'reason': f"recipient delivery check failed ({type(err).__name__}); underlying cause is unknown"}
                    mail.report(p, [m for m in incoming if not m['delivered_at']], 'delayed', **known)
                    mail.report(p, [m for m in incoming if m['delivered_at'] and m['ask'] and not m['answer']],
                                'reply-delayed', **known)
                except Exception:
                    pass

    def _mail_project(self, p, closing, woke_starting):
        from . import mail, vision
        incoming = mail.inbox(p)
        # Quiet mail (`colony send --dev`, R148) waits for the next turn and wakes no one, a sleeper included.
        letters = [m for m in incoming if not m['delivered_at'] and not m.get('quiet')]
        asks = [m for m in incoming if m['delivered_at'] and m['ask'] and not m['answer']]
        # A quiet note waits for the next turn, except to a sleeping console (R142): any message wakes it, and once
        # woken it is told, as for any note (the person, 2026-11-01: "Sending any message in colony should wake")
        from . import hibernate
        opened = board.open_notes(p)
        if hibernate.asleep(p):
            self.woke_for.update(n['id'] for n in opened if not n['delivered_at'] and n.get('quiet'))
        notes = [n for n in opened if not n['delivered_at'] and (not n.get('quiet') or n['id'] in self.woke_for)
                 and not vision.is_update(n)]
        waiting = {m['id'] for m in letters} | {n['id'] for n in notes}
        if not waiting and not asks:
            return woke_starting

        def delayed(known, *, retry=True, replies=True):
            mail.report(p, letters, 'delayed', **known)
            if replies:
                mail.report(p, asks, 'reply-delayed', **known)
            # The notice may have landed before this failure arose. Once it clears, try again immediately.
            if retry:
                self.nudged.difference_update(m['id'] for m in letters)
                for m in letters:
                    self.nudged_at.pop(m['id'], None)

        if closing and any(n['id'] == closing['note'] and n['delivered_at'] for n in board.notes(p)):
            delayed({'code': 'project-closing', 'reason': 'recipient is closing; new mail waits until it reopens'})
            return woke_starting

        name, provider = console.session_name(p), providers.of(p)

        def health(snap):
            state = snap['state']
            if state != 'working':
                end = console.died(name) if state == 'off' else None
                text = '\n'.join(end['lines']) if end else (
                    '\n'.join(snap.get('lines', [])) if state == 'off' else console.screen(name))
                problem = provider.mail_problem(text, stopped=state == 'off')
                if problem:
                    snap['delivery_blocker'] = problem
            known = mail.blocker(p, snap)
            # A cached sign-out is explicitly only a last check. It must not prevent a fresh attempt.
            if known and known['code'] in ('signed-out', 'console-off'):
                known = None
            if not known and state == 'needs you':
                known = {'code': 'waiting-choice', 'reason': 'recipient console is waiting for a choice on screen'}
            return known

        def off_problem(*, attempted=False):
            end = console.died(name)
            if not console.COMMAND and not providers.installed(provider):
                return {'code': 'program-missing', 'reason': f"recipient program {provider.program} is not installed"}
            if end:
                return {'code': 'console-stopped',
                        'reason': f"recipient console stopped with an error (exit {int(end['status'])}); underlying cause is unknown"}
            when = ' after its startup attempt' if attempted else ''
            from . import hibernate
            if hibernate.asleep(p) and not attempted:
                return {'code': 'console-off', 'reason': 'recipient console is asleep after a day idle; '
                                                          'the next message for it wakes it'}
            return {'code': 'console-off',
                    'reason': f'recipient console is not running{when}; underlying cause is unknown'}

        snap = console.snapshot(p, lines=12)
        state = snap['state']
        known = health(snap)
        urgent = any(m.get('urgent') for m in letters)
        resume = [n['id'] for n in notes if n.get('kind') == 'resume']
        if known:
            delayed(known, retry=not (known['code'] == 'usage-pause' and urgent), replies=state != 'working')
        if state == 'working' or (not known and state != 'off'):
            mail.report(p, [m for m in asks if m['delivery']['status'] == 'reply-delayed'], 'handed')
        if known and (known['code'] == 'project-paused'  # held words must not start a sleeping console, even urgent mail
                      or (known['code'] == 'usage-pause' and not urgent)
                      or (known['code'] != 'usage-pause' and state != 'off')):
            return woke_starting                  # retain the safe pause's existing nonurgent guard
        if not waiting:
            if state == 'off' and not known:
                delayed(off_problem())
            return woke_starting                  # already-handed backlog alone never starts another turn
        if state == 'off':
            try:
                console.ensure(p)
            except Exception as err:
                delayed({'code': 'console-start',
                         'reason': f"recipient console could not start ({type(err).__name__}); underlying cause is unknown"})
                return woke_starting
            snap = console.snapshot(p, lines=12)
            known = health(snap)
            if known:
                delayed(known)
            elif snap['state'] == 'off':
                delayed(off_problem(attempted=True))
            # A healthy start needs time to draw its prompt; wake it on the next look, one new console at a time.
            return woke_starting

        person_drafting = console.drafting(name) and not (console.draft(name) or '').startswith('[colony]')
        if person_drafting:
            delayed({'code': 'console-draft', 'reason': 'recipient console has unsent input; the queued message waits'},
                    retry=False, replies=False)
            return woke_starting
        now = time.time()
        for i in resume:
            if now - self.nudged_at.get(i, now) > RENUDGE:
                self.nudged.discard(i)
        if state == 'idle' and not known:
            unclaimed = [m for m in letters if m['id'] in self.nudged
                         and now - self.nudged_at.get(m['id'], now) > RENUDGE]
            if unclaimed:
                mail.report(p, unclaimed, 'delayed', code='handoff-unconfirmed',
                            reason='recipient notification was accepted but no hook confirmed handoff; cause is unknown')
                self.nudged.difference_update(m['id'] for m in unclaimed)
        if waiting <= self.nudged:
            return woke_starting
        if state != 'idle' and not ((urgent or resume) and state == 'working'):
            return woke_starting
        starting = console.age(name) < STARTUP_WINDOW
        if starting and woke_starting:
            return woke_starting
        what = " and ".join(filter(None, [
            "a note from the person on the board" if any(n.get('author') not in board.NOT_PERSON for n in notes) else "",
            # notes the retired steward left (R152), still to be handed over: the person's words it relayed, its ideas
            "a note relaying the person's words" if any(n.get('author') == 'monitor' for n in notes) else "",
            "a suggestion" if any(n.get('author') == 'suggestion' for n in notes) else "",
            "an update from Colony" if any(n.get('author') == 'colony' for n in notes) else "",
            "a note from another agent or process" if any(n.get('author') == 'agent' for n in notes) else "",
            "an observed file change" if any(n.get('author') == 'observation' for n in notes) else "",
            "mail from another project in the colony" if letters else ""]))
        try:
            accepted = console.type_into(name, f"[colony] You have {what}.")
        except Exception:
            accepted = False
        if accepted:
            self.nudged |= waiting
            self.nudged_at.update((i, now) for i in ({m['id'] for m in letters} | set(resume)))
            return woke_starting or starting
        # A race may have replaced the input box with a choice or a provider failure since our first glance.
        snap = console.snapshot(p, lines=12)
        known = health(snap) or {'code': 'console-input',
                                 'reason': 'recipient console did not accept notification; underlying cause is unknown'}
        delayed(known)
        return woke_starting

    def close(self):
        """Each project being closed (`colony close`) comes off the board once the turn its note started has ended with
        nothing running; the person is told in a notice on the board."""
        for path, row in board.closing().items():
            p = Path(path)
            if p not in board.projects() or not p.exists():
                board.finish_closing(p)               # taken off some other way: nothing to land any more
                continue
            delivered = any(n["id"] == row["note"] and n["delivered_at"] for n in board.notes(p))
            state = console.snapshot(p, lines=1)["state"]
            self.close_idle[path] = self.close_idle.get(path, 0) + 1 if delivered and state == "idle" else 0
            if board.close_ready(p, state, self.close_idle[path]):
                board.finish_closing(p)
                self.close_idle.pop(path, None)
                board.notice(f"{p.name} is closed, as you asked: it landed its work and is off the board. Its files and "
                             f"conversation are kept; colony track {p} reopens it where it stopped.")

    def models(self):
        """Once a day (and at start), with no tokens: read each program's own list of its models; while any model
        has no Artificial Analysis data yet, ask Artificial Analysis once; keep every project's helper tiers at
        what the data says."""
        if time.time() - self.discovered < 86400:
            return
        self.discovered = time.time()
        threading.Thread(target=self.models_daily, daemon=True).start()

    def signin(self):
        """Cached token-free native status plus current console failures."""
        from . import signin
        import hashlib
        import math
        from datetime import datetime
        signin.tick()
        if time.time() - self.signin_observed < 30:
            return
        self.signin_observed = time.time()
        connected = {}
        for key, row in signin.states().items():
            try:
                connected[key] = datetime.fromisoformat(row.get('connected_at') or '').timestamp()
            except (ValueError, TypeError):
                pass
        seats = [(root, console.session_name(root)) for root in board.projects() if root.exists()]

        for root, nm in seats:
            if console.running(nm):
                provider = providers.PROVIDERS.get(console.running_program(nm)) or providers.of(root)
                age = console.age(nm)
                began = round(time.time() - age) if math.isfinite(age) else 0
                # An older process still holds the old login until its safe reload. Its stale failure must not
                # reject the new sign-in, including when no watcher saw that screen before the reconnect.
                if began < connected.get(providers.key(provider), 0):
                    continue
                text, stopped = console.screen(nm), False
                if provider.classify(text) == 'working':
                    continue
            else:
                end = console.died(nm)
                if not end:
                    continue
                provider = providers.PROVIDERS.get(console.running_program(nm)) or providers.of(root)
                began = end.get('at', 0)
                if began < connected.get(providers.key(provider), 0):
                    continue
                text, stopped = '\n'.join(end['lines']), True
            problem = provider.mail_problem(text, stopped=stopped)
            if problem and problem['code'] == 'authentication':
                marker = hashlib.sha256((nm + '\0' + str(began) + '\0' + text).encode()).hexdigest()
                signin.observe(providers.key(provider), problem, marker)

    def models_daily(self):
        from . import bench
        if board.registry()["settings"]["auto_update"]:
            for p in providers.PROVIDERS.values():
                if providers.usable(p):
                    p.update()                          # installed beside the running version: consoles move over when idle
        from . import codex_remote
        if providers.usable(providers.get("codex")) and codex_remote.check_wanted():
            codex_remote.check_version()                # a new Codex, checked for app access with no tokens, when
                                                        # a project's remote or the repair connection needs it
        providers.discover(calls=False)                  # files each program keeps: nothing is called
        bench.lineup_changed()
        if bench.aa_key() and bench.days_since_fetch() >= 0.9:
            bench.refresh()     # once a day, a few requests (the free API allows 100): new scores, costs, prices
        from . import selection
        selection.reconcile()
        board.rewire_projects()                         # after the updates and the data: each project's files, its helpers' tiers with them

    def usage(self):
        """Every minute, with no tokens: each program's usage limits; past the threshold its projects wind down
        (told on their next turn, not woken), and at the reset they're woken to carry on. Then turbo, which turns
        projects up while a program's week runs behind pace."""
        if board.restore_held() or time.time() - self.usage_checked < 60:
            return
        self.usage_checked = time.time()
        from . import turbo, usage
        try:
            winding = [p.name for p, what in usage.check() if what == "paused"]
        except Exception:
            turbo.stand_down("the safe pause can't read usage", stale=True)    # turbo never runs past it
            raise
        usage.cut_off()                             # and those the limit itself stopped mid-turn, woken as it lifts
        if winding:
            board.notice(f"Winding down at a usage limit: {', '.join(winding)}. Each agent says where things stand on "
                         "its next turn; colony wakes them at the reset.")
        try:
            turbo.tick()
        except Exception as err:                # turbo is extra: its trouble stops nothing else; lasting, it stands down
            turbo.trouble(err)
            turbo.stand_down("turbo ran into trouble", stale=True)

    def current(self):
        """A console set to another program than the one it runs (a switch the person asked for) is
        switched at its first safe moment: idle, nothing typed in it, no one looking at it, and nothing of its own still
        running out of sight (a command or helper it left at work, a turn its program runs elsewhere). Time adds nothing
        to that. So is one whose agent switched a connector (colony connect --on), whose tools load only at a start; once
        it runs with them, its agent is told in one line and carries on. Every two minutes, with no tokens, a console whose program has been updated, or whose settings or helper
        tiers changed, is reloaded the same way into the same conversation, once it has also sat idle a few minutes:
        no one is waiting on that, and a reload under someone reading it is a jolt. Nothing in the middle of work is touched.
        One console a pass, and only with memory to spare: each reads its whole conversation back as it starts, and all
        of them at once ran the machine out (2026-10-28). Each pass also lets go of what a program left running that
        no project needs any more (Provider.let_go)."""
        from . import signin, usage
        import math
        from datetime import datetime
        reconnected = {}
        for k, s in signin.states().items():
            try:
                reconnected[k] = datetime.fromisoformat(s.get('connected_at') or '').timestamp()
            except (ValueError, TypeError):
                pass
        due = time.time() - self.current_checked >= 120
        if due:
            self.current_checked = time.time()
            for p in providers.PROVIDERS.values():
                try:
                    p.let_go()
                except Exception:
                    pass                        # tidying is extra: its trouble stops nothing else
        full = due and board.registry()["settings"]["auto_update"]
        paused = usage.paused()
        seats = [(p, console.session_name(p)) for p in board.projects() if p.exists()]
        reloaded = False
        from . import connections
        switched = connections.pending()
        for root, nm in seats:
            connect = switched.get(str(Path(root).resolve()))
            if not console.running(nm):
                self.idle_since.pop(nm, None)
                if connect:
                    connections.settled(root, connect)    # its next start loads them
                continue
            switch = console.switching(root, nm)
            provider = console.providers_of(root)
            connected = reconnected.get(providers.key(provider))
            age = console.age(nm) if connected else float('inf')
            refresh_signin = bool(connected and math.isfinite(age) and time.time() - age < connected)
            if refresh_signin and str(root) in paused:
                continue                                  # reconnecting never releases their configured work pause
            if not (switch or full or refresh_signin or connect):
                continue
            state = console.snapshot(root, lines=1, name=nm)["state"]
            # a console on the wrong program may sit at that program's own first question (Codex's folder trust):
            # what it waits on isn't the person's, so the switch to their setting goes ahead
            settled = state == "idle" or (switch and state == "needs you")
            if not settled or console.drafting(nm) or console.attached(nm):
                self.idle_since.pop(nm, None)
                continue
            first = self.idle_since.setdefault(nm, time.time())
            if not switch and not refresh_signin and not connect and time.time() - first < RELOAD_AFTER:
                continue
            why = (f"switching from {switch[1]} to {switch[0]}" if switch else
                   "provider sign-in reconnected" if refresh_signin else console.stale(root, nm))
            if connect and not why:
                # its agent switched a connector and the console now runs with it: told in one line, it carries on
                if not any(connect.values()) or console.type_into(nm, connections.notice(connect)):
                    connections.settled(root, connect)      # one switched off needs no word: nothing is waiting on it
                continue
            if not why or reloaded or not console.room() or console.busy(root, nm):
                continue
            if refresh_signin and not provider.signin_refresh(root):
                continue
            console.reload(root, nm)
            reloaded = True
            self.idle_since.pop(nm, None)
            board.home().mkdir(parents=True, exist_ok=True)
            with (board.home() / "reloads.jsonl").open("a") as fh:
                fh.write(json.dumps({"at": board.now(), "console": nm, "why": why}) + "\n")

    def freshen(self):
        """Daily and occupancy refreshes preserve each provider conversation."""
        from . import context
        context.tick_all()

    def tick(self):
        if board.restore_held():
            return
        from . import audit, vision, continuation, recovery, repair
        console.expire_inputs()                     # retract only proven stale system input, never the person's draft
        repair.tick()                               # optional native repair host
        self.signin()
        vision.observe_all()
        continuation.tick_all()
        if time.time() - getattr(self, "ids_kept", 0) > 300:
            self.ids_kept = time.time()
            from . import repoint
            repoint.refind()                        # a project whose folder moved, found by its identity and followed
            board.keep_ids()                        # each project known by its identity, copies told apart (R127)
        self.freshen()
        self.close()                                # a project being closed comes off once it has landed its work
        try:
            from . import carryon
            carryon.tick()                          # idle with work it can start: told so, by rule
        except Exception:
            pass
        try:
            from . import lingering
            lingering.tick()                        # background work gone quiet: named to its agent once
        except Exception:
            pass
        try:
            from . import wakes
            wakes.tick()                            # timed wakes due: a note each, handed over by mail() below
        except Exception:
            pass
        try:
            from . import hibernate
            hibernate.tick()                        # a console idle a day, nothing set to wake it, sleeps (R142)
        except Exception:
            pass
        from . import tokens
        # the tokens spent, kept current for the Usage page: a helper's session is filed under the project that
        # started it while it still runs, so a look every few seconds catches even short ones
        threading.Thread(target=tokens.update, daemon=True).start()
        if time.time() - getattr(self, "reads_kept", time.time()) > 120:
            self.reads_kept = time.time()
            from . import cleanup
            # what each project's sessions read, kept as their transcripts grow, for the cleanup check (R139)
            threading.Thread(target=cleanup.update, daemon=True).start()
        self.models()
        self.usage()                                # the safe pause, its waking at the reset, and turbo
        self.current()                              # a switch or an update reaches its consoles
        self.mail()
        recovery.tick_all()                         # work a network drop stopped, once the network is back
        audit.kick()                                # a connection an agent added is audited
        self.events()                               # each project's own checks, start-up prompts, the board's order

    def at_work(self, p, working, behind=False):
        seen = getattr(self, "stirring", None)
        if seen is None:
            seen = self.stirring = {}
        back = getattr(self, "behind", None)
        if back is None:
            back = self.behind = set()
        (back.add if working and behind else back.discard)(str(p))
        if working and str(p) not in seen:
            board.woke(p)
            seen[str(p)] = time.time()
        elif not working:
            seen.pop(str(p), None)
        board.working_now(seen, back)                 # written only as it changes, or each minute

    def run(self):
        mine = board.home()          # the board it was started for: its home changed under it, it stops
        while board.home() == mine:
            try:
                self.tick()
                passed()
            except Exception as err:
                failed(err)
            time.sleep(self.interval)


# The watcher's pass carries mail, recovery and nudges: one failing in silence every few seconds would
# leave the board looking well while nothing it does happens (OpenClaw's update failed 182 mornings so; KERNOS lay dead
# three weeks). Its error is kept, and from the third in a row every page of the board says so, until a pass gets through.
TROUBLE_AFTER = 3


def _trouble_path():
    return board.home() / "watcher-trouble.json"


def trouble():
    """The watcher failing pass after pass, or None: {"since", "count", "error"}."""
    try:
        t = json.loads(_trouble_path().read_text())
    except (OSError, ValueError):
        return None
    return t if t.get("count", 0) >= TROUBLE_AFTER else None


def failed(err):
    """A pass failed: counted, with its error, and its traceback kept in watcher-errors.log for whoever fixes it."""
    import traceback
    try:
        t = json.loads(_trouble_path().read_text())
    except (OSError, ValueError):
        t = {}
    t = {"since": t.get("since") or board.now(), "count": t.get("count", 0) + 1,
         "error": f"{type(err).__name__}: {err}"[:300]}
    try:
        board.home().mkdir(parents=True, exist_ok=True)
        _trouble_path().write_text(json.dumps(t))
        log = board.home() / "watcher-errors.log"
        if log.exists() and log.stat().st_size > 200_000:
            log.write_text(log.read_text()[-100_000:])
        with log.open("a") as fh:
            fh.write(f"--- {board.now()} pass {t['count']} in a row\n{''.join(traceback.format_exception(err))}")
    except OSError:
        pass


def passed():
    """A pass got through: whatever was failing no longer is."""
    try:
        _trouble_path().unlink()
    except FileNotFoundError:
        pass


def start():
    """The watcher, alongside the board: the safe pause, turbo, mail and the rest of colony's own work."""
    watcher = Watcher()
    threading.Thread(target=watcher.run, daemon=True).start()
    return watcher
