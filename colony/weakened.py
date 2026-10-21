"""A weakened test, caught and owned: an agent can make its work pass by loosening the tests instead of fixing it.

Read from each new commit, by rule, no model: in the test files, assertions or test functions taken out (more than put
back, so a test moved or rewritten is not one), a skip or a forced pass put in, or a test file deleted. The signals
follow tampercheck's (measured at 0.7% false alarms on 1,331 commits). Where the commit's message gives
no reason (says nothing of tests), the agent hears it as a note: restore them, or say why with colony noted. Until it
does, the note is on the project's Roadmap tab with the person's other open notes.
"""
import json
import re
import subprocess
from pathlib import Path

from . import board

TESTS = re.compile(r"(^|/)(tests?|__tests__|specs?)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$"
                   r"|Tests?\.(java|kt|cs)$")
ASSERTS = re.compile(r"\bassert\w*\b|\bexpect\(|\bt\.(Error|Errorf|Fatal|Fatalf|Fail)\b|\bassert(_eq|_ne)?!|"
                     r"\b(pytest\.raises|assertRaises)\b|\.should\b")
CASES = re.compile(r"^\s*(async\s+)?def test\w*|^\s*(it|test)\(|\bfunc Test\w+\(|#\[test\]|@Test\b")
# unconditional only: a skipif (on a platform, a missing tool) is a test that still runs where it can
SKIPS = re.compile(r"@(pytest\.mark\.)?(skip|xfail)\b(?!if)|unittest\.skip\b(?!If|Unless)|\.skipTest\(|"
                   r"\b(it|test|describe)\.skip\(|\bx(it|describe)\(|\bt\.Skip(Now|f)?\(|#\[ignore\]|@Disabled\b|@Ignore\b")
# a pass forced: only these run, or a check that cannot fail
FORCED = re.compile(r"\b(it|test|describe)\.only\(|^\s*assert\s+True\b|\bassert\(true\)|"
                    r"\|\|\s*true\b|\bsys\.exit\(0\)")
REASON = re.compile(r"\b(tests?|tested|testing|assert\w*|skip\w*|xfail)\b", re.I)
MOST = 50                    # commits read at once, after a long gap


def git(root, *args):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, errors="replace")
    return r.stdout if r.returncode == 0 else ""


def weakened(root, commit):
    """What the commit took from its tests, as plain facts ([] when nothing): assertions or tests out, skips in, files gone."""
    out, gone, lines, tests = [], [], {"+": [], "-": []}, set()
    for row in git(root, "show", "--format=", "-M", "--name-status", commit).splitlines():
        status, *paths = row.split("\t")
        tests |= {x for x in paths if TESTS.search(x)}
        if status == "D" and TESTS.search(paths[0]):
            gone.append(paths[0])
    if not tests:
        return out
    diff = git(root, "show", "--format=", "-M", "-U0", "--no-color", commit, "--", *sorted(tests))
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            if line.startswith("+++ "):
                path = line[6:] if line[4:6] == "b/" else None
            elif line != "--- /dev/null":
                path = line[6:]                         # a deleted file has no b/ side: its removed lines still count
            continue
        if path and TESTS.search(path) and line[:1] in "+-":
            lines[line[0]].append(line[1:])
    count = lambda pat, side: sum(bool(pat.search(x)) for x in lines[side])
    asserts = count(ASSERTS, "-") - count(ASSERTS, "+")
    cases = count(CASES, "-") - count(CASES, "+")
    skips = count(SKIPS, "+") - count(SKIPS, "-")
    forced = count(FORCED, "+") - count(FORCED, "-")
    if gone:
        out.append(f"deleted {', '.join(gone[:3])}{' …' if len(gone) > 3 else ''}")
    if cases > 0:
        out.append(f"{cases} test{'s' if cases > 1 else ''} taken out")
    if asserts > 0:
        out.append(f"{asserts} assertion{'s' if asserts > 1 else ''} taken out")
    if skips > 0:
        out.append(f"{skips} skip{'s' if skips > 1 else ''} put in")
    if forced > 0:
        out.append(f"{forced} forced pass{'es' if forced > 1 else ''} put in (.only, assert True, || true)")
    return out


def check(root):
    """Called every few seconds per project: each commit since the last look that weakens its tests and does not say
    why is put to the agent, once."""
    head = git(root, "rev-parse", "HEAD").strip()
    if not head:
        return
    path = Path(root) / ".board" / "weakened.json"
    try:
        seen = json.loads(path.read_text()).get("seen")
    except (OSError, ValueError):
        seen = None
    if seen == head:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"seen": head}))
    if not seen:                                        # first look: what was committed before isn't news
        return
    commits = git(root, "rev-list", "--no-merges", f"-n{MOST}", f"{seen}..{head}").split()
    for c in reversed(commits):
        found = weakened(root, c)
        said = git(root, "log", "-1", "--format=%B", c)
        if found and not REASON.search(said):
            board.add_note(root, None, f"Commit {c[:7]} ({said.splitlines()[0][:60] if said else ''}) weakened its tests "
                           f"and its message gives no reason: {'; '.join(found)}. If the code was what needed fixing, "
                           "restore them; if the tests were wrong, say why with colony noted.", author="colony", kind="weakened")
