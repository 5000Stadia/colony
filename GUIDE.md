# Long projects with one strong agent — what the tests showed

Measured on Claude Opus 5.5 in Claude Code, September 2026, in the garden lab: pre-registered
forecasts, hidden test suites, replicates, a blind judge with labels swapped. Each point says how
strong its evidence is. Read it once at the start of a project; it is not a rulebook to recite.

## Starting

1. **Settle the model and effort with the person.** Effort was the largest lever measured: low →
   medium on Opus 5.5 was +11 judged points for $0.75 (three runs each). Opus 5.5 at medium is the
   default. xhigh and max were not tested here; published evaluations found diminishing returns. For
   another model, find the vendor's guidance and one independent evaluation, and answer the same
   questions for it: where does effort stop paying for building to a clear spec, does it pay for
   looking past the obvious, do more agents pay.
2. **Put the goal in the person's own words, and a plan they approve.** The smallest thing that works
   end to end first; for work whose path is unknown — research, a proof, a market — a first pass that
   shows the shape of the answer. The plan is a plain document in the repo; an agent writes and
   follows one natively.
3. **Match the structure to the work.** If it fits in one sitting, just do it.

## Building

4. **One agent is the default.** Every multi-agent shape tested lost on value: a group of standing
   agents cost 5× for a blind-judged win on one run; a critic on work built to a full spec cost
   1.2–1.7× for no measurable gain (pilots 5 and 6, three runs each); verifying its fixes stayed
   inside the noise.
5. **A plain continuing session is enough.** Given one step after another, a single session matched
   a harness that started a fresh agent every step — 86.3 against 86 of 87 hidden tests — at 0.74× the
   cost (three runs; 14 steps). Beyond a few dozen steps and repeated compaction, untested.
6. **The project is the memory.** Keep its docs true in the same step as the work, and let tests hold
   its rules. Fresh agents recovered every rule of a 14-row project from code and tests, even with its
   README cut to one line (12 runs); a status page and reconciled history read every step cost 1.7–2×
   and helped nothing there, and nothing at 14 rows in pilot 7 either.
7. **Keep lessons where the next worker looks.** When you fix a mistake later work could make again,
   leave a test that fails if it returns and a line in the project's own conventions. Plainly true:
   whoever does not know a lesson repeats the miss.
8. **Handle small gaps where you are.** Agents built one-off gaps in place, and met a large recurring
   need by building one shared module and telling the others (gap test, three runs each). That
   instinct is the strength to protect; do not add rules that stop it.
9. **Make the small calls; ask only when a decision is costly to undo** or an act leaves the person's
   hands. Say which calls you made.
10. **Judge stakes by consequence and reversibility, never by subject.** Models carry trained caution
    about some subjects; it is not a reason for review or ceremony.

## When more than one agent shares the work

11. **Name each agent for its role, scope and lifetime** — `builder · billing · row 12`. In 16 of 16
    runs names alone decided behaviour at a border: every scope-named agent left the neighbour's files
    and told the owner; every neutral-named one reached in. No harm resulted there, but with a
    neighbour mid-change that crossing is where conflicts come from. It is free; the shortest name that
    carries scope is enough.
12. **A second reviewer earns its place, if anywhere, where tests cannot judge** — something about to
    leave the person's hands for someone else, like a proposal to a client. Untested. The one group win
    (pilot 1) came from agents raising what a maintainer would fear — the product refused a tampered
    log — at 5× the cost; the same idea later arose by chance in a solo build.
13. **If you review:** the reviewer reads, never edits; it sees first what is at stake and what the
    builder is least sure of; a finding two reviewers made independently is more likely real.

## Shape

14. **Robustness bought with code has a price later.** The most heavily guarded product cost about
    twice as much to extend as a lean one (extension test, 17 products). Build what is asked, cleanly;
    add guards where a failure would matter.
15. **Do not reorganise on a guess.** No agent over-structured in the gap test, and the need to split
    one agent's work into lanes has not been seen at the scales tested.

## Measuring a change before adopting it

16. **Test it against the plain agent first.** Pre-register a forecast; run at least three replicates
    (identical runs spread by up to 11 judged points); judge blind with labels swapped; keep the
    acceptance tests hidden from the builder; never hint with hindsight.
17. **Replay a trigger against real histories before trusting it.** It is free: two automatic triggers
    here fired on most healthy projects when replayed.
18. **Seed a long history instead of paying to grow one.**
19. **Meter from `modelUsage`,** and remember a resumed session reports the whole session's cost so far:
    take differences, or every resumed call counts what came before it again.
20. **At milestones, look back:** is the work moving and the workflow effective first, token use second;
    put tradeoffs the numbers cannot settle to the person.

## Safety

21. **Refuse outward commands in Claude Code's own settings** — `git push`, `gh`, package publishing,
    `ssh`. Deny rules hold even with permissions bypassed. A floor, not a sandbox: for isolation, use a
    container.

The evidence behind every point is in `design/claims.md` and the garden's results
(`~/Projects/garden/results/`). A point changes when a better-run test says so.
