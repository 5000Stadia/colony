---
name: colony
description: Start or continue a long project with colony — use when the person wants to begin a project that will span many steps (software, a book, a business), continue one that has a design/spine.md, see where it stands, or see what it has cost.
---

# Colony

Colony keeps one strong agent at its best across a long project. It runs the work row by row, a fresh
agent for each row, with the goal in the person's own words, the project's checks, a cost meter, and
review only where the person has said a mistake would be expensive.

## Starting a project — the front door is a conversation

1. Ask for the project's name and where it should live; create it with `colony init DIR`.
2. Have the conversation, in the person's words: what it is and who it is for, what would make them
   proud of it (not merely satisfied), the best real example of this kind of work, what would ruin it,
   what must never happen, and where it goes. Reflect drafts rather than asking open questions.
3. Write `design/spine.md` with them — sections *What we're making* (their words, quoted), *What "good"
   means here*, *What it must never do*, *Where it goes*, *Checks* (commands, each "- `command`"),
   *Risky areas* (paths where every change is reviewed; usually empty), and *The spec list* as a table
   `| # | What to build now | What done looks like | Impact |`, smallest end-to-end thing first, impact
   written as `1–10 — one sentence on what a mistake would hurt`. For a quick
   draft instead, `colony door --goal "..."` writes one for them to correct.
4. When they recognise themselves in it, they run `colony approve` — not you.

## Running and reporting

- `colony run --rows N --cap USD` builds the next N rows; it stops for a fork, a failing check, or the cap.
- `colony page` serves the project at a glance for the person — what waits on them, the roadmap, the
  history, the cost — with a note box on every row that reaches that row's builder.
- `colony status` shows the rows, where the project stands and any open signals; `colony cost` shows
  dollars and tokens by row and by agent. Report cost beside every result.
- Settings live in `.colony/config.json`: `effort` (default medium), `review` (`auto` reviews only the
  spine's risky areas; `always`, `never`), `reconcile` (keep NOW, and write each row's narrative into its
  closing commit so `git log` is the history — worth it once a project is too large to re-read cheaply).
- The project's reviewers live in `.claude/agents/` (marked `colony: reviewer`), so they can also be
  called as subagents in any session.

## What never happens without the person

Publishing, deploying, spending, or contacting anyone. The spine lists these; a row that needs one
stops with a fork for the person to answer.
