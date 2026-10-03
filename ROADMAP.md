# Colony roadmap

One board to follow and steer a person's long projects, each driven by its own agent, with a monitor that acts for them across the colony.


## Vision

Colony is where a person brings ambitious, long projects to an excellent finish with AI agents, from their
phone as easily as their desk. Each project is driven by its own agent, which holds a shared vision with the
person, lays the path toward it, and builds each step to fit it and to make the next easier. The person
steers; the agents carry the work, judging with the smartest models where judgement matters, spending lightly
where it doesn't, and bringing the person only what truly needs them. A monitor acts for the person across
the colony, within the helm they give it. Colony works from a fresh install on whichever agent programs are
there, follows new models on its own, keeps itself current, and never gets in the way of the work.

- One board: every project, its vision, its path and what waits on the person, readable on a phone.
- The person's word is never lost: notes, direct conversation and the vision reach the agents and the monitor.
- Intelligence where it counts: the smartest model for judgement, effort by role, two fresh views at costly
  decisions; no extra spend without substantial gain.
- Provider-agnostic: Claude Code, Codex and programs to come, each reachable from its own app.
- Light machinery: a guardrail only where it helps more than it hinders; nothing interrupted mid-work.

## Working agreement

Agreed 2026-10-02: before developing or implementing each new Colony roadmap item, present the person
with the proposed improvement, any overhead in use, whether it remains relevant to the current Vision
and what is already built, and Claude's current assessment of whether it is still the smartest next step.
Include any disagreement or better alternative from Claude. Wait for the person's answer before starting
the item; approving a completed version does not release the next item automatically.

The person's words: "Approve on the colony roadmap items however before each new item developed and
inplemented pleasse surface to me the proposed improvement, overhead in use, if its still relevant and
if Claude still agreed its the smartest next step".

Agreed 2026-10-02: keep this conversation and its pending-approval checks scoped to the shared Colony
project. Do not aggregate other projects' approvals or follow their approval acknowledgements here.
Bring another project's information in only for a specific Colony dependency or an explicit request.
Claude's paired review of Colony's work belongs to this shared project.

The person's words: "Additionally, mucking up your context with other projects approvals is not something
that should be a colony board behavior. I probably shouldn’t have. Had you concern yourself with any
updates on any other projects. Let’s just refocus here."

Agreed 2026-10-02: the primary Codex agent keeps its goal aimed at the next agreed pause and refreshes
it automatically when the next scope is authorised or a required check-in is cleared. The person should
not need to issue another `/goal`. This preserves the approval required before each new Colony item;
goal updates do not release unapproved work. This clarifies R71, rather than adding an upcoming item.

The person's words: "Also codex goals should be encouraged to automatically be updated to the next
expected pause.  Im not sure if we specced that in upcoming or not".

Agreed 2026-10-02: completed items need no check by the person. Mark them done once the agent's own checks pass (tests, review); bring the person only nuanced judgements the next item depends on, or what needs their own eye (how it looks, feels or reads). The pre-item proposal above still applies.

The person's words: "Consider it not necessary for me to check any specs unless there's nuanced judgments that need to be made before the next soec" … "Or my eye is important".

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
- [x] R71 One switchable primary and canonical Vision/roadmap for a shared project, inherited item owners with explicit exceptions, bounded native Codex goals and human checkpoints at completed versions. Built: engagement sync to the last tested integration with preserved checkpoints and routed Git conflict hashes; the completing agent integrates, tests and delivers under a shared lock. Single-provider projects use their existing agent without secondary-provider coordination. Specification: docs/project-lead-spec.md.
  Approved in m1222c6 / g99da32, refined by mc2b869, mb3fb46 and mdd202f; consultations cd37d08 ($0.96) and c97f627 ($1.28) complete. The requested live lead switch to colony-codex has landed. The person's notification refinement is built: explicit source records suppress own changes, relevant updates wait quietly during idle periods, and engagement delivers one complete catch-up with receipts after output. Lead and helper catch-up preserve unread history across assignments and handoffs; staged generated instructions stay out of checkpoints. Anthropic peer review m7f1e8a informed that refinement. All 318 regression checks pass, including isolated Git and native-goal lifecycle coverage of ownership, exclusive handoff, synchronization, review and recovery. The person approved completed version M4-R71 in conversation on 2026-10-02. Requested follow-up: generated receipts identify Colony explicitly, and known approval/correction decisions do not echo back to the recording lead. Catch-up acknowledges only the exact flushed snapshot and preserves unread outside changes through retries and assignment switches. Anthropic review mdf7eb8 recommends this as a bug fix with no model calls in normal use.
