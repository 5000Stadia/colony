import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from colony import claude, clock, field, mapper, memory, specialists  # noqa: E402
from colony.project import Project  # noqa: E402

SPINE = """# Test — spine

## What we're making
"a thing"

## What it must never do
Publish anything.

## Checks
- `{check}`

## The spec list
| # | What to build now | What done looks like |
|---|---|---|
| 1 | Write the first line | work.txt exists |
| 2 | Write the second line | work.txt has two lines |

**Next ID:** 3
**Approved:** {approved}
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "proj"
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        subprocess.run([sys.executable, "-m", "colony", "init", str(self.dir)], env=env, check=True, capture_output=True)
        self.project = Project(self.dir)
        os.environ["COLONY_CLAUDE"] = str(ROOT / "tests" / "fake_claude.py")
        os.environ["PYTHONPATH"] = str(ROOT)
        for k in ("FAKE_FORK", "FAKE_LIMIT_ONCE", "COLONY_AGENT", "COLONY_ROW", "COLONY_WAVE"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.tmp.cleanup()

    def spine(self, check="test -f work.txt", approved="yes"):
        self.project.spine.write_text(SPINE.format(check=check, approved=approved))


class FieldTest(Base):
    def test_same_place_reinforces_and_confirmation_wakes_later_waves(self):
        field.post(self.project, by="a@w2", row=1, wave=2, kind="hole", severity="major", at="x.py:f", text="one")
        [s] = field.signals(self.project, row=1, wave=2)
        self.assertEqual(s["strength"], 1)
        self.assertFalse(field.wakes_builder(s, 2), "an unconfirmed major after wave 1 does not wake the builder")
        self.assertTrue(field.wakes_builder(s, 1), "in the first wave a major wakes the builder")
        field.post(self.project, by="b@w2", row=1, wave=2, kind="hole", severity="minor", at="x.py:f", text="two")
        [s] = field.signals(self.project, row=1, wave=2)
        self.assertEqual((s["strength"], s["severity"]), (2, "major"))
        self.assertTrue(field.wakes_builder(s, 2))

    def test_unconfirmed_minor_fades_and_resolved_leaves(self):
        field.post(self.project, by="a", row=1, wave=1, kind="friction", severity="minor", at="r.md", text="t")
        self.assertEqual(len(field.signals(self.project, row=1, wave=2)), 1)
        self.assertEqual(field.signals(self.project, row=1, wave=3), [])
        n = field.post(self.project, by="a", row=1, wave=1, kind="hole", severity="critical", at="y", text="t")
        field.resolve(self.project, by="builder", row=1, wave=1, of=n, text="done", fixed=True)
        self.assertEqual([s for s in field.signals(self.project, row=1, wave=1) if s["id"] == n], [])

    def test_only_the_builder_answers(self):
        env = dict(os.environ, COLONY_ROOT=str(self.dir), COLONY_AGENT="reuse@w1", COLONY_ROW="1", COLONY_WAVE="1")
        subprocess.run([sys.executable, "-m", "colony", "field", "signal", "--kind", "hole", "--severity", "major",
                        "--at", "a", "--text", "t"], env=env, check=True, capture_output=True)
        done = subprocess.run([sys.executable, "-m", "colony", "field", "resolve", "1", "--fixed", "--text", "x"],
                              env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 2)
        self.assertIn("only the builder", done.stderr)


class MapTest(Base):
    def test_map_finds_code_and_prose_and_rereads_only_changes(self):
        (self.dir / "tax.py").write_text('def compute_invoice_total(lines):\n    """Sum the invoice lines, tax included."""\n')
        (self.dir / "story.md").write_text("# Chapter one\n\nMara finds the lighthouse key.\n")
        _, changed = mapper.build(self.project)
        self.assertIn("tax.py", changed)
        hits = mapper.query(self.project, "invoice total with tax")
        self.assertEqual(hits[0]["name"], "compute_invoice_total")
        self.assertIn("lighthouse", mapper.render(mapper.query(self.project, "the lighthouse key")))
        _, changed = mapper.build(self.project)
        self.assertNotIn("tax.py", changed)


class MemoryTest(Base):
    def test_rows_checks_approval_and_close(self):
        self.spine(approved="no")
        self.assertEqual([r[0] for r in memory.rows(self.project)], [1, 2])
        self.assertEqual(memory.checks(self.project), ["test -f work.txt"])
        self.assertFalse(memory.approved(self.project))
        memory.close_row(self.project, 1)
        self.assertEqual([r[0] for r in memory.rows(self.project)], [2])

    def test_brief_carries_goal_now_row_map_and_signals(self):
        self.spine()
        (self.dir / "work.txt").write_text("first line\n")
        mapper.build(self.project)
        field.post(self.project, by="a", row=1, wave=1, kind="hole", severity="major", at="work.txt", text="broken")
        b = memory.brief(self.project, memory.rows(self.project)[0])
        for part in ("a thing", "Row 1: Write the first line", "work.txt", "broken"):
            self.assertIn(part, b)


class ClockTest(Base):
    def test_a_row_closes_end_to_end(self):
        self.spine()
        clock.run(self.project, max_rows=1)
        self.assertEqual([r[0] for r in memory.rows(self.project)], [2])
        self.assertTrue((self.dir / "design" / "now.md").read_text().startswith("Status: row closed."))
        history = (self.dir / "design" / "history.md").read_text()
        self.assertTrue(history.startswith("# History") and "## Row closed" in history, "history is appended, with its header")
        usage = self.project.read("usage.jsonl")
        agents = {u["agent"].split("@")[0] for u in usage}
        self.assertEqual(agents, {"builder", "reuse", "fresh-eyes", "reconciler"})
        self.assertTrue(all(u["output"] == 100 and u["cache_read"] == 5000 for u in usage))
        [closed] = [e for e in self.project.read("ledger.jsonl") if e["kind"] == "row-closed"]
        self.assertAlmostEqual(closed["cost_usd"], sum(u["cost_usd"] for u in usage))
        lineages = specialists.load(self.project)
        self.assertEqual(len(lineages["reuse"]["memory"]), 1, "a fixed, reinforced signal becomes one lesson")
        self.assertIn("work.txt:1", lineages["fresh-eyes"]["memory"][0])
        log = subprocess.run(["git", "-C", str(self.dir), "log", "--format=%s"], capture_output=True, text=True).stdout
        self.assertIn("row 1 closed", log)

    def test_the_builder_keeps_one_session_per_row(self):
        self.spine()
        clock.run(self.project, max_rows=1)
        builder_calls = [u for u in self.project.read("usage.jsonl") if u["agent"] == "builder"]
        self.assertEqual(len(builder_calls), 2, "one build, one wave of fixes")

    def test_a_fork_stops_the_run(self):
        self.spine()
        os.environ["FAKE_FORK"] = "1"
        with self.assertRaises(clock.Stop) as stop:
            clock.run(self.project, max_rows=1)
        self.assertIn("fork", str(stop.exception))
        self.assertEqual([r[0] for r in memory.rows(self.project)], [1, 2], "a forked row does not close")

    def test_a_failing_check_blocks_the_close(self):
        self.spine(check="false")
        with self.assertRaises(clock.Stop) as stop:
            clock.run(self.project, max_rows=1)
        self.assertIn("cannot close", str(stop.exception))

    def test_an_unapproved_spine_does_not_run(self):
        self.spine(approved="no")
        with self.assertRaises(clock.Stop):
            clock.run(self.project)

    def test_the_cap_stops_before_a_call(self):
        self.spine()
        with self.assertRaises(clock.Stop) as stop:
            clock.run(self.project, max_rows=1, cap=0.6)
        self.assertIn("cap", str(stop.exception))


class EffortTest(Base):
    def test_each_role_can_run_at_its_own_effort(self):
        import json as _json
        self.spine()
        (self.project.state / "config.json").write_text(_json.dumps({"effort": "medium", "effort_builder": "low",
                                                                      "effort_specialist": "high"}))
        clock.run(self.project, max_rows=1)
        by_role = {u["agent"].split("@")[0]: u["effort"] for u in self.project.read("usage.jsonl")}
        self.assertEqual(by_role, {"builder": "low", "reuse": "high", "fresh-eyes": "high", "reconciler": "medium"})


class LimitTest(Base):
    def test_a_refused_call_waits_and_is_repeated_not_counted(self):
        self.spine()
        os.environ["FAKE_LIMIT_ONCE"] = "1"
        slept = []
        rec = claude.call(self.project, "Your role: the reconciler.", agent="reconciler", row=1, wave=0,
                          budget=1, sleep=slept.append)
        self.assertEqual(len(slept), 1)
        self.assertGreater(slept[0], 0)
        self.assertTrue(rec["ok"])
        self.assertEqual(len(self.project.read("waits.jsonl")), 1)
        self.assertEqual(len(self.project.read("usage.jsonl")), 1, "the refused attempt is not metered as a call")

    def test_limit_wait_reads_the_reset_time(self):
        self.assertIsNone(claude.limit_wait({"is_error": False, "result": "ok"}))
        self.assertEqual(claude.limit_wait({"is_error": True, "api_error_status": 429, "result": "slow down"}), 900)
        w = claude.limit_wait({"is_error": True, "api_error_status": 429,
                               "result": "You've hit your session limit · resets 3:30am (America/Los_Angeles)"})
        self.assertTrue(0 < w <= 86400 + 120)


if __name__ == "__main__":
    unittest.main()
