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

A newly added project's first work is a vision conversation between its own agent and the person.
The monitor's setup does not replace it. New projects start with an empty Vision section and no
placeholder milestones. Joined projects can read their plans, specs and history first; their existing
plans remain intact. The agent records the vision once clearly agreed, then lays the roadmap toward it.
Tracking queues this opening conversation once, including for projects that already have a roadmap,
so `colony new`, the add form, clones and shared-folder projects follow the same sequence.

- The project's Vision box saves directly to the file and records a dated change. A quiet note gives
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

The watcher runs even when the monitor agent is disabled. File and Vision updates never wake an idle
or stopped agent. The next engagement hook delivers one catch-up with the first pending state, the
current Vision and the number of intervening changes. All original history remains available on the
board. Notes on an unstarted item wait until that work becomes relevant; project Vision is relevant
to every declared member. Ordinary messages, assignments and person questions can still engage agents.

Conversation commands record the source agent and do not echo its own action. Programmatic plan
and checkpoint commits include a `Colony-Agent` trailer. An exact matching committed Vision transition
can identify an edit observed before that commit; unstamped outside edits keep their source unknown.
There is no inference from an agent being busy, from its Git email, or from the prose. The delivery
hook writes its receipt after flushing the catch-up; failed output leaves it available for retry.
Acknowledging the catch-up's representative note also acknowledges its constituent notes.

## Persistence and rollout

Each explicitly shared project owns one canonical `.board/vision.jsonl`, `vision.lock` and Vision in
its canonical roadmap. Each member keeps its own delivery and acknowledgement records. Separate
projects retain separate histories; sharing is declared, never inferred from a working directory.

Board startup installs the protocol and quietly offers existing projects a starting draft from their
legacy goal, to shape with the person in the next normal conversation. It does not publish that draft.
Project discovery covers projects added after startup. The migration note is emitted once, including
across restarts, and never wakes the agent on its own. Existing visions are left intact; those projects
receive a quiet explanation of the editing flow without being asked to redraft them. Newly added
projects already receive their opening conversation note and do not also receive the migration note.

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
