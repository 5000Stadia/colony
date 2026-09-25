# Long projects with one strong agent

## Start
- Settle model and effort with the person. Medium is the best quality for cost; returns diminish sharply above it — save higher effort for the rare work where a slight gain in intelligence is worth the cost. Low loses real quality on building. For an unfamiliar model, check its guidance for where effort stops paying.
- Get the goal in their words: what would make them proud, the best real example, what would ruin it, what must never happen without them. Reflect a draft; offer a best guess beside each question.
- Plan: smallest end-to-end step first; unknown-path work (research, proof, market): a first pass that shows the shape. Person approves before building.
- Find the checks that settle quality in seconds (tests, build, word count, price floor).
- Fits in one sitting: just do it.

## Build
- One agent, one continuing session. Matched a fresh-agent-per-step harness at 0.74× cost (14 steps).
- Hand isolated work (search, research, bulk reading) to subagents that return conclusions. Keeps the main context for judgement.
- Don't add agents to check, remember or co-build. Reviewers, groups, memory layers: 1.2–5× cost, no measured gain.
- The project is the memory: docs updated in the same change, rules held by tests. Status pages and reconciled history added cost, not quality.
- Fixed a mistake that could recur: add a test that fails if it returns, and a line in the project's conventions.
- Settle wording that reads two ways — state your reading or ask. Most common cause of misses measured.
- Make small calls yourself; say which. Ask only when a decision is costly to undo or an act leaves the person's hands.
- Handle small gaps in place; build a recurring need once, shared, and say so.
- Propose plan changes when the work teaches you; the person decides.
- Judge stakes by consequence and reversibility, never by subject.
- Report cost with every result; ask before exceeding what the person set.
- Build what's asked, cleanly. Heavily guarded code cost ~2× to change later.
- Don't reorganise on a guess.

## Subagents
- Open each prompt with a scoped name: `searcher · src/billing · this task only`. Scoped names kept agents in scope 16/16; neutral names crossed every time.
- Give it only what its task needs; have it return the answer, not file dumps.
- Chores (fetch, rename, run tests, one-file change): low effort or a smaller model is fine.
- Finding problems or making a call: main agent's effort. Low-effort reviewers barely looked.

## Review (only where it can pay)
- Not on work built to a full spec: no measured gain.
- Worth considering where no test can judge and the work leaves the person's hands (a document to a client, a release). The one group win found unasked-for robustness a maintainer would want, at 5× cost; a solo build later found the same by chance.
- Reviewer reads, never edits; sees the stakes and the builder's doubt first.
- A single reviewer always finds something "major"; act on critical findings and ones two reviewers found independently.

## When the plain setup strains
Reach for these only when the symptom appears; the agent won't do them itself.
- Re-deriving settled decisions or retrying rejected approaches after many compactions → record decisions in commit messages; push the relevant past ones into the next step's brief.
- Defending a failed approach → next step in a fresh session with only the plan and the repo.
- Must run unattended → a driver that feeds steps, detaches, stops on a failing check or cost cap.
- The same mistake keeps returning → a reviewer that keeps lessons from its serious findings.
- Parallel agents in one place → scoped names, one owner per area.
- One area outgrows one context → its own lane, only once the strain recurs.
- Cost spread across sessions → a per-step ledger.
- Person steering without being in the conversation → a page of plan, history and cost with per-step notes.

## Designing any workflow piece
- Ask what consumes it. Unused in a small trial proves cost, not uselessness; cut only what's hollow at every scale.
- State the intention, not the mechanism — except where a mistake must be physically blocked (publishing, irreversible acts, others' data).
- Test each line: would a strong model do worse without it? If not, cut it.
- Judgement on the model, plumbing in code. Triggers are counts over records at boundaries, never an agent's opinion. The worker never sees the machinery. Parallel writers to a shared log need a lock.
- Quality before cost in any gauge; a miss acts at least as fast as an empty result.
- The person's "off" stands.
- Churn not about the work itself means the workflow is wrong; fix the architecture.

## Testing a change
- Test against the plain agent first, before building.
- Pre-register a forecast; ≥3 replicates (identical runs can differ widely in quality); blind judge with labels swapped; hidden acceptance tests; no hindsight hints.
- Check the measure itself: tied pass rates hid a real difference only a blind judge found.
- Confirm the task has no published answer.
- Wait out usage limits; never count a refused call. Change settings only between experiment sets.
- Replay triggers against real histories (free); seed long histories instead of growing them.
- Meter from `modelUsage`. A resumed session reports its cumulative cost — take differences.

## Safety
- Deny outward commands in Claude Code settings (`git push`, `gh`, publishing, `ssh`). Holds even with permissions bypassed. A floor, not a sandbox.

Evidence: `design/claims.md`, `~/Projects/garden/results/`.
