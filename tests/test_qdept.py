"""Q Branch in colony (colony/qdept.py): the research flag, when passes run, delivery, re-runs, curated matches and
upkeep, with a fake core (no model is called), and once end to end with the real core on fake runners."""
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from colony import board, qdept

SECTION = """## Research
Trigger: Stale: the field moved this year.
Subject: how do long-running agents keep what they learned across sessions?
Requirements: runs offline on one laptop; no paid services
Decisions:
- Which store for notes? Default: plain Markdown files.
- When to compact? (default: at the harness's own limit)
"""


class Fake:
    """A stand-in for the core process: records each call's command and input, and writes what the core would."""

    def __init__(self):
        self.calls = []
        self.fail = set()
        self.refuse = None                    # a why the core gives for not weaving finding 1

    def __call__(self, cmd, input, **kw):
        mode, inp = cmd[-1], json.loads(input)
        self.calls.append((mode, inp))
        usage = kw["env"]["COLONY_QB_USAGE"]
        Path(usage).parent.mkdir(parents=True, exist_ok=True)
        with open(usage, "a") as fh:
            fh.write(json.dumps({"usage": {"input_tokens": 10, "cache_read_input_tokens": 100,
                                           "cache_creation_input_tokens": 20, "output_tokens": 5}, "cost_usd": 0.01}) + "\n")
        if mode in self.fail:
            return subprocess.CompletedProcess(cmd, 3, "", "runner failed: no")
        if mode == "brief":
            out = Path(inp["out"])
            out.mkdir(parents=True, exist_ok=True)
            (out / "brief.md").write_text("curated hand-over" if inp.get("entry") else "THE GUIDE\n")
            (out / "log.json").write_text(json.dumps({"entry": inp.get("entry", ""), "domain": "agent memory"}))
        elif mode == "decision":
            out = Path(inp["out"])
            out.mkdir(parents=True, exist_ok=True)
            (out / "addendum.md").write_text(f"- {inp['decision']}: the default holds ({inp['default']}).\n")
        elif mode == "learn" and self.refuse:
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"woven": [], "not_woven": [{"n": 1, "why": self.refuse}]}), "")
        elif mode == "learn":
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"woven": [{"n": 1, "edition": 2}], "not_woven": []}), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def modes(self):
        return [m for m, _ in self.calls]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="qdept-test-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        patcher = patch.dict(os.environ, {"COLONY_BOARD_HOME": str(self.base / "home")})
        patcher.start()
        self.addCleanup(patcher.stop)
        board.save_registry({"roots": [], "settings": {"messaging": False, "active_hours": "off"}})
        self.root = self.base / "Gadgets"
        (self.root / ".board").mkdir(parents=True)
        (self.root / "roadmap").mkdir()
        self.fake = Fake()
        self.drained = patch.object(qdept, "drain", lambda wait=False: None)    # the worker runs when a test says so
        self.drained.start()
        self.addCleanup(self.drained.stop)

    # ---- the project's plan

    def plan(self, states, flagged=(), extra=None):
        lines = ["# Gadgets", "", "## M1 — First", ""]
        for i, s in enumerate(states, 1):
            text = (extra or {}).get(f"R{i}", f"Item number {i}")
            lines.append(f"- [{s}] R{i} {text}" + (" (research)" if f"R{i}" in flagged else ""))
        (self.root / "ROADMAP.md").write_text("\n".join(lines) + "\n")

    def details(self, iid, text):
        (self.root / "roadmap" / f"{iid}.md").write_text(f"# {iid}\n\nSome details.\n\n{text}")

    def look(self):
        qdept.check(self.root, run=self.fake)

    def notes(self):
        return [n["text"] for n in board.read(self.root, "notes.jsonl")]

    def jobs(self):
        return [(j["kind"], j["item"]) for j in qdept.queue()]

    def work(self):
        with patch.object(qdept.subprocess, "run", self.fake):
            qdept._work()

    def rec(self, iid):
        return qdept.load(self.root)["items"].get(iid, {})


