"""The cleanup check (R139): transcripts read on from a cursor for both programs, reading counted per file and carried
until compaction, the payback arithmetic and its bar, and the check at finished items: once, at the seam, recorded."""
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from colony import board, cleanup, providers

DAY = 86400
PLAN = """# Garden

## M1 — Start

- [x] R1 Seeds
- [ ] R2 Soil
- [ ] R3 Water
- [ ] R4 Light
- [ ] R5 Pots

## M2 — Grow

- [ ] R6 Prune
- [ ] R7 Harvest
- [ ] R8 Store
"""


def stamp(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


def claude_rows(cwd, t, path, reads):
    """A Claude Code session: one reply (written as two parts carrying one usage), then a Read and a Bash per read."""
    rows = [{"type": "user", "cwd": cwd, "timestamp": stamp(t), "message": {"content": "start"}}]
    for k, (inp, tool) in enumerate(reads):
        mid = f"m{k}"
        for part in range(2):
            content = [{"type": "tool_use", "id": f"t{k}", "name": tool[0], "input": tool[1]}] if part and tool else []
            rows.append({"type": "assistant", "cwd": cwd, "timestamp": stamp(t + k), "message": {
                "id": mid, "model": "claude-opus-5-5", "content": content,
                "usage": {"input_tokens": inp, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}})
        if tool:
            rows.append({"type": "user", "cwd": cwd, "timestamp": stamp(t + k), "message": {"content": [
                {"type": "tool_result", "tool_use_id": f"t{k}", "content": "x" * tool[2]}]}})
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(json.dumps(r) + "\n" for r in rows))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        env = patch.dict(os.environ, {"COLONY_BOARD_HOME": str(self.base / "home"), "CLAUDE_CONFIG_DIR": str(self.base / "claude"),
                                      "CODEX_HOME": str(self.base / "codex"), "COLONY_CODEX_SOURCE_HOME": str(self.base / "codex")})
        env.start()
        self.addCleanup(env.stop)
        self.root = self.base / "garden"
        (self.root / ".board").mkdir(parents=True)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "guide.md").write_text("guide line\n" * 400)
        (self.root / "app.py").write_text("x = 1\n" * 2000)
        (self.root / "ROADMAP.md").write_text(PLAN)
        board.save_registry(dict(roots=[], projects=[str(self.root)], settings={"messaging": False}))
        for args in (("init", "-q", "-b", "main"), ("config", "user.name", "Fixture"),
                     ("config", "user.email", "fixture@users.noreply.github.com"), ("add", "-A"), ("commit", "-qm", "Foundation")):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)


class TranscriptStepsTest(Base):
    def test_claude_steps_count_a_reply_once_and_read_on_from_the_cursor(self):
        path = self.base / "claude" / "projects" / "-garden" / "s.jsonl"
        claude_rows(str(self.root), 1000, path, [(100, ("Read", {"file_path": str(self.root / "app.py")}, 400)),
                                                 (200, ("Bash", {"command": "cat docs/guide.md"}, 800))])
        st = {}
        steps = providers.get("claude").transcript_steps(path, st)
        self.assertEqual([s for s in steps if s[0] == "call"], [("call", 1000.0, 100), ("call", 1001.0, 200)])
        self.assertEqual([s[2:] for s in steps if s[0] == "tool"],
                         [("read", str(self.root / "app.py"), 400), ("shell", "cat docs/guide.md", 800)])
        self.assertEqual(providers.get("claude").transcript_steps(path, st), [])    # nothing new
        with path.open("a") as fh:
            fh.write(json.dumps({"type": "assistant", "timestamp": stamp(1005), "message": {
                "id": "m9", "content": [], "usage": {"input_tokens": 5}}}) + "\n" + '{"type": "assist')   # half a line
        self.assertEqual(providers.get("claude").transcript_steps(path, st), [("call", 1005.0, 5)])

    def test_codex_steps_skip_a_forked_childs_inherited_history(self):
        path = self.base / "codex" / "sessions" / "r.jsonl"
        path.parent.mkdir(parents=True)

        def row(kind, payload, t=2000):
            return json.dumps({"timestamp": stamp(t), "type": kind, "payload": payload}) + "\n"
        tc = lambda n: row("event_msg", {"type": "token_count", "info": {"last_token_usage": {"input_tokens": n}}})
        call = lambda cid, cmd: row("response_item", {"type": "custom_tool_call", "name": "exec", "call_id": cid,
                                                      "input": f'text(await tools.exec_command({{cmd:"{cmd}"}}))'})
        out = lambda cid, n: row("response_item", {"type": "custom_tool_call_output", "call_id": cid,
                                                   "output": [{"type": "input_text", "text": "y" * n}]})
        path.write_text(row("session_meta", {"cwd": str(self.root), "forked_from_id": "parent"}) + call("a", "cat app.py")
                        + out("a", 999) + tc(50_000)
                        + row("response_item", {"type": "message", "role": "developer", "content": [
                            {"type": "input_text", "text": "You are a newly spawned child agent."}]})
                        + call("b", "sed -n 1,20p docs/guide.md") + out("b", 120) + tc(700))
        steps = providers.get("codex").transcript_steps(path, {})
        self.assertEqual(steps, [("cwd", str(self.root)), ("tool", 2000.0, "shell", "sed -n 1,20p docs/guide.md", 120),
                                 ("call", 2000.0, 700)])


