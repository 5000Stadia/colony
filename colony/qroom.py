"""Q Branch's runners, through the research room: the command its core runs for each model call (the prompt on stdin,
only the answer on stdout), so every web pass goes through room.research and every other call is a pass with no
tools at all. Run as a script, with python3 -I (what it prints came from web pages):

    python3 -I colony/qroom.py research FAMILY MODEL   one web pass (search and read pages), unscreened: the core
                                                       screens what it keeps, once
    python3 -I colony/qroom.py screen FAMILY MODEL     the hand-off screen's model: a pass with no tools, on a model
                                                       other than MODEL (the research pass's), as room.screener picks
    python3 -I colony/qroom.py ask FAMILY ROLE         a pass with no tools on one of colony's tiers (rote, chores)

Each call appends what it did to $QBRANCH_RUNLOG (the core's: {"tool", "input"} per call, then {"usage"}) and its
usage, cost and model to $COLONY_QB_USAGE, one JSON line, so the caller can count each pass's tokens.
"""
import json
import os
import secrets
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from colony import providers, room, tokens  # noqa: E402


def cost(run_dir):
    """What a pass's program said it cost, in US dollars, from its events in the room's log (0 if it said nothing)."""
    total = 0.0
    for log in Path(run_dir).rglob("log.jsonl"):
        try:
            rows = log.read_text().splitlines()
        except OSError:
            continue
        for line in rows:
            try:
                e = json.loads(line).get("event")
            except ValueError:
                continue
            if isinstance(e, dict) and e.get("type") == "result" and e.get("total_cost_usd"):
                total += float(e["total_cost_usd"])
    return round(total, 4)


def record(mode, family, model, usage, run_dir, tools=()):
    paid = cost(run_dir)
    log = os.environ.get("QBRANCH_RUNLOG")
    if log:
        with open(log, "a") as fh:
            for t in tools:
                fh.write(json.dumps({"tool": t.get("tool"), "input": t.get("input")}) + "\n")
            fh.write(json.dumps({"usage": dict(usage or {}, cost_usd=paid)}) + "\n")
    ledger = os.environ.get("COLONY_QB_USAGE")
    if ledger:
        with open(ledger, "a") as fh:
            fh.write(json.dumps({"mode": mode, "family": family, "model": model, "usage": usage or {}, "cost_usd": paid,
                                 "run_dir": str(run_dir), "at": round(time.time())}) + "\n")


def bare(family, model, effort, prompt, kind):
    """One pass with no tools at all, in the room; its answer, or None."""
    p = providers.PROVIDERS[family]
    here = room.folder() / f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    here.mkdir(parents=True)
    r = room._pass(p, prompt, model, effort, here, room.SCREEN_TIMEOUT, tools=False)
    tokens.note(family, r["usage"], kind)
    record(kind, family, model, r["usage"], here)
    if r["status"] != "done":
        print(f"{kind}: {r['status']} ({r['error']})", file=sys.stderr)
        return None
    return room._unfence(r["text"])


def main(argv):
    if len(argv) != 4 or argv[1] not in ("research", "screen", "ask"):
        print(__doc__, file=sys.stderr)
        return 2
    mode, family, what = argv[1:]
    if family not in providers.PROVIDERS:
        print(f"no program {family}", file=sys.stderr)
        return 2
    prompt = sys.stdin.read()
    if mode == "research":
        r = room.research(prompt, family=family, model=what, screen=False, kind="research")
        record("research", family, what, (json.loads((Path(r["run_dir"]) / "result.json").read_text()).get("usage")
                                          if (Path(r["run_dir"]) / "result.json").exists() else None),
               r["run_dir"], r["tools"])
        if r["status"] != "done":
            print(f"research: {r['status']} ({r['error']})", file=sys.stderr)
            return 1
        print(r["text"])
        return 0
    if mode == "screen":
        here = room.folder() / f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}-screen"
        here.mkdir(parents=True)
        try:
            ask = room.screener(family, what, here)
        except Exception as err:                # no model to screen with: the core holds the deliverable
            print(f"screen: {err}", file=sys.stderr)
            return 1
        text = bare(ask.family, ask.model, None, prompt, "screen")
    else:
        model, effort = room.tier(family, what)
        text = bare(family, model, effort, prompt, "qbranch")
    if not text:
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
