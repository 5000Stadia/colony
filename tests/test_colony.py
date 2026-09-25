import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from colony import claude, clock, field, health, mapper, memory, specialists  # noqa: E402
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
        for k in ("FAKE_FORK", "FAKE_LIMIT_ONCE", "FAKE_LONG_NOW", "FAKE_ASSESS", "FAKE_BREAK_ROW", "FAKE_BUILD_ERROR", "FAKE_BUILDER_FORK", "FAKE_TAMPER", "COLONY_AGENT", "COLONY_ROW", "COLONY_WAVE"):
            os.environ.pop(k, None)
        # Most tests exercise the full machinery: review on, reconciliation on, two named lineages.
        for f in self.project.specialists.glob("*.md"):
            f.unlink()
        specialists.write(self.project, "reuse", "Find what this change re-makes.")
        specialists.write(self.project, "fresh-eyes", "Meet the work as its audience would.")
        self.configure(review="always", reconcile=True, waves_per_row=3)

    def configure(self, **cfg):
        (self.project.state / "config.json").write_text(json.dumps(cfg))

    def tearDown(self):
        self.tmp.cleanup()

    def spine(self, check="test -f work.txt", approved="yes"):
        self.project.spine.write_text(SPINE.format(check=check, approved=approved))


class InitTest(unittest.TestCase):
    def test_an_existing_project_keeps_its_own_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "existing"
            d.mkdir()
            (d / ".gitignore").write_text(".invoicer/\n")
            (d / "CLAUDE.md").write_text("Our own rules.\n")
            env = dict(os.environ, PYTHONPATH=str(ROOT))
            subprocess.run([sys.executable, "-m", "colony", "init", str(d)], env=env, check=True, capture_output=True)
            self.assertIn(".invoicer/", (d / ".gitignore").read_text())
            self.assertIn(".colony/transcripts/", (d / ".gitignore").read_text())
            self.assertTrue((d / "CLAUDE.md").read_text().startswith("Our own rules."))
            self.assertIn("design/spine.md", (d / "CLAUDE.md").read_text())


class BriefTest(Base):
    def test_no_now_means_no_claim_about_the_state(self):
        self.spine()
        self.configure(reconcile=False)
        b = memory.brief(self.project, (1, "Write the first line", "work.txt exists"))
        self.assertNotIn("Where the project is now", b)
        self.assertNotIn("Nothing has been built", b)
        self.project.now.write_text("Status: row 3 built.\n")
        self.assertIn("Status: row 3 built.", memory.brief(self.project, (1, "x", "y")))


class RunTest(Base):
    def test_a_run_detaches_and_wait_says_how_it_went(self):
        self.spine()
        self.configure(review="never")
        env = dict(os.environ, COLONY_ROOT=str(self.dir))
        cli = lambda *a: subprocess.run([sys.executable, "-m", "colony", *a], env=env, capture_output=True, text=True)
        started = cli("run", "--rows", "2")
        self.assertIn("running in the background", started.stdout)
        done = cli("wait", "--timeout", "120")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("2 row(s) closed", done.stdout)
        self.assertFalse((self.project.state / "run.json").exists())

    def test_a_killed_run_is_named_and_the_next_one_can_start(self):
        self.spine()
        (self.project.state / "run.json").write_text(json.dumps({"pid": 999999, "started": "then"}))
        env = dict(os.environ, COLONY_ROOT=str(self.dir))
        out = subprocess.run([sys.executable, "-m", "colony", "wait"], env=env, capture_output=True, text=True).stdout
        self.assertIn("killed before it finished", out)
        self.assertIn("row 1", out)


