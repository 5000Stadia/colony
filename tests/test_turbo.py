"""Turbo: a program's weekly pace, and what turbo does for its projects while the week runs behind it."""
import io
import json
import os
import threading
from contextlib import redirect_stdout
import time
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from colony import bench, board, console, consult, intelligence, monitor, providers, selection, supports, turbo, usage
from tests.test_board import ROADMAP, BoardBase

DAY, HOUR = turbo.DAY, turbo.HOUR
REAL_STALE = console.stale
REAL_AUTO, REAL_BACKGROUND = consult.auto, turbo.background


def reading(used, day, now, at_ago=0):
    """A weekly reading `day` days into its window, taken `at_ago` seconds before now."""
    return {"at": now - at_ago, "windows": {"weekly": {"used": used, "resets_at": now + turbo.WEEK - day * DAY}}}


class PaceTest(unittest.TestCase):
    now = 1_800_000_000.0

    def test_before_day_two_it_stays_off_however_far_behind(self):
        for day in (0, 0.5, 1, 1.99):
            p = turbo.pace(reading(0, day, self.now), self.now, on=True)
            self.assertFalse(p["on"], f"day {day}")
            self.assertIsNone(p["expected"])
            self.assertIn("before day 2", p["why"])
        self.assertTrue(turbo.pace(reading(0, 2, self.now), self.now)["on"], "from day 2 it judges")

    def test_more_than_five_points_behind_turns_it_on_and_catching_up_turns_it_off(self):
        exp = turbo.expected(self.now + 3 * DAY, self.now)                    # day 4 of 7
        self.assertAlmostEqual(exp, 99 * 4 * DAY / (turbo.WEEK - HOUR))
        for used, was, on, why in ((exp - 5.1, False, True, "more than 5 points behind: on"),
                                   (exp - 4.9, False, False, "within 5 points: stays off"),
                                   (exp - 4.9, True, True, "on, it stays on until caught up: no flapping"),
                                   (exp - 0.1, True, True, "nearly caught up: still on"),
                                   (exp, True, False, "caught up: off"),
                                   (exp + 10, False, False, "ahead: off")):
            self.assertEqual(turbo.pace(reading(used, 4, self.now), self.now, on=was)["on"], on, why)

    def test_the_target_is_99_percent_by_the_hour_before_the_reset(self):
        resets = self.now + 2 * DAY
        self.assertAlmostEqual(turbo.expected(resets, resets - HOUR), 99)
        self.assertEqual(turbo.expected(resets, resets - 60), 99, "the final hour holds at 99")
        self.assertAlmostEqual(turbo.expected(resets, resets - 2 * DAY), 99 * 5 * DAY / (turbo.WEEK - HOUR))
        line = [turbo.expected(resets, resets - h * HOUR) for h in range(48, 0, -1)]
        self.assertEqual(line, sorted(line), "rising through the final two days to the target")
        six_left = reading(0, 7 - 0.25, self.now)                             # six hours before the reset
        exp = turbo.pace(six_left, self.now)["expected"]
        self.assertAlmostEqual(exp, 99 * (turbo.WEEK - 6 * HOUR) / (turbo.WEEK - HOUR))
        self.assertTrue(turbo.pace(dict(six_left, windows={"weekly": dict(six_left["windows"]["weekly"], used=exp - 6)}),
                                   self.now)["on"], "behind in the final hours: on")

    def test_a_missing_or_old_reading_keeps_it_off(self):
        for r in (None, {"at": self.now, "windows": {}}, {"windows": {"weekly": {"used": 1, "resets_at": self.now + DAY}}},
                  {"at": self.now, "windows": {"5-hour": {"used": 1, "resets_at": self.now + HOUR}}},
                  {"at": self.now, "windows": {"weekly": {"used": 1, "resets_at": None}}}):
            self.assertFalse(turbo.pace(r, self.now, on=True)["on"], r)
        old = turbo.pace(reading(0, 4, self.now, at_ago=turbo.STALE + 1), self.now, on=True)
        self.assertFalse(old["on"], "an old reading: off, even mid-turbo")
        self.assertIn("no usage reading in the last 12 hours", old["why"])
        self.assertTrue(turbo.pace(reading(0, 4, self.now, at_ago=11 * HOUR), self.now)["on"],
                        "a reading from last night still counts: Claude's ages while no console of its runs")
        self.assertTrue(turbo.pace(reading(0, 4, self.now, at_ago=turbo.STALE - 60), self.now)["on"])
        self.assertFalse(turbo.pace(reading(0, 7.5, self.now), self.now, on=True)["on"],
                         "the week turned over since the reading: off until a fresh one")

    def test_the_note_comes_at_once_then_daily_and_every_six_hours_in_the_final_two_days(self):
        far, near = self.now + 4 * DAY, self.now + 2 * DAY
        self.assertTrue(turbo.due(None, self.now, far), "at once")
        self.assertFalse(turbo.due(self.now - 23 * HOUR, self.now, far))
        self.assertTrue(turbo.due(self.now - DAY, self.now, far), "then daily")
        self.assertFalse(turbo.due(self.now - 5 * HOUR, self.now, near))
        self.assertTrue(turbo.due(self.now - 6 * HOUR, self.now, near), "every six hours in the final two days")
        self.assertFalse(turbo.due(self.now - 6 * HOUR, self.now, near + HOUR), "two days and an hour out: still daily")

    def test_stronger_models_raise_the_balance_two_positions_never_past_intelligence(self):
        self.assertEqual([turbo.raised(b) for b in range(7)], [0, 0, 0, 1, 2, 3, 4])
        self.assertEqual(turbo.raised("3"), 1)
        with self.assertRaises(ValueError):
            turbo.raised(7)


