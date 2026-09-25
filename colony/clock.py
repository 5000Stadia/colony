"""The clock: drives each row through build, checks, specialist waves and close."""
import concurrent.futures as cf
import re
import subprocess
import time
import uuid
from pathlib import Path

from . import claude, field, health, mapper, memory, specialists

PROMPTS = Path(__file__).parent / "prompts"


def _git(project, *args):
    return subprocess.run(["git", "-C", str(project.root), *args], capture_output=True, text=True).stdout


def commit(project, message):
    _git(project, "add", "-A")
    _git(project, "-c", "user.name=colony", "-c", "user.email=colony@localhost", "commit", "-qm", message, "--allow-empty")
    return _git(project, "rev-parse", "HEAD").strip()


def discard_edits(project):
    """Reviewers read, never edit: whatever one changed in the work through its shell is put back.
    Their signals (.colony/) and their scratch/ stay."""
    _git(project, "checkout", "--", ".", ":!.colony", ":!scratch")
    _git(project, "clean", "-fdq", "--", ".", ":!.colony", ":!scratch")


def spent(project):
    return sum(r.get("cost_usd", 0) for r in project.read("usage.jsonl"))


def run_checks(project, row, wave):
    """Run the spine's checks; a failure is a critical signal, a pass clears an earlier failure."""
    for command in memory.checks(project):
        place = f"check:{command}"
        done = subprocess.run(command, shell=True, cwd=project.root, capture_output=True, text=True, timeout=1800)
        health.record_check(project, row, wave, command, done.returncode == 0)
        prior = [s for s in field.signals(project, row=row, wave=wave) if s["at"] == place]
        if done.returncode == 0:
            for s in prior:
                field.resolve(project, by="clock", row=row, wave=wave, of=s["id"], text="the check passes", fixed=True)
        elif not prior:
            tail = (done.stdout + done.stderr).strip()[-600:]
            field.post(project, by="clock", row=row, wave=wave, kind="check", severity="critical", at=place,
                       text=f"exit {done.returncode}: {tail}")


def verification(project, row, name):
    """A specialist's own earlier signals on this row and how they were answered, to re-check first.

    A fix nobody re-checks can be partial, overclaimed in the docs, or open a new hole; measured in
    the garden pilots, it is where a critic's gains were lost."""
    events = project.read("field.jsonl")
    mine = {e["id"]: e for e in events if e["type"] == "signal" and e.get("row") == row and e["by"].split("@")[0] == name}
    answers = [(mine[e["of"]], e) for e in events if e["type"] == "resolve" and e.get("of") in mine]
    if not answers:
        return ""
    lines = [f"- {s['at']}: you found \"{s['text'][:160]}\" — the builder {'fixed it' if a.get('fixed') else 'declined it'}: {a['text'][:160]}"
             for s, a in answers]
    return ("# Verify first\n\nBefore anything new, re-run the demonstration behind each of your earlier signals below "
            "against the work as it is now. If a fix did not hold, or it broke something near it, or the documentation now "
            "claims more than the fix does, signal it with `--kind unfixed` at the same `--at`; that always reaches the "
            "builder. If a decline was wrong, say why the same way.\n\n" + "\n".join(lines))


def changed_files(project, a, b):
    return [f for f in _git(project, "diff", "--name-only", a, b).split() if f]


ASSESSMENT = re.compile(r"ASSESSMENT:\s*confidence\s*(\d+)\s*/\s*10\s*[,—–-]*\s*(.*)", re.I)


def parse_assessment(said):
    """The builder's one-line forecast about its own row, if it gave one."""
    m = ASSESSMENT.search(said or "")
    if not m:
        return None
    return {"confidence": min(int(m.group(1)), 10), "note": m.group(2).strip()[:300]}


def review_decision(project, start, last, assessment=None):
    """Trust simple work; review where the person declared risk, or where the builder's own forecast puts
    the expected damage — chance of being wrong times how much it would hurt — over the project's
    standing rule. The forecast is recorded either way, so the rule can be checked against what review
    actually finds."""
    cfg = project.config()
    if cfg["review"] in ("never", "always"):
        return cfg["review"] == "always", f"review is set to {cfg['review']}"
    threshold = review_threshold(project)
    if assessment and assessment.get("risk") is not None and threshold is not None and assessment["risk"] >= threshold:
        return True, (f"risk {assessment['risk']}: the row's impact {assessment['impact']}/10, set when it was assigned, "
                      f"and the builder's confidence {assessment['confidence']}/10 (rule: review at {threshold})")
    lines = sum(int(a) + int(d) for a, d, *_ in (row.split("\t") for row in
                _git(project, "diff", "--numstat", start, last).splitlines()) if a.isdigit() and d.isdigit())
    files = changed_files(project, start, last)
    risky = [f for f in files for area in memory.risky(project) if f.startswith(area)]
    if risky:
        return True, f"touches risky areas: {', '.join(sorted(set(risky)))}"
    if cfg["review_min_lines"] is not None and lines >= cfg["review_min_lines"]:
        return True, f"{lines} lines changed"
    if cfg["review_min_files"] is not None and len(files) >= cfg["review_min_files"]:
        return True, f"{len(files)} files changed"
    return False, f"small change ({lines} lines, {len(files)} files): trusted"


