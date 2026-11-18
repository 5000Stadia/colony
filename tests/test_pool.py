"""The subscriptions as one pool (R169): a helper tier runs on whichever program does it best for what it costs of
that program's week; the agent's own start of it is caught, the brief runs there, and the report comes back as a note."""
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from colony import board, hand, pool, providers, selection, tokens, usage


def pick(model, effort, score, cost):
    return {"model": model, "effort": effort, "evidence": {"score": score, "cost": cost}}


class PoolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, COLONY_BOARD_HOME=self.tmp.name)
        env.start()
        self.addCleanup(env.stop)
        self.root = Path(self.tmp.name) / "proj"
        (self.root / ".board").mkdir(parents=True)
        self.now = time.time()
        # Nov 17: Claude's week held about $3,000 of work at API prices, Codex's about $680
        self.weeks = {"claude": (50, self.now + 3.5 * 86400), "codex": (50, self.now + 3.5 * 86400)}
        self.holds = {"claude": 3000.0, "codex": 680.0}
        self.home = pick("claude-opus-5-5", "high", 51.8, 3.47)
        self.other = pick("gpt-6.2-sol", "medium", 52.7, 0.41)
        for target, name, value in (
                (providers, "of", lambda root: providers.PROVIDERS["claude"]),
                (providers, "usable", lambda p: True),
                (pool, "week", lambda key: self.weeks[key]),
                (pool, "holds", lambda key, now=None: self.holds.get(key)),
                (usage, "week_limits", lambda root: {}),
                (selection, "helper", lambda root, tier: dict(self.home, own=False)),
                (selection, "pin_of", lambda root, role: None),
                (selection, "auto", lambda family, role, root=None: self.other)):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_a_routine_helper_goes_where_it_takes_less_of_a_week_at_no_less_intelligence(self):
        got = pool.choose(self.root, "routine", self.now)
        self.assertEqual((got["program"], got["model"], got["effort"]), ("codex", "gpt-6.2-sol", "medium"))
        self.assertIn("as much of its week", got["why"])

    def test_it_stays_home_where_the_other_week_is_dearer_less_smart_or_kept(self):
        self.holds["codex"] = 300.0                      # a week that holds less: the same task is a bigger share
        self.assertEqual(pool.choose(self.root, "routine", self.now)["program"], "claude")
        self.holds["codex"] = 680.0
        self.weeks["codex"] = (92, self.now + 3.5 * 86400)
        self.assertEqual(pool.choose(self.root, "routine", self.now)["program"], "claude", "kept for consultations")
        self.weeks["codex"] = (50, self.now + 3.5 * 86400)
        self.other = pick("gpt-6.2-sol", "low", 49.0, 0.2)
        self.assertEqual(pool.choose(self.root, "routine", self.now)["program"], "claude", "never less smart")
        self.other = pick("gpt-6.2-sol", "medium", 52.7, 0.41)
        with patch.object(usage, "week_limits", lambda root: {"codex": 40}):
            self.assertEqual(pool.choose(self.root, "routine", self.now)["program"], "claude", "the project's own limit")
        self.assertEqual(pool.choose(self.root, "rote", self.now)["program"], "claude", "small tasks stay home")
        with patch.object(selection, "pin_of", lambda root, role: {"model": "claude-opus-5-5"}):
            self.assertEqual(pool.choose(self.root, "routine", self.now)["program"], "claude", "a chosen model is theirs")
        self.holds["claude"] = None
        self.assertEqual(pool.choose(self.root, "routine", self.now)["program"], "claude", "unmeasured: no move")

    def test_a_week_ahead_of_its_pace_weighs_dearer(self):
        start = self.now - 3.5 * 86400
        self.other = pick("gpt-6.2-sol", "high", 53.5, 0.7)     # a little the cheaper by its week, at an even pace
        self.weeks["codex"] = (50, start + pool.WEEK)
        self.assertEqual(pool.choose(self.root, "routine", self.now)["program"], "codex")
        self.weeks["codex"] = (95 - 6, start + pool.WEEK)    # far ahead at half its week, but under the reserve
        self.assertGreater(pool.pressure("codex", self.now), 1.5)
        self.assertEqual(pool.choose(self.root, "routine", self.now)["program"], "claude")
        self.weeks["codex"] = (10, start + pool.WEEK)        # far behind: cheaper still
        self.assertLess(pool.pressure("codex", self.now), 0.5)
        self.assertEqual(pool.choose(self.root, "routine", self.now)["program"], "codex")

    def test_the_agents_start_of_the_helper_is_caught_and_the_brief_runs_there(self):
        payload = {"tool_name": "Agent", "cwd": str(self.root), "tool_input": {
            "subagent_type": "colony-routine", "description": "Build the export", "prompt": "Build the CSV export."}}
        started = MagicMock()
        out = io.StringIO()
        with patch.object(board, "root_of", lambda path=".": self.root), patch("subprocess.Popen", started):
            hand.hook(io.StringIO(json.dumps(payload)), out)
        said = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual(said["permissionDecision"], "deny")
        self.assertIn("ran this routine helper on Codex", said["permissionDecisionReason"])
        self.assertIn("comes to you as a note", said["permissionDecisionReason"])
        job = json.loads(next(hand.folder(self.root).glob("*.json")).read_text())
        self.assertEqual((job["program"], job["model"], job["prompt"]), ("codex", "gpt-6.2-sol", "Build the CSV export."))
        self.assertTrue(started.call_args.kwargs["start_new_session"], "the run outlives the hook")
        for other in ({"subagent_type": "Explore", "prompt": "Look"}, {"subagent_type": "bf-builder", "prompt": "Build"}):
            out = io.StringIO()
            with patch.object(board, "root_of", lambda path=".": self.root):
                hand.hook(io.StringIO(json.dumps(dict(payload, tool_input=other))), out)
            self.assertEqual(out.getvalue(), "", "a project's own helpers run as they are")
        out = io.StringIO()
        hand.hook(io.StringIO("garbage"), out)
        self.assertEqual(out.getvalue(), "", "any error lets the helper start")

    def test_a_handed_run_reports_to_the_agent_as_a_note(self):
        choice = dict(program="codex", model="gpt-6.2-sol", effort="medium", why="cheaper")
        job = hand.start(self.root, "routine", choice, {"prompt": "Build it.", "description": "Build"}, str(self.root),
                         popen=MagicMock())
        seen = {}

        def codex(cmd, **kw):
            seen["cmd"] = cmd
            Path(cmd[cmd.index("-o") + 1]).write_text("Built it; tests pass.")
            proc = MagicMock(returncode=0)
            proc.stdin.write = lambda text: seen.setdefault("brief", text)
            return proc
        with patch.object(board, "project_settings", lambda root: ({"permissions": "all"}, {})):
            done = hand.run(self.root, job["id"], popen=codex)
        self.assertEqual(done["state"], "done")
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", seen["cmd"])
        self.assertEqual(seen["cmd"][seen["cmd"].index("-C") + 1], str(self.root))
        self.assertTrue(seen["brief"].endswith("Build it."))
        self.assertIn("CLAUDE.md", seen["brief"], "the project's instructions, named for the other program")
        note = board.notes(self.root)[-1]
        self.assertEqual(note["kind"], "helper")
        self.assertIn("Built it; tests pass.", note["text"])


