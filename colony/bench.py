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
import re
from pathlib import Path

from . import board

EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
DOMAINS = ("overall", "coding", "agentic", "reasoning", "math", "long-context", "instructions", "preference")
MEASURES = ("cost", "latency")                 # recorded as the source states them, never converted
KEYS = ("model", "effort", "source", "kind", "benchmark", "version", "domain", "value", "unit", "date", "url", "note")
# The work a project hands its helpers, and the domains that speak to it. Chores also weigh cost.
NEAR = 5                                    # points of a role's score that more effort must add to be worth it
INDEX = "intelligence index"                # the overall benchmark whose cost and time per task price every entry
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
        if not providers.usable(p):
            continue                             # only what the person has, and has on
        for mid, label in providers.available(p):
            out.append((key, mid, label, providers.efforts_of(p, mid)))
    return out


UNSTATED = "effort not stated"


def variant(r):
    """Which variant of a model a record measured: its effort level, or, where the source names none, the variant
    it does name (non-reasoning, reasoning, a thinking budget). A record whose source states neither can't be
    compared like with like: it stays on the model's card and out of every ranking."""
    if r["effort"]:
        return r["effort"]
    note = (r.get("note") or "").lower()
    if "non-reasoning" in note:
        return "non-reasoning"
    if "(reasoning)" in note:
        return "reasoning"
    if m := re.search(r"_(\d+k)\b", note):
        return f"{m.group(1).upper()} thinking"
    if m := re.search(r"; variant ([a-z-]+)$", note):
        return m.group(1)
    return UNSTATED


def standings(rows=None):
    """Every entry (model, effort) the records know for the lineup, with its domain scores on one scale and
    its overall score, relative to the lineup. Only independent scores are scaled and averaged."""
    rows = records() if rows is None else rows
    models = {mid for _, mid, _, _ in lineup()}
    rows = [r for r in rows if r["model"] in models]
    if any(r["source"] == API_SOURCE for r in rows):      # one source: once it has spoken, it alone is ranked
        rows = [r for r in rows if r["source"] == API_SOURCE]
    latest = {}                                  # one value per entry and (source, benchmark, version): the newest
    for r in rows:
        k = (r["model"], variant(r), r["source"], r["benchmark"], r["version"], r["domain"])
        if k not in latest or r["date"] >= latest[k]["date"]:
            latest[k] = r
    groups = {}
    for (m, eff, src, bench, ver, dom), r in latest.items():
        if r["kind"] == "independent" and dom in DOMAINS and eff != UNSTATED:     # only like with like is scaled
            groups.setdefault((src, bench, ver, dom), []).append(r)
    entries = {}
    for (m, var, *_rest) in latest:
        entries.setdefault((m, var), {"model": m, "variant": var, "effort": var if var in EFFORTS else None,
                                      "comparable": var != UNSTATED, "scaled": {}, "raw": [], "measures": []})
    for r in latest.values():
        e = entries[(r["model"], variant(r))]
        (e["measures"] if r["domain"] in MEASURES else e["raw"]).append(r)
    for (src, bench, ver, dom), rs in groups.items():
        lo, hi = min(r["value"] for r in rs), max(r["value"] for r in rs)
        for r in rs:
            if hi > lo:                          # one entry alone has nothing to be ranked against
                s = 100 * (r["value"] - lo) / (hi - lo)
                entries[(r["model"], variant(r))]["scaled"].setdefault(dom, []).append((s, f"{src} {bench} {ver or ''}".strip()))
    for e in entries.values():
        e["domains"] = {d: round(sum(s for s, _ in v) / len(v)) for d, v in e["scaled"].items()}
        # the overall standing: every headline score it has (overall indexes, and human preference), averaged
        head = e["scaled"].get("overall", []) + e["scaled"].get("preference", [])
        e["overall"] = round(sum(s for s, _ in head) / len(head)) if head else None
        e["headline"] = sorted({src for _, src in head})
        e["sources"] = sorted({src for v in e["scaled"].values() for _, src in v})
        # cost and time per task, of the benchmark the overall score rests on, so every entry is priced alike;
        # other costs (another benchmark's, a whole run's) stay in the record list, never compared
        index = lambda dom: [r for r in e["measures"] if r["domain"] == dom and r["kind"] == "independent"
                             and per(r["benchmark"]) and INDEX in r["benchmark"].lower()]
        price = [r for r in e["measures"] if r["domain"] == "cost" and r["kind"] == "independent" and pricing(r)]
        e["cost"] = (price or index("cost") or [None])[0]      # price per 1M tokens; else an older per-task cost
        e["time"] = (index("latency") or [None])[0]
        e["index"] = next((r["value"] for r in e["raw"] if r["domain"] == "overall" and r["kind"] == "independent"
                           and INDEX in r["benchmark"].lower() and r["source"] == "Artificial Analysis"), None)
        e["pending"] = not any(r["domain"] in ("overall", "preference") and r["kind"] == "independent" for r in e["raw"])
    return sorted(entries.values(), key=lambda e: (not e["comparable"], e["overall"] is None, -(e["overall"] or 0)))