class QDept(Base):
    # ---- parsing

    def test_flag_is_read_and_left_out_of_the_text(self):
        self.plan(" ", flagged={"R1"}, extra={"R1": "Pick a store (related: R2)"})
        it = board.items(board.roadmap(self.root))["R1"]
        self.assertTrue(it["research"])
        self.assertEqual(it["text"], "Pick a store")
        self.assertEqual(it["related"], ["R2"])
        self.plan(" ")
        self.assertFalse(board.items(board.roadmap(self.root))["R1"]["research"])

    def test_section_parsing(self):
        got = qdept.parse(qdept.section("# R1\n\n" + SECTION + "\n## Notes\nnot part of it\n", "Research"))
        self.assertEqual(got["trigger"], "Stale: the field moved this year.")
        self.assertEqual(got["subject"], "how do long-running agents keep what they learned across sessions?")
        self.assertEqual(got["requirements"], ["runs offline on one laptop", "no paid services"])
        self.assertEqual(got["decisions"], [{"decision": "Which store for notes?", "default": "plain Markdown files"},
                                            {"decision": "When to compact?", "default": "at the harness's own limit"}])
        bullets = qdept.parse("Subject: a question\nRequirements:\n- one\n- two\nDecisions:\n- A bare decision\n")
        self.assertEqual(bullets["requirements"], ["one", "two"])
        self.assertEqual(bullets["decisions"], [{"decision": "A bare decision", "default": "none stated"}])
        self.assertIsNone(qdept.parse("Trigger: no subject named\n"))
        self.assertIsNone(qdept.section("## Other\ntext", "Research"))
        self.assertNotIn("not part of it", qdept.section("## Research\nSubject: x\n## Notes\nnot part of it", "Research"))

    def test_added_decisions_only_count_growth(self):
        given = [{"decision": "Which store?", "default": "files"}, {"decision": "When to compact?", "default": "late"}]
        reworded = [{"decision": "Which store should notes use?", "default": "files"}, given[1]]
        self.assertEqual(qdept.added(given, reworded), [])                        # a rewording
        self.assertEqual(qdept.added(given, [dict(given[0], default="SQLite"), given[1]]), [])   # a changed default
        self.assertEqual(qdept.added(given, given[:1]), [])                        # a removal
        new = {"decision": "How to search old notes?", "default": "grep"}
        self.assertEqual(qdept.added(given, given + [new]), [new])
        self.assertEqual(qdept.added(given, reworded + [new]), [new])              # reworded and added at once

    def test_candidates(self):
        body = ("- Expand retrieved passages before trusting their qualifiers. Source: https://example.org/a\n"
                "- Something with no source.\n- Two\n  lines here https://example.org/b.\n- c https://x.org/c\n- d https://x.org/d\n")
        self.assertEqual(qdept.candidates(body), [
            {"text": "Expand retrieved passages before trusting their qualifiers.", "url": "https://example.org/a"},
            {"text": "Two lines here", "url": "https://example.org/b"}, {"text": "c", "url": "https://x.org/c"}])
        self.assertEqual(qdept.candidates("Nothing.\n"), [])

    # ---- when passes run

    def test_next_flagged_item_in_order_is_queued_when_any_item_starts(self):
        self.plan("    ", flagged={"R3", "R4"})
        for iid in ("R3", "R4"):
            self.details(iid, SECTION)
        self.look()                                           # the first look only notes where things are
        self.plan("~   ", flagged={"R3", "R4"})
        self.look()
        self.assertEqual(self.jobs(), [("pass", "R3")])
        self.assertEqual(self.rec("R3")["status"], "queued")
        self.assertEqual(self.notes(), [])                    # R1's agent hears nothing: R1 isn't flagged
        self.look()
        self.assertEqual(self.jobs(), [("pass", "R3")])       # once

    def test_flagged_item_without_a_section_is_skipped(self):
        self.plan("    ", flagged={"R2", "R3"})
        self.details("R2", "## Research\nTrigger: only a trigger\n")
        self.details("R3", SECTION)
        self.look()
        self.plan("~   ", flagged={"R2", "R3"})
        self.look()
        self.assertEqual(self.jobs(), [("pass", "R3")])

    def test_flagged_item_started_without_a_guide_runs_then_and_is_told(self):
        self.plan("  ", flagged={"R1"})
        self.details("R1", SECTION)
        self.look()
        self.plan("~ ", flagged={"R1"})
        self.look()
        self.assertEqual(self.jobs(), [("pass", "R1")])
        self.assertEqual(len(self.notes()), 1)
        self.assertIn("its guide is coming", self.notes()[0])
        self.work()
        self.assertEqual(self.fake.modes(), ["brief"])
        self.assertEqual(self.rec("R1")["status"], "ready")
        self.assertIn(f"is at {self.rec('R1')['path']}", self.notes()[1])     # started while its pass ran: told it's here

    def test_pass_gets_only_the_section(self):
        self.plan("  ", flagged={"R2"}, extra={"R2": "Gadgets memory, secret plan"})
        self.details("R2", SECTION.replace("long-running agents", "Gadgets agents") + "\nPrivate project notes.\n")
        self.look()
        self.plan("~ ", flagged={"R2"})
        self.look()
        self.work()
        mode, inp = self.fake.calls[0]
        self.assertEqual(mode, "brief")
        self.assertEqual(inp["subject"], "how do the project agents keep what they learned across sessions?")
        self.assertEqual(inp["requirements"], ["runs offline on one laptop", "no paid services"])
        text = json.dumps(inp)
        for private in ("secret plan", "Private project notes", "Gadgets", "Stale", "Which store"):
            self.assertNotIn(private, text)
        self.assertFalse(inp["sourced"])
        self.assertIs(inp["screen"], True)
        self.assertIn("qroom.py research claude sonnet", inp["runner"]["research"]["cmd"])
        out = Path(inp["out"])
        self.assertTrue(str(out).startswith(str(self.base / "home" / "qbranch" / "guides")))
        given = json.loads((out / "given.json").read_text())
        self.assertNotIn("Gadgets", json.dumps(given))
        from colony import research
        self.assertEqual([r["by"] for r in research.entries()], ["Q Branch"])      # in the shared library, by no project

    def test_ready_guide_is_delivered_at_the_start(self):
        self.plan("   ", flagged={"R2"})
        self.details("R2", SECTION)
        self.look()
        self.plan("~  ", flagged={"R2"})
        self.look()
        self.work()
        self.assertEqual(self.notes(), [])                    # ready before R2 starts: nothing yet
        self.plan("x~ ", flagged={"R2"})
        self.look()
        self.assertEqual(self.notes(), [qdept.READY.format(iid="R2", path=self.rec("R2")["path"])])
        self.assertEqual(self.jobs(), [])                     # it has its guide: no second pass
        self.assertEqual(self.fake.modes(), ["brief"])

    def test_tokens_are_recorded_and_shown(self):
        self.plan("  ", flagged={"R1"})
        self.details("R1", SECTION)
        self.look()
        self.plan("~ ", flagged={"R1"})
        self.look()
        self.work()
        s = qdept.item_state(self.root, "R1")
        self.assertEqual(s["tokens"], 135)
        self.assertEqual(s["cost_usd"], 0.01)
        it = board.items(board.roadmap(self.root))["R1"]
        self.assertIn("research: ready", qdept.badge(self.root, it))
        self.assertIn("pass · ready", qdept.item_card(self.root, it))

    def test_one_pass_at_a_time_machine_wide(self):
        self.plan("   ", flagged={"R1", "R2"})
        for iid in ("R1", "R2"):
            self.details(iid, SECTION)
        self.look()
        qdept.enqueue({"kind": "pass", "root": str(self.root), "item": "R1"})
        qdept.enqueue({"kind": "pass", "root": str(self.root), "item": "R2"})
        qdept.folder().mkdir(parents=True, exist_ok=True)
        with open(qdept.folder() / "run.lock", "a") as held:          # another board process is running one
            fcntl.flock(held, fcntl.LOCK_EX)
            self.work()
            self.assertEqual(self.fake.calls, [])
            self.assertEqual(len(qdept.queue()), 2)
        active, overlap = [0], []

        def one(cmd, input, **kw):
            active[0] += 1
            overlap.append(active[0])
            try:
                return self.fake(cmd, input, **kw)
            finally:
                active[0] -= 1
        with patch.object(qdept.subprocess, "run", one):
            qdept._work()
        self.assertEqual([inp["subject"][:6] for _, inp in self.fake.calls], ["how do", "how do"])
        self.assertEqual(max(overlap), 1)
        self.assertEqual(qdept.queue(), [])

    def test_real_drain_runs_one_worker(self):
        self.drained.stop()
        self.addCleanup(self.drained.start)
        self.plan("  ", flagged={"R1"})
        self.details("R1", SECTION)
        qdept.enqueue({"kind": "pass", "root": str(self.root), "item": "R1"})
        with patch.object(qdept.subprocess, "run", self.fake):
            t = qdept.drain()
            self.assertIs(qdept.drain(), t)                   # a second call finds it running
            t.join(30)
        self.assertEqual(self.fake.modes(), ["brief"])

    # ---- re-runs

    def ready(self):
        self.plan("~ ", flagged={"R1"})
        self.details("R1", SECTION)
        state = {"states": {"R1": "doing", "R2": "todo"}, "items": {}}
        board.save_json(qdept._state_path(self.root), state)
        self.plan("~~", flagged={"R1"})        # (R2 starting is a look that changes nothing for R1)
        qdept._queue_pass(self.root, "R1")
        self.work()
        self.fake.calls.clear()

    def test_rewording_or_a_changed_default_runs_nothing(self):
        self.ready()
        self.details("R1", SECTION.replace("Which store for notes?", "Which store should notes go in?")
                     .replace("plain Markdown files", "SQLite") + "\nMore detail the research doesn't bear on.\n")
        self.look()
        self.assertEqual(self.jobs(), [])

    def test_an_added_decision_runs_decision_mode_for_it_alone(self):
        self.ready()
        guide = Path(self.rec("R1")["path"])
        self.details("R1", SECTION + "- How to search old notes? Default: grep.\n")
        self.look()
        self.look()
        self.assertEqual(self.jobs(), [("decision", "R1")])
        self.work()
        mode, inp = self.fake.calls[0]
        self.assertEqual(mode, "decision")
        self.assertEqual((inp["decision"], inp["default"]), ("How to search old notes?", "grep"))
        self.assertEqual(inp["guide"], str(guide))
        self.assertEqual(inp["subject"], "how do long-running agents keep what they learned across sessions?")
        self.assertTrue(guide.read_text().endswith("THE GUIDE\n\n- How to search old notes?: the default holds (grep).\n"))
        self.assertIn("the default holds", self.notes()[-1])
        self.look()
        self.assertEqual(self.jobs(), [])                     # once

    def test_a_decision_added_while_the_pass_runs_waits_then_runs_alone(self):
        self.plan("~ ", flagged={"R1"})
        self.details("R1", SECTION)
        board.save_json(qdept._state_path(self.root), {"states": {"R1": "doing", "R2": "todo"}, "items": {}})
        qdept._queue_pass(self.root, "R1")
        seen = []

        def mid_pass(cmd, input, **kw):
            if cmd[-1] == "brief":                            # while the pass runs, the agent adds a decision
                self.details("R1", SECTION + "- How to search old notes? Default: grep.\n")
                qdept.check(self.root, run=self.fake)
                seen.append(self.jobs())
            return self.fake(cmd, input, **kw)
        with patch.object(qdept.subprocess, "run", mid_pass):
            qdept._work()
        self.assertEqual(seen, [[("decision", "R1")]])
        self.assertEqual(self.fake.modes(), ["brief", "decision"])
        self.assertEqual(self.fake.calls[1][1]["decision"], "How to search old notes?")

    # ---- curated guides, matched by code

    def test_match_by_whole_word_and_phrase_never_a_substring(self):
        qdept.shelf_dir()
        hit = lambda text: qdept.match(text)
        self.assertEqual(hit("Keep MEMORY  RECALL working"), ("memory", "memory recall"))
        self.assertEqual(hit("Add compaction to the loop"), ("memory", "compaction"))
        self.assertEqual(hit("an agent-memory design"), None)                   # agent memory is a phrase of words
        self.assertIsNone(hit("Fix GPU memory leaks"))                          # bare memory isn't a term
        self.assertIsNone(hit("decompaction of the archive"))                   # inside another word
        self.assertIsNone(hit("embeddingsfoo"))
        self.assertIsNone(hit("recall the precision and recall numbers"))
        self.assertTrue(qdept.term_hit("vector store", "a Vector\nStore here"))
        self.assertFalse(qdept.term_hit("vector store", "vector stores"))

    def test_matched_unflagged_item_gets_the_guide_and_no_search(self):
        self.plan("   ", extra={"R2": "Add long-term memory to the helper"})
        self.look()
        self.plan("~  ", extra={"R2": "Add long-term memory to the helper"})
        self.look()                                           # the item before it starts: matched now
        self.assertEqual(self.fake.calls[0][0], "brief")
        self.assertEqual(self.fake.calls[0][1]["entry"], "memory")
        self.assertIs(self.fake.calls[0][1]["screen"], False)
        self.assertEqual(self.notes(), [])
        log = board.read(self.root, "qbranch-log.jsonl")
        self.assertEqual((log[0]["event"], log[0]["term"]), ("match", "long-term memory"))
        self.plan("x~ ", extra={"R2": "Add long-term memory to the helper"})
        self.look()
        self.assertEqual(len(self.notes()), 1)
        self.assertIn('A curated Q Branch guide bears on R2: "Agent memory architecture"', self.notes()[0])
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.fake.modes(), ["brief"])        # the hand-over only: no pass, no search
        self.assertIn("guide: Agent memory architecture",
                      qdept.badge(self.root, board.items(board.roadmap(self.root))["R2"]))

    # ---- upkeep at [x]

    def curated_done(self):
        self.plan("  ", extra={"R1": "Design memory across sessions"})
        self.look()
        self.plan("~ ", extra={"R1": "Design memory across sessions"})
        self.look()                                           # started first: matched and handed over at once
        self.plan("x ", extra={"R1": "Design memory across sessions"})
        self.look()

    def test_note_at_done_only_for_curated_guide_items(self):
        self.curated_done()
        self.assertEqual(len(self.notes()), 2)
        self.assertIn("R1 is done, and it was handed the curated guide", self.notes()[1])
        self.assertIn("would change what the next agent decides after reading the section it belongs in", self.notes()[1])
        self.look()
        self.assertEqual(len(self.notes()), 2)                # once
        self.plan("x~", extra={"R1": "Design memory across sessions", "R2": "Tidy the CSS"})
        self.look()
        self.plan("xx", extra={"R1": "Design memory across sessions", "R2": "Tidy the CSS"})
        self.look()
        self.assertEqual(len(self.notes()), 2)                # R2 had no guide: no question

    def test_a_flagged_item_with_a_guide_gets_both_and_both_maintain_it(self):
        # the person, 2026-11-06: an item flagged for research, where a domain guide exists, does both, for thoroughness
        # and to maintain the guide
        text = {"R2": "Add long-term memory to the helper"}
        self.plan("   ", flagged={"R2"}, extra=text)
        self.details("R2", SECTION)
        self.look()
        self.plan("~  ", flagged={"R2"}, extra=text)
        self.look()
        self.work()
        self.plan("x~ ", flagged={"R2"}, extra=text)
        self.look()
        start = self.notes()
        self.assertEqual(len(start), 2)
        self.assertTrue(any("A curated Q Branch guide bears on R2" in n for n in start))
        self.assertIn(qdept.READY.format(iid="R2", path=self.rec("R2")["path"]), start)
        self.plan("xx ", flagged={"R2"}, extra=text)
        self.look()
        self.assertIn("Did your research guide, your own search or your build turn up anything", self.notes()[-1])

    def test_the_question_at_done_comes_after_both_the_guide_and_the_agents_own_search(self):
        # the person, 2026-11-06: it only works if the agent considers it after both using the guide and searching
        # online outside it
        self.curated_done()
        start, done = self.notes()
        self.assertIn("its hand-over before you design", start)
        from colony.qbranch import qbranch
        self.assertIn("Read it, then search the web yourself for what this item needs beyond it, both before you "
                      "design", qbranch.HANDOVER)
        self.assertIn("one from your search needs a primary source", done)
        self.assertIn("saying whether your build tested it", done)
        self.assertIn("Answer this only after both using the guide and searching the web yourself beyond it", done)
        self.assertIn("if you didn't search while building, run a short search now", done)
        quiet = [bool(n.get("quiet")) for n in board.read(self.root, "notes.jsonl") if n.get("type", "note") == "note"]
        self.assertEqual(quiet, [True, False])               # the finish question wakes an agent gone idle

    def test_for_the_guide_goes_to_learn_with_private_names(self):
        (self.base / "home").mkdir(exist_ok=True)
        (self.base / "home" / "nests.json").write_text(json.dumps(
            {"Workshop": {"members": [str(self.root.resolve()), str(self.base / "Sprockets")]}}))
        self.curated_done()
        self.details("R1", "## For the guide\n- Expand retrieved passages before trusting qualifiers: https://example.org/a\n")
        self.look()
        self.assertEqual(self.jobs(), [("learn", "R1")])
        self.work()
        mode, inp = self.fake.calls[-1]
        self.assertEqual(mode, "learn")
        self.assertEqual(inp["candidates"], [{"text": "Expand retrieved passages before trusting qualifiers",
                                              "url": "https://example.org/a"}])
        self.assertEqual(sorted(inp["private"]), ["Gadgets", "Sprockets", "Workshop"])
        self.assertEqual(inp["shelf_dir"], str(self.base / "home" / "qbranch" / "shelf"))
        self.assertEqual(inp["out"], str(self.root / ".board" / "qbranch" / "R1"))
        self.look()
        self.assertEqual(self.jobs(), [])

    def test_a_finding_refused_for_its_form_is_told_and_read_again_once_fixed(self):
        self.curated_done()
        self.assertIn("(80 words at most)", self.notes()[1])                    # the limit is in the question
        long = "- " + "word " * 90 + "https://example.org/long\n"
        self.details("R1", "## For the guide\n- A short one that passes: https://example.org/a\n" + long)
        self.look()
        self.fake.refuse = "candidate text must contain 1 to 80 words"
        self.work()
        told = self.notes()[-1]
        self.assertIn("finding 1: candidate text must contain 1 to 80 words", told)
        self.assertIn("read again", told)
        before = len(self.fake.calls)
        self.look()
        self.assertEqual(self.jobs(), [])                                       # unchanged section: not sent again
        self.details("R1", "## For the guide\n- A short one that passes: https://example.org/a\n"
                           "- The long one, shortened: https://example.org/long\n")
        self.fake.refuse = None
        self.look()
        self.assertEqual(self.jobs(), [("learn", "R1")])
        self.work()
        self.assertEqual(len(self.fake.calls), before + 1)
        self.assertEqual(self.fake.calls[-1][1]["candidates"],                  # only the fixed one: the other was sent
                         [{"text": "The long one, shortened", "url": "https://example.org/long"}])
        self.assertTrue(self.rec("R1")["curated"]["answered"])

    def test_the_learn_step_reads_a_long_items_end(self):
        self.assertEqual(qdept.outcome_text("a" * 20000), "a" * 20000)
        long = "START " + "x" * 40000 + " THE ANSWER"
        cut = qdept.outcome_text(long)
        self.assertTrue(cut.startswith("START ") and cut.endswith(" THE ANSWER"))
        self.assertLess(len(cut), 24100)
        self.curated_done()
        self.details("R1", "word " * 3000 + "THE RESULT CAME LAST.\n\n## For the guide\n- A finding: https://example.org/a\n")
        self.look()
        self.work()
        self.assertIn("THE RESULT CAME LAST.", self.fake.calls[-1][1]["outcome"]["built"])

    def test_nothing_for_the_guide_runs_nothing(self):
        self.curated_done()
        self.details("R1", "## For the guide\nNothing.\n")
        self.look()
        self.assertEqual(self.jobs(), [])
        self.assertTrue(self.rec("R1")["curated"]["answered"])

    # ---- settings

    def test_research_off_means_nothing_runs(self):
        board.project_settings(self.root, {"research": "off"})
        self.plan("   ", flagged={"R1", "R2"}, extra={"R3": "memory across sessions"})
        for iid in ("R1", "R2"):
            self.details(iid, SECTION)
        self.look()
        self.plan("~  ", flagged={"R1", "R2"}, extra={"R3": "memory across sessions"})
        self.look()
        self.plan("x~~", flagged={"R1", "R2"}, extra={"R3": "memory across sessions"})
        self.look()
        self.assertEqual((self.jobs(), self.notes(), self.fake.calls), ([], [], []))
        qdept.enqueue({"kind": "pass", "root": str(self.root), "item": "R2"})
        self.work()
        self.assertEqual(self.fake.calls, [])                 # a job queued before it was turned off runs nothing
        board.project_settings(self.root, {"research": "on"})
        self.assertNotIn("research", board.project_settings(self.root)[1])

    def test_sourced_is_a_setting_off_by_default(self):
        self.assertFalse(qdept.sourced())
        board.set_setting("research_sourced", "on")
        self.assertTrue(qdept.sourced())

    def test_standing_source_entry_in_the_instructions(self):
        self.assertEqual(qdept.standing_line(self.root), "")
        board.project_settings(self.root, {"research_source": "memory"})
        line = qdept.standing_line(self.root)
        self.assertIn('Standing source: this project\'s facts rest on Q Branch\'s guide "Agent memory architecture"', line)
        self.assertIn(line, board.protocol(self.root))
        with self.assertRaises(KeyError):
            board.project_settings(self.root, {"research_source": "../etc"})

    def test_looking_before_researching_finds_the_shelfs_guide(self):
        """An agent that looks in the library before researching is shown the curated guide on its subject."""
        for asked in ("memory architecture", "agent memory guide", "a vector store for recall", "compaction"):
            self.assertEqual([g["title"] for g in qdept.shelf_look(asked)], ["Agent memory architecture"], asked)
        for asked in ("memory leak in a C program", "payment providers for small shops", "architecture", ""):
            self.assertEqual(qdept.shelf_look(asked), [], asked)
        import contextlib, io
        from colony import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["research", "memory", "architecture"])
        self.assertEqual(code, 0)
        first = out.getvalue().splitlines()[0]
        self.assertTrue(first.startswith("guide  Agent memory architecture"), first)
        self.assertIn(str(qdept.shelf_dir() / "memory.md"), first)

    def test_seed_shelf_carries_no_project_labels(self):
        text = (qdept.SEED / "memory.md").read_text()
        self.assertNotIn("observed in", text)
        for name in ("Memory Research", "Memory Survey", "Kernos"):
            self.assertNotIn(name, text)
        self.assertEqual(text, (qdept.SEED / "editions" / "memory-e1.md").read_text())


