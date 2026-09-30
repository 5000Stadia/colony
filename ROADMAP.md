# Colony roadmap

One board to follow and steer a person's long projects, each driven by its own agent, with a monitor that acts for them across the colony.

## M1 — A harness that earns its shape

- [x] R1 Intention and blueprint, from the garden pilots' measurements — design/intention.md, design/blueprint.md
- [x] R2 The runtime: spine in the person's words, a fresh builder per row, checks, the meter, forks, refused outward commands — design/blueprint.md "The core"
- [x] R3 Review only where the person declared risk — design/blueprint.md "Review"
- [x] R4 Every part as a claim tested against a single agent; what failed was cut — design/claims.md
- [x] R5 The guide: measured guidance for any long project, deployed to every agent — GUIDE.md

## M2 — The board: every project on one page

- [x] R6 The board: every project, its live console in the browser, reachable from a phone and the Claude app — README.md "The board"
- [x] R7 The colony protocol: roadmap, notes, gates, questions, ready for your OK, pins, all delivered by hooks — colony/board.py PROTOCOL
- [x] R8 Mail between projects — colony/mail.py
- [x] R9 The phone console: swipe to scroll, key row, Select, full screen — colony/console.py
- [x] R10 Add a project (new, existing folder, from GitHub); remove or delete it — README.md "The board"
- [x] R11 Waiting on you: one definition, counted as moments, clearable — colony/board.py waiting_items
- [x] R12 Providers: Claude Code today, every assumption marked PROVIDER: — colony/providers.py

## M3 — The monitor: acting for the person

- [x] R13 The monitor and its watcher: wakes on what needs the person, costs no tokens otherwise — colony/monitor.py
- [x] R14 The helm, project by project, with a standing direction — colony/monitor.py
- [x] R15 Supports: tools and references offered when a project's work calls for one — colony/supports.py
- [x] R16 Scouting — colony/monitor.py
- [x] R17 A console survives a memory kill and comes back to its conversation — colony/console.py

## M4 — Colony on its own board

- [x] R18 Colony as a project on its own board: its agent builds colony; the monitor reports and changes nothing — CLAUDE.md
- [x] R19 The roadmap brought on board — ROADMAP.md
- [x] R20 The watcher reports what is true: peek reads past the screen, no false "finished" or "needs you", no repeated questions
- [x] R21 Message: one place to write, at the top: into the project's own console by default or to another project, with a file uploaded or browsed; what was said is kept on the Roadmap tab
- [x] R30 Finished milestones load folded, and those before the first unfinished one fold into Completed
- [x] R31 Nothing is typed onto what the person has half-typed in a console
- [x] R32 The roadmap folds and orders itself by the work: the latest started first, finished last in Completed, a live dot on what's in progress, each item's time in progress, and each heading's elapsed time
- [x] R33 Not yet takes an item off the person's list until the agent says it's ready again
- [x] R34 The overview line reads Roadmap: #/# · Active: the agent's time at work, all told

## M5 — Public: anyone can start with it

- [x] R35 Ready to publish: nothing private in the repository or its history, a README with a quick start that works from a fresh clone, then public on the person's word

## M7 — Codex as a full option: from install to a project's agent

- [x] R36 A project's console runs on Codex (colony new/track --provider codex): typed into, its screen read, the protocol in AGENTS.md, notes fetched when nudged
- [x] R37 Two projects can share one folder (holo-emitter and holo-emitter-codex): names, .board and instructions kept apart
- [x] R38 A new console's start-up questions (folder trust, permission mode, Remote Control, hooks) answered so it runs as set up, both providers, a setting on by default
- [x] R39 Delivery for Codex through its own hooks: notes and mail at each turn, questions at turn end, resume (colony-codex)
- [x] R40 Model and effort guidance for Codex, researched and marked claimed or measured, with the Codex entry's defaults (colony-codex drafts; the monitor reviews)
- [x] R41 Colony starts with either program installed (Claude Code or Codex) and makes the one it finds the default
- [x] R42 Each provider knows its program: forms, colony new/track, consoles and colony doctor handle one that isn't installed
- [x] R43 Claude-only extras (Remote Control, plugin suggestions, the unattended runtime) step aside where only Codex is installed
- [x] R44 The monitor runs on whichever program is installed, its brief where that program reads it
- [x] R50 Settings: the person ticks which agent programs colony uses; one that's off isn't offered or the default, and projects already on it keep running
- [x] R62 Agent programs kept current: when Claude Code or Codex has an update, colony installs it and reloads each console on it only while that console is idle, resuming the same conversation; nothing mid-task is interrupted
- [x] R63 Usage limits watched: each program's 5-hour and weekly use read without tokens (Codex's session files; Claude Code's status line) and shown on the board; past a threshold (90% by default) each active agent on that program is told after its turn, and then winds down, hands off to the other program or pauses until the reset, as set colony-wide or per project
- [ ] R22 A project keeps a helper seat run by another provider, messaged like a project and shown like any console; holo-emitter's painter first — design/seats.md