class TurboBase(BoardBase):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {"CODEX_HOME": str(Path(self.tmp.name) / "codex")})   # no one's real Codex readings
        env.start()
        self.addCleanup(env.stop)
        board.track(self.root)
        self.now = time.time()
        self.resets = self.now + 3 * DAY                                     # day 4 of the week
        self.readings = {}
        self.read_at(self.now)
        self.patch(usage, "reading", lambda key: self.readings.get(key))
        self.state, self.keys = {"state": "idle", "lines": []}, []
        self.patch(console, "snapshot", lambda root, lines=6, name=None: dict(self.state))
        self.patch(console, "drafting", lambda name: False)
        self.patch(console, "attached", lambda name: False)
        self.patch(console, "stale", lambda root, name=None, label=None: None)
        self.patch(console, "type_into", lambda name, text: self.keys.append(text) or True)
        self.patch(console, "press", lambda name, keys: self.keys.append(keys))
        # Research advice: never a real model here; the consultant pick is fixed, and it runs inline unless a test says so.
        self.consulted = []
        self.answer = {"text": "Seed libraries keep germination records by variety: a week reading them could set R3's "
                               "reminder defaults from data rather than guesses.", "cost": 0.42, "usage": {}, "error": None}

        def ask(brief, model, effort, project, price=None):
            self.consulted.append(dict(brief=brief, model=model, effort=effort, project=project))
            return dict(self.answer)
        for p in providers.PROVIDERS.values():
            self.patch(p, "consult", ask)
        self.patch(consult, "auto", lambda key: ("claude-opus-5-5", "max", "the ceiling, at its best effort"))
        self.patch(turbo, "background", lambda fn, *args: fn(*args))
        self.addCleanup(turbo._advising.clear)

    def patch(self, obj, name, value):
        p = patch.object(obj, name, value)
        p.start()
        self.addCleanup(p.stop)

    def read_at(self, t, used=20):
        """Claude Code's weekly reading, taken at t, in this test's week (far behind at 20%)."""
        self.readings["claude"] = {"at": t, "windows": {"weekly": {"used": used, "resets_at": self.resets}}}

    def turbo_notes(self):
        return [n for n in board.notes(self.root) if n["author"] == "colony" and "spare capacity" in n["text"]]

    def advice_notes(self):
        return [n for n in board.notes(self.root) if n["author"] == "suggestion" and n.get("kind") == "advice"]

    def mine(self):
        return ((turbo.load().get("claude") or {}).get("projects") or {}).get(str(self.root), {})

    def asked(self):
        path = board.home() / "to_monitor.jsonl"
        return [json.loads(l)["text"] for l in path.read_text().splitlines()] if path.exists() else []


