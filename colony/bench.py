"""Benchmark cards: what the evidence says about each model the board can run, at each effort level.

Scores are researched once, when a model joins colony (the agent that adds it to providers.py does the check),
and kept on this machine as append-only records: one score, its source, the benchmark and its version, the
effort it was measured at, and the date. Cards and rankings are derived from those records, relative to the
lineup the board has now, so adding a model reranks the rest without re-pulling anyone's data.

Two or three respected overall scores carry the ranking (Artificial Analysis' Intelligence Index, LMArena, Epoch
AI's Capabilities Index): each is put on one scale across the lineup (0 the lowest entry measured there, 100 the
highest) within its own source, benchmark and version, and the scales are averaged. Vendor numbers are shown,
labelled, and never averaged in. Each effort level is its own entry; nothing is blended, and a gap stays a gap.

A record, one JSON object per line for `colony bench import FILE`:
  {"model": "claude-opus-5-5", "effort": "high" (or null when the source names none), "source": "Artificial Analysis",
   "kind": "independent" | "vendor", "benchmark": its own name, "version": as the source states it (or null),
   "domain": overall|coding|agentic|reasoning|math|long-context|instructions|preference|cost|latency,
   "value": a number as the source states it, "unit": points|elo|%|usd|s|tokens/s, "date": "YYYY-MM-DD",
   "url": where it was found, "note": anything a reader needs (the source's own name for the variant)}
"""
import json
from pathlib import Path

from . import board

DOMAINS = ("overall", "coding", "agentic", "reasoning", "math", "long-context", "instructions", "preference")
MEASURES = ("cost", "latency")                 # recorded as the source states them, never converted
KEYS = ("model", "effort", "source", "kind", "benchmark", "version", "domain", "value", "unit", "date", "url", "note")
# The work a project hands its helpers, and the domains that speak to it. Chores also weigh cost.
ROLES = {"planning": ("reasoning", "overall"), "building": ("coding", "agentic"),
         "review": ("coding", "reasoning"), "chores": ("overall",)}


def path():
    return board.home() / "bench" / "records.jsonl"


def records():
    p = path()
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def problems(r):
    """What's wrong with a record, if anything: every key present, a known domain and kind, a number, a URL."""
    out = [f"missing {k}" for k in KEYS if k not in r]
    if r.get("domain") not in DOMAINS + MEASURES:
        out.append(f"domain {r.get('domain')!r} is not one of {', '.join(DOMAINS + MEASURES)}")
    if r.get("kind") not in ("independent", "vendor"):
        out.append("kind is independent or vendor")
    if not isinstance(r.get("value"), (int, float)) or isinstance(r.get("value"), bool):
        out.append("value is a number")
    if not str(r.get("url") or "").startswith("http"):
        out.append("url is where the number was found")
    return out


def add(rows):
    """Append the records that are sound and not already kept. Returns (added, [(line, problems)])."""
    have = {json.dumps({k: r.get(k) for k in KEYS}, sort_keys=True) for r in records()}
    added, bad = 0, []
    path().parent.mkdir(parents=True, exist_ok=True)
    with path().open("a") as fh:
        for i, r in enumerate(rows, 1):
            wrong = problems(r)
            if wrong:
                bad.append((i, wrong))
                continue
            key = json.dumps({k: r.get(k) for k in KEYS}, sort_keys=True)
            if key in have:
                continue
            have.add(key)
            fh.write(json.dumps({k: r.get(k) for k in KEYS}, ensure_ascii=False) + "\n")
            added += 1
    return added, bad


def lineup():
    """Every model the board can run now: (provider key, model id, its name, its effort levels)."""
    from . import providers
    out = []
    for key, p in providers.PROVIDERS.items():
        for mid, label in p.models:
            efforts = getattr(p, "efforts_for", {}).get(mid, p.efforts)
            out.append((key, mid, label, [e[0] if isinstance(e, tuple) else e for e in efforts]))
    return out


