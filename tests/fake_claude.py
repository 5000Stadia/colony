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
seen = json.loads(state.read_text()) if state.exists() else {"calls": 0}
seen["calls"] += 1
state.write_text(json.dumps(seen))


def colony(*args):
    subprocess.run([sys.executable, "-m", "colony", *args], cwd=root, check=True, capture_output=True)


def finish(text, cost=0.1, error=False, status=None):
    result = {"type": "result", "is_error": error, "result": text, "total_cost_usd": 0 if error else cost,
              "modelUsage": {} if error else {"claude-opus-5-5": {"inputTokens": 10, "outputTokens": 100,
              "thinkingTokens": 20, "cacheCreationInputTokens": 1000, "cacheReadInputTokens": 5000}}}
    if status:
        result["api_error_status"] = status
    print(json.dumps({"type": "system", "subtype": "init"}))
    print(json.dumps(result))
    sys.exit(0)


if os.environ.get("FAKE_LIMIT_ONCE") and not seen.get("limited"):
    seen["limited"] = True
    state.write_text(json.dumps(seen))
    finish("You've hit your session limit · resets 3:30am (America/Los_Angeles)", error=True, status=429)

if "Your role: the builder." in prompt and "Do this row now" in prompt:
    row = re.search(r"^Row (\d+): (.*)$", prompt, re.M)
    with open(root / "work.txt", "a") as fh:
        fh.write(f"row {row.group(1)}: {row.group(2)}\n")
    finish("built\nSTATUS: done", cost=0.5)
elif prompt.startswith("Wave "):
    for sid in re.findall(r"^#(\d+) ", prompt, re.M):
        colony("field", "resolve", sid, "--fixed", "--text", "fixed it")
    finish("fixed\nSTATUS: done", cost=0.3)
elif "Your role: the specialist" in prompt:
    name = agent.split("@")[0]
    if os.environ.get("FAKE_FORK") and name == "reuse":
        colony("field", "signal", "--kind", "fork", "--severity", "critical", "--at", "design/spine.md", "--text", "needs a decision")
    elif wave == 1:
        colony("field", "signal", "--kind", "hole", "--severity", "major", "--at", "work.txt:1", "--text", f"{name} found a hole")
    finish("probed", cost=0.05)
elif "Your role: the reconciler." in prompt:
    (root / "design" / "now.md").write_text("Status: row closed.\nNext: the next row.\n")
    with open(root / "design" / "history.md", "a") as fh:
        fh.write("## Row closed\nBuilt the row; decided nothing new.\n\n")
    finish("reconciled", cost=0.02)
elif "Your role: the front door." in prompt:
    (root / "design" / "spine.md").write_text("# Draft — spine\n\n## The spec list\n| # | What | Done |\n|---|---|---|\n| 1 | a | b |\n\n**Approved:** no\n")
    finish("drafted", cost=0.2)
else:
    finish("unknown role", cost=0.0)