class TurboTest(TurboBase):
    def test_only_projects_with_queued_work_or_research_are_turned_up(self):
        self.assertTrue(turbo.queued(self.root))
        self.assertEqual(turbo.wanted(self.root), {"models": True, "deeper": True})
        (self.root / "ROADMAP.md").write_text("# Roadmap\n\n## M1 — v1\n\n- [x] R1 add plants\n- [?] R2 water log\n\n"
                                              "### Later\n\n- [ ] R7 frost alerts\n\n## M2 — Later polish\n\n- [ ] R5 themes\n\n"
                                              "## Ideas\n\n- [ ] R8 a plant swap\n\n## Later\n\n- [ ] R3 reminders\n- [~] R4 sharing\n")
        self.assertFalse(turbo.queued(self.root), "done, for the person's eye, Later, or under no version: nothing queued")
        self.assertEqual(turbo.wanted(self.root), {})
        with patch.object(board, "roadmap", lambda root: {"milestones": [{"id": "M1", "title": "v1", "items": [
                {"id": "R6", "state": "todo", "owner": "/elsewhere/the-lead", "after": [], "milestone": "M1"}]}]}):
            self.assertFalse(turbo.queued(self.root), "another member's item isn't this agent's queued work")
        (self.root / "ROADMAP.md").write_text(ROADMAP)
        from colony import lead
        me = str(self.root.resolve())
        group = dict(id="g1", canonical=me, members=[me], lead=me, generation=0, owners={}, assignments={},
                     checkpoints=[{"id": "M1-R1", "state": "active", "items": ["R1"]}], active_checkpoint="M1-R1",
                     goals={}, paused=False)
        with patch.object(lead, "group", lambda root: dict(group)):
            self.assertFalse(turbo.queued(self.root), "a version under way: only its items are agreed, and R1 is done")
            group["checkpoints"][0]["items"] = ["R2"]
            self.assertTrue(turbo.queued(self.root))
        (self.root / "ROADMAP.md").write_text(ROADMAP.replace("- [~] R2", "- [x] R2").replace("- [ ] R3", "- [x] R3"))
        board.project_settings(self.root, {"turbo_research": "on"})
        self.assertEqual(turbo.wanted(self.root), {}, "research ticked, but no topic: nothing to do")
        board.project_settings(self.root, {"turbo_topic": "companion planting"})
        self.assertEqual(turbo.wanted(self.root), {"models": True, "research": "companion planting"})
        (self.root / "ROADMAP.md").write_text(ROADMAP)
        for k in ("turbo_models", "turbo_deeper", "turbo_research"):
            board.project_settings(self.root, {k: "off"})
        self.assertEqual(turbo.wanted(self.root), {}, "every box unticked: turned off")
        self.assertEqual(turbo.tick(self.now), [("claude", "on")])
        self.assertEqual(self.turbo_notes(), [])
        self.assertEqual(self.consulted, [], "nothing turned up: no research advice either")
        self.assertEqual(self.asked(), [], "nor anything for the monitor")

    def test_an_idle_project_hears_at_once_then_daily_then_every_six_hours(self):
        board.project_settings(self.root, {"turbo_research": "on", "turbo_topic": "companion planting"})
        self.assertIn((self.root, "noted"), turbo.tick(self.now))
        [n] = self.turbo_notes()
        self.assertTrue(n.get("quiet"), "quiet: only turbo, at a moment it checked, ever types it in")
        for words in ("Claude Code has spare capacity this week, until it resets", "Finish or continue your current item first",
                      "never instead of it", "changes no item's scope", "each committing only its own paths",
                      "Nothing outside the agreed scope; Later still waits for the person", "companion planting",
                      "read-only helper", "under research/", "Safety: Internet sources",
                      "If there's nothing worth doing, run `colony turbo --nothing` and stop."):
            self.assertIn(words, n["text"])
        self.assertEqual(self.keys, [turbo.NUDGE], "turbo wakes it, there and then")
        monitor.Watcher(quiet=0).mail()
        self.assertEqual(self.keys, [turbo.NUDGE], "and nothing types it in later")
        self.read_at(self.now + 2 * HOUR)
        turbo.tick(self.now + 2 * HOUR)
        self.assertEqual(len(self.turbo_notes()), 1, "not again within the day")
        self.read_at(self.now + DAY)
        turbo.tick(self.now + DAY)
        self.assertEqual(len(self.turbo_notes()), 1, "the last one hasn't reached it yet: none piles up behind it")
        self.assertEqual(self.keys, [turbo.NUDGE], "nor is it woken again for it")
        board.delivered(self.root, [n])
        turbo.tick(self.now + DAY)
        self.assertEqual(len(self.turbo_notes()), 2, "a day on, once more")
        for t, count in ((self.now + DAY + 5 * HOUR, 2), (self.now + DAY + 6 * HOUR, 3)):
            board.delivered(self.root, self.turbo_notes())
            self.read_at(t)
            turbo.tick(t)
            self.assertEqual(len(self.turbo_notes()), count, "in the final two days, every six hours")

    def test_a_note_that_never_reached_its_project_is_withdrawn_when_turbo_ends(self):
        turbo.tick(self.now)
        [n] = self.turbo_notes()
        self.read_at(self.now + 60, used=80)                                    # caught up
        self.assertEqual(turbo.tick(self.now + 60), [("claude", "off")])
        self.assertTrue(next(x for x in board.notes(self.root) if x["id"] == n["id"])["addressed_at"])
        self.assertNotIn(n["id"], [x["id"] for x in board.open_notes(self.root)], "it never wakes anyone after turbo")

    def test_a_note_still_waiting_is_let_go_once_the_person_is_needed_or_its_console_stops(self):
        started = []
        self.patch(console, "ensure", lambda root, name=None, label=None: started.append(root))
        with patch.object(console, "type_into", lambda name, text: False):    # someone began typing just then
            turbo.tick(self.now)
        [n] = self.turbo_notes()
        board.record_ask(self.root, "turn-2", "Shall I start on reminders?")   # its turn ends asking the person
        monitor.Watcher(quiet=0).mail()
        self.assertEqual(self.keys, [], "a quiet note: nothing types it in over their question")
        self.read_at(self.now + 60)
        turbo.tick(self.now + 60)
        self.assertTrue(next(x for x in board.notes(self.root) if x["id"] == n["id"])["addressed_at"], "let go")
        board.answer_asks(self.root, "test")
        self.read_at(self.now + 120)
        turbo.tick(self.now + 120)
        [m] = [x for x in self.turbo_notes() if not x["addressed_at"]]
        self.assertEqual(self.keys, [turbo.NUDGE], "it hears once nothing waits on the person")
        self.state = {"state": "off", "lines": []}                               # say it never got there, and stopped
        self.read_at(self.now + 180)
        turbo.tick(self.now + 180)
        self.assertTrue(next(x for x in board.notes(self.root) if x["id"] == m["id"])["addressed_at"])
        monitor.Watcher(quiet=0).mail()
        self.assertEqual(started, [], "a stopped console stays stopped")

    def test_a_waking_that_met_a_draft_is_tried_again_once_the_box_is_clear_and_only_once(self):
        with patch.object(console, "type_into", lambda name, text: False):    # a draft was there
            turbo.tick(self.now)
        self.assertEqual((len(self.turbo_notes()), self.keys), (1, []))
        self.read_at(self.now + 60)
        turbo.tick(self.now + 60)
        self.assertEqual(self.keys, [turbo.NUDGE], "the box is clear: woken now")
        self.read_at(self.now + 120)
        turbo.tick(self.now + 120)
        self.assertEqual(self.keys, [turbo.NUDGE], "once, even if its hooks never hand the note over")
        self.assertEqual(len(self.turbo_notes()), 1)

    def test_an_agent_that_finds_nothing_worth_doing_hears_no_more_until_its_roadmap_changes_shape(self):
        turbo.tick(self.now)
        [n] = self.turbo_notes()
        board.delivered(self.root, [n])
        r = self.cli("turbo", "--nothing")                                    # its answer, run in its own folder
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(next(x for x in board.notes(self.root) if x["id"] == n["id"])["addressed_at"], "the note is answered")
        (self.root / "ROADMAP.md").write_text(ROADMAP.replace("A tool for my plants.", "A tool for my plants and herbs.")
                                              .replace("- [ ] R3 reminders", "- [ ] R3 reminders\n  by first frost dates"))
        for t in (self.now + DAY, self.now + DAY + 6 * HOUR, self.now + DAY + 12 * HOUR):  # daily, then every six hours
            self.read_at(t)
            turbo.tick(t)
        self.assertEqual(len(self.turbo_notes()), 1, "no note while its roadmap keeps its shape, rewording aside")
        self.assertEqual(self.keys, [turbo.NUDGE], "nor is it woken")
        self.assertIn("plants: stronger models, going deeper; it found nothing worth doing (", turbo.report(self.now + DAY))
        self.assertIn("its agent found nothing worth doing, so no note until its roadmap changes", turbo.project_line(self.root))
        (self.root / "ROADMAP.md").write_text(ROADMAP + "- [ ] R4 seed swaps\n")
        self.read_at(self.now + DAY + 18 * HOUR)
        turbo.tick(self.now + DAY + 18 * HOUR)
        self.assertEqual(len(self.turbo_notes()), 2, "its roadmap changed shape: it hears again")
        self.assertEqual(self.keys, [turbo.NUDGE, turbo.NUDGE])
        board.delivered(self.root, self.turbo_notes())
        turbo.nothing(self.root)
        self.read_at(self.now + 2 * DAY)
        turbo.tick(self.now + 2 * DAY)
        self.assertEqual(len(self.turbo_notes()), 2, "nothing again, for the new shape")
        board.project_settings(self.root, {"turbo_research": "on", "turbo_topic": "companion planting"})
        self.read_at(self.now + 2 * DAY + 6 * HOUR)
        turbo.tick(self.now + 2 * DAY + 6 * HOUR)
        self.assertEqual(len(self.turbo_notes()), 3, "a research topic the person typed is new work: it hears it")
        self.assertIn("companion planting", self.turbo_notes()[-1]["text"])

    def test_it_never_interrupts_work_in_progress(self):
        from colony import cli
        started = []
        self.patch(console, "ensure", lambda root, name=None, label=None: started.append(root))
        for snap, drafting, attached, why in (({"state": "working"}, False, False, "working"),
                                              ({"state": "idle"}, True, False, "a draft in its box"),
                                              ({"state": "idle"}, False, True, "someone has it open"),
                                              ({"state": "idle", "scrolled": True}, False, False, "scrolled up: can't tell"),
                                              ({"state": "needs you"}, False, False, "a question on its screen"),
                                              ({"state": "off"}, False, False, "not running: turbo starts nothing")):
            self.state = dict(snap, lines=[])
            with patch.object(console, "drafting", lambda n: drafting), patch.object(console, "attached", lambda n: attached), \
                    patch.object(providers.get("claude"), "choice", lambda screen: None, create=True), \
                    patch.object(console, "screen", lambda name: ""), redirect_stdout(io.StringIO()):
                turbo.tick(self.now)
                cli.main(["suggest", "plants", f"an idea, while {why}"])          # the monitor's suggestion waits too
                monitor.Watcher(quiet=0).mail()
            self.assertEqual(self.turbo_notes(), [], why)
            self.assertEqual(self.keys, [], why)
        self.assertEqual(started, [], "nothing was started for turbo or its suggestions")
        self.assertTrue(all(n.get("quiet") for n in board.notes(self.root) if n["author"] == "suggestion"),
                        "each waits for the agent's next turn")
        self.state = {"state": "idle", "lines": []}
        turbo.tick(self.now)
        self.assertEqual(len(self.turbo_notes()), 1, "idle and untouched: now")
        self.assertEqual(self.keys, [turbo.NUDGE])
        with redirect_stdout(io.StringIO()):
            cli.main(["suggest", "plants", "an idea, while it sits idle"])
        self.assertEqual(self.keys, [turbo.NUDGE, "[colony] You have a suggestion from the monitor."])

    def test_it_never_wakes_a_project_waiting_on_the_person(self):
        from colony import cli, continuation, lead, progress

        def nothing(why):
            done = turbo.tick(self.now)
            self.assertFalse([x for x in done if x[0] == self.root], why)
            self.assertEqual(self.turbo_notes(), [], why)
            self.assertNotIn("boost", self.mine(), f"{why}: no model change either")
            self.assertEqual(self.consulted, [], f"{why}: nor research advice")
            with redirect_stdout(io.StringIO()):
                cli.main(["suggest", "plants", f"an idea, while {why}"])         # nor does the monitor's suggestion
            monitor.Watcher(quiet=0).mail()
            self.assertEqual(self.keys, [], why)
        gate = board.add_gate(self.root, "Which reminders first?")
        nothing("an open gate")
        board.clear_gate(self.root, gate["id"])
        board.record_ask(self.root, "turn-1", "Shall I start on reminders?")
        nothing("a question to the person")
        board.answer_asks(self.root, "test")
        (self.root / "ROADMAP.md").write_text(ROADMAP.replace("- [x] R1", "- [?] R1"))
        nothing("an item waiting for their eye")
        (self.root / "ROADMAP.md").write_text(ROADMAP)
        usage.folder().mkdir(parents=True, exist_ok=True)
        (usage.folder() / "paused.json").write_text(json.dumps({str(self.root): {"provider": "claude", "window": "weekly"}}))
        nothing("paused at a usage limit")
        (usage.folder() / "paused.json").write_text("{}")
        me = str(self.root.resolve())
        group = dict(id="g1", canonical=me, members=[me], lead=me, generation=0, owners={}, assignments={},
                     checkpoints=[], goals={}, paused=False)
        with patch.object(lead, "group", lambda root: dict(group)), patch.object(continuation, "status", lambda root: {}):
            for hold in ("person paused", "version waiting for human review", "lead handoff", "context refresh",
                         "helper assignment delivered", "waiting for an assigned helper or predecessor"):
                with patch.object(progress, "hold", lambda root, member: hold):
                    nothing(hold)
            with patch.object(progress, "hold", lambda root, member: "no bounded version selected"):
                with patch.object(continuation, "status", lambda root: {"manual_stop": True}):
                    nothing("the person stopped its continuation")
                group["checkpoints"] = [{"id": "M1", "state": "accepted", "items": ["R1"]}]
                nothing("a version finished and no next released: the person stopped it there")
                group["checkpoints"] = []
                self.assertIn((self.root, "noted"), turbo.tick(self.now),
                              "a project not working in versions: no version is its everyday state")
        self.assertEqual(self.keys, [turbo.NUDGE], "nothing in their hands: now it hears")

    def test_when_unsure_it_does_nothing(self):
        for r, why in ((None, "no reading"), ({"at": self.now, "windows": {}}, "no weekly window"),
                       (reading(10, 4, self.now, at_ago=turbo.STALE + 60), "an old reading")):
            self.readings["claude"] = r
            self.assertEqual(turbo.tick(self.now), [], why)
        self.assertEqual(self.turbo_notes(), [])
        self.assertIn("turbo off (no usage reading in the last 12 hours)", turbo.line("claude", self.now))
        w = monitor.Watcher(quiet=0)
        w.usage_checked = 0
        with patch.object(turbo, "tick", side_effect=RuntimeError("a surprise")):
            w.usage()                                                           # turbo's trouble stops nothing else
        self.assertIn("RuntimeError: a surprise", turbo.report(self.now))

    def test_one_projects_trouble_never_stops_turbo_for_the_rest(self):
        other = Path(self.tmp.name) / "seeds"
        other.mkdir()
        (other / "ROADMAP.md").write_text(ROADMAP)
        board.track(other)
        real = turbo.options
        with patch.object(turbo, "options", lambda root: (_ for _ in ()).throw(OSError("unreadable")) if root == self.root
                          else real(root)):
            done = turbo.tick(self.now)
        self.assertIn((other, "noted"), done)
        self.assertNotIn((self.root, "noted"), done)
        self.assertIn("OSError: unreadable", turbo.report(self.now))

    def test_turbo_off_leaves_selection_instructions_and_consoles_untouched(self):
        name = console.ensure(self.root)

        def everything():
            files = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file() and ".git" not in p.parts}
            with patch.object(console, "COMMAND", None):                       # the real command, read, not run
                command, fingerprint = console.command("plants", self.root), console.fingerprint(self.root)
            return dict(files=files, consoles=(board.home() / "consoles.json").read_bytes(),
                        ledger=(board.home() / "model-selection.json").read_bytes(), command=command,
                        fingerprint=fingerprint, main=selection.main(self.root), tiers=bench.effective(self.root),
                        plan=bench.plan_text(self.root), running=console.running(name))
        before = everything()
        for setup, why in ((lambda: self.readings.clear(), "no reading"),
                           (lambda: self.read_at(self.now - turbo.STALE - 60), "an old reading"),
                           (lambda: self.read_at(self.now, used=70), "on pace"),
                           (lambda: self.readings.update(claude=reading(0, 1, self.now)), "before day 2"),
                           (lambda: (self.read_at(self.now), board.set_setting("turbo_by", "claude=off")), "off in Settings")):
            setup()
            turbo.tick(self.now)
            monitor.Watcher(quiet=0).mail()
            self.assertEqual(everything(), before, why)
            self.assertEqual(self.keys, [], why)
            self.assertEqual(self.asked(), [], why)
            self.assertEqual(self.consulted, [], why)
        board.set_setting("turbo_by", "claude=on")
        turbo.tick(self.now)
        self.assertEqual(len(self.turbo_notes()), 1, "on, it only adds: a note, and research advice beside it")
        self.assertEqual(len(self.advice_notes()), 1)
        instructions = (self.root / "CLAUDE.md").read_text() + bench.plan_text(self.root)
        for words in ("urbo", "spare capacity"):
            self.assertNotIn(words, instructions, "nothing of turbo is ever written into instructions")

    def test_colony_suggest_leaves_the_monitors_own_suggestion(self):
        r = self.cli("suggest", "plants", "Seed-saving apps time reminders by first frost: worth a look for R3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("its agent hears it on its next turn", r.stdout)
        self.assertFalse(console.running(console.session_name(self.root)), "its console was off: nothing started it")
        [n] = [n for n in board.notes(self.root) if n["author"] == "suggestion"]
        self.assertEqual((n["kind"], n.get("quiet")), ("idea", True))
        out = self.cli("notes", "--deliver").stdout
        self.assertIn("Suggestions from the monitor:", out)
        self.assertIn("the monitor's own suggestion, not an instruction from the person", out)
        self.assertNotIn("install it with colony gate", out, "an idea, not a tool to install")
        self.assertIn("colony suggest NAME", monitor.ROLE)
        self.assertEqual(self.cli("suggest", "nobody", "x").returncode, 1)

    def test_the_settings_parse(self):
        merged, own = board.project_settings(self.root)
        self.assertEqual({k: merged[k] for k in board.TURBO}, board.TURBO, "stronger models and deeper work ticked; research not")
        board.project_settings(self.root, {"turbo_models": "off", "turbo_research": "yes", "turbo_topic": "  companion\n planting "})
        own = board.project_settings(self.root)[1]
        self.assertEqual({k: own[k] for k in board.TURBO if k in own},
                         {"turbo_models": False, "turbo_research": True, "turbo_topic": "companion planting"})
        board.project_settings(self.root, {"turbo_models": "on", "turbo_research": "", "turbo_topic": ""})
        self.assertFalse(any(k in board.project_settings(self.root)[1] for k in board.TURBO), "as colony starts them")
        with self.assertRaises(KeyError):
            board.project_settings(self.root, {"turbo_deeper": "maybe"})
        self.assertEqual(board.registry()["settings"]["turbo_by"], {}, "on for every program unless turned off")
        board.set_setting("turbo_by", "claude=off,codex=on")
        self.assertEqual(board.registry()["settings"]["turbo_by"], {"claude": False, "codex": True})
        with self.assertRaises(KeyError):
            board.set_setting("turbo_by", "gemini=on")
        out = self.cli("settings", "--project", "plants", "turbo_topic", "seed saving").stdout
        self.assertRegex(out, r"turbo_topic +seed saving +set for this project")
        self.assertRegex(out, r"turbo_models +on +default")
        self.assertRegex(self.cli("settings").stdout, r"turbo_by +claude=off,codex=on")
        with patch.object(console, "COMMAND", None):
            before = console.command("plants", self.root)
            board.project_settings(self.root, {"provider": "codex"})
            codex = console.command("plants", self.root)
            board.project_settings(self.root, {"turbo_research": "on", "turbo_deeper": "off"})
            self.assertEqual(console.command("plants", self.root), codex, "what turbo means for it never changes how it starts")
            self.assertNotIn("turbo", codex, "not even in the settings Codex is launched with")
            board.project_settings(self.root, {"provider": ""})
            self.assertEqual(console.command("plants", self.root), before)

    def test_the_board_shows_the_pace_and_takes_the_options(self):
        self.assertTrue(turbo.tick(self.now))
        page = board.settings_page(board.registry())
        self.assertIn("Pace: 20% of its week used, 57% expected by now: turbo on until it resets", page)
        self.assertIn("name='turbo_claude' value='on' checked", page)
        self.assertIn("Turbo: Claude Code: 20% of its week used", board.models_page(board.registry()))
        form = board.project_settings_form(0, {}, root=self.root)
        self.assertIn("name='turbo_models' value='on' checked><input type='hidden' name='turbo_models' value='off'>", form)
        self.assertIn("Turbo is on for Claude Code until it resets", form)
        usage.folder().mkdir(parents=True, exist_ok=True)
        (usage.folder() / "claude.json").write_text(json.dumps(self.readings["claude"]))    # for the command, run apart
        out = self.cli("turbo").stdout
        self.assertIn("Claude Code: 20% of its week used, 57% expected by now: turbo on", out)
        self.assertIn("  plants: stronger models, going deeper; last note", out)
        self.assertRegex(out, r"; research advice .+ \(turbo-advice-\d{4}-\d{2}-\d{2}\.md\)")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), board.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            port = httpd.server_address[1]
            post = lambda path, fields: urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:{port}{path}", data=urllib.parse.urlencode(fields).encode()))
            post("/project-settings", [("p", "0"), ("turbo_models", "off"), ("turbo_deeper", "on"), ("turbo_deeper", "off"),
                                       ("turbo_research", "on"), ("turbo_research", "off"), ("turbo_topic", "first frost dates")])
            own = board.project_settings(self.root)[1]
            self.assertEqual({k: own.get(k) for k in board.TURBO},
                             {"turbo_models": False, "turbo_deeper": None, "turbo_research": True, "turbo_topic": "first frost dates"},
                             "an unticked box says off; a ticked one at its default keeps nothing of its own")
            board.set_setting("turbo_by", "codex=off")
            board.set_setting("providers", "claude")                         # Codex off: its box isn't shown
            post("/options", [("monitor", "on")])
            self.assertEqual(board.registry()["settings"]["turbo_by"], {"codex": False, "claude": False},
                             "unticked in Settings: off for Claude Code; Codex, not shown, keeps its own")
        finally:
            httpd.shutdown()
            httpd.server_close()


