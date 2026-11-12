# Colony roadmap

One board to follow and steer a person's long projects, each driven by its own agent, with colony keeping watch across them.


## Vision

Colony is where a person brings ambitious, long projects to an excellent finish with AI agents, from their
phone as easily as their desk: one person, many ambitious works, carried to excellent finishes over years, by
a colony that remembers why, talks within itself, and gets wiser with every project. Each project is driven by
its own agent, which holds a shared vision with the person, lays the path toward it, and builds each step to
fit it and to make the next easier. The person steers; the agents carry the work, judging with the smartest
models where judgement matters, spending lightly where it doesn't, and bringing the person only what truly
needs them. Colony itself keeps watch across the projects for the person, and their word reaches one project or
many at once, as their own. The colony is more
than projects side by side: they speak to each other, work for each other, carry what one learns to the
others, and hold together over months and years, so the whole stands as a special and impressive execution of
many projects as one system. Colony works from a fresh install on whichever agent programs are there, follows
new models on its own, keeps itself current, and never gets in the way of the work.

- One board: every project, its vision, its path and what waits on the person, readable on a phone.
- The person's word is never lost: notes, direct conversation and the vision reach the agents, one project or
  several at once.
- One colony, not silos: projects talk directly (asks, replies, nests with a shared vision), one project's
  output can be another's input, and each stays coherent with the others and with the person's direction over
  the long run.
- Work for others too: the person can build custom workflow software for their own customers, each one its own
  project from a base primed for it; the customer uses theirs as a web app over Tailscale, hosted by the person
  or on the customer's own computers, and when they ask "can we change x or y", it goes through to the project's
  agent working on it and comes back updated.
- Wiser with every project: what one project shows works, measured, becomes guidance for all, so each new
  project starts smarter than the last; and the colony can tell whether its own changes made the work better or
  worse, not only that each seemed a good idea.
- A research department built in: the agent writing an item, who sees its need best, flags it for research, and
  one restrained guide is ready when the item starts. The guides evolve like a field's textbook from edition 1 to
  edition 30: when an item that used one finishes, its agent is asked whether what it found belongs in the guide, and
  a light model weaves that into the one section it fits, so the guides grow with the agents without making them
  token-hungry; the worthy ones ship with colony for everyone. An item flagged for research where a guide exists
  does both, for thoroughness and to keep the guide; and a guide is used where it has shown it pays and skipped
  where it doesn't, with the reason known.
- Intelligence where it counts: the smartest model for judgement, effort by role, two fresh views at costly
  decisions; no extra spend without substantial gain.
- Provider-agnostic: Claude Code, Codex and programs to come, each reachable from its own app.
- Light machinery: guidance as principles the agents interpret, mechanics as exact commands, hard limits in code;
  structure (tests, memory, review, rules) only where the work shows it helps more than it hinders; nothing
  interrupted mid-work.

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

Agreed 2026-10-02: completed items need no check by the person. Mark them done once the agent's own checks pass (tests, review); bring the person only nuanced judgements the next item depends on, or what needs their own eye (how it looks, feels or reads). The pre-item proposal above is needed only for an item not already discussed and agreed with the person; one settled in conversation starts without another proposal.

The person's words: "Consider it not necessary for me to check any specs unless there's nuanced judgments that need to be made before the next soec" … "Or my eye is important" … "These specs are mostly things we've discussed already".

## M1 — A harness that earns its shape

- [x] R1 Intention and blueprint, from the garden pilots' measurements
- [x] R2 The runtime
- [x] R3 Review only where the person declared risk
- [x] R4 Every part as a claim tested against a single agent
- [x] R5 The guide
- [~] R154 What the person hears: an item's shape, not each nail — on hold: built and in use; what is left is a count that needs more items started than there are yet (wake Sat Nov 14, 9 AM). The day's watch (Nov 11) found the person shown about a quarter of the words per step of work, and notes reading as notes, but the one item started that day did not open with its shape. Not done: next, the same count over every project's items started since Nov 10 (wake Nov 14). Built: the roadmap and asking guides say what Chat carries (an item starting, something to weigh, an item done, each sized) and what a line between steps is; the idle wake asks for an item's shape as it starts; Chat folds working notes with the work, and reading aloud leaves them out. (related: R144, R96, R140)