def standings(rows=None):
    """Every entry (model, effort) the records know for the lineup, with its domain scores on one scale and
    its overall score, relative to the lineup. Only independent scores are scaled and averaged."""
    rows = records() if rows is None else rows
    models = {mid for _, mid, _, _ in lineup()}
    rows = [r for r in rows if r["model"] in models]
    latest = {}                                  # one value per entry and (source, benchmark, version): the newest
    for r in rows:
        k = (r["model"], r["effort"], r["source"], r["benchmark"], r["version"], r["domain"])
        if k not in latest or r["date"] >= latest[k]["date"]:
            latest[k] = r
    groups = {}
    for (m, eff, src, bench, ver, dom), r in latest.items():
        if r["kind"] == "independent" and dom in DOMAINS:
            groups.setdefault((src, bench, ver, dom), []).append(r)
    entries = {}
    for (m, eff, *_rest) in latest:
        entries.setdefault((m, eff), {"model": m, "effort": eff, "scaled": {}, "raw": [], "measures": []})
    for r in latest.values():
        e = entries[(r["model"], r["effort"])]
        (e["measures"] if r["domain"] in MEASURES else e["raw"]).append(r)
    for (src, bench, ver, dom), rs in groups.items():
        lo, hi = min(r["value"] for r in rs), max(r["value"] for r in rs)
        for r in rs:
            if hi > lo:                          # one entry alone has nothing to be ranked against
                s = 100 * (r["value"] - lo) / (hi - lo)
                entries[(r["model"], r["effort"])]["scaled"].setdefault(dom, []).append((s, f"{src} {bench} {ver or ''}".strip()))
    for e in entries.values():
        e["domains"] = {d: round(sum(s for s, _ in v) / len(v)) for d, v in e["scaled"].items()}
        # the overall standing: every headline score it has (overall indexes, and human preference), averaged
        head = e["scaled"].get("overall", []) + e["scaled"].get("preference", [])
        e["overall"] = round(sum(s for s, _ in head) / len(head)) if head else None
        e["headline"] = sorted({src for _, src in head})
        e["sources"] = sorted({src for v in e["scaled"].values() for _, src in v})
        per_task = lambda dom: sorted((r for r in e["measures"] if r["domain"] == dom and r["kind"] == "independent"),
                                      key=lambda r: not per(r["benchmark"]))            # per task first, as asked
        e["cost"] = (per_task("cost") or [None])[0]
        e["time"] = (per_task("latency") or [None])[0]
        e["pending"] = not any(r["domain"] in ("overall", "preference") and r["kind"] == "independent" for r in e["raw"])
    return sorted(entries.values(), key=lambda e: (e["overall"] is None, -(e["overall"] or 0)))


def per(benchmark):
    """Whether a cost or time is per task (what the person asked for) rather than for a whole benchmark run."""
    b = benchmark.lower()
    return "per" in b and "task" in b


def role_score(e, role):
    """How an entry serves a role: the mean of that role's domains it has scores for (chores: per dollar)."""
    have = [e["domains"][d] for d in ROLES[role] if d in e["domains"]]
    if not have:
        return None
    score = sum(have) / len(have)
    if role == "chores":
        if not e["cost"] or not e["cost"]["value"]:
            return None
        return score / e["cost"]["value"]
    return score


def best_for(role, provider=None, entries=None):
    """The entries that serve a role best, highest first; within one provider's models if given."""
    from . import providers
    entries = standings() if entries is None else entries
    mine = {mid for key, mid, _, _ in lineup() if provider is None or key == provider}
    scored = [(role_score(e, role), e) for e in entries if e["model"] in mine]
    return [e for s, e in sorted(((s, e) for s, e in scored if s is not None), key=lambda x: -x[0])]


def name(model):
    return next((label for _, mid, label, _ in lineup() if mid == model), model)


def entry_name(e):
    return f"{name(e['model'])}" + (f" · {e['effort']}" if e["effort"] else "")


