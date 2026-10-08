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
INDEX = "intelligence index"                # the overall benchmark whose cost and time per task price every entry
# The tiers a project's helpers run at, chosen on one early, general score: Artificial Analysis' Intelligence
# Index, in its own points. A new model gets it first and it covers every domain roughly enough; domains stay on
# the Models page as information and play no part in the choice (the person's call). Chores and rote are the
# person's split of simple work: chores takes minor discernment and has a goal, rote is mechanical and takes value.
TIERS = ("routine", "step-up", "chores", "rote")


def path():
    return board.home() / "bench" / "records.jsonl"


def records():
    """Researched records, and the latest Artificial Analysis snapshot read as records."""
    p = path()
    kept = [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []
    from . import intelligence
    return intelligence.bundled_records() + kept + api_records()


def snapshots_dir():
    return board.home() / "bench" / "artificial-analysis"


def snapshot():
    """The latest Artificial Analysis reply kept on this machine, and the day it came: ([], None) if none reads."""
    snaps = sorted(snapshots_dir().glob("*.json")) if snapshots_dir().exists() else []
    if not snaps:
        return [], None
    try:
        data = json.loads(snaps[-1].read_text())
    except (OSError, ValueError):
        return [], None
    return ([e for e in data if isinstance(e, dict)] if isinstance(data, list) else []), snaps[-1].stem[:10]


def api_records():
    """The latest Artificial Analysis reply, read as records for today's lineup: derived each time from what came,
    so a better reading of names applies to it at once."""
    data, date = snapshot()
    if not data:
        return []
    return records_from(data, [mid for _, mid, _, _ in lineup()], date)[0]


def token_price(model):
    """A model's price per 1M input and output tokens from the latest Artificial Analysis reply, or None: what
    costs a run for a program that doesn't report its cost."""
    data, _ = snapshot()
    for e in data:
        pr = e.get("pricing") or {}
        if match(model, e) is not None and pr.get("price_1m_input_tokens") is not None and pr.get("price_1m_output_tokens") is not None:
            return (pr["price_1m_input_tokens"], pr["price_1m_output_tokens"])
    return None


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


def task_curves(rows):
    """AA's fixed-task score/cost pairs, from one dated benchmark version at a time.

    Keep these researched curves even when the API supplies the ranking. API token
    prices and scores from another date/version must not be spliced into a task curve.
    """
    groups = {}
    for r in rows:
        if (not r['source'].startswith('Artificial Analysis') or r['kind'] != 'independent'
                or INDEX not in r['benchmark'].lower() or not r.get('version') or not r.get('effort')):
            continue
        metric = ('score' if r['domain'] == 'overall' and r['unit'] == 'points' else
                  'cost' if r['domain'] == 'cost' and r['unit'] == 'usd' and per(r['benchmark']) else None)
        if not metric:
            continue
        group = groups.setdefault((r['model'], r['source'], r['version'], r['date']), {})
        entry = group.setdefault(r['effort'], dict(effort=r['effort'], version=r['version'], date=r['date'], url=r['url']))
        entry[metric] = r['value']
    out = {}
    for (model, source, version, date), curve in sorted(groups.items(), key=lambda item: item[0][3]):
        # A gap is an estimate, not a measured bend across incomparable efforts.
        if len(curve) >= 3 and all('score' in e and 'cost' in e for e in curve.values()):
            out[model] = sorted(curve.values(), key=lambda e: EFFORT_ORDER.get(e['effort'], -1))
    return out


def standings(rows=None):
    """Every entry (model, effort) the records know for the lineup, with its domain scores on one scale and
    its overall score, relative to the lineup. Only independent scores are scaled and averaged."""
    rows = records() if rows is None else rows
    from . import intelligence
    pair_points = intelligence.candidates(rows, lineup())
    curves = task_curves(rows)
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
        if len(rs) < 3:                          # "best of two" isn't a standing: shown raw, not scaled
            continue
        lo, hi = min(r["value"] for r in rs), max(r["value"] for r in rs)
        for r in rs:
            if hi > lo:                          # one entry alone has nothing to be ranked against
                s = 100 * (r["value"] - lo) / (hi - lo)
                entries[(r["model"], variant(r))]["scaled"].setdefault(dom, []).append((s, f"{src} {bench} {ver or ''}".strip()))
    for e in entries.values():
        e["task_curve"] = curves.get(e["model"], [])
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
                           and INDEX in r["benchmark"].lower() and r["source"].startswith("Artificial Analysis")), None)
        e["pending"] = not any(r["domain"] in ("overall", "preference") and r["kind"] == "independent" for r in e["raw"])
    for point in pair_points:
        ident = (point['model'], point['effort'])
        e = entries.setdefault(ident, dict(model=point['model'], variant=point['effort'] or 'default', effort=point['effort'],
            comparable=True, scaled={}, raw=[], measures=[], domains={}, overall=None, headline=[], sources=[],
            cost=None, time=None, index=point['score'], pending=False, task_curve=curves.get(point['model'], [])))
        e['pair'] = point
    return sorted(entries.values(), key=lambda e: (not e['comparable'], e.get('index') is None, -(e.get('index') or 0)))


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
    measured = {e["effort"] for e in mine if e['raw']}
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
    key = next((k for k, mid, _, _ in lineup() if mid == model), None)
    for tier, t in (tiers_for(key, entries) if key else {}).items():
        if t["model"] == model:
            out["notes"].append(f"{name(model)} at {t['effort']}: the {tier} tier")
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