## M2 — The board: every project on one page

- [x] R6 The board
- [x] R7 The colony protocol
- [x] R8 Mail between projects
- [x] R9 The phone console
- [x] R10 Add a project
- [x] R11 Waiting on you
- [x] R12 Providers
- [x] R97 Roadmap shapes that serve a project's shape
- [x] R98 The Library
- [x] R99 One board for the person
- [x] R100 Carry on by rule
- [x] R101 A weakened test is caught and owned
- [x] R119 HTTPS for the board and for projects' own servers, shared through Tailscale
- [x] R121 Colony comes back by itself after the machine restarts — the board survives a reboot with no one at the keyboard; confirmed on real reboots 2026-11-02 (related: R142, R120)
- [~] R120 After the drive upgrade, reassess memory and the machine's configuration — done on the new 2 TB drive and 40 GB; 3400 failed the memory test, a clean pass at the speed kept is left, the person's (related: R121)
- [x] R122 Timed wakes
- [x] R123 The player as the unheard line
- [x] R124 The steward goes by the name the person gives it
- [x] R133 History plays onward
- [x] R134 Pause a project, and resume it
- [x] R135 The project band
- [x] R136 Active states drawn apart, helpers as dots, and the band ordered by them
- [x] R142 Idle sessions hibernate
- [x] R145 Send is type and Enter
- [x] R150 Status reports
- [x] R151 Playback steps part by part

## M3 — The monitor: acting for the person

- [x] R13 The monitor and its watcher
- [x] R14 The helm, project by project, with a standing direction
- [x] R15 Supports
- [x] R16 Scouting
- [x] R17 A console survives a memory kill and comes back to its conversation
- [x] R152 Tell projects, then the steward retired

## M4 — Colony on its own board

- [x] R18 Colony as a project on its own board
- [x] R19 The roadmap brought on board
- [x] R20 The watcher reports what is true
- [x] R21 Message
- [x] R30 Finished milestones load folded, and those before the first unfinished one fold into Completed
- [x] R31 Nothing is typed onto what the person has half-typed in a console
- [x] R32 The roadmap folds and orders itself by the work
- [x] R33 Not yet takes an item off the person's list until the agent says it's ready again
- [x] R34 The overview line reads Roadmap
- [x] R71 One switchable primary and canonical Vision/roadmap for a shared project, inherited item owners wit…
- [x] R74 Fix and simplify R71 and continuation from the 2026-10-02 review
- [x] R75 Research reads, never acts
- [x] R76 Questions that wait for their moment
- [x] R77 Every record of the person's words carries the agent's reading
- [x] R78 Guidance as principles, mechanics as exact commands

## M5 — Public: anyone can start with it

- [x] R35 Ready to publish
- [x] R102 A colony showcase
- [~] R125 A fresh install reaches a working board — a short recheck on Claude remains (related: R130, R152)
- [x] R126 Overview, Roadmap, Library and Showcase
- [x] R128 Pictures, PDFs and tables in Chat open full screen
- [x] R129 What the person asked colony-codex directly during Claude's pause
- [~] R130 One native Codex repair connection for the installation — the person can repair colony from the ChatGPT app when the board is down; waits on their one-time pairing (related: R125)
- [x] R127 Migrate and go

## M7 — Codex as a full option: from install to a project's agent

- [x] R36 A project's console runs on Codex
- [x] R37 Two projects can share one folder
- [x] R38 A new console's start-up questions
- [x] R39 Delivery for Codex through its own hooks
- [x] R40 Model and effort guidance for Codex, researched and marked claimed or measured, with the Codex entr…
- [x] R41 Colony starts with either program installed
- [x] R42 Each provider knows its program
- [x] R43 Claude-only extras
- [x] R44 The monitor runs on whichever program is installed, its brief where that program reads it
- [x] R50 Settings
- [x] R62 Agent programs kept current
- [x] R63 Usage limits watched
- [x] R113 Work behind a turn shows and is minded on both programs
- [x] R114 A Codex consult starts clean, as Claude Code's does
- [x] R115 The person's standing rules have one home for every seat and program, kept with their words, why an…
- [x] R116 Provider shapes, from one source
- [x] R117 Codex's missing analogues from the audit

