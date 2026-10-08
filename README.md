# colony

**One page to run all your long projects with Claude Code or Codex.** Each project gets its own agent, live in
the browser and on your phone. You see what each one is doing, what it's waiting on you for, and how
far along its roadmap it is, and you steer them all from one board. A monitor agent can hold the helm
for you across them.

And [GUIDE.md](GUIDE.md): what measured tests showed about running long projects with one strong agent.

### Leads and completed versions

Each project has one agent responsible for its roadmap by default. If you add another agent to the same folder, choose **Add helper** or **Switch lead**. Shared agents read one canonical Vision and roadmap; the lead assigns individual items when work is split. The completing agent integrates and delivers its tested work, and Colony synchronizes helpers only when they are engaged. A project with one provider needs no coordination setup.

During the Vision conversation, your agent proposes a few useful completed versions and where to pause for you. Codex continues with a bounded native goal; the board shows **Pause work**, the current version and its candidate when ready. Approving a version can stop there or explicitly release a planned next one. Clearing a review card leaves it unapproved. [Mechanics and commands](docs/project-lead-spec.md).


## Quick start

    git clone https://github.com/5000Stadia/colony ~/colony && ~/colony/start

That's it. It installs colony, starts the board and prints where to open it: on this machine, from
your phone on the same network, and in the Claude or ChatGPT app, where every project's session appears.
The monitor, an agent of its own, then walks you through setup. You need
[Claude Code](https://claude.com/claude-code) or [Codex](https://developers.openai.com/codex), tmux and
Python 3.10+.

Then **+ Add project**: start a new one, pick a folder you already have, or clone one from GitHub. Its
agent starts working in its own console right away (it spends tokens from then on), and the project joins
the board.

`colony stop` ends everything. `~/colony/start` brings the board back, and each console returns to its
conversation when you open it.

## What you get

- **Every project's live console**, in the browser and on a phone (swipe to scroll, a key row for
  Esc/Tab/arrows, full screen). Sessions run in tmux, so closing the page stops nothing, and
  `tmux attach -t board-…` reaches the same session from a terminal.
- **Waiting on you:** what each project needs from you, in one list. That covers decisions it put to
  you (gates), questions it asked, choices on its screen, and work ready for your OK (Approve / Not
  yet). The sidebar counts them.
- **Message**, at the top of every project: type or paste into its console as if you were there,
  or have its agent write to another project. Attach a file from your device or the project.
- **The roadmap**, kept by the agent in `ROADMAP.md`: milestones fold away as they finish, the
  latest work comes first, and every item and milestone shows its time in progress. The overview reads
  `Roadmap: 16/43 · Active: 6d 15h`, where Active counts only the time its agent was actually working.
- **The shared vision**, above the roadmap: the finished work's narrative, feel and fundamental elements.
  Edit it on the project page or shape it with the agent in conversation. Saving tells the agent what
  changed so it can adjust the work. Details for a few items go into those items' descriptions or specs.
- **Since you were last here:** commits, roadmap moves, gates and replies, newest first, until you Clear.
- **The monitor**, one more session at the top of the sidebar. It tells you when a project needs you
  or has finished. It relays what you say to a project in full, as yours. Given the helm, it settles
  routine questions within the direction you set for each project, and brings back anything about scope,
  cost or that can't be undone. It never polls: a watcher reads every screen every few seconds for
  free and wakes it only when there is something to hear.
- **Projects talk to each other:** `colony send NAME "…"` (`--ask` for an answer), `colony reply`,
  and `colony projects` lists them with their goals, so an agent asks rather than guesses.
- **Turbo:** when a program's weekly usage runs more than 5 points behind pace (from day 2, aiming for 99%
  the hour before the reset), its projects with queued work are turned up, each as it ticks: stronger
  models, going deeper on its agreed work, research on a topic you type. Alongside the work, never instead
  of it: an idle console with nothing waiting on you is woken, and models change only at an idle reload; an
  agent with nothing worth doing says so (`colony turbo --nothing`) and isn't woken again until its roadmap
  changes. Once a day, with consulting on, the program's strongest model also writes each such project a
  short research advice, apart from its agent: a report in its `research/` folder, with a quiet note.
  `colony turbo` shows each program's pace; Settings turns it off per program.
- **Active hours:** when you're around, 7:30 AM to 11:45 PM unless you set your own in Settings (or off).
  Outside them agents keep working but don't end on a question to you: the decisions they need are held off
  Waiting on you and arrive together when your hours begin, and the monitor isn't woken just to pass
  something on. What you send goes through at any hour.

## How it works

`colony track` (or Add project) puts a short protocol in the project's `CLAUDE.md` and installs Claude
Code hooks. The agent keeps `ROADMAP.md` current, puts decisions to you with `colony gate`, marks work
ready with `colony ready`, and records what it did with each of your notes (`colony noted`). The hooks
hand it your notes, answers and mail at the start of each turn, so nothing relies on its memory. The
board and the monitor reach a session by typing into its console, never over something you have
half-typed there. The board's record of a project is in its `.board/` folder.

At consulted decisions, gates show the relevant recommendations with their sources.
Accept or reject each point, with an optional comment; only accepting a change allows
the consultants' checking round. Choices and revisions stay in the decision's record.
[How agents link consultant points to a gate](docs/consultation-gates.md).

    colony board [--local]   start the board (open to your network by default; --local: this machine only)
    colony restart           reload the board after a change; consoles and the monitor keep running
    colony doctor            is everything up and wired? what to do if not
    colony urls              where the board answers
    colony peek NAME [-n N]  a project's state and its last lines
    colony vision           this project's live vision; --history shows its dated changes
    colony tell NAME "…"     a note to a project, as yours
    colony settings          global settings; a project's own with `colony settings --project NAME`

**Settings.** Each project can have its own model, effort, permissions and Remote Control; blank keeps
the global choice. The **provider** (the program that runs the agent) is Claude Code or Codex. Another joins
by adding an entry to `colony/providers.py`; every place specific to one program is marked `PROVIDER:` in
the code.

**Where projects live.** Any folder can be a project. Folders you put in `~/colony/projects` join the
board by themselves, new projects are created there, and Settings adds other folders that work the same way.

The first time Claude Code opens in a folder it asks whether you trust it: answer once, from the
board (the choice shows as buttons) or the app. The state shown for each session (working, needs you,
idle) is read off its screen, so a change in Claude Code's wording could mislabel it; the lines shown
are always the real ones.

## The guide

[GUIDE.md](GUIDE.md) is what the tests showed about running a long project with one strong agent:
horizon first, effort, one agent over many, the project as its own memory, when review pays, naming
subagents, and how to test a change before adopting it. It rests on the lab's results, which are not
published.

## What we measured, and on what

Every number in this README was measured on **Claude Opus 5.5** in Claude Code, in September 2026, on
software tasks with pre-registered tests, replicates and a blind judge. A
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

Sources: the lab's results (not published); [CodeRabbit's Opus 5.5
review](https://www.coderabbit.ai/blog/opus-5-5-model-review);
[Anthropic's Opus 5.5 page](https://www.anthropic.com/claude-opus-5-5).

## More

Tests: `python3 -m unittest tests.test_board tests.test_selection tests.test_effort tests.test_codex_remote tests.test_vision tests.test_context tests.test_catalog tests.test_catalog_freshness tests.test_lead tests.test_continuation` (they start no real agent).

MIT licensed: see [LICENSE](LICENSE).