class FeedTest(Base):
    def test_reading_is_carried_until_compaction_and_start_reads_are_counted(self):
        st, data, paths = {"cwd": str(self.root)}, {"days": {}, "hours": {}}, cleanup._Paths()
        t = 10 * DAY
        root = str(self.root)
        steps = [("call", t, 1000), ("tool", t, "read", f"{root}/.claude/worktrees/w1/docs/guide.md", 400),
                 ("call", t + 1, 2000), ("call", t + 2, 3000),
                 ("tool", t + 2, "shell", "python3 -m unittest tests.test_x", 4000),       # a test run reads nothing
                 ("tool", t + 2, "search", "", 80),                                        # a search over the tree
                 ("call", t + 3, 1000),                                                    # compacted
                 ("call", t + 4, 1200)]
        cleanup.feed(st, data, steps, root, paths)
        day = data["days"][str(t)]
        self.assertEqual(day["files"]["docs/guide.md"][0], 100)        # 400 chars, 100 tokens, read once
        self.assertEqual(day["files"]["docs/guide.md"][1], 200)        # carried by the two calls before compaction
        self.assertEqual(day["files"]["docs/guide.md"][2], 1)          # read at the session's start
        self.assertEqual(day["tree"], 0)                               # the search was dropped at compaction, unread
        self.assertEqual((day["input"], day["sessions"]), (8200, 1))
        cleanup.feed(st, data, [("call", t + 5, 1500)], root, paths)   # the next look: nothing carried from before
        self.assertEqual(data["days"][str(t)]["files"]["docs/guide.md"][1], 200)

    def test_carried_cost_spans_looks_and_lands_on_the_day_it_was_paid(self):
        st, data, paths = {"cwd": str(self.root)}, {"days": {}, "hours": {}}, cleanup._Paths()
        t, root = 20 * DAY, str(self.root)
        cleanup.feed(st, data, [("call", t, 100), ("tool", t, "shell", "head -50 app.py && cat docs/guide.md", 800)], root, paths)
        cleanup.feed(st, data, [("call", t + DAY, 200), ("call", t + DAY + 1, 300)], root, paths)
        self.assertEqual(data["days"][str(t + DAY)]["files"]["app.py"][1], 200)     # 100 tokens x 2 later calls
        self.assertEqual(data["days"][str(t)]["files"]["app.py"][1], 0)


def fake_measure(sessions=40, weeks=2.0, files=None, tree=0.0):
    files = files or {}
    return {"files": files, "tree": tree, "carried": tree + sum(v[1] for v in files.values()), "input": 5e9,
            "sessions": sessions, "weeks": weeks}