class LedgerTest(unittest.TestCase):
    def test_a_programs_spend_is_priced_by_model(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, COLONY_BOARD_HOME=home), \
                patch.object(selection.bench, "token_price", lambda m: {"claude-opus-5-5": (4, 20), "gpt-6.2-sol": (1, 8)}.get(m)):
            hour = int(time.time()) // 3600 * 3600
            state = {"files": {}, "hours": {}, "v": 3}
            tokens._add_model(state, hour, "claude", "claude-opus-5-5", 1_000_000, 10_000_000, 1_000_000, 100_000)
            tokens._add_model(state, hour, "codex", "gpt-6.2-sol", 1_000_000, 0, 0, 1_000_000)
            tokens._save(state)
            self.assertAlmostEqual(tokens.dollars("claude", hour - 10), 4 + 4 + 5 + 2)
            self.assertAlmostEqual(tokens.dollars("codex", hour - 10), 1 + 8)
            self.assertEqual(tokens.dollars("claude", hour - 10, model="fable"), 0)

    @patch.dict(os.environ, COLONY_BOARD_HOME="/nonexistent-colony-test-home")
    def test_claude_consoles_get_the_helper_hook_beside_the_shell_one(self):
        entries = providers.hook_entries(providers.get("claude"))["PreToolUse"]
        self.assertEqual({e["matcher"] for e in entries}, {"Bash", "Agent|Task"})
        self.assertEqual(providers.get("codex").hand_command("m", "low", "/w", "/o", "plan")[:2], ["codex", "exec"])


if __name__ == "__main__":
    unittest.main()
