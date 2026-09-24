"""One headless Claude Code call: launched clean, metered, and patient with usage limits."""
import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def limit_wait(result):
    """Seconds to wait if the call was refused by a usage limit, else None."""
    said = result.get("result") or ""
    refused = result.get("is_error") and (result.get("api_error_status") == 429
                                          or re.search(r"usage limit|rate limit|session limit|limit reached", said, re.I))
    if not refused:
        return None
    m = re.search(r"resets (\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*\(([^)]+)\)", said, re.I)
    if not m:
        return 900
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
    now = datetime.now(ZoneInfo(m.group(4)))
    reset = now.replace(hour=hour, minute=int(m.group(2) or 0), second=0, microsecond=0)
    if reset <= now:
        reset += timedelta(days=1)
    return int((reset - now).total_seconds()) + 120


def meter(result):
    """Tokens and cost from `modelUsage`, which carries the full counts behind the billed cost."""
    models = list((result.get("modelUsage") or {}).values())
    total = lambda key: sum(m.get(key, 0) or 0 for m in models)
    return {"cost_usd": result.get("total_cost_usd") or 0.0, "input": total("inputTokens"),
            "output": total("outputTokens"), "thinking": total("thinkingTokens"),
            "cache_write": total("cacheCreationInputTokens"), "cache_read": total("cacheReadInputTokens")}


# A floor, not a sandbox: the commands that publish or reach other machines are refused to every agent
# (deny rules hold even with permissions bypassed). Other routes out exist; for isolation, use a container.
OUTWARD = ["Bash(git push:*)", "Bash(gh:*)", "Bash(npm publish:*)", "Bash(twine:*)", "Bash(cargo publish:*)",
           "Bash(docker push:*)", "Bash(ssh:*)", "Bash(scp:*)"]


def call(project, prompt, *, agent, row, wave, budget, session=None, resume=False, sleep=time.sleep):
    """Run one agent to completion in the project root and record what it cost."""
    cfg = project.config()
    binary = os.environ.get("COLONY_CLAUDE", "claude")
    role = agent.split("@")[0]
    role = role if role in ("builder", "reconciler", "door") else "specialist"
    effort = cfg.get(f"effort_{role}") or cfg["effort"]
    cmd = [binary, "-p", prompt, "--model", cfg["model"], "--effort", effort,
           "--setting-sources", "", "--strict-mcp-config", "--permission-mode", "bypassPermissions",
           "--output-format", "stream-json", "--verbose", "--max-budget-usd", f"{budget:.2f}"]
    cmd += ["--disallowedTools", *OUTWARD, *(["Edit", "Write", "NotebookEdit"] if role == "specialist" else [])]
    if session:
        cmd += ["--resume", session] if resume else ["--session-id", session]
    else:
        cmd.append("--no-session-persistence")
    package_home = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, COLONY_ROOT=str(project.root), COLONY_AGENT=agent,
               COLONY_ROW=str(row), COLONY_WAVE=str(wave),
               PYTHONPATH=os.pathsep.join(filter(None, [package_home, os.environ.get("PYTHONPATH")])))
    transcripts = project.state / "transcripts"
    transcripts.mkdir(parents=True, exist_ok=True)
    out = transcripts / f"row{row}-w{wave}-{agent}.jsonl"
    while True:
        started = time.time()
        with open(out, "w") as fh:
            try:
                subprocess.run(cmd, cwd=project.root, env=env, stdout=fh, stderr=subprocess.STDOUT, timeout=3600)
            except subprocess.TimeoutExpired:
                pass
        result = {}
        for line in out.read_text().splitlines():
            if line.startswith("{") and '"type":"result"' in line.replace(" ", ""):
                result = json.loads(line)
        wait = limit_wait(result)
        if wait is None:
            break
        project.append("waits.jsonl", {"row": row, "wave": wave, "agent": agent, "wait_s": wait, "at": time.time()})
        sleep(wait)
    record = {"row": row, "wave": wave, "agent": agent, "effort": effort, "seconds": round(time.time() - started),
              "ok": not result.get("is_error", True), "subtype": result.get("subtype", ""),
              "said": (result.get("result") or "")[-400:]}
    record.update(meter(result))
    project.append("usage.jsonl", record)
    return record