class ArithmeticTest(unittest.TestCase):
    def test_start_up_documents_pay_and_a_big_busy_file_does_not(self):
        m = fake_measure(files={"NOW.md": [1e6, 60e6, 20], "method.md": [1e6, 40e6, 15], "board.py": [5e6, 300e6, 30]},
                         tree=600e6)
        sizes = {"NOW.md": (450, 21_000), "method.md": (1100, 18_000), "board.py": (12_000, 207_000)}
        size = lambda fs: tuple(map(sum, zip(*(sizes[f] for f in fs))))
        found = cleanup.assess(m, size, 30_000, ahead=50)
        by = {h["key"]: h for h in found}
        self.assertEqual(by["startup"]["files"], ["NOW.md", "method.md"])        # code read at start is no start-up doc
        self.assertTrue(by["startup"]["pays"])
        self.assertFalse(by["file:board.py"]["pays"])
        self.assertLess(by["file:board.py"]["weeks"][0], by["file:board.py"]["weeks"][1])
        self.assertEqual(found[0]["key"], "startup")
        self.assertFalse(cleanup.assess(m, size, 30_000, ahead=0.5)[0]["pays"])   # the work ends before it pays back

    def test_too_little_evidence_or_too_small_a_saving_says_nothing(self):
        files = {"NOW.md": [1e6, 60e6, 20]}
        size = lambda fs: (450, 21_000)
        self.assertTrue(cleanup.assess(fake_measure(files=files, tree=600e6), size, 30_000, 50)[0]["pays"])
        self.assertEqual(cleanup.assess(fake_measure(files=files, tree=600e6, weeks=0.5), size, 30_000, 50), [])
        self.assertEqual(cleanup.assess(fake_measure(files=files, tree=600e6, sessions=5), size, 30_000, 50), [])
        tiny = {"NOW.md": [1e6, 10e6, 20]}                                           # 1.6% of reading: 0.7% saved
        self.assertFalse(cleanup.assess(fake_measure(files=tiny, tree=600e6), size, 30_000, 50)[0]["pays"])

    def test_horizon_grows_with_the_plan_and_a_growing_plan_lasts_as_long_again(self):
        self.assertAlmostEqual(cleanup.horizon(10, 8, 4), 10 / 0.5 / 2)            # half an item added per item done
        self.assertAlmostEqual(cleanup.horizon(10, 8, 40), 10 / 0.2 / 2)           # growth counted at most 5x
        self.assertAlmostEqual(cleanup.horizon(2, 40, 38, age=6 * 7 * DAY), 6)     # few open, still growing: its age
        self.assertAlmostEqual(cleanup.horizon(2, 40, 0, age=6 * 7 * DAY), 0.2)    # winding down: what's open
        self.assertEqual(cleanup.per_line(1e9, 50), cleanup.PER_LINE)
        self.assertEqual(cleanup.per_line(1e9, 10_000), 100_000)