## M8 — Models chosen from evidence

- [x] R45 Benchmark records and a card per model and effort level
- [x] R46 Today's lineup researched and recorded, once
- [x] R47 A Models page on the board
- [x] R48 Each project's model plan
- [x] R49 Adding a model to colony includes its research check
- [x] R51 Each connected program's models are discovered
- [x] R52 Artificial Analysis as the single source
- [x] R53 Settings
- [x] R54 First-time setup
- [x] R55 Choosing models
- [x] R61 Three tiers from one early, general score
- [x] R64 Every role chosen by the data unless the person picks
- [x] R65 Effort by role
- [x] R66 Codex consoles reachable from the ChatGPT app, as Claude Code's are through Remote Control
- [x] R67 Vision, path and next step


- [x] R68 Rolling fresh context

- [x] R69 Choose model-and-effort pairs by an intelligence goal first

- [x] R72 Auto keeps consultants and the step-up helper at the shared Intelligence Index ceiling at all seven…
- [x] R73 Restoration skips repeating a carry-over whose ID is already present in the native compaction summa…
- [x] R153 Model data from Artificial Analysis's new free list: only the models colony weighs, no page reading

## M9 — Consulting at decisions costly to change: the most insight per dollar

- [x] R56 The consult call for each program
- [x] R57 colony consult
- [x] R58 Gates carry the consultants' points, each accepted or rejected by the person
- [x] R59 The rule in every project's instructions, with examples of costly decisions

## M10 — Connections: outside services every project can use, safely

- [x] R103 One colony element through which projects reach outside services

## M11 — Wiser colony: what one project learns, the next one starts with

The person, 2026-10-25, asked: "How do you propose we can improve the wiser per project evolution? Would we have shared lessons for like actions? Would we have a research department subagents that were doing research would reference in conjunction with ?" To the proposal (gate g6e6733): "Add it, start with lessons and the research library". Agent's reading: a lesson is worth most at the moment of the action it is about, the way the hold rule rides in each connection, not in a document read once. Lessons come from evidence colony already keeps (declines, ratings, gate answers, redone work), counted by the steward, and the person adopts each with a tap. Research done once is kept for every project, with a date to look again. No standing research agents.
Measure: adopted lessons the mistake came back despite · count: colony lesson --recurred
- [x] R104 Lessons tied to the action they're for
- [x] R105 A shared research library
- [x] R106 Standing rules that survive compaction
- [x] R107 The board checks ready work
- [x] R131 Q Branch, colony's research department (removed by R159)
- [x] R146 Knowing whether colony's changes helped — a warning system read from what colony already records: outcomes, friction and cost kept apart, labels with evidence, a frozen ruler, exposure marked; shaped with colony-codex; builds after Friday's reset (related: R131, R144)
- [x] R147 Research in a locked room — one standard way any agent or helper researches the open web: prompt in, text out, a profile per provider, an OS sandbox that sees only its workspace, every call logged, output screened before an agent with tools reads it; Q Branch its first user; builds after Friday's reset (related: R75, R131)
- [x] R148 Dev mode: projects report where colony's shape gets in their way

