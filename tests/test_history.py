"""A project's history kept by section, with a one-line ledger (R171): each conversation cut where its program
compacted it, each section's prose stored in colony's home and titled in one line by a light model, kept before a
program forgets it; `colony history` lists, opens and searches it; items carry when they were created."""
import gzip
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from colony import board, codex_remote, context, history, providers, selection, usage
from colony.migrate import claude_key


def claude_said(kind, text, t, **extra):
    role = "user" if kind == "user" else "assistant"
    return dict(type=kind, timestamp=t, message=dict(role=role, content=text), **extra)


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        env = patch.dict(os.environ, COLONY_BOARD_HOME=str(base / "home"), CLAUDE_CONFIG_DIR=str(base / "claude"))
        env.start()
        self.addCleanup(env.stop)
        self.root = base / "plants"
        (self.root / ".board").mkdir(parents=True)
        (self.root / ".board" / "id").write_text("a1b2c3d4e5f6")
        (self.root / "ROADMAP.md").write_text("# Plants\n\n## M1 — Start\n- [x] R1 Water reminders\n- [ ] R2 Photos\n")
        self.claude_dir = base / "claude" / "projects" / claude_key(self.root.resolve())
        self.claude_dir.mkdir(parents=True)
        self.codex_home = base / "codex"
        (self.codex_home / "sessions" / "2026" / "11" / "19").mkdir(parents=True)
        for target, name, value in ((codex_remote, "home_for", lambda root: self.codex_home),
                                    (providers, "usable", lambda p: True),
                                    (selection, "resolve", lambda family, role, pin, root=None: {"model": f"{family}-light", "effort": "low"}),
                                    (selection, "pin_of", lambda root, role: None),
                                    (usage, "paused", lambda: {})):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def write_claude(self, name="0f1e2d3c-aaaa-bbbb-cccc-000000000001", rows=None, mode="w"):
        path = self.claude_dir / f"{name}.jsonl"
        with path.open(mode) as fh:
            for r in rows if rows is not None else self.claude_rows():
                fh.write(json.dumps(r) + "\n")
        return path

    def claude_rows(self):
        return [
            claude_said("user", "Let's add watering reminders (R1).", "2026-11-01T10:00:00Z"),
            claude_said("assistant", [{"type": "text", "text": "Building R1: reminders at dawn."},
                                      {"type": "tool_use", "name": "Bash", "input": {"command": "git commit -m 'R1'\nmore"}},
                                      {"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}}], "2026-11-01T10:01:00Z"),
            claude_said("user", [{"type": "tool_result", "content": "a huge output nobody said"}], "2026-11-01T10:02:00Z"),
            claude_said("user", "<system-reminder>colony's own aside</system-reminder>Make them gentle.", "2026-11-01T10:03:00Z"),
            {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-11-02T09:00:00Z"},
            claude_said("user", "Summary: reminders for R1 built and pushed.", "2026-11-02T09:00:01Z", isCompactSummary=True),
            claude_said("user", "Now photos, R2.", "2026-11-03T08:00:00Z"),
            claude_said("assistant", [{"type": "text", "text": "Starting R2 with the camera roll."}], "2026-11-03T08:01:00Z"),
        ]

    def write_codex(self):
        path = self.codex_home / "sessions" / "2026" / "11" / "19" / "rollout-2026-11-19T05-23-29-01a1f3f9-fdc2-78a3-b805-9f593896efa5.jsonl"
        rows = [{"type": "session_meta", "payload": {"cwd": str(self.root)}},
                {"type": "response_item", "timestamp": "2026-11-04T10:00:00Z",
                 "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>cwd</environment_context>"}]}},
                {"type": "response_item", "timestamp": "2026-11-04T10:00:01Z",
                 "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Check the soil sensor."}]}},
                {"type": "response_item", "timestamp": "2026-11-04T10:01:00Z",
                 "payload": {"type": "function_call", "name": "exec_command", "arguments": json.dumps({"cmd": "pytest -q"})}},
                {"type": "response_item", "timestamp": "2026-11-04T10:02:00Z",
                 "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "The sensor reads fine."}]}},
                {"type": "compacted", "timestamp": "2026-11-05T10:00:00Z",
                 "payload": {"message": "", "replacement_history": [
                     {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Kept: the sensor works."}]}]}},
                {"type": "response_item", "timestamp": "2026-11-06T10:00:00Z",
                 "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Next the pump."}]}}]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return path

    def test_each_conversation_is_cut_at_its_compactions_and_only_what_was_said_is_kept(self):
        claude, codex = self.write_claude(), self.write_codex()
        led = history.scan(self.root)
        self.assertEqual(history._name(codex, 0), "3896efa5.0")              # its uuid's random end
        self.assertEqual(sorted(led["sections"]), sorted(["0f1e2d3c.0", history._name(codex, 0)]))
        first = led["sections"]["0f1e2d3c.0"]
        self.assertEqual(first["summary"], "Summary: reminders for R1 built and pushed.")
        prose = (history.folder(self.root) / "0f1e2d3c.0.txt").read_text()
        for said in ("Let's add watering reminders", "Building R1: reminders at dawn.", "git commit -m 'R1'",
                     "Make them gentle."):
            self.assertIn(said, prose)
        for unsaid in ("a huge output", "colony's own aside", "more", "Read /x"):
            self.assertNotIn(unsaid, prose)
        self.assertEqual(led["sections"][history._name(codex, 0)]["summary"], "Kept: the sensor works.")
        theirs = (history.folder(self.root) / f"{history._name(codex, 0)}.txt").read_text()
        self.assertIn("pytest -q", theirs)
        self.assertNotIn("environment_context", theirs)
        # the history lives in colony's home, under the project's identity, never in the project
        self.assertTrue(str(history.folder(self.root)).endswith("history/a1b2c3d4e5f6"))
        self.assertFalse(list(self.root.rglob("*.txt")))
        lines = history.ledger_lines(self.root)
        self.assertTrue(any(l.startswith("0f1e2d3c.0") and "(not titled yet)" in l for l in lines))
        self.assertTrue(any(l.startswith("0f1e2d3c.1") and "open" in l for l in lines))

    def test_a_later_look_reads_only_what_was_added_and_a_late_summary_finds_its_section(self):
        path = self.write_claude()
        history.scan(self.root)
        self.write_claude(rows=[{"type": "system", "subtype": "compact_boundary", "timestamp": "2026-11-04T09:00:00Z"}], mode="a")
        led = history.scan(self.root)
        self.assertIn("0f1e2d3c.1", led["sections"])                     # the open span closed at its compaction
        self.assertEqual(led["sections"]["0f1e2d3c.1"]["summary"], "")
        self.write_claude(rows=[claude_said("user", "Summary: R2 photos begun.", "2026-11-04T09:00:01Z", isCompactSummary=True)], mode="a")
        led = history.scan(self.root)
        self.assertEqual(led["sections"]["0f1e2d3c.1"]["summary"], "Summary: R2 photos begun.")
        self.assertEqual(led["scans"][str(path)]["n"], 2)

    def test_a_light_model_titles_closed_sections_in_one_line_and_a_failure_waits(self):
        self.write_claude()
        asked = []

        def ask(brief, model, effort, folder):
            asked.append((model, effort, brief))
            return {"text": "R1 watering reminders built, gentle tone\nmore", "error": None}
        self.assertEqual(history.title_pending(self.root, ask=lambda *a: {"text": "", "error": "API Error: offline"}), 0)
        self.assertEqual(history.title_pending(self.root, ask=ask), 1)        # the open span waits for its compaction
        model, effort, brief = asked[0]
        self.assertEqual((model, effort), ("claude-light", "low"))           # the rote helper of the program it ran on
        self.assertIn("Summary written at its compaction", brief)
        self.assertIn("Let's add watering reminders", brief)
        self.assertNotIn("git commit", brief)                                # commands aren't what a title reads
        self.assertTrue(any("R1 watering reminders built, gentle tone" in l and "more" not in l
                            for l in history.ledger_lines(self.root)))

    def test_an_agent_opens_a_section_searches_them_and_places_an_item_in_time(self):
        self.write_claude()
        shown = history.show(self.root, "0f1e2d3c.0")
        self.assertIn("Summary: reminders for R1 built and pushed.", shown)
        self.assertIn("Make them gentle.", shown)
        self.assertNotIn("Building R1", shown)                               # the messages; --raw has all of it
        self.assertIn("Building R1", history.show(self.root, "0f1e2d3c.0", raw=True))
        self.assertIn("camera roll", history.show(self.root, "0f1e2d3c.1", raw=True))     # the open span, read live
        hits = history.search(self.root, "camera roll")
        self.assertEqual([h[0] for h in hits], ["0f1e2d3c.1"])
        self.assertEqual(history.search(self.root, "gentle nothing-like-this")[0][0], "0f1e2d3c.0")   # most words
        day = 86400
        times = {"R1": {"state": "done", "created": time.mktime((2026, 10, 30, 12, 0, 0, 0, 0, -1)),
                        "first": time.mktime((2026, 11, 1, 9, 0, 0, 0, 0, -1)),
                        "at": time.mktime((2026, 11, 2, 9, 0, 0, 0, 0, -1)), "worked": day}}
        with patch.object(board, "item_times", lambda root: times):
            out = history.item(self.root, "R1")
        self.assertIn("Created Oct 30 · started Nov 1 · finished Nov 2", out)
        self.assertIn("0f1e2d3c.0", out)
        with self.assertRaises(KeyError):
            history.item(self.root, "R9")

    def test_before_the_program_forgets_a_conversation_colony_keeps_it(self):
        current = self.write_claude()
        other = self.write_claude(name="9e8d7c6b-aaaa-bbbb-cccc-000000000002")
        fresh = self.write_claude(name="5a5a5a5a-aaaa-bbbb-cccc-000000000003")
        old = time.time() - 25 * 86400
        for p in (current, other):
            os.utime(p, (old, old))
        history.scan(self.root)
        with patch.object(context, "session", lambda root: {"path": str(current), "provider": "claude", "id": "x"}):
            done = history.keep(self.root)
        self.assertEqual(sorted((a, p.name) for a, p in done), [("kept", other.name), ("touched", current.name)])
        self.assertGreater(current.stat().st_mtime, time.time() - 60)       # resume and Chat keep it
        raw = history.folder(self.root) / "raw" / (other.name + ".gz")
        self.assertEqual(gzip.open(raw).read(), other.read_bytes())          # a whole copy, compressed
        self.assertIn("9e8d7c6b.1", history._load(self.root)["sections"])    # its last span closed as it stands
        self.assertLess(time.time() - fresh.stat().st_mtime, 60)
        self.assertNotIn(fresh.name, [p.name for _, p in done])
        self.assertIsNone(providers.get("codex").forgets_after)             # Codex keeps its sessions

    def test_only_the_folders_own_conversations_are_its_history(self):
        sessions = self.codex_home / "sessions" / "2026" / "11" / "19"

        def session(name, **meta):
            path = sessions / f"rollout-2026-11-19T06-00-00-{name}.jsonl"
            path.write_text(json.dumps({"type": "session_meta", "payload": dict(cwd=str(self.root), **meta)}) + "\n")
            return path
        talk = session("talk", source="cli", originator="codex-tui")
        once = session("once", source="exec", originator="codex_exec")            # a helper, consult or title
        session("thread", source={"subagent": {"thread_spawn": {"depth": 1}}}, originator="colony")
        session("call", source="vscode", originator="Claude Code")               # another program's agent calling in
        self.assertEqual(providers.get("codex").transcripts(self.root), [talk])
        sealed = {"type": "compacted", "payload": {"message": "", "replacement_history": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "[colony] You have mail."}]},
            {"type": "compaction", "encrypted_content": "gAAA"}]}}
        self.assertEqual(providers.get("codex").history_events(sealed), [("boundary", "")])   # no summary to read
        # one recorded before the rule goes, with its prose; one its program deleted keeps its sections
        led = history.scan(self.root)
        gone = str(sessions / "rollout-2026-11-19T07-00-00-gone.jsonl")
        led["scans"][str(once)] = led["scans"][gone] = dict(program="codex", inode=1, size=1, open=0, n=1)
        for sid, path in (("once.0", once), ("gone.0", gone)):
            led["sections"][sid] = dict(id=sid, program="codex", path=str(path), title="x")
            (history.folder(self.root) / f"{sid}.txt").write_text("prose")
        history._save(self.root, led)
        led = history.scan(self.root)
        self.assertNotIn("once.0", led["sections"])
        self.assertFalse((history.folder(self.root) / "once.0.txt").exists())
        self.assertIn("gone.0", led["sections"])
        # a seat working in this folder on Codex has none of the folder's Claude conversations
        self.write_claude()
        seat = Path(self.tmp.name) / "seat"
        (seat / ".board").mkdir(parents=True)
        (seat / ".board" / "id").write_text("f6e5d4c3b2a1")
        (seat / ".board" / "settings.json").write_text(json.dumps({"workdir": str(self.root), "provider": "codex"}))
        self.assertEqual({k for k, _, _ in history._sources(seat)}, {"codex"})
        self.assertIn("claude", {k for k, _, _ in history._sources(self.root)})

    def test_after_each_compaction_the_agent_is_told_where_its_history_is(self):
        text = context.historical(self.root, {"id": "j1", "carry": "x", "tail": []})
        self.assertIn("colony history", text)
        self.assertIn("colony history --item R#", text)