def review_threshold(project):
    """The project's standing review threshold as experience has moved it, or None if it has none."""
    cfg = project.config()
    if cfg.get("review_if_risk_at_least") is None:
        return None
    moves = [e for e in project.read("ledger.jsonl") if e["kind"] == "threshold"]
    return moves[-1]["to"] if moves else cfg["review_if_risk_at_least"]


def adapt_threshold(project, row, closed=True):
    """Gentle guidance from outcomes, leaning to quality: a review that found real problems, or a row review
    trusted that then broke a check, lowers the threshold a step at once; only three empty reviews in a
    row raise it a step. Bounded, and recorded, so it can be read and undone."""
    cfg = project.config()
    current = review_threshold(project)
    if current is None or not cfg.get("review_adapt"):
        return
    ledger = project.read("ledger.jsonl")
    reviewed = {e["row"] for e in ledger if e["kind"] == "review" and e["review"]}
    outcomes = [e["review_fixes"] for e in ledger if e["kind"] == "row-closed" and e["row"] in reviewed]
    since = [e for e in ledger if e["kind"] == "threshold"]
    last_move_row = since[-1]["row"] if since else 0
    recent = [e["review_fixes"] for e in ledger if e["kind"] == "row-closed" and e["row"] in reviewed and e["row"] > last_move_row]
    step, new, why = cfg["review_adapt_step"], None, None
    missed = row not in reviewed and any(e["kind"] == "evidence" and e["type"] == "regression" and e["row"] == row
                                         for e in ledger)
    if missed:
        new, why = current - step, f"row {row} was trusted without review and broke a check that had passed"
    elif closed and row in reviewed and outcomes and outcomes[-1] > 0:
        new, why = current - step, f"row {row}'s review found {outcomes[-1]} real problem(s)"
    elif closed and len(recent) >= 3 and not any(recent[-3:]):
        new, why = current + step, "three reviews in a row found nothing"
    if new is not None:
        new = min(max(new, cfg["review_floor"]), cfg["review_ceiling"])
        if new != current:
            memory.ledger(project, "threshold", row=row, **{"from": current, "to": new, "why": why})


class Stop(Exception):
    """The run stops for the person: a fork, a check that will not pass, or the cap."""


def run_row(project, row, cap, baseline=0.0):
    number, target, _ = row
    cfg = project.config()
    session = str(uuid.uuid4())
    start = commit(project, f"row {number}: start")
    brief = memory.brief(project, row)

    def budget(want):
        left = cap - (spent(project) - baseline) if cap else want      # the cap is this run's, not the project's
        if left < 0.5:
            raise Stop(f"the cap of ${cap:.2f} is reached")
        return min(want, left)

    built = claude.call(project, (PROMPTS / "builder-row.md").read_text().format(brief=brief, row=number),
                        agent="builder", row=number, wave=0, budget=budget(cfg["builder_budget_usd"]), session=session)
    last = commit(project, f"row {number}: build")
    if not built["ok"] or "STATUS: done" not in built["said"]:
        raise Stop(f"row {number}'s builder did not finish ({built['subtype'] or 'no result'}): "
                   f"{built['said'][-200:]}; the row stays open")
    forks = [s for s in field.signals(project, row=number, wave=0) if s["kind"] == "fork"]
    if forks:
        raise Stop("a fork needs the person: " + "; ".join(field.render(s) for s in forks))
    mapper.build(project)
    lineages = specialists.load(project)
    assessment = parse_assessment(built["said"])
    row_impact = memory.impact(project, number)
    if assessment and row_impact is not None:
        # Impact from whoever assigned the row, before the work; confidence from whoever did it, after.
        assessment.update(impact=row_impact, risk=(10 - assessment["confidence"]) * row_impact)
    review, why = review_decision(project, start, last, assessment)
    memory.ledger(project, "review", row=number, review=review, why=why, assessment=assessment)
    for wave in range(1, (cfg["waves_per_row"] if review and lineages else 0) + 1):
        run_checks(project, number, wave)
        mapper.build(project)
        change = _git(project, "diff", "--stat", start, last).strip() or "nothing changed"
        brief_now = memory.specialist_brief(project, row, changed_files(project, start, last), assessment)

        def attack(name):
            lineage = lineages[name]
            prompt = (PROMPTS / "specialist.md").read_text().format(
                name=name, row=number, wave=wave, mission=lineage["mission"], limit=cfg["signals_per_specialist"],
                memory="\n".join(f"- {m}" for m in lineage["memory"]) or "- none yet",
                brief=brief_now, change=change, verify=verification(project, number, name))
            return claude.call(project, prompt, agent=f"{name}@w{wave}", row=number, wave=wave,
                               budget=budget(cfg["specialist_budget_usd"]))

        with cf.ThreadPoolExecutor(max(1, len(lineages))) as pool:
            list(pool.map(attack, lineages))
        discard_edits(project)
        live = field.signals(project, row=number, wave=wave)
        if any(s["kind"] == "fork" for s in live):
            commit(project, f"row {number}: fork")
            raise Stop("a fork needs the person: " + "; ".join(field.render(s) for s in live if s["kind"] == "fork"))
        wake = [s for s in live if field.wakes_builder(s, wave)]
        commit(project, f"row {number}: wave {wave} signals")
        if not wake:
            break
        claude.call(project, (PROMPTS / "builder-fix.md").read_text().format(
            wave=wave, row=number, signals="\n".join(field.render(s) for s in wake)),
            agent="builder", row=number, wave=wave, budget=budget(cfg["fix_budget_usd"]), session=session, resume=True)
        last = commit(project, f"row {number}: wave {wave} fixes")
    run_checks(project, number, cfg["waves_per_row"] + 1)
    failing = [s for s in field.signals(project, row=number, wave=99) if s["kind"] == "check"]
    if failing:
        # One try, as any builder would, before the person is needed.
        claude.call(project, (PROMPTS / "builder-fix.md").read_text().format(
            wave=cfg["waves_per_row"] + 1, row=number, signals="\n".join(field.render(s) for s in failing)),
            agent="builder", row=number, wave=cfg["waves_per_row"] + 1, budget=budget(cfg["fix_budget_usd"]),
            session=session, resume=True)
        commit(project, f"row {number}: check fixes")
        run_checks(project, number, cfg["waves_per_row"] + 2)
        failing = [s for s in field.signals(project, row=number, wave=99) if s["kind"] == "check"]
    if failing:
        health.review_health(project, number)   # a row that cannot close is evidence too
        adapt_threshold(project, number, closed=False)
        raise Stop(f"row {number} cannot close: " + "; ".join(field.render(s) for s in failing))
    close(project, row, start, cap, baseline)


