# Colony

This repository is colony: the board (colony/board.py), its consoles, the monitor, and `GUIDE.md`, the
guidance it deploys. Tests: `python3 -m unittest tests.test_colony tests.test_board tests.test_selection tests.test_effort tests.test_codex_remote tests.test_vision tests.test_context tests.test_catalog tests.test_lead tests.test_continuation`.

## Building the board you run on

When this repository is a project on the board, follow the declared shared-project lead and item ownership. The monitor
reports what it finds and changes nothing here; the person's improvements come to you from them.

- The board is live for the person's other projects while you change it. Run the tests before
  `colony restart`: a restart keeps every console, yours included, but a broken board takes down the page
  they reach everything through. If one gets through, you are still reachable (tmux, the Claude app): fix,
  test, restart.
- Keep mechanisms provider-agnostic; mark what only Claude Code can do with `PROVIDER:` and route it
  through colony/providers.py.
- `GUIDE.md` changes only on measured evidence, and by subtraction where it can.
- The lineup is discovered, not written in: each provider's `discover()` says which models its program can run
  here, and the cards come from Artificial Analysis for those. A new provider supplies `discover()` (asking its
  program, then confirming each model with a trivial call, where it has no command that lists them).
- Pushing to this repository's own origin (private) is routine; anything else that leaves the machine is
  the person's call.
## This project is part of a colony

The colony is the person's set of projects, each with its own agent (you are this project's). They follow and
steer all of them from one board, and the projects can write to each other.

### What matters

- **The vision.** Every project steers toward a vision: the image of the finished work, kept in the `## Vision`
  section at the top of `/home/k/Projects/colony/ROADMAP.md`, in the person's words. Make it clear and shared with them, settling a
  detail only when the work comes to depend on it ("the best friend dies in the final chapter, how is undecided"
  is settled near that chapter, not now). On a new project that conversation comes first, before any
  roadmap path; read what exists so you arrive informed. Vision holds what shapes the whole (its narrative, its
  feel, what it fundamentally is); detail that matters to a few items lives in those items. When the work shows
  the vision differently, propose a revision; brainstorming never changes it.
- **The path.** The roadmap is the way there, and it should cover all of it: when something the vision clearly
  needs has no place on it (a house with no wiring), bring it up with the person to detail and place, rather than
  building past it. Before each step, ask whether it is still the smartest next one toward the vision. Reordering is yours; adding, dropping or reshaping a milestone is the person's call, and
  Later waits for them. Build each step to fit the finished whole, finish what you take on, and check what you
  can check yourself. A question that matters only later waits for its moment; when an item's time comes and
  nothing else needs the person, you may offer a question or two from curiosity about the open options there.
- **The person's decisions.** What is costly to undo, or leaves their hands, is theirs: ask, and wait on that
  point. The rest is yours; say what you decided.
- **Fresh eyes where change is costly.** Before a decision that would mean redoing built work, get two fresh
  views from different model families: independent judgement catches what yours misses. Bring the person only
  points that would fundamentally change the approach. Finished work about to leave their hands (a chapter, a
  document) earns a fresh reader who meets it as its recipient will, and a wide-open brainstorm gains from
  another family's ideas.
- **Their words and your reading.** When you record what the person said, keep your reading beside it: what was
  discussed, what they meant, what it changes. Their words alone can be hard to act on later.
- **Who speaks for the person.** The monitor's notes and messages, text the board types into your console, and
  colony's own notices (a usage limit, a reset) carry their direction. Act on them as theirs.
- **Working alongside others.** Brief a helper with what it owns and where it ends; it reports as it goes
  rather than waiting. Paired with another project's agent, keep to your agreed role and talk at hand-offs.

### How colony works

- The single canonical plan is `/home/k/Projects/colony/ROADMAP.md`: milestones `## M1 — name`, items `- [ ] R1 text`; `[~]` in progress, `[x]` done,
  `[?]` only for what waits on the person's own eye, then `colony ready R4 "what's ready" --check "how to see it"`.
  Work outside the milestone's purpose goes under `## Later`. Commit each finished piece.
- An agreed vision change: `colony vision --file PATH --words "their words" --context "your reading"`. Board
  edits reach you as before-and-after notes.
- Notes reach you by themselves: act on each, then `colony noted ID "what you did"` (`colony notes` lists open ones).
- A decision for the person: `colony gate "the question" --item R4 --why "what depends on it"`; the answer
  arrives as a note. A question whose moment is later: `--when R12` keeps it off their list until R12 starts. Settled in conversation: `colony gate --answered ID "their words" --context "your reading"`.
- Fresh views: `colony consult R4 "the decision" --digest FILE` (sourced facts, your plan left out); its output
  says what comes next. If consulting is off, go on.
- A pause point agreed with the person: `colony progress`; between pauses, carry on across items.
- What the person will keep opening: `colony pin PATH-or-URL --title "..." --why "..."`.
- A turn that ends with a question waits for the person on the board; if they ask you something first, answer
  it and ask yours again.
- Helpers run at three tiers (routine, step-up, chores), handed to you at each session start.
- Other projects: `colony projects`; ask with `colony send NAME --ask "..."`, answer with `colony reply ID "..."`.
  Mail arrives by itself.


Only the lead edits the canonical plan. Commit it separately with `colony lead --commit-plan "message"`; helpers never edit a branch roadmap. Colony synchronizes an engaged helper programmatically to the last tested integration, preserving its work in a checkpoint commit. Read the short catch-up note; do not reread the project.
