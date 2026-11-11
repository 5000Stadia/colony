"""Warning signs (R146): three views kept apart, too little evidence said plainly, reopened items found by code,
the frozen ruler's fields on every label, a labeller change caught as a break on the anchor cases, and no batch when
nothing new arrived. Synthetic projects only."""
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from colony import board, warning_signs as ws

WEEK = 7 * 86400
T0 = 1767571200          # Monday 2026-01-05 00:00 UTC


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(t))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        env = patch.dict(os.environ, {"COLONY_BOARD_HOME": str(self.base / "home"),
                                      "CLAUDE_CONFIG_DIR": str(self.base / "claude"),
                                      "CODEX_HOME": str(self.base / "codex"),
                                      "COLONY_CODEX_SOURCE_HOME": str(self.base / "codex")})
        env.start()
        self.addCleanup(env.stop)
        self.root = self.base / "garden"
        (self.root / ".board").mkdir(parents=True)
        board.save_registry(dict(roots=[], projects=[str(self.root)], settings={"messaging": False}))
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@users.noreply.github.com")

    def git(self, *args, at=None):
        env = dict(os.environ, **({"GIT_AUTHOR_DATE": iso(at), "GIT_COMMITTER_DATE": iso(at)} if at else {}))
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True,
                              env=env).stdout

    def plan(self, items, at, msg="plan", extra=None):
        """Write ROADMAP.md with these items ({id: (state char, text)}) and commit it at a time."""
        lines = ["# Garden", "", "## M1 — Grow", ""] + [f"- [{s}] {i} {t}" for i, (s, t) in items.items()]
        (self.root / "ROADMAP.md").write_text("\n".join(lines) + "\n")
        for name, text in (extra or {}).items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text)
        self.git("add", "-A")
        self.git("commit", "-qm", msg, at=at)

    def commit(self, msg, at, name="work.txt"):
        with (self.root / name).open("a") as f:
            f.write(msg + "\n")
        self.git("add", "-A")
        self.git("commit", "-qm", msg, at=at)

    def build(self, now=None, **kw):
        return ws.build(roots=[self.root], now=now or T0 + 30 * WEEK, **kw)

    def project(self, rep):
        return rep["projects"][str(self.root)]


class FakeLabeller:
    """Judges every case the same way, under a picked model of its own; counts its calls."""

    def __init__(self, model, label, family="claude"):
        self.model, self.lab, self.calls = model, label, 0
        self.ruler = (ws.RUBRIC_VERSION, family, model)

    def __call__(self, packets):
        self.calls += 1
        return {"labels": {p["case"]: (self.lab, "fixture") for p in packets}, "model": self.model + "-20260101",
                "picked": self.model, "family": self.ruler[1], "cost": 0.001, "usage": {"output_tokens": 10}}


