"""Consulting at a decision costly to change: two fresh consultants from different model families, on a brief in the
person's own words, then (only if the person accepted a change) one checking round. At most two rounds a decision.

The recipe, from the holodeck pilot and two rounds on the rule itself (design/consult-log.md): any second pass beats
a first draft; a second model family adds what one misses; a checking round pays most consistently; reading is the
expensive part, so the asking agent, who knows the ground, writes the digest of sourced facts and the consultants
think from it and check at the source only what their answer turns on. The task is bounded (a few points, a few
hundred words), so a consultant takes what it takes: no caps or budgets, whose cutting a sound answer short would
cost more than they save (the person's call). Every round is logged with its cost, and later with what was adopted.
"""
import json
import secrets
from pathlib import Path

from . import board, providers

ROUND1 = ("What hasn't been considered whose absence would make this worse, or what fundamentally better approach "
          "exists? At most four points, each with why it matters here and what it protects. Only points that would "
          "fundamentally change or improve how this is approached: what gets built or dropped, the order, the "
          "structure, which risk is guarded, how the result is judged. Rewording, renaming or reorganising never "
          "count. If nothing would, say so plainly. At most 500 words.")
ROUND2 = ("Below is the revised approach, after round one's accepted points. Check it for problems those changes "
          "introduced: something that would fail, conflict with what exists, cost far more than intended, or miss what "
          "the person asked. At most three, each with how you'd check it. Not new ideas. If it's sound, say so. At "
          "most 350 words.")


def records(root):
    out = {}
    for e in board.read(root, "consults.jsonl"):
        if e["type"] == "consult":
            out[e["id"]] = dict(e, adopted=None)
        elif e["type"] == "adopted" and e["of"] in out:
            out[e["of"]]["adopted"] = e["points"]
    return list(out.values())


def pick(key):
    """The consultant for one family: the person's own choice in Settings if they made one, else auto()."""
    from . import selection
    value = selection.consultant(key)
    return value['model'], value['effort'], value['why']


def auto(key):
    """The family's accepted Auto consultant, including any pending adoption hold."""
    from . import selection
    value = selection.consultant(key, automatic=True)
    return value['model'], value['effort'], value['why']


def consultants():
    """One consultant from each of up to two families (ticked and installed), and a note if only one family is
    here: never a second model of the same family passed off as another view."""
    fams = [k for k, p in providers.PROVIDERS.items() if providers.usable(p) and hasattr(p, "consult")][:2]
    note = None if len(fams) != 1 else "only one model family is available here, so one consultant"
    return [(k, *pick(k)) for k in fams], note


def brief(root, decision, question, digest, plan=None, rnd=1):
    """The brief, assembled here rather than by the asking agent: the person's own words (the roadmap's goal line;
    the decision's roadmap item and the person's notes on it), the agent's digest of sourced facts, the question.
    Round one leaves the agent's plan out; round two shows the revised plan to check."""
    root = Path(root).resolve()
    road = board.roadmap(root)
    item = board.items(road).get(decision)
    words = [f"The project's goal, in the person's words: {road['goal']}"] if road["goal"] else []
    if item:
        words.append(f"The decision concerns roadmap item {decision}: {item['text']}" + (f" ({item['desc']})" if item["desc"] else ""))
    for n in board.notes(root):
        if (n["anchor"] or {}).get("item") == decision and n.get("author") in ("person", "monitor"):
            words.append(f"The person's note on it: {n['text']}")
    parts = [f"You are `consultant · {root.name} · this decision only`: a fresh, independent view on a decision in "
             "someone's project. Read nothing into who asked.", "## In the person's own words", "\n".join(words) or "(none recorded)",
             f"## The decision\n{question}",
             "## The asking agent's digest of the facts, with their sources",
             f"The digest is your material. Check a fact at its source (read-only, under {root}) only when your "
             "answer turns on it and you doubt it; don't survey the project.", digest.strip()]
    if rnd == 2 and plan:
        parts += ["## The revised approach", plan.strip(), "## Your task", ROUND2]
    else:
        parts += ["## Your task", ROUND1]
    return "\n\n".join(parts)