# ---- once end to end: the real core, on fake runners

RUNNER = r'''
import json, os, sys
p = sys.stdin.read()
mode = sys.argv[1]
with open(os.environ["FAKE_CALLS"], "a") as f:
    f.write(json.dumps({"mode": mode, "prompt": p}) + "\n")
with open(os.environ["COLONY_QB_USAGE"], "a") as f:
    f.write(json.dumps({"usage": {"input_tokens": 3, "output_tokens": 2}, "cost_usd": 0.001}) + "\n")
if p.startswith("You screen untrusted research"):
    print('{"cuts": [], "verdict": "pass"}')
elif mode == "research" and "DECISION TO RESEARCH" in p:
    print("=== DECISION ===\ndefault confirmed | two sources agree\n=== SOURCES THAT FED IT ===\n"
          "https://example.org/d | web | docs\n=== SOURCE VERDICTS ===\n")
elif mode == "research":
    print("domain: agent memory\ntrigger: before designing memory\n=== GUIDE ===\n# Guide\n" + "word " * 320 +
          "\nhttps://example.org/g\n=== SOURCES THAT FED IT ===\nhttps://example.org/g | web | all of it\n=== SOURCE VERDICTS ===\n")
elif p.startswith("Choose the one section"):
    print('{"pick": 3}')
elif p.startswith("Rewrite this one section"):
    body = p.split("SECTION BODY (data):\n", 1)[1].split("\n\nCANDIDATE (data):", 1)[0]
    print(body + " Observed in one real build: expand retrieved passages first (https://example.org/a).")
else:
    print(json.dumps({"lessons": [], "sources": [], "candidates": [{"n": 1, "keep": "yes", "why": "held"}]}))
'''


