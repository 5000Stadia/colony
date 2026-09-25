# colony

**Start with [GUIDE.md](GUIDE.md):** what the tests showed about running long projects with one strong
agent in Claude Code — effort, one agent over many, the project as its own memory, lessons, naming
when agents share work, where a reviewer might earn its place, and how to test a change before
adopting it.

The runtime below was built alongside those tests. At fourteen steps, one plain Claude Code session
matched it on quality at lower cost (`garden/results/SESSION.md`); a test at the scale it was built
for — many dozens of steps, repeated compaction — decides whether it stays. Until then it is
experimental.

## Quickstart — for the agent you hand this to

> Tell your agent: *"Read the Quickstart in github.com/5000Stadia/colony and follow it. I want to start
> a project."*

You are starting a long project for a person with colony. Colony is for work that outlives one
session — many steps, built over days or weeks. If what they want fits in one sitting, or every step
is an act in the world only they can take, tell them so and just do it with them; colony would only
be in the way. Otherwise, follow these steps in order.

1. **Install.** `gh repo clone 5000Stadia/colony ~/colony` (or `git clone`), then
   `python3 -m pip install --user -e ~/colony` — if pip refuses, add `--break-system-packages`, or
   run everything as `PYTHONPATH=~/colony python3 -m colony …`. Copy `~/colony/claude/skills/colony`
   into `~/.claude/skills/`. Check with `colony --help`.
2. **Make the project.** Ask the person for its name and where it should live; invent neither.
   `colony init PATH`, then work from inside `PATH`.
3. **Settle the model with the person** before anything is built. Ask which model will do the work.
   Colony runs its agents through Claude Code, so any model Claude Code runs can be the builder.
   - **Claude Opus 5.5:** the measured defaults apply (see *What we measured, and on what*); say so in
     a line and move on.
   - **Any other model:** look up what is published about its effort or reasoning levels: the
     vendor's guidance and at least one independent evaluation. Answer the three questions in *What
     we measured* for it, and tell the person what you found, with sources and how solid they are.
     Then propose a `model` and an `effort`, and let them decide. If the sources are thin or
     disagree, start at the vendor's recommended default; the checkpoint's cost lines will show
     whether to move.
   - **A model Claude Code cannot run** (Codex, for one): say so plainly. Colony's agents run through
     Claude Code; the findings still guide effort, but there is no runner for that model yet.

   Write the choice to `.colony/config.json` (`"model"`, `"effort"`, or `"effort_builder"` and
   `"effort_specialist"` apart).
4. **The front door is a conversation, not a form.** Find out, in their words: what it is and who it
   is for; what would make them *proud* of it, not merely satisfied; the best real example of this
   kind of work, and what makes it the bar; what would ruin it; what must never happen without them
   (publishing, spending, sending, touching data that is not theirs); where it goes. Reflect drafts
   rather than asking open questions. `colony door --goal "their words"` writes a first draft of
   `design/spine.md` to correct together.
5. **The spine they approve** holds: their goal, quoted; what "good" means; what must never happen;
   **checks** — commands that settle quality in seconds (tests, a build, a word count); **risky
   areas** — only paths where a mistake is truly expensive, since every change there is reviewed;
   and **rows**, smallest end-to-end thing first, each with *what done looks like* and an **impact**
   `1–10 — one sentence on what a mistake would hurt`. Put every other fact where the work's own
   tools already read it (a manifest, a manuscript folder, a calendar), not in new files.
6. **The person approves.** They run `colony approve`, or tell you to; it shows what the risky areas
   will cost. Never approve on their behalf.
7. **Run and report.** `colony run --rows 3 --cap 10` builds the next rows in the background — closing
   this session cannot kill it — and stops cleanly at the cap, a failing check, or a fork that needs
   them. `colony wait` blocks until it stops and says how it went. After each run, give the person `colony cost` and
   point them to `colony page`, where a note left on any row reaches that row's builder. Builders may
   propose rows under `## Proposed rows` in the spine when the work teaches them something; the
   person decides which join the plan.
   Every few rows run `colony checkpoint`: it costs nothing. Tell the person what it shows about
   the workflow and tokens, put its questions to them, and record each answer with `colony answer`
   (`--always` if they want it to become a rule). Never answer on their behalf.
8. **Leave the defaults alone unless the project gives a reason.** Everything colony does was tested
   against a single fresh agent; what did not earn its place was cut. Raise `effort` only for work
   that proves hard. Every agent is refused `git push`, `gh`, publishing and `ssh`; if the work truly
   needs one, the person can lift it with `"allow_outward": ["Bash(gh:*)"]`. Add nothing without
   evidence.