class ViewsTest(Base):
    def test_an_item_only_mentioned_is_not_worked_again(self):
        """A research project's finished milestone, 2026-11-10: ten signals, none of them rework. An item named twice
        counted twice, another project's item counted as this one's, and a passing mention counted as work."""
        self.plan({"R1": (" ", "Seeds"), "R2": (" ", "Soil"), "R3": (" ", "Water")}, T0)
        self.plan({"R1": ("x", "Seeds"), "R2": ("x", "Soil"), "R3": (" ", "Water")}, T0 + 3600, msg="R1 and R2 done")
        day = T0 + 2 * 86400
        self.commit("R1: fix the seed tray again, as R1's check asked", day)             # leads with it, named twice: once
        self.commit("R3 started: fix its feed; R2's mix unchanged", day + 60)             # R2 only mentioned
        self.commit("Ledger: fix the rows for R1 and R2", day + 120)                      # leads with neither, names two
        self.commit("Fix the label on R2's bag", day + 180)                               # the only item it names
        with patch.object(ws.board, "projects", return_value=[self.root, self.base / "shop"]), \
                patch.object(ws.board, "name_of", side_effect=lambda q: Path(q).name):
            self.commit("A brief on shop R2: fix its wording", day + 240)                 # another project's R2
            p = self.project(self.build())
        again = [(s["item"], s["evidence"]["line"][:12]) for s in p["signals"] if s["kind"] == "finished item worked again"]
        self.assertEqual(again, [("R1", "R1: fix the "), ("R2", "Fix the labe")])

    def test_views_are_kept_apart_and_never_added_up(self):
        self.plan({"R1": (" ", "Seeds"), "R2": (" ", "Soil"), "R3": (" ", "Water")}, T0)
        self.plan({"R1": ("x", "Seeds"), "R2": ("~", "Soil"), "R3": (" ", "Water")}, T0 + 3600)
        self.commit("Revert the soil mix", T0 + 7200)
        cwd = str(self.root)
        tr = self.base / "claude" / "projects" / "-garden" / "s.jsonl"
        tr.parent.mkdir(parents=True)
        tr.write_text(json.dumps({"type": "assistant", "cwd": cwd, "timestamp": iso(T0 + 3000), "message": {
            "id": "m1", "model": "claude-x", "usage": {"input_tokens": 1000, "cache_read_input_tokens": 0,
                                                       "cache_creation_input_tokens": 0, "output_tokens": 100}}}) + "\n")
        p = self.project(self.build())
        wk = ws.week(T0)
        self.assertEqual(p["weeks"][wk]["written"], 3)
        self.assertEqual(p["weeks"][wk]["finished"], 1)
        self.assertEqual([s["kind"] for s in p["signals"] if s["explicit"]], ["revert"])
        self.assertTrue(all(s["view"] == "friction" for s in p["signals"]))
        self.assertEqual(p["cost"][wk]["tokens"], 1100)
        self.assertEqual(p["cost"][wk]["weighted"], 1000 + 500)
        self.assertNotIn("revert", json.dumps(p["weeks"]))               # friction is not folded into outcomes
        v = ws.view(self.root)
        row = v["weeks"][0]
        self.assertEqual(set(row) - {"week", "evidence"}, {"outcomes", "friction", "intent", "awaiting", "cost"})
        self.assertFalse(any(k in json.dumps(v) for k in ('"score"', '"composite"', '"health"')))
        self.assertEqual([s["evidence"]["type"] for s in row["friction"]], ["commit"])

    def test_a_sparse_week_says_too_little_evidence_and_never_green(self):
        self.plan({"R1": (" ", "Seeds")}, T0)
        p = self.project(self.build())
        self.assertFalse(ws.evidence(p["weeks"][ws.week(T0)]))
        self.assertEqual(p["concerns"], [])
        html = board.warnings_html(self.root)
        self.assertIn("too little evidence", html)
        self.assertNotIn("no warning sign", html)
        self.assertNotIn("reached", html)
        self.assertEqual(board.warnings_html(self.root, notice_only=True), "")

    def test_a_concern_only_past_the_thresholds_with_its_cases(self):
        items = {f"R{i}": (" ", f"Bed {i}") for i in range(1, 40)}
        self.plan(items, T0 - WEEK)
        n = 1
        for w in range(10):                           # ten quiet weeks of steady work, then four with many reverts
            for _ in range(3):
                items[f"R{n}"] = ("x", items[f"R{n}"][1])
                n += 1
            self.plan(items, T0 + w * WEEK + 3600, msg=f"week {w}")
            if w >= 6:
                for k in range(2):
                    self.commit(f"Revert bed {w}-{k}", T0 + w * WEEK + 7200 + k)
        now = T0 + 9 * WEEK + 86400
        p = self.project(self.build(now=now))
        self.assertEqual([c["kind"] for c in p["concerns"]], ["friction"])
        self.assertIn("2x", p["concerns"][0]["text"])
        self.assertEqual(len(p["concerns"][0]["signals"]), 8)
        notice = board.warnings_html(self.root, notice_only=True)
        self.assertIn("Warning sign:", notice)
        p2 = self.project(self.build(now=T0 + 5 * WEEK + 86400))    # too few earlier weeks: no comparison at all
        self.assertEqual(p2["concerns"], [])


