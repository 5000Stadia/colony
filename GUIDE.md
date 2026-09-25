# Starting a long project

## Horizon first
- Before planning the path, agree the horizon with the person: the few large milestones and what each looks like (v1, v2, v3 for a product; year 1, 2, 3 for a business; drafts for a book). Build each step toward them; a path built without the horizon leads to costly rebuilds.

## Day one
- Settle the model and effort with the person. Plan at high effort, then build at medium: planning is a small share of the tokens and the most leveraged judgement in the project, since a wrong milestone costs a rebuild. Medium effort gives the best quality for the cost on real building; higher effort adds little there, so save it for planning and rare work where a small gain is worth the price. Low effort is enough for clear, small, well-specified steps at about half the cost, but loses real quality when one step is itself a large build.
- Get the goal in the person's words: what would make them proud, the best real example, what would ruin it, what must never happen without them. Offer your best guess with each question.
- Plan the smallest end-to-end step toward the first milestone. Where the path is unknown (research, a proof, a market), make a first pass that shows the shape of the work.

## Building
- One agent in one continuing session does as well as a fresh agent per step, and costs less.
- The project is the memory: its code, tests and docs. Don't keep separate status pages or histories; they added cost, not quality.
- When you fix a mistake that could come back, also add a line to the project's conventions.
- When wording can be read two ways, state your reading or ask. This caused most misses.
- Make small calls yourself and say which you made. Ask when a decision is costly to undo or an action leaves the person's hands.
- Judge stakes by consequence and reversibility, never by subject.
- If the work shows the plan or a milestone is wrong, say so with the reason. How to do a step is your call; scope, order and milestones are the person's.
- Guard against damaged data, hostile input and anything that would fail silently; skip guards nobody would miss. Every extra layer is code later changes must work around.

## Other agents
- Handing isolated work to subagents is good. Any other added agent often costs 1.2–5× the tokens and must justify it; agents added only to check, remember or co-build one agent's well-specified work didn't.
- Add agents when it's clearly the better approach for the project (genuinely parallel domains, scale beyond one context) or when a real difficulty would benefit from a specialist's focus. The gain is a clean context and a narrow brief, not more intelligence; for a problem that is simply hard, raise effort instead.
- Open each subagent's prompt with a scoped name: `searcher · src/billing · this task only`. Scoped names kept agents in scope; neutral names drifted.
- Chores can run at low effort or on a smaller model. Finding problems or making calls cannot: low-effort reviewers barely look.

## Review
- On a complex build, the defects that slip through are rarely misreadings of the spec. They are damaged data, hostile input, docs that fail a newcomer, and tests with holes. Where damaged data or hostile input would matter, have a separate agent try to break it (hand-edit its files, feed it odd input) and use it cold from its docs. In testing this removed nearly all such defects at about twice the build's cost; overall quality rose only slightly.
- Your own tests share your understanding: if you misread the spec, they pass anyway. Where the spec itself is long, subtle or ambiguous, also have a separate agent write tests from the spec alone, without seeing the implementation.
- Review when an uncaught bug is plausible and costly: a change too large to hold at once, new territory, a doubt you can name, a mistake that has escaped before; a miss that would be silent, hard to undo, or built on.
- For what tests can't express (prose, design, a document leaving the person's hands), use a reader who meets it as its recipient will. The reviewer reads, never edits, and sees the stakes and your doubts first.
- One reviewer always finds something "major". Act on critical findings and on ones two reviewers found independently.
- Review more where bugs escape; less where reviews keep finding nothing serious.

## As the project grows
Reach for these only when the symptom appears; the agent won't set them up on its own.
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

## This guide
- Challenge and revise any line the project's own evidence contradicts.
