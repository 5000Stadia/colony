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
2. **Put the goal in the person's own words, and a plan they approve.** Find out, in their words, what
   would make them proud of it (not merely satisfied), the best real example of this kind of work and
   what makes it the bar, what would ruin it, and what must never happen without them. Reflect a draft
   back rather than asking open questions, and give your best guess beside each open question so they
   correct instead of starting from scratch. The smallest thing that works end to end first; for work
   whose path is unknown — research, a proof, a market — a first pass that shows the shape of the
   answer. The plan is a plain document in the repo. From one sentence each, this drew a sound plan,
   checks and stakes for a receipts tool, a thousand-page novel and a pottery business (door test).
2a. **Find the checks that settle quality in seconds** — tests and a build for code; a word count,
   placeholder and cliché checks for a novel; a price floor for a shop. Most kinds of work have some.
3. **Match the structure to the work.** If it fits in one sitting, just do it.

## Building

4. **Keep the judgement in one agent; hand off isolated work.** Agents added to check, remember or
   co-build lost on value every time they were tested: a group of standing agents cost 5× for a
   blind-judged win on one run; a critic on work built to a full spec cost 1.2–1.7× for no measurable
   gain (pilots 5 and 6, three runs each); verifying its fixes stayed inside the noise. Handing a
   self-contained task — a broad search, research, bulk reading — to a subagent that returns only its
   conclusion is a different thing and plainly good: it keeps the main context for the judgement that
   needs it. Name each one for its role, scope and lifetime (see *Delegating to subagents*): that is
   where the naming result applies most.
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
   hands. Say which calls you made. When the work teaches you what should come next, propose the
   change to the plan; the person decides.
9a. **Settle wording that can be read two ways.** It was the most common cause of misses measured:
   pilot 7's shared miss (5 of 6 runs) traced to "a paid invoice", and the seeded test's one miss to a
   field that could be a count or a list. When a step can be read two ways, say which reading you took,
   or ask if the difference matters.
9b. **Report cost beside every result, and ask before going past what the person set.**
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
    builder is least sure of. A reviewer always finds something and calls it major — with "any major
    finding wakes the builder" the loop never went quiet (pilot 3) — so act on critical findings and on
    those two reviewers made independently.

## Delegating to subagents

A main agent at medium effort handing work to subagents:

- **Open every subagent's prompt with a name that carries its role, scope and lifetime** —
  `searcher · src/billing · this task only`, `reviewer · the export change · reads, never edits`. The
  naming result above (16 of 16 runs) is exactly this situation: a delegate told who it is and what it
  owns stayed inside it and reported what lay outside. It costs nothing.
- **Chores can go cheap; judgement cannot.** Small, clear doing — fetch, rename, run the tests and
  report, a one-file change — went well at low effort ($0.18–0.47 a task in the naming and gap tests).
  Looking and deciding did not: low-effort critics barely looked (≈30 s, no fixes, pilot 5). Send a
  subagent that must find problems or make a call at the main agent's effort. In Claude Code a main
  agent usually chooses a subagent's model rather than its effort; which cheap route serves chores
  better — a smaller model or low effort — is untested.
- **Give it only what its task needs,** and have it return the answer, not the file dumps.

## Shape

14. **Robustness bought with code has a price later.** The most heavily guarded product cost about
    twice as much to extend as a lean one (extension test, 17 products). Build what is asked, cleanly;
    add guards where a failure would matter.
15. **Do not reorganise on a guess.** No agent over-structured in the gap test, and the need to split
    one agent's work into lanes has not been seen at the scales tested.

## When the plain setup strains

None of this is needed at the scales tested, and none of it is tested at the scale where it would be.
It is here for the day a workflow outgrows the plain setup. Each remedy is one the agent will not
reach for on its own from inside its session; what it does unprompted (running its tests, searching
the code, asking when stuck, suggesting next steps) is left out. Before adopting one, test it against
the plain setup on a seeded version of the problem.