- [x] R74 Fix and simplify R71 and continuation from the 2026-10-02 review: integration tests the exact commit that lands, never in the lead's live checkout; sync never commits a lead's unfinished work; delivery hooks fail safe without losing mail; carry dedupe keeps carry-overs a summary lacks; notes carry their true author; Later never enters a version; idle agents are not woken; only explicit links serialize items; instructions sent once; dead state removed. Findings: ~/.config/colony/reviews/2026-10-02-codex-work.md. Approved by the person 2026-10-02 ("Yes, I'm starting R 74").
- [~] R75 Research reads, never acts: the monitor's scouting web reading moves to a read-only scout helper (search and fetch only; PROVIDER: tool limits differ by program) that returns each find as plain fields, so strangers' text never reaches the session that speaks for the person, answers prompts or changes settings. Every research helper colony runs is read-only, and every research report carries a built-in notice: Internet sources (projects, packages, code, sites) may be malicious, and anything taken from them gets an independent safety pass on those sources before it is adopted. Agreed 2026-10-02, after R74. The person's words: "all researchers should be read only and further the reports they produce should be presented with a built-in disclaimer that any further adoption from the research for Internet-based resources, projects, etc. are not immune to being malicious and should have an independent safety pass on those sources prior to reaching out there for Actual adoption". Source: ~/.config/colony/reviews/2026-10-02-openai-dots-research.md #2.

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
- [x] R63 Usage limits watched: each program's 5-hour and weekly use read without tokens (Codex's session files; Claude Code's status line) and shown on the board; a safe pause (98% by default): each agent on that program is told on its next turn to land what's in flight, save its work and tell the person where things stand and their options; woken at the reset; colony-wide or per project
  Follow-up: hold each blocking window through threshold rounding; resume when each resets or drops at least two percentage points below the configured threshold. Missing readings do not release a pause. The person's live setting is now 99%; the installation default remains 98%.
- [ ] R22 A project keeps a helper seat run by another provider, messaged like a project and shown like any console; holo-emitter's painter first — design/seats.md

## M8 — Models chosen from evidence

- [x] R45 Benchmark records and a card per model and effort level: two or three respected overall scores (Artificial Analysis, LMArena, Epoch AI) put on one scale and averaged, cost and time per task, gaps shown as gaps; kept on this machine
- [x] R46 Today's lineup researched and recorded, once (Claude models by colony, Codex models by colony-codex)
- [x] R47 A Models page on the board: the cards, a domain comparison, an effort-vs-cost curve per model, best for each role; an ⓘ beside each model when adding a project
- [x] R48 Each project's model plan: which model and effort its helpers use for which work, recommended at its start and agreed with the person, kept in .board and handed over at every session start, revisited when a model is added
- [x] R49 Adding a model to colony includes its research check; colony notices a new model, builds its card, reranks, and tells each project
- [x] R51 Each connected program's models are discovered (Codex's catalog; Claude Code asked, then each ID confirmed with a tiny call), at setup, when a program updates, and on request: the lineup is only what the person can run
  Follow-up: separate writable Codex host catalogs, retain newer-client discovery against older writers, and respect catalog identity across account changes. Settings shows Codex client/fetch/identity and Claude fetch/expiry, flags older or expired catalogs, and each program’s session-start hook re-reads its native catalog without model calls. Seven catalog/freshness checks and all twelve native/remote checks pass on Codex 0.160.0. The benchmark integration fixture now isolates native catalogs across session-start subprocesses and exercises real daily discovery before evidence changes helper selection; all 246 checks in colony’s requested eight-module regression suite pass.