class AdviceTest(TurboBase):
    """Research advice: once a turbo day, colony asks the program's consultant, apart from the project's agent, and
    leaves its answer in the project as a report with one quiet note. The agent decides what to do with it."""

    def report_of(self, n):
        [path] = [e["report"] for e in board.read(self.root, turbo.LOG) if e.get("note") == n["id"]]
        return Path(path)

    def day(self, t):
        return time.strftime("%Y-%m-%d", time.localtime(t))

    def test_once_a_turbo_day_as_a_report_and_a_quiet_note_with_nothing_typed_and_the_monitor_never_asked(self):
        board.project_settings(self.root, {"turbo_deeper": "off"})            # stronger models only: turbo types nothing
        monitor.muted(True)                                                    # the monitor's mute is beside the point
        monitor.queue("a word from the person's board")
        self.assertIn((self.root, "research advice"), turbo.tick(self.now))
        self.assertEqual(len(self.consulted), 1)
        [n] = self.advice_notes()
        self.assertTrue(n.get("quiet"), "it waits for the agent's next turn")
        path = self.report_of(n)
        self.assertEqual(path, self.root / "research" / f"turbo-advice-{self.day(self.now)}.md")
        self.assertIn(str(path), n["text"], "the note points at the report")
        report = path.read_text()
        self.assertIn(self.answer["text"], report)
        self.assertIn("from this project's agent while Claude Code had spare weekly capacity", report)
        self.assertTrue(report.rstrip().endswith(supports.NOTICE), "it ends with the safety line")
        self.assertIn("?? research/", self.git("status", "--porcelain"), "left to the agent: nothing is committed")
        monitor.Watcher(quiet=0).mail()
        self.assertEqual(self.keys, [], "nothing is typed into its console")
        self.assertEqual(self.asked(), ["a word from the person's board"], "and the monitor is asked nothing")
        self.read_at(self.now + DAY)
        turbo.tick(self.now + DAY)
        self.assertEqual(len(self.consulted), 1, "a day on, but the last hasn't reached the agent: none piles up behind it")
        out = self.cli("notes", "--deliver").stdout                            # its next turn
        self.assertIn("Research advice colony asked for, apart from you:", out)
        self.assertIn(board.ADVICE, out)
        self.assertNotIn("Suggestions from the monitor:", out, "never passed off as the monitor's")
        self.assertEqual(self.cli("noted", n["id"], "Worth a look once R3 starts").returncode, 0)
        self.assertEqual(self.asked(), ["a word from the person's board"], "its answer isn't the monitor's to hear")
        mine = self.root / "research" / f"turbo-advice-{self.day(self.now + DAY)}.md"
        mine.write_text("the agent's own notes\n")
        turbo.tick(self.now + DAY)
        self.assertEqual(len(self.consulted), 2, "heard, and a day on: once more")
        second = self.advice_notes()[1]
        self.assertEqual(self.report_of(second).name, f"turbo-advice-{self.day(self.now + DAY)}-2.md")
        self.assertEqual(mine.read_text(), "the agent's own notes\n", "never over a file already there")
        board.delivered(self.root, [second])
        self.read_at(self.now + DAY + HOUR)
        turbo.tick(self.now + DAY + HOUR)
        self.assertEqual(len(self.consulted), 2, "heard, but not again within the day")
        self.read_at(self.now + DAY + HOUR + 60, used=80)
        self.assertEqual(turbo.tick(self.now + DAY + HOUR + 60), [("claude", "off")])
        self.assertEqual(self.asked(), ["a word from the person's board"], "turbo ending takes back nothing it never queued")
        self.read_at(self.now + DAY + HOUR + 120)
        turbo.tick(self.now + DAY + HOUR + 120)
        self.assertEqual(len(self.consulted), 2, "behind again the same day: at most once a day, episodes aside")
        self.assertEqual(self.keys, [])
        self.assertIn("plants: stronger models; research advice ", turbo.report(self.now + DAY + HOUR + 120))

    def test_its_brief_is_the_projects_own_words_and_work_then_the_question(self):
        board.project_settings(self.root, {"turbo_research": "on", "turbo_topic": "companion planting"})
        (self.root / "ROADMAP.md").write_text(ROADMAP.replace("- [ ] R3 reminders", "- [ ] R3 reminders\n  by first frost dates"))
        self.commit("R2 water log: daily entries")
        turbo.tick(self.now)
        [asked] = self.consulted
        self.assertEqual((asked["model"], asked["effort"]), ("claude-opus-5-5", "max"), "the program's consultant pick")
        self.assertEqual(asked["project"], self.root, "it reads the project by path, read-only, where its program allows")
        for words in ("apart from the project's own agent", "in the person's words\nA tool for my plants.",
                      "M1 — v1: it works for me", "- R1 (done) add plants", "- R2 (under way) water log",
                      "- R3 (to do) reminders: by first frost dates", "- R2 water log: daily entries", "- start",
                      "companion planting", "never in its place", "nobody has considered", "purpose, not its topic",
                      "At most two suggestions", "under 400 words", "the single word NONE", "don't survey it"):
            self.assertIn(words, asked["brief"])

    def test_nothing_standing_out_or_a_failed_answer_leaves_nothing_to_weigh(self):
        self.answer = {"text": "NONE.", "cost": 0.2, "usage": {}, "error": None}
        turbo.tick(self.now)
        self.assertEqual((len(self.consulted), self.advice_notes()), (1, []))
        self.assertFalse((self.root / "research").exists())
        self.assertIn("(nothing stood out)", turbo.report(self.now))
        self.answer = {"text": "", "cost": None, "usage": {}, "error": "TimeoutExpired"}
        self.read_at(self.now + DAY)
        turbo.tick(self.now + DAY)
        self.assertEqual((len(self.consulted), self.advice_notes()), (2, []))
        self.assertIn("research advice for plants: TimeoutExpired", turbo.report(self.now + DAY))
        self.read_at(self.now + DAY + 60)
        turbo.tick(self.now + DAY + 60)
        self.assertEqual(len(self.consulted), 2, "tried again tomorrow, not every minute")

    def test_none_with_consulting_off_its_own_turbo_off_paused_or_while_it_waits_on_the_person(self):
        board.set_setting("consult", "off")
        turbo.tick(self.now)
        self.assertEqual((self.consulted, self.advice_notes()), ([], []), "consulting off: no advice")
        self.assertFalse((self.root / "research").exists())
        board.set_setting("consult", "on")
        for k in ("turbo_models", "turbo_deeper", "turbo_research"):
            board.project_settings(self.root, {k: "off"})
        self.read_at(self.now + 60)
        turbo.tick(self.now + 60)
        self.assertEqual(self.consulted, [], "turbo turned off for this project: none")
        board.project_settings(self.root, {"turbo_models": "on"})
        usage.folder().mkdir(parents=True, exist_ok=True)
        (usage.folder() / "paused.json").write_text(json.dumps({str(self.root): {"provider": "claude", "window": "5-hour"}}))
        self.read_at(self.now + 120)
        turbo.tick(self.now + 120)
        self.assertEqual(self.consulted, [], "paused at its program's usage limit: none")
        (usage.folder() / "paused.json").write_text("{}")
        gate = board.add_gate(self.root, "Which reminders first?")
        self.read_at(self.now + 180)
        turbo.tick(self.now + 180)
        self.assertEqual(self.consulted, [], "waiting on the person: it waits too")
        board.clear_gate(self.root, gate["id"])
        self.read_at(self.now + 240)
        self.assertIn((self.root, "research advice"), turbo.tick(self.now + 240))

    def test_it_runs_beside_the_watchers_minute_never_twice_at_once(self):
        self.patch(turbo, "background", REAL_BACKGROUND)
        go = threading.Event()

        def slow(brief, model, effort, project, price=None):
            self.consulted.append(dict(project=project))
            go.wait(30)
            return dict(self.answer)

        def finish():
            go.set()
            for t in threading.enumerate():
                if t.name == "colony-turbo-advice":
                    t.join(30)
        self.patch(providers.get("claude"), "consult", slow)
        other = Path(self.tmp.name) / "seeds"
        other.mkdir()
        (other / "ROADMAP.md").write_text(ROADMAP)
        board.track(other)
        try:                                    # released here, while this test's board still stands, whatever happens
            began = time.monotonic()
            done = turbo.tick(self.now)
            self.assertLess(time.monotonic() - began, 10, "the watcher's minute goes on while the model thinks")
            self.assertIn((self.root, "research advice"), done)
            self.assertNotIn((other, "research advice"), done, "one at a time for a program")
            self.assertFalse(turbo.advise(self.root, "claude", self.now + 2 * DAY), "never twice at once")
            self.assertIn("research advice asked", turbo.report(self.now))
            finish()
            self.assertEqual((len(self.consulted), len(self.advice_notes())), (1, 1))
            self.read_at(self.now + 60)
            self.assertIn((other, "research advice"), turbo.tick(self.now + 60), "then the next")
        finally:
            finish()
        self.assertEqual(len(self.consulted), 2)