class ItemDatesTest(unittest.TestCase):
    def test_an_item_keeps_when_it_was_created_and_shows_its_three_dates(self):
        its = {"R1": {"state": "todo"}}
        first = board.moved({}, its, 1000.0)
        self.assertEqual(first["R1"]["created"], 1000.0)
        later = board.moved(first, {"R1": {"state": "doing"}}, 2000.0)
        done = board.moved(later, {"R1": {"state": "done"}}, 3000.0)
        self.assertEqual((done["R1"]["created"], done["R1"]["first"], done["R1"]["at"]), (1000.0, 2000.0, 3000.0))
        year = time.localtime().tm_year
        rec = {"state": "done", "created": time.mktime((year, 3, 1, 12, 0, 0, 0, 0, -1)),
               "first": time.mktime((year, 3, 2, 12, 0, 0, 0, 0, -1)), "at": time.mktime((year, 3, 4, 12, 0, 0, 0, 0, -1))}
        self.assertEqual(board.item_dates(rec), "created Mar 1 · started Mar 2 · finished Mar 4")
        self.assertEqual(board.item_dates(dict(rec, state="doing")), "created Mar 1 · started Mar 2")
        self.assertEqual(board.item_dates({"state": "done", "at": 0}), "")

    def test_records_kept_before_dating_take_the_earlier_times_git_knows(self):
        seen = {"R1": {"state": "done", "since": "x", "at": 9000.0, "worked": 0},           # noticed done late
                "R2": {"state": "doing", "since": "y", "at": 500.0, "worked": 0, "created": 400.0, "first": 500.0},
                "R3": {"state": "todo", "since": "z", "at": 700.0, "worked": 0}}            # git doesn't know it
        hist = {"R1": {"state": "done", "since": "h", "at": 3000.0, "created": 1000.0, "first": 2000.0},
                "R2": {"state": "done", "since": "k", "at": 800.0, "created": 450.0, "first": 600.0}}
        out = board.dated(seen, hist)
        self.assertEqual((out["R1"]["created"], out["R1"]["first"], out["R1"]["at"], out["R1"]["since"]),
                         (1000.0, 2000.0, 3000.0, "h"))
        self.assertEqual((out["R2"]["created"], out["R2"]["first"], out["R2"]["at"]), (400.0, 500.0, 500.0))
        self.assertEqual(out["R3"]["at"], 700.0)
        self.assertTrue(all(r["dated"] for r in out.values()))
        self.assertTrue(board.moved({}, {"R4": {"state": "todo"}}, 1.0)["R4"]["dated"])


if __name__ == "__main__":
    unittest.main()