def pricing(r):
    """Whether a cost is a price per million tokens (the same for every effort level of a model)."""
    return "per 1m tokens" in r["benchmark"].lower()


def cost_label(r):
    return "price per 1M tokens" if r and pricing(r) else "cost per task"


EFFORT_ORDER = {x: i for i, x in enumerate(EFFORTS)}


def per(benchmark):
    """Whether a cost or time is per task (what the person asked for) rather than for a whole benchmark run."""
    b = benchmark.lower()
    return "per" in b and "task" in b


def role_score(e, role):
    """How an entry serves a role: the mean of that role's domains it has scores for. Chores are value: the
    Intelligence Index's own points per dollar a task costs (not the 0-100 scale, whose lowest entry is 0
    however cheap it is)."""
    if role == "chores":
        if e.get("index") is None or not e["cost"] or not e["cost"]["value"]:
            return None
        value = e["index"] / e["cost"]["value"]
        # a price per token is the same at every effort: among them, the lowest effort spends the fewest tokens
        return value - (EFFORT_ORDER.get(e["effort"], 0) * 1e-6 if pricing(e["cost"]) else 0)
    have = [e["domains"][d] for d in ROLES[role] if d in e["domains"]]
    return sum(have) / len(have) if have else None


def best_for(role, provider=None, entries=None):
    """The entries that serve a role best, highest first; within one provider's models if given."""
    from . import providers
    entries = standings() if entries is None else entries
    mine = {mid for key, mid, _, _ in lineup() if provider is None or key == provider}
    scored = [(role_score(e, role), e) for e in entries if e["model"] in mine and e["comparable"]]
    return [e for s, e in sorted(((s, e) for s, e in scored if s is not None), key=lambda x: -x[0])]


def name(model):
    return next((label for _, mid, label, _ in lineup() if mid == model), model)


def entry_name(e):
    return f"{name(e['model'])} · {e['variant']}"


def card(model, entries=None):
    """What the evidence says about one model: each effort level as its own entry, its strengths and weaknesses
    against the lineup, where more effort pays, and the gaps."""
    entries = standings() if entries is None else entries
    mine = [e for e in entries if e["model"] == model]
    efforts = next((eff for _, mid, _, eff in lineup() if mid == model), [])
    measured = {e["effort"] for e in mine}
    out = {"model": model, "name": name(model), "entries": mine,
           "untested": [x for x in efforts if x not in measured],
           "pending": not any(e["comparable"] and not e["pending"] for e in mine), "notes": []}
    for e in mine:
        top = [d for d, s in e["domains"].items() if s >= 67 and d != "overall"]
        low = [d for d, s in e["domains"].items() if s <= 33 and d != "overall"]
        if top:
            out["notes"].append(f"{entry_name(e)}: strong in {', '.join(top)} for this lineup")
        if low:
            out["notes"].append(f"{entry_name(e)}: weak in {', '.join(low)} for this lineup")
    ranked = sorted((e for e in mine if e["overall"] is not None and e["effort"]), key=lambda e: EFFORT_ORDER.get(e["effort"], 0))
    for a, b in zip(ranked, ranked[1:]):
        gain = b["overall"] - a["overall"]
        verdict = "pays" if gain >= 10 else "little gain" if gain < 3 else "pays a little"
        if b["cost"] and a["cost"] and not pricing(b["cost"]) and a["cost"]["value"]:
            times = b["cost"]["value"] / a["cost"]["value"]
            out["notes"].append(f"{b['effort']} over {a['effort']}: {gain:+d} points for {times:.1f}× the cost: "
                                + ("costs more for little" if gain < 3 else verdict))
        else:
            out["notes"].append(f"{b['effort']} over {a['effort']}: {gain:+d} points, at the same price per token "
                                f"but more tokens a task: {verdict}")
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
        ranked = [x for x in ranked if x["effort"]] or ranked      # one that names an effort can be acted on
        if not ranked:
            continue
        top, e = ranked[0], ranked[0]
        if role != "chores":
            # more effort pays only while it scores clearly more: the cheapest entry within NEAR of the best
            near = [x for x in ranked if role_score(x, role) >= role_score(top, role) - NEAR and x["cost"]]
            # the cheapest model, then its lowest effort: fewer tokens a task
            e = min(near, key=lambda x: (x["cost"]["value"], EFFORT_ORDER.get(x["effort"], 0))) if near else top
        if role == "chores":
            why = (f"most Intelligence Index points per dollar among {providers.of(root).label} models "
                   f"({e['index']:g} points at ${e['cost']['value']:.2f} {'per 1M tokens' if pricing(e['cost']) else 'a task'}"
                   + ("; the lowest effort, for the fewest tokens" if pricing(e["cost"]) else "") + ")")
        else:
            why = (f"{role} score {role_score(e, role):.0f} of 100 across the lineup"
                   + (f", within {NEAR} of the best ({entry_name(top)}, {role_score(top, role):.0f})"
                      + (f" at a lower effort: fewer tokens a task" if e["model"] == top["model"] else
                         f" at ${e['cost']['value']:.2f} against ${top['cost']['value']:.2f} {cost_label(e['cost'])}")
                      if e is not top and top["cost"] else ""))
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