class StrongerModelsTest(TurboBase):
    """Stronger models come once a turbo episode, through the console's idle reload, and leave without one."""

    def setUp(self):
        super().setUp()
        from colony import intelligence
        rows = intelligence.records(json.loads(Path(bench.__file__).with_name("data").joinpath("aa-pairs.json").read_text()))
        models, levels = ["claude-opus-5-5", "claude-sonnet-5-5"], ["low", "medium", "high", "xhigh", "max"]
        for obj, name, value in ((bench, "records", lambda: rows), (bench, "lineup", lambda: [("claude", m, m, levels) for m in models]),
                                 (providers, "available", lambda p: [(m, m) for m in models] if providers.key(p) == "claude" else []),
                                 (providers, "efforts_of", lambda p, m: levels)):
            self.patch(obj, name, value)
        claude = providers.get("claude")
        self.name = console.session_name(self.root)
        self.patch(type(claude), "version", lambda self: None)
        self.patch(console, "running_version", lambda name: None)
        self.patch(console, "running", lambda name: name == self.name)
        self.patch(console, "stale", REAL_STALE)
        console.COMMAND = None                  # the real command is read, never started (BoardBase puts it back)
        selection.reconcile()
        bench.write_helpers(self.root)
        self.base = {r: selection.pair(selection.main(self.root) if r == "main" else selection.helper(self.root, r))
                     for r in ("main", "routine", "step-up", "chores", "rote")}
        console._started(self.name, console.fingerprint(self.root))           # its console, as started before turbo
        self.assertIsNone(console.stale(self.root))

    def seats(self):
        return {r: selection.pair(selection.main(self.root) if r == "main" else selection.helper(self.root, r))
                for r in ("main", "routine", "step-up", "chores", "rote")}

    def routine_file(self):
        return (self.root / ".claude" / "agents" / "colony-routine.md").read_text()

    def test_they_come_once_through_the_idle_reload_and_leave_without_one(self):
        self.assertIn((self.root, "stronger models"), turbo.tick(self.now))
        up = self.seats()
        ceiling = max(x["score"] for x in intelligence.pairs(bench.standings()))
        for role in ("main", "routine"):
            self.assertNotEqual(up[role], self.base[role], f"{role}: stronger")
            want = bench.role_pick("claude", role, bench.standings(), balance=1, ceiling=ceiling)
            self.assertEqual(up[role], selection.pair(want), f"{role}: Auto two positions toward Intelligence")
        self.assertEqual({r: up[r] for r in ("step-up", "chores", "rote")}, {r: self.base[r] for r in ("step-up", "chores", "rote")},
                         "step-up is already at the ceiling; chores and rote are left unboosted")
        self.assertIn(f"model: {up['routine']['model']}", self.routine_file())
        self.assertTrue(selection.main(self.root)["why"].startswith("Turbo: Intelligence + 1"))
        self.assertEqual(console.stale(self.root), "its settings or helper tiers changed",
                         "one reload is due, which the watcher makes only once the console sits idle and untouched")
        self.assertEqual(self.turbo_notes(), [], "the deeper work waits for the stronger models")
        console._started(self.name, console.fingerprint(self.root))           # the watcher's idle reload
        turbo.tick(self.now + 60)
        self.assertEqual(len(self.turbo_notes()), 1, "then it hears")
        (self.root / "ROADMAP.md").write_text(ROADMAP.replace("[~]", "[x]").replace("[ ]", "[x]"))
        self.read_at(self.now + 120)
        turbo.tick(self.now + 120)
        self.assertEqual(self.seats(), up, "kept for the episode, even once its queued work is done")
        self.assertIsNone(console.stale(self.root), "no second reload in an episode")
        self.read_at(self.now + 180, used=80)
        self.assertIn(("claude", "off"), turbo.tick(self.now + 180))
        self.assertEqual(self.seats(), self.base, "turbo over: Auto's own picks")
        self.assertIn(f"model: {self.base['routine']['model']}", self.routine_file())
        self.assertIsNone(console.stale(self.root), "no reload back: it moves over when it next restarts for its own reasons")

    def test_unticked_mid_turbo_they_go_without_a_reload_and_dont_come_back_that_episode(self):
        turbo.tick(self.now)
        console._started(self.name, console.fingerprint(self.root))
        board.project_settings(self.root, {"turbo_models": "off"})
        turbo.tick(self.now + 60)
        self.assertEqual(self.seats(), self.base)
        self.assertIsNone(console.stale(self.root))
        board.project_settings(self.root, {"turbo_models": "on"})
        turbo.tick(self.now + 120)
        self.assertEqual(self.seats(), self.base, "at most one change an episode")

    def test_with_the_monitor_off_turbo_stands_down_without_a_reload(self):
        turbo.tick(self.now)
        console._started(self.name, console.fingerprint(self.root))           # reloaded onto them
        with patch.object(monitor.threading, "Thread"):                        # no watcher thread in a test
            monitor.start(enabled=False)
        self.assertEqual(self.seats(), self.base, "nothing keeps turbo current: it stands down")
        self.assertIsNone(console.stale(self.root), "and no reload back")
        self.assertIn("turbo isn't running (the monitor is off, and turbo runs with it)", turbo.line("claude"))

    def test_lasting_trouble_stands_it_down_and_a_new_turn_reloads_nothing_the_console_already_runs(self):
        turbo.tick(self.now)
        up = self.seats()
        console._started(self.name, console.fingerprint(self.root))
        w = monitor.Watcher(quiet=0)
        with patch.object(turbo, "tick", side_effect=RuntimeError("a surprise")):
            w.usage_checked = 0
            w.usage()
            self.assertEqual(self.seats(), up, "a passing trouble changes nothing")
            state = turbo.load()
            state["claude"]["at"] = time.time() - turbo.FRESH - 60
            turbo.save(state)
            w.usage_checked = 0
            w.usage()
        self.assertEqual(self.seats(), self.base, "lasting: it stands down")
        self.assertIsNone(console.stale(self.root))
        self.read_at(time.time())
        turbo.tick(time.time())                                                # back, and still behind: a new turn
        self.assertEqual(self.seats(), up)
        self.assertIsNone(console.stale(self.root), "the console already runs them: no reload into the same")

    def test_ending_survives_one_projects_trouble(self):
        turbo.tick(self.now)
        console._started(self.name, console.fingerprint(self.root))
        self.read_at(self.now + 60, used=80)
        with patch.object(bench, "write_helpers", side_effect=ValueError("no models listed")):
            self.assertIn(("claude", "off"), turbo.tick(self.now + 60))
        self.assertFalse(turbo.load()["claude"]["on"])
        self.assertIsNone(console.stale(self.root), "its record still moves on: no reload back")
        self.assertIn("ValueError: no models listed", turbo.report())

    def test_a_pinned_seat_is_left_to_its_pin(self):
        board.project_settings(self.root, {"model": "claude-sonnet-5-5", "effort": "low"})
        turbo.tick(self.now)
        self.assertNotIn("main", self.mine().get("boost", {}), "turbo doesn't claim a seat the person pinned")
        self.assertEqual(selection.pair(selection.main(self.root)), {"model": "claude-sonnet-5-5", "effort": "low"})

    def test_research_advice_comes_from_its_programs_consultant_as_auto_picks_it(self):
        self.patch(consult, "auto", REAL_AUTO)
        board.set_setting("consultants", "claude=claude-sonnet-5-5:low")          # the person's own, for their decisions
        self.assertIn((self.root, "research advice"), turbo.tick(self.now))
        auto = selection.consultant("claude", automatic=True)
        [asked] = self.consulted
        self.assertEqual((asked["model"], asked["effort"]), (auto["model"], auto["effort"]))
        self.assertNotEqual((asked["model"], asked["effort"]), ("claude-sonnet-5-5", "low"))
        ceiling = max(x["score"] for x in intelligence.pairs(bench.standings()) if x["family"] == "claude")
        best = [x for x in intelligence.pairs(bench.standings()) if (x["model"], x["effort"]) == (auto["model"], auto["effort"])]
        self.assertGreaterEqual(best[0]["score"], ceiling - 1, "the ceiling, within Auto's one-point tolerance")

    def test_a_rejected_model_is_never_turbos_pick(self):
        turbo.tick(self.now)
        up = self.seats()
        with selection.transaction() as state:
            state["rejected"].append(up["main"]["model"])
        self.assertNotEqual(selection.pair(selection.main(self.root)), up["main"], "the person's rejection wins")


if __name__ == "__main__":
    unittest.main()