class ReopenedTest(Base):
    def test_an_item_back_from_done_is_reopened_and_one_commit_reopening_many_is_left_to_the_labeller(self):
        self.plan({"R1": (" ", "Seeds"), "R2": (" ", "Soil")}, T0)
        self.plan({"R1": ("x", "Seeds"), "R2": ("x", "Soil")}, T0 + 3600, msg="R1 and R2 done")
        self.plan({"R1": ("~", "Seeds"), "R2": ("x", "Soil")}, T0 + 7200, msg="R1: the seeds rot, back to it")
        p = self.project(self.build())
        sig = [s for s in p["signals"] if s["kind"] == "reopened"]
        self.assertEqual([(s["item"], s["explicit"]) for s in sig], [("R1", True)])
        self.assertEqual(sig[0]["evidence"]["type"], "roadmap")
        self.assertIn("[~] R1 Seeds", sig[0]["evidence"]["line"])
        self.assertEqual(p["weeks"][ws.week(T0)]["reopened"], 1)
        items = {f"R{i}": ("x", f"Bed {i}") for i in range(3, 7)}
        self.plan(dict(items), T0 + WEEK)
        self.plan({k: (" ", v[1]) for k, v in items.items()}, T0 + WEEK + 60, msg="Reformat the plan")
        p = self.project(self.build())
        bulk = [s for s in p["signals"] if s["kind"].startswith("reopened with others")]
        self.assertEqual(len(bulk), 4)
        self.assertFalse(any(s["explicit"] for s in bulk))

    def test_a_helper_seat_on_a_shared_plan_does_not_repeat_the_leads_signals(self):
        from colony import lead
        self.plan({"R1": (" ", "Seeds")}, T0)
        self.plan({"R1": ("x", "Seeds")}, T0 + 3600, msg="R1 done")
        self.plan({"R1": ("~", "Seeds")}, T0 + 7200, msg="R1: the seeds rot, back to it")
        seat = self.base / "garden-helper"
        subprocess.run(["git", "clone", "-q", str(self.root), str(seat)], check=True, capture_output=True)
        with patch.object(lead, "plan_root", return_value=self.root):
            rep = ws.build(roots=[self.root, seat], now=T0 + 30 * WEEK, scan=False)
        self.assertEqual([s["kind"] for s in self.project(rep)["signals"]], ["reopened"])
        self.assertEqual(rep["projects"][str(seat)]["signals"], [], "the same history, counted in the plan's folder only")
        rep = ws.build(roots=[self.root, seat], now=T0 + 30 * WEEK, scan=False)
        self.assertEqual([s["kind"] for s in rep["projects"][str(seat)]["signals"]], ["reopened"],
                         "two projects with plans of their own each keep theirs")

    def test_colonys_own_answer_tagged_dev_is_not_a_dev_report(self):
        here = Path(ws.__file__).resolve().parent.parent.name
        mail = [dict(type="message", dev=True, id="m1", at=iso(T0), text="The pause notice was wrong", **{"from": "garden"}),
                dict(type="message", dev=True, id="m2", at=iso(T0 + 60), text="Fixed and deployed", **{"from": here})]
        with patch.object(ws, "jsonl", return_value=mail):
            sigs = ws.dev_reports({"garden": self.root, here: self.base / here})
        self.assertEqual([s["evidence"]["ref"] for s in sigs], ["m1"])