- [x] R52 Artificial Analysis as the single source: fetched with the person's free key when the lineup changes, the cards derived from it, attribution shown
- [x] R53 Settings: connect or reconnect the Artificial Analysis key, with step-by-step instructions beside it
- [x] R54 First-time setup: the monitor's first conversation walks the person through agent programs, the key, defaults and options
- [x] R55 Choosing models: the form's suggestion is the cards', framed as theirs, the same an agent sees; no fixed suggestion
- [x] R61 Three tiers from one early, general score: routine, step-up and chores per family, picked from the Intelligence Index (lowest effort within a few points), a colony-wide default each project can change in its own settings; written as helper definitions (Claude Code: .claude/agents; Codex: its equivalent, derived with colony-codex) so the model and effort actually run; no plan to agree before work, no proposal per project when a model is released; the consultants are each family's step-up
- [x] R64 Every role chosen by the data unless the person picks: Auto always an exact model colony chose (main agents included; no program alias), one colony-wide switch for new models (switch automatically, or ask once per model listing every role it would take, pinned ones included; "not this one" remembered), each switch showing its cost change with a one-click way back. Built by colony-codex, deployed and approved by the person in conversation on 2026-10-02 — consults c6ed30b / c0219af; docs/model-selection.md
- [x] R65 Effort by role: smartest model at max for rare judgement (consultants, the step-up helper), xhigh for the monitor, the knee for main agents; routine keeps R61's cheapest model within reach, at its knee; any role still overridable
  Inactive provider seats also refresh their accepted effort; this leaves the running monitor and runtime provider unchanged.
  Hard judgement goes to the max-effort step-up helper with a tight brief even when the main agent uses the same model at its knee; ordinary work stays with the main agent.
- [x] R66 Codex consoles reachable from the ChatGPT app, as Claude Code's are through Remote Control: on by default, set up by colony
  Live and confirmed reachable in ChatGPT for colony-codex. First connection offers pairing through either monitor provider; Yes generates the fresh code with Codex → Add manually instructions, Not now remembers the choice. Board pairing includes expiry and acceptance checks. Deployed and approved by the person in conversation on 2026-10-02.