def run(root, decision, question, digest, plan=None, rnd=1, pool=None):
    """One round: checks the rules, asks the consultants side by side, logs it all. Returns the
    record, or raises ValueError with the reason it won't run."""
    root = Path(root)
    s = board.registry()["settings"]
    if not s["consult"]:
        raise ValueError("consulting is off (colony settings consult on)")
    mine = [r for r in records(root) if r["decision"] == decision]
    if rnd == 1 and any(r["round"] == 1 for r in mine):
        raise ValueError(f"{decision} has had its first round; a second runs only after the person accepts a change")
    if rnd == 2 and not any(r["round"] == 1 and r["adopted"] for r in mine):
        raise ValueError(f"a second round for {decision} needs the person to have accepted a change from the first")
    if rnd == 2 and any(r["round"] == 2 for r in mine):
        raise ValueError(f"{decision} has had both its rounds")
    if rnd == 2 and not plan:
        raise ValueError("a checking round needs the revised plan")
    who, note = consultants()
    if not who:
        raise ValueError("no provider that can consult is installed and on")
    text = brief(root, decision, question, digest, plan, rnd)
    import concurrent.futures as cf

    def ask(k, model, effort):
        p = providers.get(k, strict=True)
        if getattr(p, "reports_cost", True):
            return p.consult(text, model, effort, root)
        # a program that doesn't report its cost is priced from the benchmark data (unknown: no cost shown)
        return p.consult(text, model, effort, root, price=bench.token_price(model))
    from . import bench
    with (pool or cf.ThreadPoolExecutor)(max_workers=2) as ex:
        futs = [(k, m, e, why, ex.submit(ask, k, m, e)) for k, m, e, why in who]
        answers = [dict(f.result(), provider=k, model=m, effort=e, why=why) for k, m, e, why, f in futs]
    cost = sum(a.get("cost") or 0 for a in answers)
    rec = {"type": "consult", "id": "c" + secrets.token_hex(3), "at": board.now(), "decision": decision, "round": rnd,
           "question": question.strip(), "answers": answers, "cost": round(cost, 4), "note": note}
    board.append(root, "consults.jsonl", rec)
    return rec


def adopt(root, cid, points):
    """Which points the person accepted (in their words or by number): the record that opens a second round."""
    if cid not in {r["id"] for r in records(root)}:
        raise KeyError(cid)
    board.append(root, "consults.jsonl", {"type": "adopted", "of": cid, "at": board.now(), "points": points.strip()})


def report(rec):
    """A consultation as the asking agent reads it: each answer with who gave it and why they were chosen, its
    cost, and what to do next."""
    out = [f"consultation {rec['id']} on {rec['decision']}, round {rec['round']} of at most 2: ${rec['cost']:.2f}"]
    if rec.get("note"):
        out.append(f"({rec['note']})")
    for n, x in enumerate(rec["answers"], 1):
        cost = f"${x['cost']:.2f}" if x.get("cost") is not None else "cost unknown"
        out += ["", f"## Consultant {n}: {x['model']} at {x['effort']} ({cost}; {x['why']})"]
        if x.get("error"):
            out.append(f"[{x['error']}]")
        out.append(x.get("text") or "(no answer)")
    if rec["round"] == 1:
        out += ["", "Next: bring the person only the points that would fundamentally change or improve the approach, "
                "a few at most, each with where it came from, as one gate (colony gate \"...\" --item "
                f"{rec['decision']}). Rewording, naming and reorganising never count. When they answer, record what they "
                f"accepted: colony consult {rec['decision']} \"their words\" --adopt {rec['id']}. Only an accepted change "
                "opens a second round, to check the revised approach (--plan FILE)."]
    else:
        out += ["", "This decision has had both its rounds. Fix what the checks found that holds up, and go on."]
    return "\n".join(out)