class ProposedTest(Base):
    def test_proposed_rows_wait_for_the_person(self):
        self.spine()
        self.project.spine.write_text(self.project.spine.read_text() + "\n## Proposed rows\n| 9 | Try another way | it works | 3 — cheap |\n")
        self.assertEqual([r[0] for r in memory.rows(self.project)], [1, 2], "a proposal is not in the plan")
        self.assertEqual(memory.proposed(self.project), [(9, "Try another way", "it works")])
        _, _, qs = health.overview(self.project)
        self.assertIn("proposed-rows", [q["kind"] for q in qs])

    def test_the_person_can_lift_one_outward_refusal(self):
        self.spine()
        self.configure(review="never", allow_outward=["Bash(gh:*)"])
        clock.run(self.project, max_rows=1)
        argv = json.loads((self.project.state / "fake-state.json").read_text())["argv"]["builder"][0]
        self.assertNotIn("Bash(gh:*)", argv)
        self.assertIn("Bash(git push:*)", argv)


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

    def test_a_lineage_does_not_confirm_itself_and_resolved_leaves(self):
        field.post(self.project, by="critic@w1", row=1, wave=1, kind="hole", severity="major", at="r.md", text="t")
        field.post(self.project, by="critic@w2", row=1, wave=2, kind="hole", severity="major", at="r.md", text="again")
        [s] = field.signals(self.project, row=1, wave=2)
        self.assertEqual(s["strength"], 1, "the same lineage in a later wave is not independent")
        n = field.post(self.project, by="a", row=1, wave=1, kind="hole", severity="critical", at="y", text="t")
        field.resolve(self.project, by="builder", row=1, wave=1, of=n, text="done", fixed=True)
        self.assertEqual([s for s in field.signals(self.project, row=1, wave=1) if s["id"] == n], [])

    def test_parallel_posts_never_share_an_id(self):
        import concurrent.futures as cf
        def post(i):
            return field.post(self.project, by=f"s{i}", row=1, wave=1, kind="hole", severity="major", at=f"f{i}", text="t")
        with cf.ThreadPoolExecutor(16) as pool:
            ids = list(pool.map(post, range(64)))
        self.assertEqual(sorted(ids), list(range(1, 65)))

    def test_an_unfixed_signal_always_wakes_the_builder(self):
        field.post(self.project, by="a@w2", row=1, wave=2, kind="unfixed", severity="minor", at="x", text="still broken")
        [s] = field.signals(self.project, row=1, wave=2)
        self.assertTrue(field.wakes_builder(s, 2))

    def test_a_specialist_is_shown_its_own_answered_signals(self):
        n = field.post(self.project, by="reuse@w1", row=1, wave=1, kind="hole", severity="major", at="a.py:f", text="breaks")
        field.post(self.project, by="fresh-eyes@w1", row=1, wave=1, kind="hole", severity="major", at="b.py", text="other")
        field.resolve(self.project, by="builder", row=1, wave=1, of=n, text="patched f", fixed=True)
        text = clock.verification(self.project, 1, "reuse")
        self.assertIn("a.py:f", text)
        self.assertIn("patched f", text)
        self.assertNotIn("b.py", text)
        self.assertEqual(clock.verification(self.project, 1, "fresh-eyes"), "")

    def test_only_the_builder_answers(self):
        env = dict(os.environ, COLONY_ROOT=str(self.dir), COLONY_AGENT="reuse@w1", COLONY_ROW="1", COLONY_WAVE="1")
        subprocess.run([sys.executable, "-m", "colony", "field", "signal", "--kind", "hole", "--severity", "major",
                        "--at", "a", "--text", "t"], env=env, check=True, capture_output=True)
        done = subprocess.run([sys.executable, "-m", "colony", "field", "resolve", "1", "--fixed", "--text", "x"],
                              env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 2)
        self.assertIn("only the builder", done.stderr)


