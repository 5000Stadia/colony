"""The clock: drives each row through build, checks, review where the person declared risk, and close."""
import concurrent.futures as cf
import re
import subprocess
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


def changed_files(project, a, b):
    return [f for f in _git(project, "diff", "--name-only", a, b).split() if f]


DOUBT = re.compile(r"LEAST CERTAIN:\s*(.*)", re.I)


def parse_doubt(said):
    """The builder's one sentence on what it is least sure of; it leads a reviewer's brief."""
    m = DOUBT.search(said or "")
    return m.group(1).strip()[:300] if m else ""


def review_decision(project, start, last, impact=None):
    """Trust the builder; review only where the person declared risk — a change touching a risky area,
    or a row whose impact, set when it was assigned, reaches `review_at_impact`."""
    cfg = project.config()
    if cfg["review"] in ("never", "always"):
        return cfg["review"] == "always", f"review is set to {cfg['review']}"
    at = cfg.get("review_at_impact")
    if impact is not None and at is not None and impact >= at:
        return True, f"impact {impact}/10, set when the row was assigned (rule: review at {at} or above)"
    files = changed_files(project, start, last)
    risky = [f for f in files for area in memory.risky(project) if f.startswith(area)]
    if risky:
        return True, f"touches risky areas: {', '.join(sorted(set(risky)))}"
    return False, f"touches no risky area ({len(files)} files): trusted"


class Stop(Exception):
    """The run stops for the person: a fork, a check that will not pass, or the cap."""


def run_row(project, row, cap, baseline=0.0):
    number, target, _ = row
    cfg = project.config()
    session = str(uuid.uuid4())
    start = commit(project, f"row {number}: start")

    def budget(want):
        left = cap - (spent(project) - baseline) if cap else want      # the cap is this run's, not the project's
        if left < 0.5:
            raise Stop(f"the cap of ${cap:.2f} is reached")
        return min(want, left)

    def fix(wave, signals):
        claude.call(project, (PROMPTS / "builder-fix.md").read_text().format(
            wave=wave, row=number, signals="\n".join(field.render(s) for s in signals)),
            agent="builder", row=number, wave=wave, budget=budget(cfg["fix_budget_usd"]), session=session, resume=True)

    built = claude.call(project, (PROMPTS / "builder-row.md").read_text().format(brief=memory.brief(project, row), row=number),
                        agent="builder", row=number, wave=0, budget=budget(cfg["builder_budget_usd"]), session=session)
    last = commit(project, f"row {number}: build")
    if not built["ok"] or "STATUS: done" not in built["said"]:
        raise Stop(f"row {number}'s builder did not finish ({built['subtype'] or 'no result'}): "
                   f"{built['said'][-200:]}; the row stays open")
    forks = [s for s in field.signals(project, row=number, wave=0) if s["kind"] == "fork"]
    if forks:
        raise Stop("a fork needs the person: " + "; ".join(field.render(s) for s in forks))
    impact, doubt = memory.impact(project, number), parse_doubt(built["said"])
    review, why = review_decision(project, start, last, impact)
    memory.ledger(project, "review", row=number, review=review, why=why, impact=impact, doubt=doubt)
    reviewers = specialists.load(project)
    if review and reviewers:
        run_checks(project, number, 1)
        change = _git(project, "diff", "--stat", start, last).strip() or "nothing changed"
        brief = memory.specialist_brief(project, row, doubt)

        def attack(name):
            prompt = (PROMPTS / "specialist.md").read_text().format(
                name=name, row=number, mission=reviewers[name], limit=cfg["signals_per_specialist"],
                brief=brief, change=change)
            return claude.call(project, prompt, agent=f"{name}@w1", row=number, wave=1,
                               budget=budget(cfg["specialist_budget_usd"]))

        with cf.ThreadPoolExecutor(len(reviewers)) as pool:
            list(pool.map(attack, reviewers))
        discard_edits(project)
        live = field.signals(project, row=number, wave=1)
        if any(s["kind"] == "fork" for s in live):
            commit(project, f"row {number}: fork")
            raise Stop("a fork needs the person: " + "; ".join(field.render(s) for s in live if s["kind"] == "fork"))
        commit(project, f"row {number}: review")
        wake = [s for s in live if field.wakes_builder(s)]
        if wake:
            fix(1, wake)
            commit(project, f"row {number}: review fixes")
    run_checks(project, number, 2)
    failing = [s for s in field.signals(project, row=number, wave=2) if s["kind"] == "check"]
    if failing:
        fix(2, failing)                     # one try, as any builder would, before the person is needed
        commit(project, f"row {number}: check fixes")
        run_checks(project, number, 3)
        failing = [s for s in field.signals(project, row=number, wave=3) if s["kind"] == "check"]
    if failing:
        health.review_health(project, number)   # a row that cannot close is evidence too
        raise Stop(f"row {number} cannot close: " + "; ".join(field.render(s) for s in failing))
    close(project, row, start)


def close(project, row, start):
    number, target, _ = row
    memory.close_row(project, number)
    memory.fold_notes(project, number)
    mapper.build(project)
    cost = sum(r["cost_usd"] for r in project.read("usage.jsonl") if r["row"] == number)
    fixed, serious = (len(x) for x in field.review_fixes(project.read("field.jsonl"), number))
    memory.ledger(project, "row-closed", row=number, target=target, cost_usd=round(cost, 4),
                  review_fixes=fixed, serious_fixes=serious, files=changed_files(project, start, "HEAD"))
    health.review_health(project, number)
    commit(project, f"row {number} closed: {target} (${cost:.2f})")


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
