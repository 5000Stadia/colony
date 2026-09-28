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
- [?] R32 The roadmap folds and orders itself by the work: the latest started first, finished last in Completed, a live dot on what's in progress, and each item's time in progress

## M5 — Later

- [ ] R22 A second provider (Codex or another CLI) — colony/providers.py
- [ ] R23 Unattended runs at the scale they were built for decide whether the runtime stays — README.md "Unattended runs"
- [ ] R24 A memory runner for a very long project, built only if a seeded long history shows it helps — design/blueprint.md "Under test"
- [ ] R25 Settle the map instruction (every builder asks `colony map` first) — design/blueprint.md "Under test"
- [ ] R26 A separate reviewer for a finished document about to leave the person's hands — design/blueprint.md "Under test"
- [ ] R27 Run a non-code goal (a novel, a business) end to end — design/claims.md #10
- [ ] R28 The extension test for structural quality — design/claims.md #12
- [ ] R29 Whether long-form work needs more than a single agent — design/claims.md #14