class HomesTest(Base):
    def test_reviewers_are_claude_code_subagents_and_the_map_is_not_committed(self):
        path = self.dir / ".claude" / "agents" / "reuse.md"
        text = path.read_text()
        self.assertTrue(text.startswith("---\nname: reuse\n"))
        self.assertIn("colony: reviewer", text)
        self.assertIn("tools: Read, Grep, Glob, Bash", text)
        (self.dir / ".claude" / "agents" / "mine.md").write_text("---\nname: mine\ndescription: the person's own\n---\nhello\n")
        self.assertNotIn("mine", specialists.load(self.project), "the person's own subagents are left alone")
        mapper.build(self.project)
        self.assertTrue((self.dir / ".colony" / "map.md").exists())
        self.assertFalse((self.dir / "design" / "map.md").exists())
        ignored = subprocess.run(["git", "-C", str(self.dir), "check-ignore", ".colony/map.md"], capture_output=True, text=True)
        self.assertEqual(ignored.returncode, 0, "the map is a projection and never committed")


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
        body = subprocess.run(["git", "-C", str(self.dir), "log", "-1", "--format=%B", "--grep", "row 1 closed"],
                              capture_output=True, text=True).stdout
        self.assertIn("Built the row; decided nothing new.", body, "the narrative is the closing commit's message")
        self.assertFalse((self.dir / ".colony" / "closing-note.md").exists())
        self.assertFalse((self.dir / "design" / "history.md").exists())
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

    def test_a_reviewers_edits_are_put_back_and_no_agent_may_publish(self):
        self.spine()
        os.environ["FAKE_TAMPER"] = "1"
        clock.run(self.project, max_rows=1)
        self.assertNotIn("rewrote", (self.dir / "work.txt").read_text())
        self.assertFalse((self.dir / "junk.txt").exists())
        self.assertTrue(self.project.read("field.jsonl"), "the reviewers' signals stay")
        seen = json.loads((self.project.state / "fake-state.json").read_text())["argv"]
        for role in ("builder", "critic"):
            for argv in seen.get(role, []):
                self.assertIn("Bash(git push:*)", argv)

    def test_a_builders_fork_stops_the_run_without_review(self):
        self.spine()
        self.configure(review="never")
        os.environ["FAKE_BUILDER_FORK"] = "1"
        with self.assertRaises(clock.Stop) as stop:
            clock.run(self.project, max_rows=1)
        self.assertIn("which one?", str(stop.exception))
        self.assertEqual([r[0] for r in memory.rows(self.project)], [1, 2])

    def test_a_builder_that_did_not_finish_leaves_its_row_open(self):
        self.spine()
        os.environ["FAKE_BUILD_ERROR"] = "1"
        with self.assertRaises(clock.Stop) as stop:
            clock.run(self.project, max_rows=1)
        self.assertIn("did not finish", str(stop.exception))
        self.assertEqual([r[0] for r in memory.rows(self.project)], [1, 2])

    def test_the_cap_belongs_to_the_run_not_the_project(self):
        self.spine()
        self.configure(review="never")
        clock.run(self.project, max_rows=1, cap=5)
        clock.run(self.project, max_rows=1, cap=0.9)        # $0.50 already spent in an earlier run
        self.assertEqual(memory.rows(self.project), [])

    def test_a_failing_check_gets_one_fix_before_it_blocks_the_close(self):
        self.spine(check="false")
        self.configure(review="never")
        with self.assertRaises(clock.Stop) as stop:
            clock.run(self.project, max_rows=1)
        self.assertIn("cannot close", str(stop.exception))
        waves = [u["wave"] for u in self.project.read("usage.jsonl") if u["agent"] == "builder"]
        self.assertEqual(len(waves), 2, "the build and one try at the failing check")

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
        self.configure(effort="medium", effort_builder="low", effort_specialist="high", review="always", reconcile=True)
        clock.run(self.project, max_rows=1)
        by_role = {u["agent"].split("@")[0]: u["effort"] for u in self.project.read("usage.jsonl")}
        self.assertEqual(by_role, {"builder": "low", "reuse": "high", "fresh-eyes": "high", "reconciler": "medium"})


