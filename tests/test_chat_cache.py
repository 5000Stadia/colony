"""Synthetic Codex rollouts keep conversation words and shell evidence, without raw tool output."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from colony import chat


def entry(kind, at="T01", outer="response_item", **payload):
    return {"timestamp": at, "type": outer, "payload": dict(type=kind, **payload)}


def result(call, value, at="T02", blocks=False):
    text = json.dumps(value)
    return entry("function_call_output", at, call_id=call,
                 output=[{"type": "input_text", "text": text}] if blocks else text)


def retained_size(value):
    seen = set()
    def size(obj):
        if id(obj) in seen:
            return 0
        seen.add(id(obj))
        children = [*obj.keys(), *obj.values()] if isinstance(obj, dict) else obj if isinstance(obj, (list, tuple)) else []
        return sys.getsizeof(obj) + sum(size(child) for child in children)
    return size(value)


class CodexCacheTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "rollout.jsonl"
        self.addCleanup(chat.forget, self.path)

    def write(self, rows, mode="w"):
        with self.path.open(mode) as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")

    def test_large_outputs_and_arguments_do_not_live_in_cache_but_prose_does(self):
        prose = "Visible long assistant prose. " * 4000
        rows = [entry("message", role="user", content=[{"type": "input_text", "text": "Please inspect it."}]),
                entry("message", "T02", role="assistant", content=[{"type": "output_text", "text": prose},
                                                                  {"type": "reasoning", "text": "HIDDEN_REASONING"}])]
        for i in range(12):
            rows += [entry("function_call", "T03", name="exec_command", call_id=str(i),
                           arguments=json.dumps({"cmd": "sleep 90", "unused": "x" * 20000 + "SECRET_ARGUMENT"})),
                     result(str(i), {"chunk_id": "x", "session_id": i, "exit_code": None,
                                     "output": "x" * 200000 + "SECRET_STDOUT_" + str(i)})]
        rows.append(entry("agent_message", "T04", content=[{"text": "FINAL_ANSWER\nSender: /root/helper\n" + "SECRET_REPORT" * 1000}]))
        unpruned_bytes = retained_size({"entries": rows})
        self.write(rows)
        events = chat.codex_events(self.path)
        self.assertEqual(events[:2], [("you", "Please inspect it.", "T01"), ("say", prose.strip(), "T02")])
        self.assertEqual(events[2:-1], [("step", "exec_command(sleep 90)", "T03")] * 12)
        self.assertEqual(events[-1], ("step", "helper /root/helper reported", "T04"))
        self.assertEqual([r["what"] for r in chat.codex_background(self.path)], ["sleep 90"] * 12)
        cached = chat._seen[str(self.path)]
        for secret in ("SECRET_STDOUT", "SECRET_ARGUMENT", "SECRET_REPORT", "HIDDEN_REASONING"):
            self.assertNotIn(secret, repr(cached))
        cached_bytes = retained_size(cached)
        self.assertLess(cached_bytes, len(prose.encode()) + 60000,
                        "multi-megabyte tool payloads must leave only small structured metadata")
        print(f"Synthetic Codex cache retained bytes: unpruned={unpruned_bytes}, lean={cached_bytes}")

    def test_background_cell_wait_ordered_results_polls_and_native_ends_survive(self):
        self.write([
            entry("custom_tool_call", name="exec", call_id="cell", input='text(await tools.exec_command({cmd:"build"}));'),
            entry("custom_tool_call_output", "T02", call_id="cell", output="Script running with cell ID 42\nOutput:\n"),
            entry("function_call", "T03", name="wait", call_id="wait", arguments='{"cell_id":"42"}'),
            result("wait", {"wall_time_seconds": 1, "session_id": 7, "output": "SECRET_CELL_STDOUT"}, "T04", True),
            entry("custom_tool_call", "T05", name="exec", call_id="parallel",
                  input='await Promise.allSettled([tools.exec_command({cmd:"serve"}), tools.write_stdin({session_id:7}), tools.exec_command({cmd:"watch"})]);'),
            result("parallel", [{"status": "fulfilled", "value": {"chunk_id": "x", "session_id": 8}},
                                {"status": "fulfilled", "value": {"chunk_id": "y", "session_id": 7}},
                                {"status": "fulfilled", "value": {"chunk_id": "z", "session_id": 9}}], "T06"),
            entry("item_completed", "T07", outer="event_msg", started_at_ms=1000,
                  item={"type": "CommandExecution", "process_id": "7", "status": "failed", "exit_code": 2,
                        "duration": {"secs": 90}, "command": ["bash", "-lc", "build"], "output": "SECRET_NATIVE_STDOUT"}),
            entry("item_completed", "T08", outer="event_msg", item={"type": "CommandExecution", "process_id": "99",
                  "status": "completed", "exit_code": 0, "duration": {"secs": 60}, "command": ["bash", "-lc", "orphan"]}),
        ])
        expected = chat.codex_background(self.path)
        by_id = {row["id"]: row for row in expected}
        self.assertEqual({key: (row["what"], row["state"], row["exit"]) for key, row in by_id.items()},
                         {"codex-7": ("build", "failed", 2), "codex-8": ("serve", "running", None),
                          "codex-9": ("watch", "running", None), "codex-99": ("orphan", "completed", 0)})
        self.assertEqual(by_id["codex-7"]["at"], "T01")
        self.assertEqual(by_id["codex-7"]["ended"], "T07")
        with patch.object(chat.json, "loads", wraps=json.loads) as loads:
            self.assertEqual(chat.codex_background(self.path), expected)
            self.assertEqual(chat.codex_background(self.path), expected)
        self.assertEqual(loads.call_count, 0, "warm background reads must not decode cached tool outputs again")
        self.assertNotIn("SECRET_", repr(chat._seen[str(self.path)]))

    def test_append_and_rewrite_refresh_background_without_stale_metadata(self):
        self.write([entry("function_call", name="exec_command", call_id="a", arguments='{"cmd":"serve"}'),
                    result("a", {"chunk_id": "x", "session_id": 71})])
        self.assertEqual(chat.codex_background(self.path)[0]["state"], "running")
        self.write([entry("item_completed", "T03", outer="event_msg", item={"type": "CommandExecution",
                    "process_id": "71", "status": "completed", "exit_code": 0,
                    "command": ["bash", "-lc", "serve"], "duration": {"secs": 60}})], "a")
        self.assertEqual(chat.codex_background(self.path)[0]["state"], "completed")
        self.write([entry("message", role="user", content=[{"type": "input_text", "text": "A different conversation"}])])
        self.assertEqual(chat.codex_background(self.path), [])
        self.assertEqual(chat.codex_events(self.path), [("you", "A different conversation", "T01")])


if __name__ == "__main__":
    unittest.main()