class LabelTest(Base):
    def setUp(self):
        super().setUp()
        self.plan({"R1": (" ", "Seeds"), "R2": (" ", "Soil"), "R3": (" ", "Water")}, T0)
        self.plan({"R1": ("x", "Seeds"), "R2": ("x", "Soil"), "R3": (" ", "Water")}, T0 + 3600, msg="R1 done")
        self.commit("R1: fix the seed tray again", T0 + 2 * 86400)

    def test_each_label_keeps_its_ruler_and_is_never_relabelled(self):
        rep = self.build()
        lab = FakeLabeller("haiku-a", "defect_regression")
        out = ws.batch(rep, labeller_for=lambda root: lab, new_only=False, now=T0 + 31 * WEEK)
        self.assertEqual(out["runs"][0]["labelled"], len(ws.latest_labels()))
        rows = [json.loads(x) for x in (ws.folder() / "labels.jsonl").read_text().splitlines()]
        case = next(r for r in rows if not r["sample"])
        for k in ("rubric_version", "packet_hash", "model", "picked", "family", "batch", "at"):
            self.assertTrue(case.get(k), k)
        self.assertEqual(case["model"], "haiku-a-20260101")          # the model the program resolved, not the pick
        self.assertEqual(case["rubric_version"], ws.RUBRIC_VERSION)
        self.assertTrue(any(r["sample"] and r["kind"] == "clean sample" for r in rows))    # R2 looked clean
        self.assertEqual(out["runs"][0]["cost"], 0.001)
        before = (ws.folder() / "labels.jsonl").read_text()
        other = FakeLabeller("haiku-a", "no_problem")
        self.assertEqual(ws.batch(rep, labeller_for=lambda root: other, new_only=False), {"skipped": "nothing new"})
        self.assertEqual((ws.folder() / "labels.jsonl").read_text(), before)
        self.assertEqual(other.calls, 0)
        p = ws.view(self.root)
        counted = [s for r in p["weeks"] for s in r["friction"]]
        self.assertEqual([s["kind"] for s in counted], ["finished item worked again"])   # labelled a defect: counted

    def test_a_new_labeller_rereads_the_anchors_first_and_a_moved_judgement_is_a_break(self):
        rep = self.build()
        first = FakeLabeller("haiku-a", "defect_regression")
        ws.batch(rep, labeller_for=lambda root: first, new_only=False, now=T0 + 31 * WEEK)
        self.assertTrue(ws.anchors())
        self.assertFalse(any(r["break"] for r in ws.rulers()))
        self.commit("R2: soil rebuilt, the mix was wrong", T0 + 3 * 86400)
        rep = self.build()
        swapped = FakeLabeller("haiku-b", "changed_intent")
        ws.batch(rep, labeller_for=lambda root: swapped, new_only=False, now=T0 + 32 * WEEK)
        last = ws.rulers()[-1]
        self.assertTrue(last["break"])
        self.assertEqual(last["picked"], "haiku-b")
        self.assertEqual(last["anchors"], len(ws.anchors()))
        self.assertEqual({m["was"] for m in last["moved"]}, {"defect_regression"})
        self.assertEqual({m["now"] for m in last["moved"]}, {"changed_intent"})
        self.assertEqual(swapped.calls, 2)                       # the anchors, then the new case
        old = [r for r in map(json.loads, (ws.folder() / "labels.jsonl").read_text().splitlines())
               if r["picked"] == "haiku-a"]
        self.assertTrue(old and all(r["label"] == "defect_regression" for r in old))    # never relabelled
        self.assertIn("Ruler breaks", board.warnings_html(self.root))

    def test_the_same_labeller_again_is_no_break_and_a_steady_one_reads_anchors_once(self):
        rep = self.build()
        lab = FakeLabeller("haiku-a", "defect_regression")
        ws.batch(rep, labeller_for=lambda root: lab, new_only=False, now=T0 + 31 * WEEK)
        self.commit("R2: soil rebuilt, the mix was wrong", T0 + 3 * 86400)
        same = FakeLabeller("haiku-a", "defect_regression")
        ws.batch(self.build(), labeller_for=lambda root: same, new_only=False, now=T0 + 32 * WEEK)
        self.assertEqual(same.calls, 1)
        self.assertFalse(any(r["break"] for r in ws.rulers()))

    def test_the_batch_is_skipped_when_nothing_new_arrived(self):
        self.build(now=T0 + 30 * WEEK)                  # the baseline: what's in history now is labelled by sample only
        lab = FakeLabeller("haiku-a", "unknown")
        self.assertEqual(ws.batch(ws.report(), labeller_for=lambda root: lab), {"skipped": "nothing new"})
        self.assertEqual(lab.calls, 0)
        self.assertFalse((ws.folder() / "runs.jsonl").exists())
        self.commit("R1: the seed tray broke again", int(time.time()) + 60)
        rep = self.build(now=time.time() + 120)
        self.assertEqual(sum(len(v) for v in ws.pending(rep).values()), 1)
        ws.batch(rep, labeller_for=lambda root: lab)
        self.assertEqual(lab.calls, 1)

    def test_the_answer_is_parsed_and_anything_else_is_unknown(self):
        said = ws.parse('Here: [{"case": "a", "label": "defect_regression", "why": "broke it"}, '
                        '{"case": "b", "label": "great"}] done')
        self.assertEqual(said, {"a": ("defect_regression", "broke it"), "b": ("unknown", "")})
        self.assertEqual(ws.parse("no json"), {})


class ExposureTest(Base):
    def test_items_carry_the_mechanisms_they_ran_under(self):
        self.plan({"R1": (" ", "Seeds")}, T0)
        self.plan({"R1": ("~", "Seeds")}, T0 + 3600)
        self.plan({"R1": ("x", "Seeds")}, T0 + 3 * 86400)
        board.append(self.root, "context-history.jsonl", {"at": iso(T0 + 86400).replace("+00:00", "Z"),
                                                          "session": "s1", "status": "topic closed"})
        p = self.project(self.build())
        x = next(e for e in p["exposure"] if e["item"] == "R1")
        self.assertEqual(x["topic_closing"], {"ran": True, "closes": 1, "conversations": ["s1"]})
        self.assertIn("guidance", x)
        self.assertEqual(ws.report()["cannot_show"], ws.CANNOT_SHOW)



