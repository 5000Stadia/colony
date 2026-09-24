# colony

Keeps one strong Claude Code agent at its best across a long project — software, a book, a business,
a plan — with the goal in the person's own words, the project's checks, a cost meter, review only
where a mistake would be expensive, and a page the person can read at a glance.

## Quickstart — for the agent you hand this to

> Tell your agent: *"Read the Quickstart in github.com/5000Stadia/colony and follow it. I want to start
> a project."*

You are starting a long project for a person with colony. Follow these steps in order.

1. **Install.** `gh repo clone 5000Stadia/colony ~/colony` (or `git clone`), then
   `python3 -m pip install --user -e ~/colony` — if pip refuses, add `--break-system-packages`, or
   run everything as `PYTHONPATH=~/colony python3 -m colony …`. Copy `~/colony/claude/skills/colony`
   into `~/.claude/skills/`. Check with `colony --help`.
2. **Make the project.** Ask the person for its name and where it should live; invent neither.
   `colony init PATH`, then work from inside `PATH`.
3. **The front door is a conversation, not a form.** Find out, in their words: what it is and who it
   is for; what would make them *proud* of it, not merely satisfied; the best real example of this
   kind of work, and what makes it the bar; what would ruin it; what must never happen without them
   (publishing, spending, sending, touching data that is not theirs); where it goes. Reflect drafts
   rather than asking open questions. `colony door --goal "their words"` writes a first draft of
   `design/spine.md` to correct together.
4. **The spine they approve** holds: their goal, quoted; what "good" means; what must never happen;
   **checks** — commands that settle quality in seconds (tests, a build, a word count); **risky
   areas** — only paths where a mistake is truly expensive, since every change there is reviewed;
   and **rows**, smallest end-to-end thing first, each with *what done looks like* and an **impact**
   `1–10 — one sentence on what a mistake would hurt`. Put every other fact where the work's own
   tools already read it (a manifest, a manuscript folder, a calendar), not in new files.
5. **The person approves.** They run `colony approve`, or tell you to; it shows what the risky areas
   will cost. Never approve on their behalf.
6. **Run and report.** `colony run --rows 3 --cap 10` builds the next rows and stops cleanly at the
   cap, a failing check, or a fork that needs them. After each run, give the person `colony cost` and
   point them to `colony page`, where a note left on any row reaches that row's builder.
7. **Leave the defaults alone unless the project gives a reason.** They are what won the tests below.
   Raise `effort` only for work that proves hard; set `"review_if_risk_at_least": 30` in
   `.colony/config.json` if they want review wherever doubt times stakes runs high; turn on
   `"reconcile": true` only once the project is too large for a fresh agent to re-read cheaply.
   Add nothing else without evidence.

## What the defaults are, and why

Every default was measured against a single fresh agent, across seven pre-registered pilots and five
focused tests; `design/claims.md` holds each part's evidence.

- **One builder per row, at medium effort.** The best value on every task tested, up to a fourteen-row
  project that lost its context at every row. Low → medium effort was the largest single gain found
  (+11 points of judged quality for 75¢).
- **Review only where it pays.** Critics on well-specified work cost 1.3–2.4× for no measurable gain.
  Review runs where the spine declares risk, or where a row's impact (set before the work) times the
  builder's doubt (reported after) crosses the project's rule — which then drifts gently with what
  reviews actually find. Reviewers are read-only and see the stakes and the builder's doubt first.
- **Memory layers off until earned.** NOW, reconciliation and narrative history cost more and grew
  faster at fourteen rows without improving results; they are insurance for larger projects.
- **Names carry scope.** Agents named for role, scope and lifetime kept to their own work at a border
  in 16 of 16 runs; neutral names crossed every time.
- **Nothing irreversible without the person.** Publishing, spending and sending stop as forks.

## Commands

    colony init DIR              make DIR a colony project
    colony door --goal "..."     draft the spine from a goal, to correct with the person
    colony approve               the person approves the spine; runs may start
    colony run --rows N --cap $  build the next N rows within a budget
    colony status                rows, where the project stands, open signals
    colony cost                  dollars and tokens by row and by agent
    colony calibration           the builder's confidence beside what review found
    colony page                  the project at a glance, with a note box on every row
    colony map QUERY             what already exists that bears on something

## More

`design/intention.md` is what colony is for; `design/blueprint.md` how it works and why;
`design/claims.md` every part as a claim with its evidence; `design/sources.md` where the ideas came
from. Tests: `python3 -m unittest discover -s tests`.
