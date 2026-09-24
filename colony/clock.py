"""The clock: drives each row through build, checks, specialist waves and close."""
import concurrent.futures as cf
import subprocess
import time
import uuid
from pathlib import Path

from . import claude, field, mapper, memory, specialists

PROMPTS = Path(__file__).parent / "prompts"


def _git(project, *args):
    return subprocess.run(["git", "-C", str(project.root), *args], capture_output=True, text=True).stdout


def commit(project, message):
    _git(project, "add", "-A")
    _git(project, "-c", "user.name=colony", "-c", "user.email=colony@localhost", "commit", "-qm", message, "--allow-empty")
    return _git(project, "rev-parse", "HEAD").strip()


def spent(project):
    return sum(r.get("cost_usd", 0) for r in project.read("usage.jsonl"))


def run_checks(project, row, wave):
    """Run the spine's checks; a failure is a critical signal, a pass clears an earlier failure."""
    for command in memory.checks(project):
        place = f"check:{command}"
        done = subprocess.run(command, shell=True, cwd=project.root, capture_output=True, text=True, timeout=1800)
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


class Stop(Exception):
    """The run stops for the person: a fork, a check that will not pass, or the cap."""


def run_row(project, row, cap):
    number, target, _ = row
    cfg = project.config()
    session = str(uuid.uuid4())
    start = commit(project, f"row {number}: start")
    brief = memory.brief(project, row)

    def budget(want):
        left = cap - spent(project) if cap else want
        if left < 0.5:
            raise Stop(f"the cap of ${cap:.2f} is reached")
        return min(want, left)

    claude.call(project, (PROMPTS / "builder-row.md").read_text().format(brief=brief),
                agent="builder", row=number, wave=0, budget=budget(cfg["builder_budget_usd"]), session=session)
    last = commit(project, f"row {number}: build")
    lineages = specialists.load(project)
    for wave in range(1, cfg["waves_per_row"] + 1):
        run_checks(project, number, wave)
        mapper.build(project)
        change = _git(project, "diff", "--stat", start, last).strip() or "nothing changed"
        brief_now = memory.brief(project, row, extra=change)

        def attack(name):
            lineage = lineages[name]
            prompt = (PROMPTS / "specialist.md").read_text().format(
                name=name, row=number, mission=lineage["mission"], limit=cfg["signals_per_specialist"],
                memory="\n".join(f"- {m}" for m in lineage["memory"]) or "- none yet",
                brief=brief_now, change=change, verify=verification(project, number, name))
            return claude.call(project, prompt, agent=f"{name}@w{wave}", row=number, wave=wave,
                               budget=budget(cfg["specialist_budget_usd"]))

        with cf.ThreadPoolExecutor(max(1, len(lineages))) as pool:
            list(pool.map(attack, lineages))
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
        raise Stop(f"row {number} cannot close: " + "; ".join(field.render(s) for s in failing))
    close(project, row, start, cap)


def close(project, row, start, cap):
    number, target, _ = row
    events = project.read("field.jsonl")
    answers = "\n".join(f"- #{e['of']} {'fixed' if e.get('fixed') else 'declined'}: {e['text']}"
                        for e in events if e["type"] == "resolve" and e.get("row") == number) or "- none"
    change = _git(project, "diff", "--stat", start, "HEAD").strip()
    history = project.design / "history.md"
    if not history.exists():
        history.write_text("# History\n\nAppended at every row close; never edited. The newest entry is last.\n\n")
    claude.call(project, (PROMPTS / "reconcile.md").read_text().format(
        row=number, target=target, change=change, answers=answers, now=memory.now_text(project),
        date=time.strftime("%Y-%m-%d")),
        agent="reconciler", row=number, wave=0, budget=project.config()["reconcile_budget_usd"])
    memory.close_row(project, number)
    gained = specialists.harvest(project, number)
    mapper.build(project)
    cost = sum(r["cost_usd"] for r in project.read("usage.jsonl") if r["row"] == number)
    memory.ledger(project, "row-closed", row=number, target=target, cost_usd=round(cost, 4),
                  lessons={k: len(v) for k, v in gained.items()})
    commit(project, f"row {number} closed: {target} (${cost:.2f})")


def run(project, max_rows=None, cap=None):
    if not memory.approved(project):
        raise Stop("the spine is not approved: read design/spine.md, correct it, then run `colony approve`")
    mapper.build(project)
    done = 0
    for row in memory.rows(project):
        if max_rows is not None and done >= max_rows:
            break
        run_row(project, row, cap)
        done += 1
    return done