class EndToEnd(Base):
    def test_real_core_on_fake_runners(self):
        script = self.base / "runner.py"
        script.write_text(RUNNER)
        cmd = lambda mode: {"cmd": shlex.join([sys.executable, "-I", str(script), mode])}
        calls = self.base / "calls.jsonl"
        env = patch.dict(os.environ, {"FAKE_CALLS": str(calls)})
        env.start()
        self.addCleanup(env.stop)
        runners = {"research": cmd("research"), "check": cmd("screen"), "weave": cmd("weave"), "learn": cmd("learn")}
        flag = "Design memory across sessions"
        self.plan("   ", flagged={"R2"}, extra={"R1": flag, "R2": "Recall store"})
        self.details("R2", SECTION)
        with patch.object(qdept, "runners", lambda: runners):
            qdept.check(self.root)
            self.plan("~  ", flagged={"R2"}, extra={"R1": flag, "R2": "Recall store"})
            qdept.check(self.root)                            # R1 starts: matched and handed over; R2's pass queued
            qdept._work()
            rec = self.rec("R2")
            self.assertEqual(rec["status"], "ready")
            guide = Path(rec["path"]).read_text()
            self.assertTrue(guide.startswith("UNTRUSTED RESEARCH FROM THE OPEN WEB"))
            self.assertIn("# Guide", guide)
            prompts = [json.loads(line) for line in calls.read_text().splitlines()]
            research = next(c["prompt"] for c in prompts if c["mode"] == "research")
            self.assertIn("THEN SEARCH, AND READ. Search the web", research)
            self.assertNotIn("FIRST, YOUR SOURCE PLAN", research)
            self.assertNotIn("Recall store", research)
            self.assertEqual(sum(1 for c in prompts if c["mode"] == "screen"), 1)      # screened once
            self.details("R2", SECTION + "- How to search old notes? Default: grep.\n")
            qdept.check(self.root)
            qdept._work()
            self.assertTrue(Path(rec["path"]).read_text().endswith(
                "- How to search old notes?: the default holds (grep).\n"))
            self.plan("x~ ", flagged={"R2"}, extra={"R1": flag, "R2": "Recall store"})
            qdept.check(self.root)
            self.assertIn('curated guide "Agent memory architecture"', self.notes()[-1])
            self.details("R1", "## For the guide\n- Expand retrieved passages first. https://example.org/a\n")
            qdept.check(self.root)
            qdept._work()
        shelf = self.base / "home" / "qbranch" / "shelf"
        self.assertIn("edition: 2", (shelf / "memory.md").read_text())
        self.assertIn("expand retrieved passages first", (shelf / "memory.md").read_text())
        self.assertEqual((shelf / "editions" / "memory-e1.md").read_text(), (qdept.SEED / "memory.md").read_text())
        self.assertEqual((qdept.SEED / "memory.md").read_text(), (qdept.SEED / "editions" / "memory-e1.md").read_text())
        runs = [r["kind"] for r in self.rec("R1")["runs"]]
        self.assertEqual(runs, ["learn"])
        self.assertEqual(self.rec("R1")["runs"][0]["woven"][0]["edition"], 2)