| What you see | What it likely means | What to reach for | Why the agent will not do it itself |
|---|---|---|---|
| After many compactions the agent re-derives settled things, or tries an approach the project already rejected | the history outgrew the context, and the decision is not in the code | record each step's decisions in its commit message, and push the few past decisions that bear on the next step into its brief (a retrieval with no model call; colony's `memory-runner` branch) | it cannot search for what it does not know it forgot |
| After a bad step the agent keeps defending a failed approach | the context is anchored on it | start the next step in a fresh session with only the plan and the repo | it cannot reset its own context |
| The work must run unattended — overnight, or with the terminal closed | a session ends when its terminal does | a driver that feeds steps, detaches, stops on a failing check or a cost cap, and reports when done (colony's runtime) | it cannot outlive its session |
| Something is about to leave the person's hands — a document to a client, a release — and no test can judge it | one reader cannot see its own blind spots | a separate read-only reviewer who meets it as the recipient will, shown the stakes and the author's doubt first | it cannot give itself independent eyes, and rarely asks for them |
| The same kind of mistake keeps returning despite fixes and conventions | the lesson is not reaching whoever repeats it | a reviewer that keeps the lessons of its serious findings and looks for them first | a new session starts without them |
| Several agents work in one place at once | their borders are unclear | name each for its role, scope and lifetime; one owner per area | only arises with parallel agents |
| One area's work no longer fits one context, or moves at its own pace | the work has outgrown one agent | a lane: its own agent and plan for that area — only once the strain recurs, never on a guess | splitting itself is not a call it can make |
| Cost is spread across many sessions and nobody can say where it goes | no per-step accounting | a per-step ledger metered from `modelUsage`, differencing resumed sessions | each session sees only its own total |
| The person is not in the conversation but wants to follow and steer | nothing shows the work at a glance | a page of plan, history and cost, with a note box per step that reaches that step's agent | it speaks only when spoken to |

## Designing any workflow piece

When something does need building, these held across AgentBridge, Kernos and colony:

- **Ask what consumes it.** An artifact nobody reads and nothing depends on is cost, however
  professional it feels. But non-use in a small trial proves its cost, not its uselessness: cut what is
  hollow at every scale, and keep scale machinery absent until the scale arrives.
- **State the intention, not the mechanism,** except where a mistake must be physically blocked —
  publishing, irreversible acts, data that is not the person's. Precise rules encode the first case
  imagined; intentions survive the ones nobody imagined. Test every line: would a strong model do worse
  without it? If not, cut it.
- **Judgement on the model, plumbing in code.** A trigger is a count over the records, taken at a
  boundary (a step's end, a checkpoint), never an agent's opinion and never every turn. The worker
  never sees the machinery around it.
- **Quality before cost in every gauge.** Report cost beside quality, always; never let a cost number
  argue quality down, and never offer the person's declared stakes up for saving. Act on a miss at
  least as fast as on an empty result.
- **The person's "off" stands.** Nothing switches itself back on over their decision.
- **Back-and-forth that is not about the work's own nuance** signals a mismatch between the workflow
  and what helps: fix the architecture, do not keep patching.

## Measuring a change before adopting it

16. **Test it against the plain agent first.** Pre-register a forecast; run at least three replicates
    (identical runs spread by up to 11 judged points); judge blind with labels swapped; keep the
    acceptance tests hidden from the builder; never hint with hindsight.
16a. **Check the measure itself.** Pass rates tied at the ceiling in pilot 1; only the blind judge
    found the real difference (the group's product refused a tampered log). A measure that cannot
    separate the arms is not evidence they are equal.
16b. **Make sure the task is fresh:** search for its published answer at design time. Pilot 2 was
    void because both arms found and installed a published record.
16c. **Wait out usage limits, repeat the refused call, and never count it as a round;** run the arms
    of a comparison together so they face the same limits. Change settings only between experiment
    sets, never inside one. Parallel writers to a shared log need a lock.
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