## The board: one place to follow and steer every project

    colony track            in a project: put it on the board (once)
    colony board            open the board: every tracked project in one page

For each project the board shows what changed since you were last there, what the agent has put in
your hands (gates), the roadmap as a list and, when the plan branches, a map. Each item has its own
page with its history, gates and a thread where you direct it. Notes on past work reach the agent on
its next turn. Notes on a roadmap item reach it when it starts that item. Nothing waits unseen: a note
whose item leaves the roadmap is delivered at once, and one delivered but not acted on is repeated at
the next session's start.

The agent's side is three habits written into the project's CLAUDE.md: keep `ROADMAP.md` current, gate
what needs you (`colony gate`), and mark notes it acted on (`colony noted`). Delivery is done by Claude
Code hooks that `colony track` installs, so it doesn't rely on the agent remembering.

## What the defaults are, and why

The core — one builder per row at medium effort, checks, the meter, review only where declared — was
measured against a single fresh agent across seven pre-registered pilots and five focused tests. The
mechanisms around it were each tested, and those that did not earn their place were removed or
made the person's call. `design/claims.md` holds
each part's evidence and its status.

- **One builder per row, at medium effort.** The best value on every task tested, up to a fourteen-row
  project that lost its context at every row. Low → medium effort was the largest single gain found
  (+11 points of judged quality for 75¢).
- **Review only where it pays.** Critics on well-specified work cost 1.2–1.7× for no measurable gain.
  Review runs where the spine declares risk: a change touching a risky area, or, if the project sets
  `review_at_impact`, a row whose impact (set before the work) reaches it. One round: reviewers are
  read-only, see the stakes and the builder's one-sentence doubt first, and the builder answers what
  they find. `colony approve` prints exactly what will and will not be reviewed.
- **No memory layer.** A rewritten status page, reconciliation and narrative history cost 1.7–2.8×
  without improving results, at fourteen rows and on a seeded project whose docs were cut to one
  line: fresh builders recovered every rule from code and tests. The project itself is the state.
- **Names carry scope.** Free, so kept: in one border scenario (16 runs, mostly low effort) scoped names
  kept agents to their side and neutral names crossed, with no measurable harm either way.
- **Nothing irreversible without the person.** Publishing and remote commands are refused to every
  agent; anything else the spine names stops as a fork. A floor, not a sandbox: for isolation, run
  colony in a container.

## What we measured, and on what

Every number above was measured on **Claude Opus 5.5** in Claude Code, in September 2026, on
software tasks with pre-registered tests, replicates and a blind judge (`design/claims.md`). A
different model changes the numbers, not the questions. These are the three to answer for it:

1. **Where is the value knee for building to a clear spec?** For Opus 5.5 it is medium. Low → medium
   was the largest gain found (+11 judged points for $0.75, three runs each, one task). Above medium,
   published evaluations found flat or negative returns on common work. CodeRabbit's lower-effort
   configuration caught 51 known bugs against 50, with better precision. Medium matched or beat the
   previous generation's high effort on about half the tokens.
2. **Does effort pay for looking past the obvious** (review, subtle faults)? A little, not reliably.
   Our low-effort reviewers barely looked and our medium ones found real problems. In CodeRabbit's
   13 hard cases, higher effort caught 10 against 8, but not consistently, and the two missed
   different faults.
3. **Do more agents pay?** On well-specified work, no: a reviewer cost 1.2–1.7× for no measurable
   gain. A fresh agent per row at medium was the best value up to fourteen rows. We did not test xhigh
   or max; the published evaluations found diminishing returns there.

Sources: `design/claims.md` and the lab's results; [CodeRabbit's Opus 5.5
review](https://www.coderabbit.ai/blog/opus-5-5-model-review);
[Anthropic's Opus 5.5 page](https://www.anthropic.com/claude-opus-5-5).

## Commands

    colony init DIR              make DIR a colony project
    colony door --goal "..."     draft the spine from a goal, to correct with the person
    colony approve               the person approves the spine; runs may start
    colony run --rows N --cap $  build the next N rows within a budget
    colony status                rows, where the project stands, open signals
    colony cost                  dollars and tokens by row and by agent
    colony checkpoint            workflow, progress and tokens since the last checkpoint (no tokens); questions for you
    colony answer KIND TEXT      answer a checkpoint question; --always keeps it as a rule in the spine
    colony page                  the project at a glance, with a note box on every row
    colony map QUERY             what already exists that bears on something

## More

`design/intention.md` is what colony is for; `design/blueprint.md` how it works and why;
`design/claims.md` every part as a claim with its evidence; `design/sources.md` where the ideas came
from. Tests: `python3 -m unittest discover -s tests`.