def card(model, entries=None):
    """What the evidence says about one model: each effort level as its own entry, its strengths and weaknesses
    against the lineup, where more effort pays, and the gaps."""
    entries = standings() if entries is None else entries
    mine = [e for e in entries if e["model"] == model]
    efforts = next((eff for _, mid, _, eff in lineup() if mid == model), [])
    measured = {e["effort"] for e in mine}
    out = {"model": model, "name": name(model), "entries": mine,
           "untested": [x for x in efforts if x not in measured],
           "pending": not mine or all(e["pending"] for e in mine), "notes": []}
    for e in mine:
        top = [d for d, s in e["domains"].items() if s >= 67 and d != "overall"]
        low = [d for d, s in e["domains"].items() if s <= 33 and d != "overall"]
        if top:
            out["notes"].append(f"{entry_name(e)}: strong in {', '.join(top)} for this lineup")
        if low:
            out["notes"].append(f"{entry_name(e)}: weak in {', '.join(low)} for this lineup")
    ranked = sorted((e for e in mine if e["overall"] is not None and e["cost"]), key=lambda e: e["cost"]["value"])
    for a, b in zip(ranked, ranked[1:]):
        gain, times = b["overall"] - a["overall"], b["cost"]["value"] / a["cost"]["value"] if a["cost"]["value"] else None
        if times:
            verdict = "pays" if gain >= 10 else "costs more for little" if gain < 3 else "pays a little"
            out["notes"].append(f"{b['effort'] or 'this'} over {a['effort'] or 'that'}: {gain:+d} points for "
                                f"{times:.1f}× the cost: {verdict}")
    for role in ROLES:
        ranks = best_for(role, entries=entries)
        for e in mine:
            if ranks and e in ranks[:3]:
                out["notes"].append(f"{entry_name(e)}: among the best for {role}")
    return out


def pending():
    """Models in the lineup whose cards wait for independent scores."""
    return [mid for _, mid, _, _ in lineup() if card(mid)["pending"]]


def lineup_changed():
    """Models that joined the lineup since the board last looked (none the first time it looks)."""
    p = board.home() / "bench" / "lineup.json"
    now = sorted(mid for _, mid, _, _ in lineup())
    before = json.loads(p.read_text()) if p.exists() else None
    if before != now:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(now))
    return [] if before is None else [m for m in now if m not in before]


# ---------------------------------------------------------------- a project's model plan

def plan(root):
    """The model and effort a project's helpers use for each kind of work, as agreed with the person: the latest
    choice for each role, kept in the project's .board so every session starts with it."""
    out = {}
    for r in board.read(root, "models.jsonl"):
        out[r["role"]] = r
    return out


def set_plan(root, role, model, effort, why=""):
    board.append(root, "models.jsonl", {"role": role, "model": model, "effort": effort or None, "why": why.strip(),
                                        "at": board.now()})


def recommend(root):
    """For each role, the best entry among the models the project's own program can run, with why."""
    from . import providers
    key = next((k for k, p in providers.PROVIDERS.items() if p is providers.of(root)), None)
    entries = standings()
    out = {}
    for role in ROLES:
        ranked = best_for(role, provider=key, entries=entries)
        if ranked:
            e = ranked[0]
            why = (f"best {'value' if role == 'chores' else 'score'} for {role} among {providers.of(root).label} models "
                   f"({', '.join(f'{d} {e['domains'][d]}' for d in ROLES[role] if d in e['domains'])}; 0-100 across the lineup)")
            out[role] = {"model": e["model"], "effort": e["effort"], "why": why}
    return out


def plan_text(root):
    """The plan as the agent is handed it at the start of each session."""
    agreed = plan(root)
    if not agreed:
        return ""
    lines = [f"- {role}: {name(r['model'])} ({r['model']})" + (f" at {r['effort']} effort" if r["effort"] else "")
             + (f": {r['why']}" if r["why"] else "") for role, r in agreed.items()]
    return ("Your model plan, agreed with the person: use these for helpers (subagents) by kind of work. Propose "
            "a change to the person, never switch silently, when a model is added, a role changes, or one keeps "
            "underperforming (colony models shows it and today's recommendation):\n" + "\n".join(lines))


def first_note(root):
    """What a new project's agent is told before any work: the recommended plan, to agree with the person."""
    rec = recommend(root)
    if not rec:
        return None
    lines = "\n".join(f"- {role}: {name(r['model'])} at {r['effort'] or 'its default'} effort ({r['why']})" for role, r in rec.items())
    return ("Before any work, agree your model plan with the person: which model and effort your helpers (subagents) "
            "use for which kind of work. The benchmark cards (the board's Models page, or `colony bench card MODEL`) "
            f"recommend:\n{lines}\nPresent this, say briefly what each choice trades (quality, cost, speed), and ask "
            "them to confirm or adjust. Record what they agree with `colony models set ROLE MODEL EFFORT --why "
            "\"...\"`; it is handed to you at every session start from then on.")