## M8 — Models chosen from evidence

- [x] R45 Benchmark records and a card per model and effort level: two or three respected overall scores (Artificial Analysis, LMArena, Epoch AI) put on one scale and averaged, cost and time per task, gaps shown as gaps; kept on this machine
- [x] R46 Today's lineup researched and recorded, once (Claude models by colony, Codex models by colony-codex)
- [x] R47 A Models page on the board: the cards, a domain comparison, an effort-vs-cost curve per model, best for each role; an ⓘ beside each model when adding a project
- [x] R48 Each project's model plan: which model and effort its helpers use for which work, recommended at its start and agreed with the person, kept in .board and handed over at every session start, revisited when a model is added
- [x] R49 Adding a model to colony includes its research check; colony notices a new model, builds its card, reranks, and tells each project
- [x] R51 Each connected program's models are discovered (Codex's catalog; Claude Code asked, then each ID confirmed with a tiny call), at setup, when a program updates, and on request: the lineup is only what the person can run
- [x] R52 Artificial Analysis as the single source: fetched with the person's free key when the lineup changes, the cards derived from it, attribution shown
- [x] R53 Settings: connect or reconnect the Artificial Analysis key, with step-by-step instructions beside it
- [x] R54 First-time setup: the monitor's first conversation walks the person through agent programs, the key, defaults and options
- [x] R55 Choosing models: the form's suggestion is the cards', framed as theirs, the same an agent sees; no fixed suggestion
- [x] R61 Three tiers from one early, general score: routine, step-up and chores per family, picked from the Intelligence Index (lowest effort within a few points), a colony-wide default each project can change in its own settings; written as helper definitions (Claude Code: .claude/agents; Codex: its equivalent, derived with colony-codex) so the model and effort actually run; no plan to agree before work, no proposal per project when a model is released; the consultants are each family's step-up


## M9 — Consulting at decisions costly to change: the most insight per dollar

- [x] R56 The consult call for each program: fresh, in an empty folder with no settings or hooks, read-only; no silent fallback to Claude for an unknown provider — design/consult-log.md
- [x] R57 colony consult: each family's consultant chosen from the benchmark cards (best for planning, the cheapest within a few points), or the person's pick in Settings; the brief assembled in code (the person's words from the roadmap, item and notes; the asking agent's sourced digest; the question), two families side by side, the decision record (at most two rounds), every cost and answer logged; an off switch (no caps or budgets: the person's call)
- [ ] R58 Gates carry the consultants' points, each accepted or rejected by the person; only an accepted change opens a second, checking round
- [?] R59 The rule in every project's instructions, with examples of costly decisions; each project's consultations and their cost on the board
- [ ] R60 Each project's actual usage (reading against thinking and writing) beside its model plan

## M6 — Later

- [ ] R23 Unattended runs at the scale they were built for decide whether the runtime stays — README.md "Unattended runs"
- [ ] R24 A memory runner for a very long project, built only if a seeded long history shows it helps — design/blueprint.md "Under test"
- [ ] R25 Settle the map instruction (every builder asks `colony map` first) — design/blueprint.md "Under test"
- [ ] R26 A separate reviewer for a finished document about to leave the person's hands — design/blueprint.md "Under test"
- [ ] R27 Run a non-code goal (a novel, a business) end to end — design/claims.md #10
- [ ] R28 The extension test for structural quality — design/claims.md #12
- [ ] R29 Whether long-form work needs more than a single agent — design/claims.md #14