class CheckTest(Base):
    def ledger(self, now, guide=60e6, rest=600e6, complete=True):
        days = {}
        for k in range(14):
            d = int((now - (k + 1) * DAY) // DAY * DAY)
            days[str(d)] = {"input": 1e7, "sessions": 4, "tree": rest / 14, "files": {"docs/guide.md": [1e5, guide / 14, 2]}}
        hours = {str(int(now // 3600 * 3600)): 7_000_000, str(int(now - 3600 * 5)): 9_000_000}   # in the pass, before it
        state = {"files": {}, "projects": {str(self.root): {"days": days, "hours": hours}}}
        if complete:
            state["complete"] = now
        board.save_json(cleanup.ledger_path(), state)

    def finish(self, *ids):
        text = (self.root / "ROADMAP.md").read_text()
        for i in ids:
            text = text.replace(f"- [ ] {i} ", f"- [x] {i} ")
        (self.root / "ROADMAP.md").write_text(text)

    def notes(self):
        return [n for n in board.notes(self.root) if n.get("kind") == "cleanup"]

    def test_it_speaks_once_at_a_finished_item_and_records_the_suggestion(self):
        now = time.time()
        self.ledger(now)
        self.assertIsNone(cleanup.check(self.root, now))                # first look: what was done isn't news
        self.finish("R2")
        pick = cleanup.check(self.root, now)
        self.assertEqual(pick["files"], ["docs/guide.md"])
        [note] = self.notes()
        self.assertTrue(note.get("quiet"))
        for part in ("docs/guide.md", "of this project's reading", "pay back in about", "between items",
                     "one gate", "skip it for now"):
            self.assertIn(part, note["text"])
        said = cleanup.history(self.root)["startup"]
        self.assertEqual((said["note"], said["after"], said["files"]), (note["id"], "R2", ["docs/guide.md"]))
        self.finish("R3")
        self.assertIsNone(cleanup.check(self.root, now + 60))           # said once until it changes
        self.assertEqual(len(self.notes()), 1)

    def test_a_milestones_end_a_few_items_away_is_the_seam_it_waits_for(self):
        now = time.time()
        self.ledger(now)
        cleanup.check(self.root, now)
        self.finish("R2", "R3")                                          # two left in M1: wait for its end
        self.assertIsNone(cleanup.check(self.root, now))
        self.assertEqual(self.notes(), [])
        self.finish("R4", "R5")
        self.assertIsNotNone(cleanup.check(self.root, now))
        self.assertIn("now, at M1's end", self.notes()[0]["text"])

    def test_quiet_where_nothing_pays_and_until_the_transcripts_are_read(self):
        now = time.time()
        self.ledger(now, complete=False)
        cleanup.check(self.root, now)
        self.finish("R2")
        self.assertIsNone(cleanup.check(self.root, now))                 # the ledger is still catching up
        self.ledger(now, guide=2e6)                                      # 0.3% of reading: nothing worth a pass
        self.finish("R3")
        self.assertIsNone(cleanup.check(self.root, now))
        self.assertEqual(self.notes(), [])

    def test_the_persons_answer_a_taken_pass_and_its_measured_payback_are_kept(self):
        now = time.time()
        self.ledger(now)
        cleanup.check(self.root, now)
        self.finish("R2")
        cleanup.check(self.root, now)
        note = self.notes()[0]
        gate = board.add_gate(self.root, "Take a cleanup pass on docs/guide.md first?", why=f"cleanup {note['id']}",
                              default="Skip for now and carry on")
        board.answer_gate(self.root, gate["id"], "Take it", tell=False)
        (self.root / "docs" / "guide.md").write_text("guide line\n" * 200)       # the pass: half the size
        subprocess.run(["git", "-C", str(self.root), "commit", "-qam", "Trim the guide"], check=True, capture_output=True)
        self.finish("R3")
        cleanup.check(self.root, now + 60)
        rec = cleanup.history(self.root)["startup"]
        self.assertEqual((rec["chose"]["answer"], rec["chose"]["by"]), ("Take it", "person"))
        self.assertEqual(rec["taken"]["tokens"], len("guide line\n") * 200 // 4)
        later = now + 8 * DAY                                                       # a week of reading after the pass
        days = {}
        for k in range(7):
            d = int((later - (k + 1) * DAY) // DAY * DAY)
            days[str(d)] = {"input": 1e7, "sessions": 4, "tree": 600e6 / 14, "files": {"docs/guide.md": [1e5, 30e6 / 14, 2]}}
        state = cleanup.load()
        state["projects"][str(self.root)]["days"].update(days)
        board.save_json(cleanup.ledger_path(), state)
        self.finish("R4")
        cleanup.check(self.root, later)
        rec = cleanup.history(self.root)["startup"]
        self.assertLess(rec["measured"]["after"], rec["measured"]["before"])
        self.assertEqual(rec["measured"]["cost"], 7_000_000)
        self.assertIn("measured since it shrank", self.notes()[-1]["text"])
        self.assertIn("calib", json.loads((self.root / ".board" / "cleanup.json").read_text()))


    def test_a_hotspot_that_shrank_in_other_work_gives_no_pass_cost(self):
        now = time.time()
        self.ledger(now)
        cleanup.check(self.root, now)
        board.append(self.root, "cleanup.jsonl", {"type": "suggested", "key": "startup", "note": "n0", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 10 * DAY)),
                                                  "files": ["docs/guide.md"], "tokens": 1100, "weekly": 30e6, "cost": 5e6})
        (self.root / "docs" / "guide.md").write_text("guide line\n" * 100)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qam", "Rewrite"], check=True, capture_output=True)
        self.finish("R2")
        cleanup.check(self.root, now)
        self.assertIsNone(cleanup.history(self.root)["startup"]["taken"]["cost"])
        self.assertNotIn("calib", json.loads((self.root / ".board" / "cleanup.json").read_text()))


class UpdateTest(Base):
    def test_update_files_each_sessions_reading_under_its_project_and_reads_on(self):
        now = time.time()
        path = self.base / "claude" / "projects" / "-garden" / "s.jsonl"
        claude_rows(str(self.root), now - 3600, path, [(100, ("Read", {"file_path": str(self.root / "docs/guide.md")}, 400)),
                                                       (200, None), (300, None)])
        other = self.base / "claude" / "projects" / "-elsewhere" / "o.jsonl"
        claude_rows(str(self.base / "elsewhere"), now - 3600, other, [(100, ("Read", {"file_path": "/etc/hosts"}, 400))])
        self.assertTrue(cleanup.update(budget=30, now=now))
        state = cleanup.load()
        data = state["projects"][str(self.root)]
        m = cleanup.measure(data, now)
        self.assertEqual(m["files"]["docs/guide.md"][:2], [100, 200])
        self.assertEqual(set(state["projects"]), {str(self.root)})
        self.assertTrue(state["complete"])
        with path.open("a") as fh:
            fh.write(json.dumps({"type": "assistant", "cwd": str(self.root), "timestamp": stamp(now - 60), "message": {
                "id": "late", "content": [], "usage": {"input_tokens": 400}}}) + "\n")
        cleanup.update(budget=30, now=now)
        m = cleanup.measure(cleanup.load()["projects"][str(self.root)], now)
        self.assertEqual(m["files"]["docs/guide.md"][1], 300)                     # one more call carried it
        self.assertEqual(cleanup.load()["files"][str(other)]["root"], "")              # a session outside any project


if __name__ == "__main__":
    unittest.main()
