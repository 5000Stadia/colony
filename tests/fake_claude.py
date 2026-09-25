#!/usr/bin/env python3
"""A stand-in for `claude -p` that plays each colony role deterministically and reports a cost."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

prompt = sys.argv[sys.argv.index("-p") + 1]
root = Path(os.environ["COLONY_ROOT"])
agent, wave = os.environ["COLONY_AGENT"], int(os.environ["COLONY_WAVE"])
state = root / ".colony" / "fake-state.json"
import fcntl
with open(root / ".colony" / "fake-state.lock", "w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)          # reviewers run in parallel; their records must not collide
    seen = json.loads(state.read_text()) if state.exists() else {"calls": 0}
    seen["calls"] += 1
    seen.setdefault("argv", {}).setdefault(agent.split("@")[0], []).append(sys.argv[1:])
    state.write_text(json.dumps(seen))


def colony(*args):
    subprocess.run([sys.executable, "-m", "colony", *args], cwd=root, check=True, capture_output=True)


def finish(text, cost=0.1, error=False, status=None):
    # Like the real CLI, a resumed session reports what the whole session has cost so far.
    calls = 1
    if "--resume" in sys.argv or "--session-id" in sys.argv:
        sid = sys.argv[sys.argv.index("--resume" if "--resume" in sys.argv else "--session-id") + 1]
        with open(root / ".colony" / "fake-state.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            st = json.loads(state.read_text())
            spent_, n = st.setdefault("sessions", {}).get(sid, [0.0, 0])
            spent_, calls = spent_ + (0 if error else cost), n + 1
            st["sessions"][sid] = [spent_, calls]
            state.write_text(json.dumps(st))
        cost = spent_
    result = {"type": "result", "is_error": error, "result": text, "total_cost_usd": 0 if error else cost,
              "modelUsage": {} if error else {"claude-opus-5-5": {"inputTokens": 10 * calls, "outputTokens": 100 * calls,
              "thinkingTokens": 20 * calls, "cacheCreationInputTokens": 1000 * calls, "cacheReadInputTokens": 5000 * calls}}}
    if status:
        result["api_error_status"] = status
    print(json.dumps({"type": "system", "subtype": "init"}))
    print(json.dumps(result))
    sys.exit(0)


if os.environ.get("FAKE_LIMIT_ONCE") and not seen.get("limited"):
    with open(root / ".colony" / "fake-state.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        seen = json.loads(state.read_text())
        seen["limited"] = True
        state.write_text(json.dumps(seen))
    finish("You've hit your session limit · resets 3:30am (America/Los_Angeles)", error=True, status=429)

if prompt.startswith("You are `builder") and "Do this row now" in prompt:
    row = re.search(r"^Row (\d+): (.*)$", prompt, re.M)
    with open(root / "work.txt", "a") as fh:
        fh.write(f"row {row.group(1)}: {row.group(2)}\n")
    if os.environ.get("FAKE_BREAK_ROW") == row.group(1):
        (root / "broken.txt").write_text("forgot what row 1 needed\n")
    if os.environ.get("FAKE_BUILD_ERROR"):
        finish("API Error: overloaded", error=True)
    if os.environ.get("FAKE_BUILDER_FORK"):
        colony("field", "signal", "--kind", "fork", "--severity", "critical", "--at", "design/spine.md", "--text", "which one?")
    doubt = os.environ.get("FAKE_ASSESS", "routine")
    finish(f"built\nLEAST CERTAIN: {doubt}\nSTATUS: done", cost=0.5)
elif prompt.startswith("Wave "):
    for sid in re.findall(r"^#(\d+) ", prompt, re.M):
        colony("field", "resolve", sid, "--fixed", "--text", "fixed it")
    finish("fixed\nSTATUS: done", cost=0.3)
elif " · reads, never edits`" in prompt:
    name = agent.split("@")[0]
    if os.environ.get("FAKE_TAMPER"):
        (root / "work.txt").write_text("a reviewer rewrote this\n")
        (root / "junk.txt").write_text("left by a reviewer\n")
    if os.environ.get("FAKE_FORK") and name == "reuse":
        colony("field", "signal", "--kind", "fork", "--severity", "critical", "--at", "design/spine.md", "--text", "needs a decision")
    elif name == "quiet":
        pass
    elif wave == 1:
        colony("field", "signal", "--kind", "hole", "--severity", "major", "--at", "work.txt:1", "--text", f"{name} found a hole")
    finish("probed", cost=0.05)
elif "Your role: the front door." in prompt:
    (root / "design" / "spine.md").write_text("# Draft — spine\n\n## The spec list\n| # | What | Done |\n|---|---|---|\n| 1 | a | b |\n\n**Approved:** no\n")
    finish("drafted", cost=0.2)
else:
    finish("unknown role", cost=0.0)
