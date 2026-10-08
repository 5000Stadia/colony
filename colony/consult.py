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
    # A linked gate is the human decision, including a rejection of every point.
    # Keep legacy adoption records readable, but never let them override its choices.
    for gate in board.gates(root):
        cid = gate.get("consult")
        if cid in out:
            rec = out[cid]
            rec["gate"] = gate["id"]
            choices = gate.get("point_decisions") or {}
            rec["point_decisions"] = [dict(point, decision=choices.get(point["id"]))
                                      for point in gate.get("points", [])]
            rec["adopted"] = "\n".join(f"{p['id']}: {p['text']}" for p in rec["point_decisions"]
                                        if p["decision"] == "accept") or None
            rec["gate_answer"] = gate["answer"]
    for rec in out.values():
        if rec["round"] == 1:
            rec["checking_round"] = next((c["id"] for c in out.values()
                                          if c["decision"] == rec["decision"] and c["round"] == 2), None)
    return list(out.values())


def gate_points(root, cid, points, item):
    """The agent curates existing recommendations; source numbers refer to report()'s consultants.

    No model call or automatic interpretation of an answer is needed. The gate
    snapshots attribution so it stays readable without fetching model catalogs.
    """
    rec = next((r for r in records(root) if r["id"] == cid), None)
    if not rec:
        raise ValueError(f"No consultation {cid} in this project.")
    if rec["round"] != 1 or rec["decision"] != item:
        raise ValueError("Link a first-round consultation on this same item or decision.")
    if rec.get("checking_round"):
        raise ValueError("This decision has already had its checking round; keep its past gate as recorded.")
    if rec.get("gate"):
        raise ValueError(f"Consultation {cid} already has gate {rec['gate']}.")
    if not isinstance(points, list) or not points:
        raise ValueError("Provide a nonempty JSON list of consultant points.")
    result = []
    for i, point in enumerate(points, 1):
        if not isinstance(point, dict) or not isinstance(point.get("text"), str) or not point["text"].strip():
            raise ValueError("Each point needs its recommendation as text.")
        numbers = point.get("consultants")
        if (not isinstance(numbers, list) or not numbers
                or any(type(n) is not int or not 1 <= n <= len(rec["answers"]) for n in numbers)
                or len(set(numbers)) != len(numbers)):
            raise ValueError("Each point needs valid, distinct consultant numbers from the report.")
        sources = []
        for number in numbers:
            answer = rec["answers"][number - 1]
            if answer.get("error") or not (answer.get("text") or "").strip():
                raise ValueError("A failed or empty consultant answer cannot be a point's source.")
            sources.append(dict(consultant=number, provider=answer["provider"],
                                model=answer["model"], effort=answer["effort"]))
        result.append(dict(id=f"P{i}", text=point["text"].strip(), sources=sources))
    return result


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


def ask(key, text, model, effort, root):
    """One fresh consultant's answer from a family's program: {"text", "cost", "usage", "error"}. A program that
    doesn't report its cost is priced from the benchmark data (unknown: no cost shown)."""
    from . import bench
    p = providers.get(key, strict=True)
    if getattr(p, "reports_cost", True):
        return p.consult(text, model, effort, root)
    return p.consult(text, model, effort, root, price=bench.token_price(model))


def consultants():
    """One consultant from each of up to two families (ticked and installed), and a note if only one family is
    here: never a second model of the same family passed off as another view."""
    fams = [k for k, p in providers.PROVIDERS.items() if providers.usable(p) and hasattr(p, "consult")][:2]
    note = None if len(fams) != 1 else "only one model family is available here, so one consultant"
    return [(k, *pick(k)) for k in fams], note


def brief(root, decision, question, digest, plan=None, rnd=1, choices=None):
    """The brief, assembled here rather than by the asking agent: the person's own words (the live vision or legacy goal;
    the decision's roadmap item and the person's notes on it), the agent's digest of sourced facts, the question.
    Round one leaves the agent's plan out; round two shows the revised plan to check."""
    root = Path(root).resolve()
    road = board.roadmap(root)
    item = board.items(road).get(decision)
    subject = 'vision' if road['vision'] else 'goal'
    words = [f"The project's {subject}, in the person's words: {road['goal']}"] if road["goal"] else []
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
        if choices:
            parts += ["## The person's choices on the consultant points", choices]
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
    accepted = next((r for r in mine if r["round"] == 1 and r["adopted"]), None)
    if rnd == 1 and any(r["round"] == 1 for r in mine):
        raise ValueError(f"{decision} has had its first round; a second runs only after the person accepts a change")
    if rnd == 2 and not accepted:
        raise ValueError(f"a second round for {decision} needs the person to have accepted a change from the first")
    if rnd == 2 and any(r["round"] == 2 for r in mine):
        raise ValueError(f"{decision} has had both its rounds")
    if rnd == 2 and not plan:
        raise ValueError("a checking round needs the revised plan")
    who, note = consultants()
    if not who:
        raise ValueError("no provider that can consult is installed and on")
    text = brief(root, decision, question, digest, plan, rnd,
                 choices=accepted.get("gate_answer") if rnd == 2 else None)
    import concurrent.futures as cf
    with (pool or cf.ThreadPoolExecutor)(max_workers=2) as ex:
        futs = [(k, m, e, why, ex.submit(ask, k, text, m, e, root)) for k, m, e, why in who]
        answers = [dict(f.result(), provider=k, model=m, effort=e, why=why) for k, m, e, why, f in futs]
    cost = sum(a.get("cost") or 0 for a in answers)
    rec = {"type": "consult", "id": "c" + secrets.token_hex(3), "at": board.now(), "decision": decision, "round": rnd,
           "question": question.strip(), "answers": answers, "cost": round(cost, 4), "note": note}
    if rnd == 2:
        rec["checked"] = dict(consult=accepted["id"], gate=accepted.get("gate"), words=accepted["adopted"],
                              points=[p for p in accepted.get("point_decisions", []) if p["decision"] == "accept"])
    board.append(root, "consults.jsonl", rec)
    return rec


def adopt(root, cid, points):
    """Which points the person accepted (in their words or by number): the record that opens a second round."""
    rec = next((r for r in records(root) if r["id"] == cid), None)
    if not rec:
        raise KeyError(cid)
    if rec.get("gate"):
        raise ValueError(f"Consultation {cid} is linked to gate {rec['gate']}: the person's choices on its points there are "
                         f"its adoption (in conversation: colony gate \"their words\" --answered {rec['gate']} --accept P1 --reject P2).")
    if not points.strip():
        raise ValueError("Record a change the person accepted.")
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
                "a few at most, as one gate. Write a JSON list of points with text and consultants (their numbered "
                "sources above), then link it: colony gate \"...\" --item "
                f"{rec['decision']} --consult {rec['id']} --points FILE. Rewording, naming and reorganising never count. "
                "The person accepts or rejects each point; the gate records adoption automatically. In conversation, "
                "use colony gate \"their words\" --answered ID --accept P1 --reject P2, covering every point. "
                "Only an accepted change opens a second round, to check the revised approach (--plan FILE)."]
    else:
        out += ["", "This decision has had both its rounds. Fix what the checks found that holds up, and go on."]
    return "\n".join(out)