- [x] R137 Waiting costs nothing
- [x] R138 A helper never wakes its lead just to say it is still waiting
- [x] R139 When a cleanup pass pays — the per-project check; its first pass (the roadmap) measured: paid back in about 2.75 weeks
- [~] R144 Topics closed as they end — the agent closes each topic as the conversation moves on (`colony topic-closed`, settled or parked), and colony clears it from context at the next pause, with a relevance-steered summary; reminded at two sizes; watched live, not blind-tested (the person's call) (related: R139, R143)
- [x] R143 A small roadmap
- [x] R132 A bar for a creative item, picked by the person from real examples
- [x] R140 Guidance arrives at its moment
- [x] R141 Questions in flight only for the unforeseen

## M12 — Work for customers: custom workflow software, one project per customer

- [ ] R149 What colony needs for a first customer's project — on hold: waits for the business project's first client to show what colony must build (the person, Nov 3: the business project takes it from there). Built as a real one shows it: the customer reaching only their app over Tailscale, their change requests reaching the project's agent, each customer walled off from the person's own projects and data; split decided 2026-11-07: the person's business project keeps the business and the customer's app, colony builds the bridge; the request box's contract agreed with that project 2026-11-07 (v1); the release yes and the walls sharpened 2026-11-09 (a yes no agent could write, the signer under its own account, walls at the operating system); then starting a project from a base, the walls and the bridge, as its first customer needs them (related: R147, R94)
- [x] R155 Are colony, the business project and the books project growing into one another? A report, not a plan — three assessments written apart, joined and presented with every difference named: they share a way of working, not a product; keep each whole and join at a few named points; nothing planned and no items opened in any project (related: R149)
- [x] R156 An audit of colony as a whole: what makes it effective, what gets in the way, and whether any part should be built differently. A short report, not a plan — the core is sound and well used (plain files, lasting consoles, one seam between programs, decisions with defaults, mail, the plan); what weighs on it is one file doing three jobs, instructions said in several places, and adding faster than checking what pays. Proposed, not built: split the big file, a pass of subtraction on the texts, count before building and once after (related: R131, R154)
- [x] R157 A commit chained to a piped test run is turned back
- [x] R158 `colony friction`: the friction on record in one list, run on demand, for a reading across reports — no model run, no words in any agent's context, nothing runs it by itself (related: R146, R148)
- [x] R159 The research flag on the library; the research department removed
- [x] R160 The Inbox: what needs the person, one item at a time, in an order that is built in; and no choice made by omission
- [x] R161 The page follows the voice by a switch the person sets, in place of rules that guessed when to: on, it goes to what is being read from anywhere; off, the page is never moved; leaving that chat by hand turns it off
- [x] R162 A link to a project's site is looked at when it is tapped: up, the person is sent on; down, colony starts it with the command the project gave, with no agent; what cannot open is said to them on a page
- [x] R163 A message to one project can be copied to others, or sent to a whole nest: Cc beside To in the message box, and each nest with two or more projects on the board as a row in the To list; every copy's first line says who else has the same words, so no agent passes them on; several ticked in Tell projects carry the same line (related: R152, R112, R93)

## M6 — Later

- [ ] R118 Programs reached other than through tmux
- [x] R24 When "the project is the memory" stops holding
- [ ] R79 When a real project runs with two agents
- [x] R81 Turbo
- [x] R82 Active hours
- [x] R80 Un-pinned 2026-10-08 by the monitor on the person's word
- [x] R83 Turbo's end returns each project to its usual posture
- [x] R84 Projects free in their own folder by default
- [x] R85 Codex keeps app access across its own updates
- [x] R86 Say how an item will be checked, and reopen what you produced before calling it done
- [x] R87 A frozen console is restarted by itself
- [x] R88 A vision aligned with the person's good, not only the task
- [x] R89 The showcase
- [x] R90 A quieter monitor
- [x] R91 Closing a project safely
- [x] R92 The vision's depth fits the project's scale
- [x] R93 Nests and the steward
- [x] R94 The board from anywhere, safely, through Tailscale
- [x] R95 The board, phone first, from a review of the person's own board
- [x] R96 Chat beside each console, and a board that keeps up with the person's day
- [x] R108 A floating player for reading aloud, over every page of the board
- [x] R109 Tokens spent, on colony's Overview
- [x] R110 The player reads every project in turn, each named, each in its own voice
- [x] R111 Digests for the steward
- [x] R112 A message box over every page, tied to a project
- [x] R26 A fresh reader for finished work about to leave the person's hands
- [x] R27 A non-code goal end to end
- [x] R29 Whether long-form work needs more than one agent