# ---------------------------------------------------------------- Artificial Analysis, the one source

AA_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
AA_STEPS = [("Create a free account at Artificial Analysis", "https://artificialanalysis.ai/login"),
            ("In its Insights Platform, generate an API key (the free API: intelligence, speed and pricing data)", None),
            ("Paste the key below and save it; colony keeps it on this machine only", None),
            ("Check it: colony makes one call and says how many models it sees", None)]


def key_path():
    return board.home() / "bench" / "aa-key"


def aa_key():
    try:
        return key_path().read_text().strip() or None
    except OSError:
        return None


def set_key(key):
    """Keep the person's Artificial Analysis key on this machine, readable by them alone; empty removes it."""
    p = key_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if not key.strip():
        p.unlink(missing_ok=True)
        return
    p.write_text(key.strip() + "\n")
    p.chmod(0o600)


def aa_get(key=None, opener=None):
    """Artificial Analysis' model data, with the key (theirs asks us to cache it, so callers keep what comes back).
    Returns (models, None) or ([], why it failed)."""
    import urllib.error
    import urllib.request
    key = key or aa_key()
    if not key:
        return [], "no Artificial Analysis key is connected (Settings)"
    req = urllib.request.Request(AA_URL, headers={"x-api-key": key, "User-Agent": "colony"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=30) as r:
            return json.loads(r.read().decode()).get("data") or [], None
    except urllib.error.HTTPError as err:
        return [], {401: "the key was refused (401): reconnect it in Settings", 429: "over the daily limit (429): try tomorrow"}.get(
            err.code, f"Artificial Analysis answered {err.code}")
    except (OSError, ValueError) as err:
        return [], f"couldn't reach Artificial Analysis ({err.__class__.__name__})"


def status():
    """Whether the key is connected and when data last came."""
    p = board.home() / "bench" / "fetched.json"
    try:
        last = json.loads(p.read_text())
    except (OSError, ValueError):
        last = {}
    return {"connected": bool(aa_key()), **last}


API_SOURCE = "Artificial Analysis (API)"
# What each field of theirs speaks to; one we don't know is left out and listed, never guessed at.
AA_FIELDS = {"artificial_analysis_intelligence_index": ("overall", "Intelligence Index", "points"),
             "artificial_analysis_coding_index": ("coding", "Coding Index", "points"),
             "artificial_analysis_math_index": ("math", "Math Index", "points"),
             "gpqa": ("reasoning", "GPQA Diamond", "fraction"), "hle": ("reasoning", "Humanity's Last Exam", "fraction"),
             "mmlu_pro": ("reasoning", "MMLU-Pro", "fraction"), "livecodebench": ("coding", "LiveCodeBench", "fraction"),
             "scicode": ("coding", "SciCode", "fraction"), "math_500": ("math", "MATH-500", "fraction"),
             "aime": ("math", "AIME", "fraction")}


def pieces(name):
    """A model's name as its pieces, however it's written: GPT-5.6 Sol, gpt_5_6_sol and gpt-5-6-sol are the same;
    a date suffix (20251001) isn't part of the name."""
    return [p for p in re.split(r"[^a-z0-9]+", name.lower()) if p and not re.fullmatch(r"\d{8}", p)]


def match(model, entry):
    """The variant of this model an Artificial Analysis entry measured ("" for the model as it is), or None if
    it's another model. Every piece of the model's ID must be in the entry's slug or name, in any order (Claude
    4.5 Haiku is claude-haiku-4-5), and what's left must be words (an effort level, reasoning or not), never a
    number (so claude-sonnet-5 isn't claude-sonnet-5-5)."""
    from collections import Counter
    mine = Counter(pieces(model))
    for label in (entry.get("slug") or "", entry.get("name") or ""):
        theirs = Counter(pieces(label))
        if not mine or mine - theirs:
            continue
        left = list((theirs - mine).elements())
        if any(p.isdigit() for p in left):
            continue
        return "-".join(p for p in left if p not in ("claude", "gpt", "openai", "anthropic"))
    return None


def records_from(data, lineup_models, date):
    """Artificial Analysis' entries as records for the models in the lineup; which of theirs matched nothing."""
    rows, matched, unmatched, unknown = [], set(), [], set()
    for entry in data:
        hits = [(m, v) for m in lineup_models if (v := match(m, entry)) is not None]
        if not hits:
            unmatched.append(entry.get("name") or entry.get("slug"))
            continue
        m, variant = min(hits, key=lambda h: len(h[1]))       # the most exact, if two could claim it
        matched.add(m)
        effort = variant if variant in EFFORTS else None
        note = f"AA entry '{entry.get('name')}' ({entry.get('slug')})" + ("" if effort else f"; variant {variant or 'default'}")
        base = dict(model=m, effort=effort, source=API_SOURCE, kind="independent", version=None, date=date,
                    url="https://artificialanalysis.ai/", note=note)
        for field, value in (entry.get("evaluations") or {}).items():
            if value is None:
                continue
            if field not in AA_FIELDS:
                unknown.add(field)
                continue
            dom, bench_, unit = AA_FIELDS[field]
            rows.append(dict(base, benchmark=bench_, domain=dom, value=value, unit=unit))
        price = (entry.get("pricing") or {}).get("price_1m_blended_3_to_1")
        if price is not None:
            rows.append(dict(base, benchmark="Price per 1M tokens (blended 3:1)", domain="cost", value=price, unit="usd"))
        if entry.get("median_output_tokens_per_second") is not None:
            rows.append(dict(base, benchmark="Output speed", domain="latency", value=entry["median_output_tokens_per_second"], unit="tokens/s"))
        if entry.get("median_time_to_first_token_seconds") is not None:
            rows.append(dict(base, benchmark="Time to first token", domain="latency", value=entry["median_time_to_first_token_seconds"], unit="s"))
    return rows, sorted(matched), unmatched, sorted(unknown)


def refresh(opener=None):
    """Fetch Artificial Analysis' data for the lineup and keep it; say what came, what matched, what didn't."""
    data, err = aa_get(opener=opener)
    out = {"at": board.now(), "error": err}
    if not err:
        models = [mid for _, mid, _, _ in lineup()]
        rows, matched, unmatched, unknown = records_from(data, models, board.now()[:10])
        added, _ = add(rows)
        out.update(models=len(data), matched=matched, missing=[m for m in models if m not in matched],
                   unmatched=len(unmatched), unknown_fields=unknown, added=added)
    p = board.home() / "bench" / "fetched.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1))
    return out


def ready_to_announce():
    """Models in the lineup whose data has come and that haven't been announced: each is announced once. The
    first time the board looks, what's there is taken as known, not as news."""
    p = board.home() / "bench" / "announced.json"
    try:
        told = set(json.loads(p.read_text()))
        first = False
    except (OSError, ValueError):
        told, first = set(), True
    ready = [mid for _, mid, _, _ in lineup() if not card(mid)["pending"]]
    new = [] if first else [m for m in ready if m not in told]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted(told | set(ready))))
    return new