def close(project, row, start, cap, baseline=0.0):
    number, target, _ = row
    events = project.read("field.jsonl")
    answers = "\n".join(f"- #{e['of']} {'fixed' if e.get('fixed') else 'declined'}: {e['text']}"
                        for e in events if e["type"] == "resolve" and e.get("row") == number) or "- none"
    change = _git(project, "diff", "--stat", start, "HEAD").strip()
    cfg = project.config()
    keep = min(cfg["reconcile_budget_usd"], cap - (spent(project) - baseline)) if cap else cfg["reconcile_budget_usd"]
    if cfg["reconcile"] and keep >= 0.1:
        claude.call(project, (PROMPTS / "reconcile.md").read_text().format(
            row=number, target=target, change=change, answers=answers, now=memory.now_text(project),
            date=time.strftime("%Y-%m-%d")),
            agent="reconciler", row=number, wave=0, budget=keep)
        enforce_now_budget(project, number)
    memory.close_row(project, number)
    memory.fold_notes(project, number)
    gained = specialists.harvest(project, number)
    mapper.build(project)
    cost = sum(r["cost_usd"] for r in project.read("usage.jsonl") if r["row"] == number)
    fixed = sum(1 for e in events if e["type"] == "resolve" and e.get("row") == number and e.get("fixed")
                and e.get("by") == "builder")
    # The outcome the builder's assessment is scored against: real problems review found and fixed.
    memory.ledger(project, "row-closed", row=number, target=target, cost_usd=round(cost, 4),
                  review_fixes=fixed, lessons={k: len(v) for k, v in gained.items()},
                  files=changed_files(project, start, "HEAD"))
    health.review_health(project, number)
    adapt_threshold(project, number)
    note_path = project.state / "closing-note.md"
    note = note_path.read_text().strip() if note_path.exists() else ""
    note_path.unlink(missing_ok=True)
    # The narrative lives in the closing commit: git is the history every agent already knows to read.
    commit(project, f"row {number} closed: {target} (${cost:.2f})" + (f"\n\n{note}" if note else ""))


def enforce_now_budget(project, row):
    """NOW is the one page every agent reads, so its length is enforced, not requested: one call to cut
    it back when it overruns, and a ledger entry if even that fails."""
    limit = project.config()["now_max_lines"]
    lines = [l for l in memory.now_text(project).splitlines() if l.strip()]
    if len(lines) <= limit:
        return
    claude.call(project, f"You are `reconciler · row {row} · trimming NOW`. `design/now.md` has {len(lines)} lines; its "
                f"budget is {limit}, because every agent reads it first. Rewrite it within {limit} lines, keeping what a "
                "fresh agent most needs to continue; what belongs to the past is already in the git history. Change no "
                "other file. Do not run git.", agent="reconciler", row=row, wave=0, budget=0.5)
    after = len([l for l in memory.now_text(project).splitlines() if l.strip()])
    if after > limit:
        memory.ledger(project, "now-over-budget", row=row, lines=after, limit=limit)


def run(project, max_rows=None, cap=None):
    if not memory.approved(project):
        raise Stop("the spine is not approved: read design/spine.md, correct it, then run `colony approve`")
    mapper.build(project)
    done, baseline = 0, spent(project)
    for row in memory.rows(project):
        if max_rows is not None and done >= max_rows:
            break
        run_row(project, row, cap, baseline)
        done += 1
    return done