class LeanTest(Base):
    def test_the_defaults_are_lean_and_trust_a_small_change(self):
        self.spine()
        (self.project.state / "config.json").unlink()
        clock.run(self.project, max_rows=1)
        agents = {u["agent"].split("@")[0] for u in self.project.read("usage.jsonl")}
        self.assertEqual(agents, {"builder"}, "no reviewer and no reconciler for a small change by default")
        [decision] = [e for e in self.project.read("ledger.jsonl") if e["kind"] == "review"]
        self.assertFalse(decision["review"])
        self.assertIn("trusted", decision["why"])
        self.assertEqual([r[0] for r in memory.rows(self.project)], [2], "the row still closes")

    def test_a_risky_area_is_reviewed_under_auto(self):
        self.spine()
        self.project.spine.write_text(self.project.spine.read_text().replace(
            "## The spec list", "## Risky areas\n- `work.txt`\n\n## The spec list"))
        self.configure(review="auto")
        clock.run(self.project, max_rows=1)
        [decision] = [e for e in self.project.read("ledger.jsonl") if e["kind"] == "review"]
        self.assertTrue(decision["review"])
        self.assertIn("risky", decision["why"])

    def test_specialists_cannot_edit(self):
        self.spine()
        clock.run(self.project, max_rows=1)
        argv = json.loads((self.project.state / "fake-state.json").read_text())["argv"]
        self.assertIn("Edit", argv["reuse"][0][argv["reuse"][0].index("--disallowedTools"):])
        self.assertNotIn("Edit", argv["builder"][0][argv["builder"][0].index("--disallowedTools"):])

    def test_now_is_trimmed_when_it_overruns(self):
        self.spine()
        os.environ["FAKE_LONG_NOW"] = "1"
        clock.run(self.project, max_rows=1)
        self.assertEqual(self.project.now.read_text(), "Status: trimmed.\n")
        self.assertEqual(sum(1 for u in self.project.read("usage.jsonl") if u["agent"] == "reconciler"), 2)


class AssessmentTest(Base):
    def decision(self):
        [d] = [e for e in self.project.read("ledger.jsonl") if e["kind"] == "review"]
        return d

    def spine_with_impact(self, impact):
        self.spine()
        text = self.project.spine.read_text().replace(
            "| 1 | Write the first line | work.txt exists |", f"| 1 | Write the first line | work.txt exists | {impact} |")
        self.project.spine.write_text(text)

    def test_a_row_at_the_impact_rule_is_reviewed(self):
        self.spine_with_impact(8)
        self.configure(review="auto", review_at_impact=8)
        os.environ["FAKE_ASSESS"] = "confidence 9/10 — the tax path is uncertain"
        clock.run(self.project, max_rows=1)
        d = self.decision()
        self.assertTrue(d["review"])
        self.assertEqual((d["assessment"]["impact"], d["assessment"]["confidence"]), (8, 9))
        self.assertIn("set when the row was assigned", d["why"])

    def test_doubt_on_harmless_work_is_trusted(self):
        self.spine_with_impact(2)
        self.configure(review="auto", review_at_impact=8)
        os.environ["FAKE_ASSESS"] = "confidence 4/10 — unsure about wording in help text"
        clock.run(self.project, max_rows=1)
        self.assertFalse(self.decision()["review"], "the builder's doubt is recorded, not a trigger")

    def test_a_row_without_impact_is_not_ruled_on(self):
        self.spine()
        self.configure(review="auto", review_at_impact=1)
        os.environ["FAKE_ASSESS"] = "confidence 1/10 — no idea"
        clock.run(self.project, max_rows=1)
        d = self.decision()
        self.assertFalse(d["review"])
        self.assertNotIn("impact", d["assessment"])

    def test_the_builder_sees_the_stakes_but_never_the_rule(self):
        # Knowing a row's impact makes a builder careful; knowing the threshold would let it shade its
        # confidence to stay under it.
        self.spine_with_impact(8)
        self.configure(review="auto", review_at_impact=9)
        clock.run(self.project, max_rows=1)
        argv = json.loads((self.project.state / "fake-state.json").read_text())["argv"]["builder"][0]
        prompt = argv[argv.index("-p") + 1]
        self.assertIn("ASSESSMENT: confidence", prompt)
        self.assertIn("| 8 |", prompt)
        self.assertNotIn("review_at", prompt)

    def test_calibration_sets_the_forecast_beside_what_review_found(self):
        self.spine_with_impact(6)
        os.environ["FAKE_ASSESS"] = "confidence 5/10 — unsure"
        clock.run(self.project, max_rows=1)
        env = dict(os.environ, COLONY_ROOT=str(self.dir))
        out = json.loads(subprocess.run([sys.executable, "-m", "colony", "calibration"], env=env,
                                        capture_output=True, text=True).stdout)
        [row] = out["rows"]
        self.assertEqual((row["confidence"], row["impact"], row["reviewed"]), (5, 6, True))
        self.assertGreaterEqual(row["review_fixes"], 1)


