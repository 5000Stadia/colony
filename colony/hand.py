"""A helper handed to another program (R169): where pool.choose finds another program does a tier's work better for
what it costs of its week, the agent's own start of the helper (Claude Code's Agent tool, by the tier's name) is
caught before it runs, and the same brief, word for word, runs on that program in the same folder. The agent is
told at once, as it would be of a background helper, and the report comes to it as a note when the run ends.

A project's own helper definitions (any name but colony's tiers) are its choice, and run as they are.
"""
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

from . import board, providers

LONGEST = 4 * 3600                  # a run that has not ended by then is stopped, and the agent told
PREAMBLE = ("You are a helper for this project's agent, which handed you the task below. This folder's {doc} holds the "
            "project's instructions: read it first, and keep to them, except what they ask of the project's own agent "
            "(talking to the person, notes, gates, roadmap, `colony` commands that ask or report): those stay the "
            "agent's. Do the task within its brief; your final message is your report to the agent.\n\nThe task:\n\n")


def tiers(provider):
    """{helper name: tier} for a program's colony tiers."""
    from . import bench
    return {provider.helper_name(t): t for t in bench.TIERS if provider.helper_name(t)}


def folder(root):
    return Path(root) / ".board" / "hands"


def hook(stdin=None, stdout=None):
    """The PreToolUse hook on starting a helper: where the tier goes to another program this time, the start is
    refused with what happens instead, and the run begins there. Any error lets the helper start as usual."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    out = ""
    try:
        payload = json.loads(stdin.read() or "null") or {}
        given = payload.get("tool_input") or {}
        root = board.root_of(payload.get("cwd") or ".")
        home = providers.of(root)
        tier = tiers(home).get(given.get("subagent_type"))
        if tier and given.get("prompt") and (root / ".board").is_dir():
            from . import pool
            choice = pool.choose(root, tier)
            if choice["program"] != providers.key(home):
                job = start(root, tier, choice, given, payload.get("cwd") or str(root))
                label = providers.get(choice["program"]).label
                why = (f"Colony ran this {tier} helper on {label} instead ({choice['model']}"
                       + (f" at {choice['effort']}" if choice.get("effort") else "") + f"; {choice['why']}), "
                       f"in {job['cwd']}, as job {job['id']}. It is running now, like a background helper: carry on "
                       "with other work, and its report comes to you as a note when it ends. Don't start the helper "
                       "again for this task.")
                out = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                         "permissionDecisionReason": why}})
    except Exception:
        out = ""
    if out:
        print(out, file=stdout, flush=True)
    return 0


def start(root, tier, choice, given, cwd, popen=None):
    """Keep the job and start its run, detached: it outlives the hook and the agent's turn."""
    job = dict(id="h" + secrets.token_hex(4), at=board.now(), tier=tier, cwd=str(cwd), program=choice["program"],
               model=choice["model"], effort=choice.get("effort"), why=choice["why"],
               description=given.get("description") or "", prompt=given["prompt"], state="running")
    folder(root).mkdir(parents=True, exist_ok=True)
    (folder(root) / f"{job['id']}.json").write_text(json.dumps(job))
    with open(folder(root) / f"{job['id']}.log", "ab") as log:
        (popen or subprocess.Popen)([sys.executable, "-m", "colony.hand", str(root), job["id"]], stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=log, start_new_session=True,
                                    env=dict(os.environ, COLONY_PROJECT=str(root)))
    return job


def run(root, ident, popen=None):
    """A handed helper's run, to its end: the report goes to the agent as a note, and the job keeps how it went."""
    root = Path(root)
    path = folder(root) / f"{ident}.json"
    job = json.loads(path.read_text())
    p = providers.get(job["program"])
    settings = board.project_settings(root)[0]
    home = providers.of(root)
    answer = folder(root) / f"{ident}.out.md"
    cmd = p.hand_command(job["model"], job.get("effort"), job["cwd"], answer, settings.get("permissions") or "ask")
    brief = PREAMBLE.format(doc=home.instructions or "instructions file") + job["prompt"]
    error = None
    try:
        proc = (popen or subprocess.Popen)(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, text=True, cwd=job["cwd"])
        proc.stdin.write(brief)
        proc.stdin.close()
        proc.wait(timeout=LONGEST)
        if proc.returncode:
            error = f"it ended with exit status {proc.returncode}"
    except subprocess.TimeoutExpired:
        proc.kill()
        error = f"it ran past {LONGEST // 3600} hours and was stopped"
    except OSError as err:
        error = f"it could not start ({err.__class__.__name__})"
    report = answer.read_text().strip() if answer.exists() else ""
    label = p.label
    what = f"Your {job['tier']} helper" + (f" ({job['description']})" if job.get("description") else "")
    if report:
        text = (f"{what}, which colony ran on {label} ({job['model']}) as job {ident}, has finished"
                + (f", though {error}" if error else "") + f". Its report:\n\n{report}")
    else:
        text = (f"{what}, which colony ran on {label} as job {ident}, gave no report: {error or 'it wrote none'}. "
                f"Its brief is kept in {path}; see what it changed in {job['cwd']}, and start the helper again "
                "if the task still needs doing.")
    board.add_note(root, None, text, author="colony", kind="helper")
    job.update(state="failed" if error or not report else "done", ended=board.now(), error=error)
    path.write_text(json.dumps(job))
    return job


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
