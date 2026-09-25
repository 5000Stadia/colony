# Starting a long project

## The horizon comes first
- **Before planning the path, agree with the person on the horizon.** That means the few large milestones and what each one looks like. Use the stages that fit the domain: v1, v2 and v3 for a product; year 1, 2 and 3 for a business; drafts or volumes for a book.
- **Build the near-term path toward those milestones, not only toward the next step.** If the path is built without the horizon in view, correcting course later means a costly rebuild.

## Day one
- **Settle the model and effort with the person.** Medium effort gives the best quality for the cost. Above medium, returns drop off sharply, so keep higher effort for the rare work where a small gain in intelligence is worth paying for. Low effort loses real quality on building but is fine for chores. For a model you don't know, check its guidance on where more effort stops paying.
- **Get the goal in their words:** what would make them proud, the best real example, what would ruin it, and what must never happen without them. Offer a best guess beside each question, then reflect a draft back.
- **Plan the smallest end-to-end step toward the first milestone.** Where the path is unknown (research, a proof, a market), make a first pass that shows the shape of the work. The person approves before you build.
- **Find the quick checks, even where there are no tests:** a word count and placeholder check for a book, a price floor for a shop.
- If it fits in one sitting, just do it.

## Building
- **Start with one agent in one continuing session.** It does as well as handing each step to a fresh agent, and costs less.
- **The project is the memory.** Keep the docs current in the same change and hold rules in tests. Status pages and reconciled histories add cost, not quality.
- **When you fix a mistake that could come back,** add a line to the project's conventions as well as the test, so later sessions know.
- **Settle any wording that can be read two ways.** State your reading or ask. This is the most common cause of misses.
- **Make small calls yourself and say which ones you made.** Ask when a decision is costly to undo or when an action leaves the person's hands.
- **Judge stakes by consequence and reversibility,** never by subject.
- **If the work shows the plan or the horizon is wrong, say so with the reason.** How to do a step is your call. Changes to scope, order or milestones are the person's. Raise a milestone that looks wrong as soon as you see it, because that's where rebuilds come from.
- **Every safeguard and extra layer is code that later changes must work around.** The most defensive version cost about twice as much to change. Add guards where a failure would matter, not everywhere.
- Don't reorganise on a guess.
- Report the cost with every result. Ask before going over what the person set.

## Adding agents
- **Hand isolated work to subagents** — search, research, bulk reading — and have them return conclusions. That keeps the main context for judgement, and is good.
- **Every other added agent costs overhead.** Reviewers, memory keepers, co-builders and parallel workers often cost 1.2–5× the tokens. Each one must justify that cost.
- **Agents added only to check, remember or co-build one agent's well-specified work don't earn their cost.**
- **More agents belong where the work really exceeds one agent:** truly parallel domains, or scale beyond one context.

## Briefing subagents
- Start each prompt with a scoped name, such as `searcher · src/billing · this task only`. Agents with scoped names stayed in scope; agents with neutral names drifted out of it.
- For chores (fetching, renaming, running tests, a one-file change), low effort or a smaller model is fine.
- For finding problems or making a call, use the main agent's effort. Low-effort reviewers barely look.

## Review, where it can pay
- Work built to a full spec gains little from review.
- Consider review where no test can judge the work and the work leaves the person's hands, such as a document for a client or a release. A review can find robustness nobody asked for, at several times the cost. A solo build may find the same thing by chance.
- The reviewer reads and never edits. Show the reviewer the stakes and the builder's doubts first.
- A single reviewer always finds something "major". Act on critical findings and on findings that two reviewers made independently.

## As the project grows
Use these when the symptom appears, not before. Each needs deliberate setup; an agent won't adopt them on its own.
- The agent re-derives settled decisions or retries rejected approaches after many compactions → record decisions in commit messages and put the relevant ones in the next step's brief.
- The agent defends an approach that failed → take the next step in a fresh session that has only the plan and the repo.
- The work must run unattended → use a driver that feeds in steps, detaches, and stops on a failing check or a cost cap.
- The same mistake keeps coming back → use a reviewer that keeps lessons from its serious findings.
- Several agents work in one place → give them scoped names and one owner per area.
- One area outgrows one context → give it its own lane once the strain recurs.
- Cost is spread across sessions → keep a per-step ledger.
- The person steers without being in the conversation → keep a page of the plan, history and cost, with notes for each step.

## Designing any workflow piece
- First ask what will use it. If it goes unused in a small trial, that shows its cost, not that it is useless. Cut only what stays empty at every scale.
- Before building it, try it against the plain setup on a small version of the problem.
- State the intention, not the mechanism. The exception is where a mistake must be physically blocked: publishing, irreversible actions, other people's data.
- Test each line: would a strong model do worse without it? If not, cut it.
- Judgement belongs to the model; plumbing belongs to code. Base triggers on counts over records at boundaries, never on an agent's opinion. Keep the machinery out of the worker's view.
- Any gauge puts quality before cost: a wrong result should set it off at least as fast as an empty result does.
- When the person turns something off, it stays off.
- Churn that isn't about the work itself means the workflow is wrong. Fix the architecture.

## Safety
- Deny outward commands in the Claude Code settings: `git push`, `gh`, publishing, `ssh`. The denial holds even when permissions are bypassed. It is a minimum safeguard, not a sandbox.

## This guide is a starting point
- If the project's own evidence contradicts a line, challenge that line and revise it.