class LookHereTest(Base):
    def test_the_reviewer_is_told_the_stakes_and_the_builders_doubt_first(self):
        self.spine()
        self.project.spine.write_text(self.project.spine.read_text().replace(
            "| 1 | Write the first line | work.txt exists |",
            "| 1 | Write the first line | work.txt exists | 8 — a wrong total reaches a tax filing |"))
        self.assertEqual(memory.stakes(self.project, 1), (8, "a wrong total reaches a tax filing"))
        os.environ["FAKE_ASSESS"] = "confidence 5/10 — the refund path"
        clock.run(self.project, max_rows=1)
        argv = json.loads((self.project.state / "fake-state.json").read_text())["argv"]["reuse"][0]
        prompt = argv[argv.index("-p") + 1]
        head = prompt.split("# Look here first", 1)[1].split("# The goal")[0]
        self.assertIn("impact 8/10 — a wrong total reaches a tax filing", head)
        self.assertIn("confidence 5/10 — the refund path", head)


class AdaptTest(Base):
    def spine_rows(self, n, impact):
        rows = "".join(f"| {i} | Write line {i} | work.txt grows | {impact} |\n" for i in range(1, n + 1))
        self.project.spine.write_text(SPINE.format(check="test -f work.txt", approved="yes").replace(
            "| 1 | Write the first line | work.txt exists |\n| 2 | Write the second line | work.txt has two lines |\n", rows))

    def moves(self):
        return [(e["from"], e["to"]) for e in self.project.read("ledger.jsonl") if e["kind"] == "threshold"]

    def test_a_review_that_finds_problems_lowers_the_threshold(self):
        self.spine_rows(1, impact=8)
        self.configure(review="auto", review_at_impact=8)             # reviewed; the fake reviewers find holes
        clock.run(self.project, max_rows=1)
        self.assertEqual(self.moves(), [(8, 7)])

    def test_one_reviewers_fixed_major_is_not_enough_to_lower_it(self):
        self.spine_rows(1, impact=8)
        self.configure(review="auto", review_at_impact=8)
        for f in self.project.specialists.glob("*.md"):
            f.unlink()
        specialists.write(self.project, "solo", "Look for holes.")
        clock.run(self.project, max_rows=1)
        [closed] = [e for e in self.project.read("ledger.jsonl") if e["kind"] == "row-closed"]
        self.assertEqual((closed["review_fixes"], closed["serious_fixes"]), (1, 0))
        self.assertEqual(self.moves(), [], "a builder fixes whatever it is shown; one lineage's major is not evidence")

    def test_a_trusted_row_that_breaks_a_check_lowers_the_threshold(self):
        self.spine_rows(2, impact=2)
        self.project.spine.write_text(self.project.spine.read_text().replace("test -f work.txt", "test ! -f broken.txt"))
        self.configure(review="auto", review_at_impact=8)             # impact 2: trusted without review
        os.environ["FAKE_BREAK_ROW"] = "2"
        with self.assertRaises(clock.Stop):
            clock.run(self.project, max_rows=2)
        self.assertEqual(self.moves(), [(8, 7)], "a miss review skipped counts at once, like a finding")

    def test_reviews_that_find_nothing_raise_it_and_the_bounds_hold(self):
        self.spine_rows(7, impact=10)
        self.configure(review="auto", review_at_impact=9, waves_per_row=1)
        for f in self.project.specialists.glob("*.md"):
            f.unlink()
        specialists.write(self.project, "quiet", "Look, but this fake finds nothing.")
        clock.run(self.project, max_rows=7)
        self.assertEqual(self.moves(), [(9, 10)], "three empty reviews move it one step, and never past 10")
        [last] = [e for e in self.project.read("ledger.jsonl") if e["kind"] == "review"][-1:]
        self.assertIn("review at 10", last["why"])