class Runner(Base):
    """colony/qroom.py: the core's runner command, through the room."""

    def run_mode(self, *argv, prompt="the prompt"):
        from colony import qroom
        import io
        out = io.StringIO()
        runlog, usage = self.base / "runlog.jsonl", self.base / "usage.jsonl"
        with patch.dict(os.environ, {"QBRANCH_RUNLOG": str(runlog), "COLONY_QB_USAGE": str(usage)}), \
                patch.object(sys, "stdin", io.StringIO(prompt)), patch.object(sys, "stdout", out), \
                patch.object(sys, "stderr", io.StringIO()):
            code = qroom.main(["qroom.py", *argv])
        rows = lambda p: [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
        return code, out.getvalue(), rows(runlog), rows(usage)

    def test_research_goes_through_the_room_unscreened(self):
        from colony import qroom, room
        seen = {}

        def research(prompt, **kw):
            seen.update(kw, prompt=prompt)
            d = self.base / "home" / "room" / "r1"
            d.mkdir(parents=True)
            (d / "result.json").write_text(json.dumps({"usage": {"input_tokens": 7, "output_tokens": 3}}))
            (d / "log.jsonl").write_text(json.dumps({"event": {"type": "result", "total_cost_usd": 0.25}}) + "\n")
            return {"status": "done", "text": "the answer", "run_dir": str(d), "error": None,
                    "tools": [{"tool": "WebSearch", "input": {"query": "q"}}]}
        with patch.object(room, "research", research):
            code, out, runlog, usage = self.run_mode("research", "claude", "sonnet")
        self.assertEqual((code, out.strip()), (0, "the answer"))
        self.assertEqual((seen["family"], seen["model"], seen["screen"], seen["prompt"]), ("claude", "sonnet", False, "the prompt"))
        self.assertEqual(runlog[0], {"tool": "WebSearch", "input": {"query": "q"}})
        self.assertEqual(runlog[1]["usage"]["cost_usd"], 0.25)
        self.assertEqual((usage[0]["mode"], usage[0]["usage"]["input_tokens"], usage[0]["cost_usd"]), ("research", 7, 0.25))

    def test_ask_and_screen_have_no_tools(self):
        from colony import room
        passes = []

        def one(p, prompt, model, effort, here, timeout, tools=True, spawn=None):
            passes.append((model, tools))
            return {"status": "done", "text": "```\n{\"ok\": 1}\n```", "usage": {"input_tokens": 1}, "error": None}

        def screener(family, pass_model, run_dir, *a, **k):
            ask = lambda q: q
            ask.model, ask.family = "screen-model", "claude"
            return ask
        with patch.object(room, "_pass", one), patch.object(room, "tier", lambda f, r: (f"{f}-{r}", None)), \
                patch.object(room, "screener", screener):
            self.assertEqual(self.run_mode("ask", "claude", "rote")[:2], (0, '{"ok": 1}\n'))
            self.assertEqual(self.run_mode("screen", "claude", "sonnet")[0], 0)
        self.assertEqual(passes, [("claude-rote", False), ("screen-model", False)])
        self.assertEqual(self.run_mode("bogus", "claude", "x")[0], 2)


if __name__ == "__main__":
    unittest.main()