# ---------------------------------------------------------------- a project's helper tiers

def plan(root):
    """A project's own choice for any tier, kept in its .board: the latest for each; a reset drops it."""
    out = {}
    for r in board.read(root, "models.jsonl"):
        if r["role"] not in TIERS:
            continue                             # roles from before the tiers are not carried over
        if r.get("model"):
            out[r["role"]] = r
        else:
            out.pop(r["role"], None)
    return out


def set_plan(root, role, model, effort, why=""):
    """A project's own choice for a tier; model None goes back to the colony default."""
    board.append(root, "models.jsonl", {"role": role, "model": model, "effort": effort or None, "why": why.strip(),
                                        "at": board.now()})


def scored(key, entries=None):
    """A provider's entries that have an Intelligence Index score, by model."""
    entries = standings() if entries is None else entries
    mine = {mid for k, mid, _, _ in lineup() if k == key}
    out = {}
    for e in entries:
        if e["model"] in mine and e["comparable"] and e["index"] is not None and e["variant"] != "non-reasoning" and e["effort"] != "ultra":
            out.setdefault(e["model"], []).append(e)
    for es in out.values():
        es.sort(key=lambda e: EFFORT_ORDER.get(e["effort"], 0))
    return out


def unmeasured(key, entries=None):
    """A provider's models with no Intelligence Index yet: shown as not yet measured, never picked."""
    have = scored(key, entries)
    return [mid for k, mid, _, _ in lineup() if k == key and mid not in have]


def nearest_effort(key, model, target):
    """Auto effort never means proactive delegation; equally near levels round down."""
    from . import providers
    supported = [e for e in providers.efforts_of(providers.get(key), model) if e != 'ultra']
    if not supported:
        return None
    position = EFFORT_ORDER[target]
    return min(supported, key=lambda e: (abs(EFFORT_ORDER.get(e, 0) - position), EFFORT_ORDER.get(e, 0)))


def tiers_for(key, entries=None):
    entries = standings() if entries is None else entries
    return {role: choice for role in TIERS if (choice := role_pick(key, role, entries))}


def role_pick(key, role, entries=None, *, balance=None, ceiling=None, blocked=()):
    from . import intelligence
    entries = standings() if entries is None else entries
    if balance is None:
        balance = board.registry()['settings'].get('auto_balance', 3)
    return intelligence.pick(key, role, intelligence.pairs(entries), balance, ceiling, blocked)


def best_effort(key, model, entries=None):
    """A model's highest-scoring effort: what a consultant runs at (rare, short, and worth the most)."""
    es = scored(key, entries).get(model)
    return max(es, key=lambda e: (e["index"], -EFFORT_ORDER.get(e["effort"], 0)))["effort"] if es else None


def effective(root):
    """The tiers a project's helpers run at: its own choice for a tier if it made one, else the colony default."""
    from . import selection
    return {t: selection.helper(root, t) for t in TIERS}


def write_helpers(root):
    """Keep a project's helper definitions at its tiers, where its program has them (see providers)."""
    from . import board as b, providers
    p = providers.of(root)
    if not hasattr(p, "write_helpers"):
        return []
    try:
        return p.write_helpers(b.workdir(root), effective(root))
    except OSError:
        return []


def plan_text(root):
    """The tiers as the agent is handed them at the start of each session."""
    from . import providers
    tiers = effective(root)
    if not tiers:
        return ""
    p = providers.of(root)
    named = hasattr(p, "helper_name")
    lines = [f"- {t}: {name(r['model'])} ({r['model']}) at {r['effort'] or 'its default'} effort"
             + (f", as the `{p.helper_name(t)}` helper" if named else "")
             + (" (this project's choice)" if r["own"] else "") for t, r in tiers.items()]
    return ("Your helpers (subagents) run at four tiers, colony's default from the benchmark cards unless this "
            "project set its own (`colony models` shows them): routine for ordinary work; chores for simple work "
            "that takes minor discernment (small edits, short clear instructions, light checks); rote for clear, "
            "mechanical tasks (copying, renaming, running a named command or test, simple lookups); step-up when "
            "the work struggles (stalls, retries, work redone). Step back down once the hard part is done. "
            "For a genuinely hard judgement, hand it to the step-up helper with a tight "
            "brief, accepting that it rebuilds context; ordinary work stays yours. Costly decisions go to consultants (colony consult)."
            + (f" Each tier is a helper: {p.HELPER_CALL}." if named else "") + "\n" + "\n".join(lines))


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
             "aime": ("math", "AIME", "fraction"), "aime_25": ("math", "AIME 2025", "fraction"),
             "ifbench": ("instructions", "IFBench", "fraction"), "lcr": ("long-context", "AA-LCR", "fraction"),
             "tau2": ("agentic", "τ²-Bench", "fraction"), "tau_banking": ("agentic", "τ²-Bench Banking", "fraction"),
             "terminalbench_hard": ("agentic", "Terminal-Bench Hard", "fraction"),
             "terminalbench_v2_1": ("agentic", "Terminal-Bench 2.1", "fraction")}