class PageTest(Base):
    def test_the_page_shows_now_roadmap_history_and_cost(self):
        from colony import page
        self.spine()
        clock.run(self.project, max_rows=1)
        html_ = page.render(self.project)
        for part in ("Roadmap", "Write the second line", "History", "row 1 closed", "Built the row; decided nothing new.",
                     "Cost", "reviewed"):
            self.assertIn(part, html_)

    def test_a_note_reaches_its_rows_builder_and_is_folded_in_after(self):
        self.spine()
        memory.add_note(self.project, 1, "person", "use the grid from the sketch")
        self.assertIn("use the grid from the sketch", memory.brief(self.project, memory.rows(self.project)[0]))
        clock.run(self.project, max_rows=1)
        argv = json.loads((self.project.state / "fake-state.json").read_text())["argv"]["builder"][0]
        self.assertIn("use the grid from the sketch", argv[argv.index("-p") + 1])
        [n] = memory.notes(self.project)
        self.assertTrue(n["folded"])

    def test_the_page_answers_only_itself(self):
        import threading, urllib.request, urllib.error
        from http.server import ThreadingHTTPServer
        from colony import page
        self.spine()
        page.Handler.project = self.project
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), page.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]
        try:
            ok = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
            self.assertIn("Roadmap", ok)
            data = b"row=2&text=from+the+page"
            req = urllib.request.Request(f"http://127.0.0.1:{port}/note", data=data, headers={"Origin": f"http://127.0.0.1:{port}"})
            urllib.request.urlopen(req)
            self.assertEqual([n["text"] for n in memory.notes(self.project)], ["from the page"])
            bad = urllib.request.Request(f"http://127.0.0.1:{port}/note", data=b"row=2&text=evil", headers={"Origin": "https://evil.example"})
            with self.assertRaises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(bad)
            self.assertEqual(err.exception.code, 403)
            self.assertEqual(len(memory.notes(self.project)), 1)
        finally:
            httpd.shutdown()