- [x] R67 Vision, path and next step: every project steers toward a vision (## Vision atop ROADMAP.md, the person's words, sharpened by its main agent with them, settled when the work reaches what depends on it); the path re-checked at every stone; each step built to fit the vision and ease the next. Built by colony-codex
  The person settled g9fd35d: direct board saves, clearly agreed conversation updates, and observed file changes with before/after notes; no confirmation clicks or approval machinery. Details affecting only a few items belong in those descriptions or specifications, including details entered through the Vision box. Consulted in cc9948d and checked in c21b5b3; implementation and rollout notes in docs/vision.md.
  Built with isolated checks: full 203-test regression suite passed, then all 14 vision tests passed after the final migration refinement. Merged and deployed by colony, including the Vision box and agent response.
  Live in colony at 635e496 (204 checks). Follow-up deployed: every newly added project's own agent discusses and records the agreed vision with the person before laying the roadmap, including joined projects with existing plans; monitor setup does not replace that conversation. CLI, browser and join checks pass. The person approved it in conversation on 2026-10-02.


- [x] R68 Rolling fresh context: at a context threshold, idle main agents write what colony's records do not hold, then continue fresh with that carry-over and the last few conversational turns verbatim; the monitor keeps its daily refresh with the same continuity. Preserve safe typing/turn boundaries and app access; consult on mechanics first. Built by colony-codex.
  Approved by the person (Yea, gate g165e3f); adoption recorded on ce22bd8. Checking round cffe64a ($3.41) completed. Same-chat compaction and bounded historical restoration are built, including both providers and the monitor. Minimal live-account Claude and Codex continuity checks passed without restarting projects; the 220-test regression suite and 28 final context/monitor checks passed. Colony merged and deployed the core with 222 checks passing; the session-bookkeeping and automatic hook-wiring follow-ups are handed over. Mechanics, recovery limits and rollout evidence: docs/rolling-context.md.

- [x] R69 Choose model-and-effort pairs by an intelligence goal first: one Intelligence Index ceiling across all runnable providers, a configurable distance below it for each role, then the cheapest task-cost pair in each provider meeting that goal, or its highest-scoring pair with the shortfall visible. Exclude Ultra on Auto. Usage never lowers intelligence; waiting for reset is acceptable. Chores retain a value pick. Preserve adoption, rollback, pins and evidence/estimate labels; no domain categories or R70.
  The person's goal-first direction and final estimation rule supersede the knee proposal (m6ac2f1, md3ecb7). At least two measured efforts use the model's own average score gap; exactly one uses the closest same-provider donor at that effort and percentage spreads; otherwise no estimate or selection participation. Costs use positive proportional steps. Qualifying estimates follow the existing adoption switch and are replaced by measurements on daily refresh. Balanced offsets: judgement 0, monitor 2, main/runtime 4, routine 10. Primary evidence and plot: docs/model-pairs-research.md. The person approved one-point score equivalence and a seven-position global Auto slider with a per-project override (m1ec0d2; g835a7b answered). First consultation c8aafb9 ($2.72) and checking round cccc136 ($2.23) complete. Built: seven-position slider and previews in colony/project settings; project-scoped adoption and rollback; Index-only Models page; measured/estimated pair provenance and daily refresh. All 245 regression checks passed, then all 13 relevant checks passed after matching the person’s daily-refresh direction. Read-only current-lineup verification picks Sol6.1/max for Codex main. Colony merged, deployed and pushed R69 with all 245 checks passing (maa87ae). After reconciliation confirmed Sol6.1/max for both Codex projects, colony cleared both temporary model/effort pins; both now use Auto. Bookflow’s and holo-emitter’s Opus high pins were preserved. Daily fetching is guarded across board restarts. The person approved the deployed slider in conversation on 2026-10-02.

- [x] R72 Auto keeps consultants and the step-up helper at the shared Intelligence Index ceiling at all seven balance positions; Economy still shifts main, runtime, routine and monitor. Approved by the person in ma3eea1; focused checks pass.
- [x] R73 Restoration skips repeating a carry-over whose ID is already present in the native compaction summary, while preserving recent conversation verbatim. Approved by the person in m9f4b5a; Claude summary and original-carry regression checks pass.

## M9 — Consulting at decisions costly to change: the most insight per dollar

- [x] R56 The consult call for each program: fresh, in an empty folder with no settings or hooks, read-only; no silent fallback to Claude for an unknown provider — design/consult-log.md
- [x] R57 colony consult: each family's consultant chosen from the benchmark cards (best for planning, the cheapest within a few points), or the person's pick in Settings; the brief assembled in code (the person's words from the roadmap, item and notes; the asking agent's sourced digest; the question), two families side by side, the decision record (at most two rounds), every cost and answer logged; an off switch (no caps or budgets: the person's call)
- [x] R58 Gates carry the consultants' points, each accepted or rejected by the person; only an accepted change opens a second, checking round
  Built: a few curated points with preserved consultant sources, individual choices and optional comments on all gate views, automatic adoption from the human's choices, checking-round snapshots and retained revisions. Incomplete or duplicate choices do not approve work; exact retries repair missing notifications without duplicate records. Fourteen isolated feature checks pass, including concurrent submissions and a single available provider. Clear closes a gate without inventing choices or allowing a checking round; retries repair its quiet receipt. Usage: docs/consultation-gates.md. Stop for the person's review after integrated checks and deployment.
  Authorised by the person in conversation on 2026-10-02: "Fully agree, please implement". Claude recommends this as the next item (mdf7eb8); reuse existing consultant answers without additional display or recording model calls. Stop at the tested, deployed R58 version for the person's review.
- [x] R59 The rule in every project's instructions, with examples of costly decisions; each project's consultations and their cost on the board. Approved by the person in conversation on 2026-10-02.
- [ ] R60 Each project's actual usage (reading against thinking and writing) beside its model plan

## M6 — Later

- [ ] R23 Unattended runs at the scale they were built for decide whether the runtime stays — README.md "Unattended runs"
- [ ] R24 A memory runner for a very long project, built only if a seeded long history shows it helps — design/blueprint.md "Under test"
- [ ] R25 Settle the map instruction (every builder asks `colony map` first) — design/blueprint.md "Under test"
- [ ] R26 A separate reviewer for a finished document about to leave the person's hands — design/blueprint.md "Under test"
- [ ] R27 Run a non-code goal (a novel, a business) end to end — design/claims.md #10
- [ ] R28 The extension test for structural quality — design/claims.md #12
- [ ] R29 Whether long-form work needs more than a single agent — design/claims.md #14
