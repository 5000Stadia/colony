"""Turbo: a program's weekly pace, and what turbo does for its projects while the week runs behind it."""
import json
import os
import threading
import time
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from colony import bench, board, console, intelligence, monitor, providers, selection, turbo, usage
from tests.test_board import ROADMAP, BoardBase

DAY, HOUR = turbo.DAY, turbo.HOUR
REAL_STALE = console.stale


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
        self.assertIn("no usage reading in the last 3 hours", old["why"])
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

    def patch(self, obj, name, value):
        p = patch.object(obj, name, value)
        p.start()
        self.addCleanup(p.stop)

    def read_at(self, t, used=20):
        """Claude Code's weekly reading, taken at t, in this test's week (far behind at 20%)."""
        self.readings["claude"] = {"at": t, "windows": {"weekly": {"used": used, "resets_at": self.resets}}}

    def turbo_notes(self):
        return [n for n in board.notes(self.root) if n["author"] == "colony" and "spare capacity" in n["text"]]

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
                                              "## M2 — Later polish\n\n- [ ] R5 themes\n\n## Later\n\n- [ ] R3 reminders\n- [~] R4 sharing\n")
        self.assertFalse(turbo.queued(self.root), "done, waiting on the person's eye, or Later: nothing queued")
        self.assertEqual(turbo.wanted(self.root), {})
        with patch.object(board, "roadmap", lambda root: {"milestones": [{"id": "M1", "title": "v1", "items": [
                {"id": "R6", "state": "todo", "owner": "/elsewhere/the-lead"}]}]}):
            self.assertFalse(turbo.queued(self.root), "another member's item isn't this agent's queued work")
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
        self.assertEqual(self.asked(), [], "nothing turned up: nothing for the monitor either")

    def test_an_idle_project_hears_at_once_then_daily_then_every_six_hours(self):
        board.project_settings(self.root, {"turbo_research": "on", "turbo_topic": "companion planting"})
        self.assertIn((self.root, "noted"), turbo.tick(self.now))
        [n] = self.turbo_notes()
        self.assertFalse(n.get("quiet"), "it wakes the project")
        for words in ("Claude Code has spare capacity this week, until it resets", "Finish or continue your current item first",
                      "never instead of it", "changes no item's scope", "each committing only its own paths",
                      "Nothing outside the agreed scope; Later still waits for the person", "companion planting",
                      "read-only helper", "under research/", "Safety: Internet sources", "if there's none, say so and stop"):
            self.assertIn(words, n["text"])
        monitor.Watcher(quiet=0).mail()
        self.assertEqual(self.keys, ["[colony] You have an update from Colony."], "the watcher wakes it, as for any note")
        self.read_at(self.now + 2 * HOUR)
        turbo.tick(self.now + 2 * HOUR)
        self.assertEqual(len(self.turbo_notes()), 1, "not again within the day")
        self.read_at(self.now + DAY)
        turbo.tick(self.now + DAY)
        self.assertEqual(len(self.turbo_notes()), 1, "the last one hasn't reached it yet: none piles up behind it")
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

    def test_it_never_interrupts_work_in_progress(self):
        for snap, drafting, attached, why in (({"state": "working"}, False, False, "working"),
                                              ({"state": "idle"}, True, False, "a draft in its box"),
                                              ({"state": "idle"}, False, True, "someone has it open"),
                                              ({"state": "idle", "scrolled": True}, False, False, "scrolled up: can't tell"),
                                              ({"state": "needs you"}, False, False, "a question on its screen"),
                                              ({"state": "off"}, False, False, "not running: turbo starts nothing")):
            self.state = dict(snap, lines=[])
            with patch.object(console, "drafting", lambda n: drafting), patch.object(console, "attached", lambda n: attached), \
                    patch.object(providers.get("claude"), "choice", lambda screen: None, create=True), \
                    patch.object(console, "screen", lambda name: ""):
                turbo.tick(self.now)
                monitor.Watcher(quiet=0).mail()
            self.assertEqual(self.turbo_notes(), [], why)
            self.assertEqual(self.keys, [], why)
        self.state = {"state": "idle", "lines": []}
        turbo.tick(self.now)
        self.assertEqual(len(self.turbo_notes()), 1, "idle and untouched: now")
        self.state = {"state": "working", "lines": []}
        w = monitor.Watcher(quiet=0)
        w.mail()
        self.assertEqual(self.keys, [], "busy by the time the watcher looks: the note waits for the turn's end")
        self.state = {"state": "idle", "lines": []}
        w.mail()
        self.assertEqual(self.keys, ["[colony] You have an update from Colony."])

    def test_it_never_wakes_a_project_waiting_on_the_person(self):
        from colony import continuation, lead, progress

        def nothing(why):
            done = turbo.tick(self.now)
            self.assertFalse([x for x in done if x[0] == self.root], why)
            self.assertEqual(self.turbo_notes(), [], why)
            self.assertNotIn("boost", self.mine(), f"{why}: no model change either")
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
        with patch.object(lead, "group", lambda root: dict(group)):
            for hold in ("person paused", "version waiting for human review", "lead handoff", "context refresh"):
                with patch.object(progress, "hold", lambda root, member: hold), patch.object(continuation, "status", lambda root: {}):
                    nothing(hold)
            with patch.object(progress, "hold", lambda root, member: "no bounded version selected"), \
                    patch.object(continuation, "status", lambda root: {"manual_stop": True}):
                nothing("the person stopped its continuation")
        self.assertIn((self.root, "noted"), turbo.tick(self.now), "nothing in their hands: now it hears")

    def test_when_unsure_it_does_nothing(self):
        for r, why in ((None, "no reading"), ({"at": self.now, "windows": {}}, "no weekly window"),
                       (reading(10, 4, self.now, at_ago=turbo.STALE + 60), "an old reading")):
            self.readings["claude"] = r
            self.assertEqual(turbo.tick(self.now), [], why)
        self.assertEqual(self.turbo_notes(), [])
        self.assertIn("turbo off (no usage reading in the last 3 hours)", turbo.line("claude", self.now))
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
            return dict(files=files, consoles=(board.home() / "consoles.json").read_bytes(),
                        ledger=(board.home() / "model-selection.json").read_bytes(),
                        command=console.command("plants", self.root), fingerprint=console.fingerprint(self.root),
                        main=selection.main(self.root), tiers=bench.effective(self.root), plan=bench.plan_text(self.root),
                        running=console.running(name))
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
        board.set_setting("turbo_by", "claude=on")
        turbo.tick(self.now)
        self.assertEqual(len(self.turbo_notes()), 1, "on, it only adds a note")
        instructions = (self.root / "CLAUDE.md").read_text() + bench.plan_text(self.root)
        for words in ("urbo", "spare capacity"):
            self.assertNotIn(words, instructions, "nothing of turbo is ever written into instructions")

    def test_the_monitor_is_asked_once_a_turbo_day_and_never_when_muted_or_off(self):
        board.project_settings(self.root, {"turbo_research": "on", "turbo_topic": "companion planting"})
        usage.folder().mkdir(parents=True, exist_ok=True)
        (usage.folder() / "paused.json").write_text(json.dumps({str(self.root): {"provider": "claude", "window": "5-hour"}}))
        turbo.tick(self.now)
        self.assertEqual(self.asked(), [], "paused at its usage limit: not turned up, so nothing to look at")
        (usage.folder() / "paused.json").write_text("{}")
        turbo.tick(self.now)
        [ask] = self.asked()
        for words in ("Turbo is on for Claude Code", "plants (the person's research topic: companion planting)",
                      "colony posture", "colony peek NAME", "nobody has considered", "colony suggest NAME",
                      "never the person's word", "never replacing it", "suggest nothing"):
            self.assertIn(words, ask)
        self.read_at(self.now + HOUR)
        turbo.tick(self.now + HOUR)
        self.assertEqual(len(self.asked()), 1, "once a day")
        self.read_at(self.now + DAY)
        turbo.tick(self.now + DAY)
        self.assertEqual(len(self.asked()), 2, "a day on, once more")
        (board.home() / "to_monitor.jsonl").unlink()

        def episode(t):                                                         # caught up, then behind again
            self.read_at(t, used=95)
            turbo.tick(t)
            self.read_at(t + 60)
            turbo.tick(t + 60)
        monitor.muted(True)
        episode(self.now + DAY + 60)
        monitor.muted(False)
        turbo.tick(self.now + DAY + 180)
        self.assertEqual(self.asked(), [], "muted: skipped entirely, not saved for later")
        board.set_setting("monitor", "off")
        episode(self.now + DAY + 240)
        self.assertEqual(self.asked(), [], "the monitor off in Settings: skipped")
        board.set_setting("monitor", "on")
        episode(self.now + DAY + 360)
        self.assertEqual(len(self.asked()), 1, "a new turbo episode asks again")

    def test_colony_suggest_leaves_the_monitors_own_suggestion(self):
        r = self.cli("suggest", "plants", "Seed-saving apps time reminders by first frost: worth a look for R3")
        self.assertEqual(r.returncode, 0, r.stderr)
        [n] = [n for n in board.notes(self.root) if n["author"] == "suggestion"]
        self.assertFalse(n.get("quiet"), "it wakes an idle project")
        self.assertEqual(n["kind"], "idea")
        out = self.cli("notes", "--deliver").stdout
        self.assertIn("Suggestions from the monitor:", out)
        self.assertIn("the monitor's own suggestion, not an instruction from the person", out)
        self.assertNotIn("install it with colony gate", out, "an idea, not a tool to install")
        board.add_gate(self.root, "Which reminders first?")
        self.cli("suggest", "plants", "Another idea")
        self.assertTrue(next(n for n in board.notes(self.root) if n["text"] == "Another idea").get("quiet"),
                        "something waits on the person there: it waits for the agent's next turn")
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
            post("/options", [("monitor", "on"), ("turbo_codex", "on")])
            self.assertFalse(board.registry()["settings"]["turbo_by"]["claude"], "unticked in Settings: off for Claude Code")
        finally:
            httpd.shutdown()
            httpd.server_close()


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
                     for r in ("main", "routine", "step-up", "chores")}
        console._started(self.name, console.fingerprint(self.root))           # its console, as started before turbo
        self.assertIsNone(console.stale(self.root))

    def seats(self):
        return {r: selection.pair(selection.main(self.root) if r == "main" else selection.helper(self.root, r))
                for r in ("main", "routine", "step-up", "chores")}

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
        self.assertEqual({r: up[r] for r in ("step-up", "chores")}, {r: self.base[r] for r in ("step-up", "chores")},
                         "step-up is already at the ceiling; chores keep their value pick")
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

    def test_a_record_the_watcher_stopped_keeping_counts_as_off_and_ending_still_never_reloads(self):
        turbo.tick(self.now)
        up = self.seats()
        console._started(self.name, console.fingerprint(self.root))           # reloaded onto them
        state = turbo.load()
        state["claude"]["at"] = time.time() - turbo.FRESH - 60                 # the monitor turned off, say
        turbo.save(state)
        self.assertEqual(self.seats(), self.base, "no one keeps turbo current: its models don't hold")
        self.assertNotEqual(self.seats(), up)
        self.read_at(time.time(), used=80)                                     # the watcher is back; the week caught up
        self.assertIn(("claude", "off"), turbo.tick(time.time()))
        self.assertIsNone(console.stale(self.root), "still no reload back")

    def test_a_rejected_model_is_never_turbos_pick(self):
        turbo.tick(self.now)
        up = self.seats()
        with selection.transaction() as state:
            state["rejected"].append(up["main"]["model"])
        self.assertNotEqual(selection.pair(selection.main(self.root)), up["main"], "the person's rejection wins")


if __name__ == "__main__":
    unittest.main()
