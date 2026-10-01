# Shared project vision

`## Vision` in the logical project's `ROADMAP.md` holds the live, multiline description of the finished
work. The board, project list, consultant brief and monitor posture all read it through `board.roadmap`.
An absent or empty section falls back to the old introductory goal. Vision prose and bullets do not
become roadmap items. Adding the section preserves the existing introduction and milestones.

The vision describes what shapes the whole product: narrative, feel, and fundamental elements that
every milestone must consider. Details that inform only a handful of items belong in those items'
descriptions or specifications. The main agent moves such details there, including details entered
through the Vision box, tells the person where they went, and records the move in the vision history.
This routing is an agent responsibility; saving a form does not attempt to classify prose automatically.

## Updating it

- The project's Vision box saves directly to the file and records a dated change. A nonquiet note gives
  the main agent the before and after. It considers the effect on current work, acts, and discusses
  unclear implications. There is no confirmation click, approval state or accepted snapshot.
- Once a conversation change is clearly agreed, the agent runs
  `colony vision --file PATH --words "the person's words agreeing it"` (`--file -` reads stdin).
  This writes the file and dated history without notifying the same agent of its own recorded action.
  Brainstorming and what-ifs never update the vision.
- Edits and merges outside that command are observed by the board watcher and delivery hooks. Their
  notes identify an observed file change, without attributing authorship or agreement. The live file
  remains the vision. Removing the section or the entire file is also recorded.
- `colony vision --history` and the page's Vision history show when and how the text changed, including
  the person's words when recorded. The history is a record, not another source of authority.

The watcher runs even when the monitor agent is disabled. A safe idle console is nudged immediately
after a board save; a stopped console is started. Busy consoles receive notes through their delivery
hooks or once idle. Existing protections for a person's half-typed input and usage pauses still apply.

## Persistence and rollout

Each logical root owns `.board/vision.jsonl`, `vision.lock` and `vision-installed`. Projects sharing a
working folder retain separate visions, histories and notes; their wired instructions name their own
roadmap explicitly. No vision is inferred from a shared working directory.

Board startup installs the protocol and quietly offers existing projects a starting draft from their
legacy goal, to shape with the person in the next normal conversation. It does not publish that draft.
Project discovery covers projects added after startup. The migration note is emitted once, including
across restarts, and never wakes the agent on its own. Existing visions are left intact; those projects
receive a quiet explanation of the editing flow without being asked to redraft them.

Vision writes and observation serialize per root. File replacement is atomic. Forms compare the
previous text directly, normalizing browser line endings, so an old editor cannot overwrite a newer
vision; a conflict page retains the unsaved draft. Module locks coordinate Colony writers; arbitrary
external editors do not take those locks. Observation records the current text it sees, not every
intermediate filesystem write between checks.

Each change has a stable notification ID. An interrupted notification is retried from history;
replayed note records preserve their delivered and addressed state. History never blocks a valid
new vision on an approval step.

## Checks

`python3 -m unittest tests.test_vision` covers multiline consumers, legacy introductions, fences,
conversation records, HTTP saves with browser line endings, stale edits, recovery, concurrent
observers and writers, removal, migration, shared folders, and delivery with the monitor off.
Run it with the existing suites listed in `CLAUDE.md` before rollout. All fixtures use isolated board
state and stand-in consoles; these checks make no model calls.

R67 was consulted in `cc9948d` and checked after the person's decision in `c21b5b3`. The second round
identified legacy intro boundaries, browser line endings, a missing revision prompt, neutral file-change
attribution, and replay preserving note state; the implementation and checks cover those corrections.