# Words a source adds to a model's name to say how it was run; any other word (pro, mini) is another model.
VARIANT_WORDS = set(EFFORTS) | {"reasoning", "non", "adaptive", "effort", "default", "fallback", "thinking",
                                "claude", "gpt", "openai", "anthropic"}


def pieces(name):
    """A model's name as its pieces, however it's written: GPT-5.6 Sol, gpt_5_6_sol and gpt-5-6-sol are the same;
    a date suffix (20251001) isn't part of the name."""
    return [p for p in re.split(r"[^a-z0-9]+", name.lower()) if p and not re.fullmatch(r"\d{8}", p)]


def match(model, entry):
    """The variant of this model an Artificial Analysis entry measured ("" for the model as listed), or None if
    it's another model. Every piece of the model's ID must be in the entry's slug and name, in any order (Claude
    4.5 Haiku is claude-haiku-4-5), and what's left must be words that say how it was run: an effort level,
    reasoning or not. A number left over (claude-sonnet-5 against Sonnet 5.5) or any other word (GPT-5.5 Pro)
    means another model. The effort is read from the name as well as the slug: 'GPT-6 Astra (max)'."""
    from collections import Counter
    mine = Counter(pieces(model))
    left = []
    for label in (entry.get("slug") or "", entry.get("name") or ""):
        theirs = Counter(pieces(label))
        if not label:
            continue
        if not mine or mine - theirs:
            return None
        extra = list((theirs - mine).elements())
        if any(p.isdigit() or p not in VARIANT_WORDS for p in extra):
            return None
        left += extra
    words = set(left)
    effort = next((x for x in ("xhigh", "max", "high", "medium", "low", "minimal", "ultra", "none") if x in words), None)
    if effort:
        return effort
    if "reasoning" in words:
        return "non-reasoning" if "non" in words else "reasoning"
    return ""


def claim(entry, lineup_models):
    """The lineup model an Artificial Analysis entry measured and its variant, (model, variant), or None: the most
    exact, if two could claim it."""
    hits = [(m, v) for m in lineup_models if (v := match(m, entry)) is not None]
    return min(hits, key=lambda h: len(h[1])) if hits else None


def records_from(data, lineup_models, date):
    """Artificial Analysis' entries as records for the models in the lineup; which of theirs matched nothing."""
    rows, matched, unmatched, unknown = [], set(), [], set()
    for entry in data:
        hit = claim(entry, lineup_models)
        if not hit:
            unmatched.append(entry.get("name") or entry.get("slug"))
            continue
        m, variant = hit
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
        snap = snapshots_dir() / f"{board.now()[:19].replace(':', '')}.json"      # their reply, kept as it came
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text(json.dumps(data))
        models = [mid for _, mid, _, _ in lineup()]
        rows, matched, unmatched, unknown = records_from(data, models, board.now()[:10])
        out.update(models=len(data), matched=matched, missing=[m for m in models if m not in matched],
                   unmatched=len(unmatched), unknown_fields=unknown, added=len(rows))
    p = board.home() / "bench" / "fetched.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1))
    return out


def days_since_fetch():
    """Days since Artificial Analysis was last asked (a large number if never)."""
    try:
        at = json.loads((board.home() / "bench" / "fetched.json").read_text())["at"]
    except (OSError, ValueError, KeyError):
        return 1e9
    import datetime
    then = datetime.datetime.fromisoformat(at.replace("Z", "+00:00"))
    return (datetime.datetime.now(datetime.timezone.utc) - then).total_seconds() / 86400


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


def framing(key):
    """The cards' tiers for a provider, framed as theirs, in a line: for the add-project form."""
    t = tiers_for(key)
    if not t:
        return ""
    return ("Helpers from the benchmark cards (Artificial Analysis): " + "; ".join(
        f"{tier}, {name(r['model'])}" + (f" at {r['effort']}" if r["effort"] else "") for tier, r in t.items())
        + ". A project can change any of them in its settings.")
