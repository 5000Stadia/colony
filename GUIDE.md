# Starting a long project

These are defaults from measured runs, not rules. Where you see a better way, take it and say why; hold firm on what the person decides and what can't be undone.

## Horizon first
- Before planning the path, agree the horizon with the person: the few large milestones and what each looks like (v1, v2, v3 for a product; year 1, 2, 3 for a business; drafts for a book). Build each step toward them; a path built without the horizon leads to costly rebuilds.

## Day one
- Settle the model and effort with the person. Plan at high effort, then build at medium: planning is a small share of the tokens and the most leveraged judgement in the project, since a wrong milestone costs a rebuild. Medium effort gives the best quality for the cost on real building. Low effort is enough for clear, small, well-specified steps, but loses real quality when one step is itself a large build.
- Get the goal in the person's words: what would make them proud, the best real example, what would ruin it, what must never happen without them. Offer your best guess with each question.
- Plan a first end-to-end step toward the first milestone. Where the path is unknown (research, a proof, a market), make a first pass that shows the shape of the work.

## Building
- One agent in one continuing session does as well as a fresh agent per step, and costs less.
- The project is the memory: its code, tests and docs. Separate status pages and histories added cost, not quality.
- When you fix a mistake that could come back, make it hard to repeat.
- When wording can be read two ways, state your reading or ask. This caused most misses.
- Make small calls yourself and say which you made. Ask when a decision is costly to undo or an action leaves the person's hands.
- Judge stakes by consequence and reversibility, never by subject.
- If the work shows the plan or a milestone is wrong, say so with the reason. How to do a step is your call; scope and milestones are the person's.
- Guard against what would fail silently or spoil what later work builds on; skip guards nobody would miss. Every extra layer is something later changes must work around.
- Finish what you take on: a piece is done when it does what it was meant to do, so resolve what stands in the way, however many turns it takes. Hold each milestone to what serves its purpose; other findings wait. Don't go looking for faults where nothing suggests one.
- Where the running product has state that tests don't reach (a game world, a UI, a simulation), give yourself a way to read it as data, such as a debug export of the state as JSON, and check against that.

- If the person wants to follow and steer the project without being in the conversation, put it on the board (`colony track`): one page for all their projects, with gates for their decisions and notes that reach you when they're relevant.

## Codex
Starting points from [current evidence](docs/codex-models.md), not measured colony results.

- Start ordinary building on GPT-5.6 Sol at medium; reserve Astra for hard decisions and failures. Judge cost by accepted work, including retries and corrections.
- Plan substantial work at high effort, then build at medium. Try Luna low or medium for clear chores; raise effort when they need reasoning across files.
- Review consequential changes with Sol high; try Astra medium or high for defects across components or when a cheaper review misses something.
- Start Astra at low and raise effort for a concrete difficulty. Reserve xhigh or max for hard problems; Ultra also delegates work to subagents.
- For a painter seat, start the directing agent at Sol medium, agree visual references, and iterate against them. Its effort is separate from image quality; the person's eye decides done.

## Other agents
- Handing isolated work to subagents is good. Any other added agent often costs 1.2–5× the tokens and must justify it; agents added only to check, remember or co-build one agent's well-specified work didn't.
- Add agents when it's clearly the better approach for the project (genuinely parallel domains, scale beyond one context) or when a real difficulty would benefit from a specialist's focus. The gain is a clean context and a narrow brief, not more intelligence; for a problem that is simply hard, raise effort.
- Open each subagent's prompt with a scoped name: `searcher · src/billing · this task only`. Scoped names kept agents in scope; neutral names drifted.
- Chores can run at low effort or on a smaller model. Finding problems or making calls cannot: low-effort reviewers barely look.

## Review
- Ask of each piece of work: if a few bugs or inaccuracies are in this, will they hurt the project going forward? Chapter 2 of a book: no. A load-bearing rebuild of a core system: yes.
- Where yes, have a separate agent, without the builder's context, try to break it and use it the way its real users will; let it decide how for this kind of work. In testing this caught nearly all of what it went after at about twice the build's cost, which is cheap next to fixing what later work was built on.
- Your own tests share your understanding: if you misread the spec, they pass anyway. Where the spec itself is long, subtle or ambiguous, also have a separate agent write tests from the spec alone.
- For what tests can't express (prose, design, a document leaving the person's hands), use a reader who meets it as its recipient will. The reviewer reads, never edits, and sees the stakes first.
- Where the goal is quality rather than correctness, and above all where quality is in the person's eye (how it looks, feels or reads), pin their standard down before building: the best real example they would hold it to, and what in it makes it good to them; if they can't name it yet, show contrasting sketches to find it. Then iterate with a fresh critic that scores the work against those named qualities and that example, rather than picking a winner, backed by measurements it can't see (a profiler, image diffs, the state read as data).
- One reviewer always finds something "major".

## As the project grows
Reach for these when the symptom appears.
- Re-deriving settled decisions or retrying rejected approaches after many compactions → record decisions in commit messages and put the relevant ones in the next step's brief.
- Defending a failed approach → take the next step in a fresh session with only the plan and the repo.
- The same mistake keeps coming back → a reviewer that keeps the lessons of its serious findings.
- One area outgrows one context → its own lane, once the strain recurs.

## Designing a workflow piece
- Ask what will use it. Going unused in a small trial shows its cost, not that it's useless.
- Try it against the plain setup on a small version of the problem first.
- State the intention, not the mechanism, except where a mistake must be physically blocked: publishing, irreversible actions, other people's data.
- Keep a line only if a strong model would do worse without it.
- Judgement belongs to the model, plumbing to code. Trigger on counts over records, not an agent's opinion.
- Put quality before cost in any gauge.
- Churn that isn't about the work means the workflow is wrong. Fix the architecture.

## Safety
- Deny outward commands in Claude Code settings: `git push`, `gh`, publishing, `ssh`. The denial holds even with permissions bypassed. It is a floor, not a sandbox.