class VerdictTest(Base):
    """The person's verdict at each finished milestone: the one view that can show the work getting better."""

    def roadmap(self, *states):
        lines = ["# Garden", ""]
        for n, st in enumerate(states, 1):
            lines += [f"## M{n} — Part {n}", "", f"- [{st}] R{n} Item {n}", ""]
        (self.root / "ROADMAP.md").write_text("\n".join(lines))

    def test_a_milestone_newly_finished_is_asked_about_once_in_words_that_never_change(self):
        self.roadmap("x", " ")
        self.assertEqual(ws.ask_verdicts(self.root), [], "what was finished before the first look isn't asked")
        self.assertEqual(ws.ask_verdicts(self.root), [])
        self.roadmap("x", "x")
        [g] = ws.ask_verdicts(self.root)
        self.assertEqual(g["question"], f"garden finished M2, Part 2. {ws.VERDICT_QUESTION}")
        self.assertEqual((g["options"], g["default"], g["verdict"]), (list(ws.VERDICTS), ws.NO_VERDICT, "M2"))
        self.assertEqual(ws.ask_verdicts(self.root), [], "asked once")
        self.assertEqual([x["id"] for x in board.gates(self.root)], [g["id"]])

    def test_a_shared_plan_is_asked_about_by_its_own_folder_only(self):
        from colony import lead
        self.roadmap(" ")
        with patch.object(lead, "plan_root", return_value=self.base / "elsewhere"):
            ws.ask_verdicts(self.root)
            self.roadmap("x")
            self.assertEqual(ws.ask_verdicts(self.root), [])
        self.assertFalse((self.root / ".board" / "verdicts.json").exists())

    def test_a_skipped_verdict_stands_quietly_and_counts_for_nothing(self):
        self.roadmap(" ")
        ws.ask_verdicts(self.root)
        self.roadmap("x")
        [g] = ws.ask_verdicts(self.root)
        with patch.object(board, "stands_at", return_value=0), patch.object(board, "due", return_value=True):
            self.assertEqual([x["id"] for x in board.stand_defaults(self.root)], [g["id"]])
        [after] = board.gates(self.root)
        self.assertEqual((after["answer"], after["answered_by"]), (f"Default stood (skipped): {ws.NO_VERDICT}", "colony"))
        self.assertEqual(board.notes(self.root), [], "no note for the agent")
        self.assertEqual(ws.verdicts(self.root), [])

    def test_a_verdict_asked_again_stays_the_milestones_and_the_reply_it_answers_counts_for_nothing(self):
        # Memory Research's report: a reply asking for a fuller brief was taken as M3's verdict, and the re-ask had no tag
        self.roadmap(" ")
        ws.ask_verdicts(self.root)
        self.roadmap("x")
        [g] = ws.ask_verdicts(self.root)
        board.answer_gate(self.root, g["id"], "I need a fuller brief before I can judge")
        again = board.add_gate(self.root, g["question"], reframes=g["id"], told="The brief: ...", options=ws.VERDICTS)
        self.assertEqual(again["verdict"], "M1", "asked again, still M1's verdict")
        self.assertEqual(ws.verdicts(self.root), [], "the request for a brief isn't a verdict")
        board.answer_gate(self.root, again["id"], ws.VERDICTS[0])
        self.assertEqual([(v["milestone"], v["verdict"], v["gate"]) for v in ws.verdicts(self.root)],
                         [("M1", ws.VERDICTS[0], again["id"])])

    def test_the_persons_verdict_reaches_the_agent_and_the_weeks_outcomes(self):
        self.roadmap(" ")
        ws.ask_verdicts(self.root)
        self.roadmap("x")
        [g] = ws.ask_verdicts(self.root)
        board.answer_gate(self.root, g["id"], "Worse: the forms got cluttered")
        [v] = ws.verdicts(self.root)
        self.assertEqual((v["milestone"], v["verdict"], v["words"]), ("M1", "Worse", "the forms got cluttered"))
        self.assertTrue(any("forms got cluttered" in n["text"] for n in board.notes(self.root)), "the agent hears it")
        self.git("add", "-A")
        self.git("commit", "-qm", "plan", at=T0)
        rep = self.build(now=time.time(), scan=False)
        wk = ws.week(g["at"])
        self.assertEqual([x["verdict"] for x in self.project(rep)["weeks"][wk]["verdicts"]], ["Worse"])
        with patch.object(ws, "report", return_value=rep):
            row = next(r for r in ws.view(self.root, rep)["weeks"] if r["week"] == wk)
        self.assertEqual([x["milestone"] for x in row["outcomes"]["verdicts"]], ["M1"], "an outcome, beside the rest")
        with patch.object(ws, "report", return_value=rep):
            self.assertIn("your verdict: M1 Worse (the forms got cluttered)", board.warnings_html(self.root))


if __name__ == "__main__":
    unittest.main()