class HealthTest(Base):
    def escalations(self):
        return [e for e in self.project.read("ledger.jsonl") if e["kind"] == "escalation"]

    def test_a_healthy_small_project_switches_nothing_on(self):
        self.spine()
        self.configure(review="never")
        clock.run(self.project, max_rows=2)
        self.assertEqual(self.escalations(), [])
        self.assertFalse(any(e["kind"] == "structure-proposal" for e in self.project.read("ledger.jsonl")))

    def test_breaks_in_two_rows_become_a_question_not_a_switch(self):
        self.spine(check="test ! -f broken.txt")
        self.project.spine.write_text(self.project.spine.read_text().replace(
            "| 2 | Write the second line | work.txt has two lines |\n",
            "| 2 | Write the second line | work.txt has two lines |\n| 3 | Write the third line | three lines |\n"))
        self.configure(review="never")
        os.environ["FAKE_BREAK_ROW"] = "2"
        with self.assertRaises(clock.Stop):
            clock.run(self.project, max_rows=2)
        _, _, qs = health.overview(self.project)
        self.assertNotIn("breaks-recur", [q["kind"] for q in qs], "one break can be a flaky check")
        (self.dir / "broken.txt").unlink()
        os.environ["FAKE_BREAK_ROW"] = "3"
        with self.assertRaises(clock.Stop):
            clock.run(self.project, max_rows=2)
        _, _, qs = health.overview(self.project)
        [q] = [q for q in qs if q["kind"] == "breaks-recur"]
        self.assertIn("rows 2, 3", q["ask"])
        self.assertFalse(self.project.config()["reconcile"], "nothing switches itself on")

    def test_the_checkpoint_is_free_puts_workflow_first_and_resets_its_window(self):
        self.spine()
        self.configure(review="always")
        clock.run(self.project, max_rows=1)
        spent = len(self.project.read("usage.jsonl"))
        env = dict(os.environ, COLONY_ROOT=str(self.dir))
        out = subprocess.run([sys.executable, "-m", "colony", "checkpoint"], env=env, capture_output=True, text=True).stdout
        self.assertEqual(len(self.project.read("usage.jsonl")), spent, "no tokens spent")
        self.assertLess(out.index("## Workflow and progress"), out.index("## Tokens"))
        self.assertLess(out.index("## Tokens"), out.index("## Questions for you"))
        self.assertIn("1 row(s) closed (rows 1–1)", out)
        clock.run(self.project, max_rows=1)
        text, _, _ = health.overview(self.project)
        self.assertIn("since row 1", text)
        self.assertIn("rows 2–2", text)

    def test_a_row_that_keeps_stopping_is_a_question_until_a_rule_settles_it(self):
        self.spine(check="test ! -f broken.txt")
        self.configure(review="never")
        (self.dir / "broken.txt").write_text("x")
        env = dict(os.environ, COLONY_ROOT=str(self.dir))
        colony = lambda *a: subprocess.run([sys.executable, "-m", "colony", *a], env=env, capture_output=True, text=True).stdout
        colony("run", "--attached"); colony("run", "--attached")
        out = colony("checkpoint")
        self.assertIn("`row-stuck` — Row 1 has stopped 2 times", out)
        self.assertEqual([q["kind"] for q in memory.open_questions(self.project)], ["row-stuck"])
        colony("answer", "row-stuck", "split it and carry on", "--always")
        self.assertEqual(memory.open_questions(self.project), [])
        self.assertIn("On `row-stuck`: split it and carry on", " ".join(n["text"] for n in memory.waiting_notes(self.project, 1)))
        self.assertEqual(memory.rules(self.project), {"row-stuck": "split it and carry on"})
        colony("run", "--attached"); colony("run", "--attached")
        out = colony("checkpoint")
        self.assertIn("## Settled by your rules\n- `row-stuck` — split it and carry on", out)
        self.assertNotIn("`row-stuck` — Row", out)

    def test_memory_that_costs_is_put_to_the_person(self):
        self.configure(reconcile=True)
        self.project.append("ledger.jsonl", {"kind": "row-closed", "row": 1, "cost_usd": 1.0, "review_fixes": 0})
        self.project.append("usage.jsonl", {"row": 1, "wave": 0, "agent": "builder", "cost_usd": 0.6})
        self.project.append("usage.jsonl", {"row": 1, "wave": 0, "agent": "reconciler", "cost_usd": 0.4})
        _, _, qs = health.overview(self.project)
        [q] = [q for q in qs if q["kind"] == "memory-cost"]
        self.assertIn("$0.40 this stretch, 67% on top of building", q["ask"])
    def test_rising_reading_and_strain_are_measured(self):
        from colony import health
        for r, (read, cost) in enumerate([(100, 1), (110, 1), (90, 1), (400, 4), (420, 4), (450, 1)], start=1):
            self.project.append("usage.jsonl", {"row": r, "wave": 0, "agent": "builder", "cache_read": read, "input": 0, "cost_usd": cost})
        self.assertIn("three rows running", health.rising_reading(self.project))
        for r in range(1, 5):
            self.project.append("ledger.jsonl", {"kind": "row-closed", "row": r, "files": ["core/engine.py", f"row{r}.py"]})
        signs = health.strain(self.project)
        self.assertEqual(len(signs), 2)
        self.assertTrue(any("core/engine.py" in s for s in signs))
        health.review_health(self.project, 6)
        [p] = [e for e in self.project.read("ledger.jsonl") if e["kind"] == "structure-proposal"]
        self.assertIn("lane", p["proposal"])
        health.review_health(self.project, 7)
        self.assertEqual(sum(1 for e in self.project.read("ledger.jsonl") if e["kind"] == "structure-proposal"), 1,
                         "a proposal is not repeated while it waits on the person")


class LimitTest(Base):
    def test_a_refused_call_waits_and_is_repeated_not_counted(self):
        self.spine()
        os.environ["FAKE_LIMIT_ONCE"] = "1"
        slept = []
        rec = claude.call(self.project, "You are `reconciler · row 1 · at its close`.", agent="reconciler", row=1, wave=0,
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
