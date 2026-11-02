#!/usr/bin/env python3
"""Colony's whole test suite, run in several processes at once so it finishes in a few minutes.

    python3 tests/run.py            # every tests/test_*.py, in parallel
    python3 tests/run.py -j 4       # four workers (default: from the machine's cores and free memory)
    python3 tests/run.py tests.test_lead tests.test_vision   # only these modules

It runs the same tests as `python3 -m unittest tests.test_board ...`: each module, or each chunk of a long module
(contiguous runs of its tests, split where its measured time would otherwise set the wall time), runs in its own
process, longest first. Every test already keeps its state to itself (a temporary board home, tmux sessions named
from that home or the process id, ports bound to 0), so processes do not meet. One line per part, the failures'
output in full, and exit status 0 only if every part passed. Each test's time is kept in the user's cache
(~/.cache/colony/test-times.json) to balance the next run; without it, parts are balanced by test count.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARK = "@@colony-run@@ "
GUESS = 0.7            # seconds per test when a test has no recorded time
TIMES = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "colony" / "test-times.json"


def modules():
    return sorted(f"tests.{p.stem}" for p in (ROOT / "tests").glob("test_*.py"))


def ids(module):
    """The test ids of a module, in the order unittest runs them."""
    out = []

    def walk(suite):
        for t in suite:
            if isinstance(t, unittest.TestSuite):
                walk(t)
            else:
                out.append(t.id())
    walk(unittest.defaultTestLoader.loadTestsFromName(module))
    return out


def workers():
    """One per free core (cores the machine isn't already busy with: the live board, its consoles, helpers' test runs
    and the voice share it, and a suite crowding them starved its own timed tests), but no more than free memory allows
    (about 600 MB each: a test process and what it starts); at least three."""
    cores = os.cpu_count() or 2
    try:
        cores = max(3, cores - round(os.getloadavg()[0]))
    except OSError:
        pass
    try:
        avail = next(int(line.split()[1]) for line in open("/proc/meminfo") if line.startswith("MemAvailable:")) // 1024
    except (OSError, StopIteration, ValueError):
        avail = 4096
    return max(1, min(cores, avail // 600, 12))


def plan(mods, times, jobs):
    """Parts to run: each module whole, or split into contiguous chunks when it is long next to the rest."""
    tests = {m: ids(m) for m in mods}
    cost = lambda t: times.get(t, GUESS)
    total = sum(cost(t) for ts in tests.values() for t in ts)
    target = max(total / jobs / 1.5, 15.0)
    parts = []
    for m, ts in tests.items():
        if not ts:
            continue
        span = sum(cost(t) for t in ts)
        k = max(1, min(len(ts), round(span / target + 0.49)))
        if k == 1 or any(not t.startswith(m + ".") for t in ts):     # one that fails to import runs whole, to say why
            parts.append((m, [m], span))
            continue
        share, chunks, chunk, acc = span / k, [], [], 0.0
        for t in ts:                     # a chunk closes before the test that would take it past its share
            if chunk and acc + cost(t) > share:
                chunks.append((chunk, acc))
                chunk, acc = [], 0.0
            chunk.append(t)
            acc += cost(t)
        chunks.append((chunk, acc))
        parts += [(f"{m} [{i}/{len(chunks)}]", c, a) for i, (c, a) in enumerate(chunks, 1)]
    return sorted(parts, key=lambda p: -p[2])


def worker(names):
    """Inside one process: run these tests, report the outcome and each test's time on a marked line."""
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    took = {}

    class Timed(unittest.TextTestResult):
        def startTest(self, test):
            self._began = time.monotonic()
            super().startTest(test)

        def stopTest(self, test):
            super().stopTest(test)
            took[test.id()] = round(time.monotonic() - self._began, 3)

    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1, resultclass=Timed).run(suite)
    sys.stdout.flush()
    print("\n" + MARK + json.dumps({"run": result.testsRun, "ok": result.wasSuccessful(), "failures": len(result.failures),
                                    "errors": len(result.errors), "skipped": len(result.skipped),
                                    "unexpected": len(result.unexpectedSuccesses), "times": took}), flush=True)
    return 0 if result.wasSuccessful() else 1


def launch(part, limit):
    """One part in its own process (and process group, so a part that hangs past `limit` is ended with all it started)."""
    label, names, _ = part
    began = time.monotonic()
    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", *names], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        err += f"\n[tests/run.py] ended after {limit}s: this part did not finish"
    p = subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
    wall = time.monotonic() - began
    report = None
    for line in reversed(p.stdout.splitlines()):
        if line.startswith(MARK):
            report = json.loads(line[len(MARK):])
            break
    return label, p, report, wall


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("modules", nargs="*", help="test modules (default: every tests/test_*.py)")
    ap.add_argument("-j", "--jobs", type=int, default=int(os.environ.get("COLONY_TEST_JOBS") or 0) or None,
                    help="worker processes (default: from cores and free memory; or COLONY_TEST_JOBS)")
    ap.add_argument("--timeout", type=int, default=480, help="seconds before a part is ended as failed (default 480)")
    ap.add_argument("--worker", nargs="+", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.worker:
        return worker(args.worker)
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    jobs = args.jobs or workers()
    try:
        times = json.loads(TIMES.read_text())
    except (OSError, ValueError):
        times = {}
    parts = plan(args.modules or modules(), times, jobs)
    jobs = min(jobs, len(parts)) or 1
    print(f"{len(parts)} parts on {jobs} workers", flush=True)
    began, failed, ran, seen = time.monotonic(), [], 0, {}
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for fut in as_completed([pool.submit(launch, p, args.timeout) for p in parts]):
            label, p, report, wall = fut.result()
            if report:
                ran += report["run"]
                seen.update(report["times"])
                bad = report["failures"] + report["errors"] + report["unexpected"]
                ok = report["ok"] and p.returncode == 0
                status = "OK" if ok else f"FAILED ({report['failures']} failures, {report['errors']} errors)"
                skip = f", {report['skipped']} skipped" if report["skipped"] else ""
                print(f"{'ok ' if ok else 'BAD'} {label:<34} {report['run']:4d} tests{skip} {wall:7.1f}s  {status}", flush=True)
            else:
                ok, bad = False, 1
                print(f"BAD {label:<34}    ? tests {wall:7.1f}s  FAILED (exit {p.returncode}, no report)", flush=True)
            if not ok or bad:
                failed.append((label, p))
    for label, p in failed:
        print(f"\n{'=' * 70}\nFAILED: {label} (exit {p.returncode})\n{'=' * 70}")
        print(p.stderr.rstrip())
        out = "\n".join(line for line in p.stdout.splitlines() if not line.startswith(MARK)).strip()
        if out:
            print(f"--- stdout ---\n{out}")
    try:
        TIMES.parent.mkdir(parents=True, exist_ok=True)
        fresh = TIMES.with_name(f"{TIMES.name}.{os.getpid()}")
        fresh.write_text(json.dumps({**times, **seen}, sort_keys=True))
        fresh.replace(TIMES)                     # whole, even with two runs at once
    except OSError:
        pass
    wall = time.monotonic() - began
    print(f"\nRan {ran} tests in {wall:.1f}s on {jobs} workers: " + ("OK" if not failed else f"FAILED ({len(failed)} parts)"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
