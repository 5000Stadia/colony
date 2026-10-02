# Project lead, bounded continuation and completed versions

Approved by the person on 2026-10-01 (m1222c6 / g99da32), then refined by mc2b869, mb3fb46, mdd202f and the subsequent conflict-routing and single-provider conversation. Consultations cd37d08 ($0.96) and checking round c97f627 ($1.28) are complete.

A project has one switchable lead. Every roadmap item inherits that primary unless an explicit item owner takes different work. Changing providers alone does not create a second primary. Adding another agent to an already registered folder offers **Add helper** or **Switch lead**.

## One canonical plan

Shared records live beside the board registry in `leads.json`. Agent conversations, provider settings, notes and mail remain scoped to their logical roots. The canonical `ROADMAP.md`, including Vision, stays at one fixed absolute path through lead changes. The lead alone edits it; helpers propose changes. The board reads that same plan for every member. Commit canonical plan changes separately with `colony lead --commit-plan "message"`; helper item worktrees exclude ROADMAP.md.

A lead switch records a generation and a landing handoff. The outgoing agent finishes the current turn and commits its work, without starting another item. Colony confirms native continuation stopped, the console is idle, no draft/attached client is present and tracked work is clean before releasing the incoming lead. Old generation callbacks cannot restore the old owner. Explicit item owners survive a project-lead change.

## Work and engagement sync

For one agent/provider, the existing agent works directly in its project. No pairing, secondary-provider deliberation, helper workspace, synchronization or integration lock setup is needed. Checkpoints and bounded continuation are available independently. Coordination activates only after another member is explicitly added.

An assigned helper gets one short-lived item worktree based on the last completed, tested integration. Linked/dependent items run in sequence; independent items may have separate owners. Non-Git shared work is serialized. The console retains its logical identity when the agent changes into its assigned workspace.

Synchronization runs in deterministic delivery hooks when any member, including the lead, is engaged by a message, assignment or session start, and before handback/integration. It never broadcasts every integration or specification, wakes dormant helpers, calls a model, or targets unfinished lead HEAD. It checkpoints unfinished changes with a clearly labelled commit before merging the last tested revision, excluding generated instructions and harness state even when already staged. Pairing seeds each member's catch-up cursor; assignments and role changes preserve unread history. A context note is composed from all relevant commit subjects and completed item IDs since the last engagement; no generated summary or whole-project reread is needed.

Update delivery follows three checks: the recorded source is another agent or outside Colony, the update concerns this agent's project or current item, and the agent is engaged. Idle agents receive no file-change nudges. Their next engagement gets one complete catch-up, while Git and JSONL retain the full history. Commands record their acting agent; automatic checkpoint and plan commits carry a `Colony-Agent` trailer. Stamped own commits are excluded from catch-up, and an unstamped edit remains neutral. Vision changes coalesce to the first pending and current state. Note receipts and Git catch-up cursors advance after successful hook output, so failures retry.

Approval and correction decisions stay in checkpoint history. Recording one from the lead's matching console does not send it back as a note, including when an explicitly released next checkpoint starts. Decisions recorded elsewhere still reach the lead as Colony-generated receipts. Every note producer must choose an author; onboarding and action receipts belong to Colony, while direct human notes and answers keep their actual source. Console headings and wake notices distinguish Colony, the person, and their monitor.

Deferred catch-up has a token for the exact snapshot and selected assignment. Only successful output acknowledges that token; a failed flush, retired assignment, stale token or conflict cannot consume a newer receipt. An own plan commit changes only the plan cursor, and only when the acknowledged before hash matches the actual pre-edit content and the committed result matches the expected hash. Unread outside changes, Git subjects, completed items and pending receipts remain intact.

Git's shared ancestor supplies three-way conflict assessment. Nonconflicting changes merge automatically. For unresolved conflicts, Colony records filenames, ancestor/checkpoint/target revisions and base/workspace/integrated blob hashes (including absent/deleted versions), aborts the merge and preserves the checkpoint. Each file goes to its most recent recorded item developer, including a previous owner/lead, with the current item primary/project lead as fallback. Repeated delivery does not duplicate the notice. The assignment waits until a deliberate resolution is committed and sync succeeds.

The agent completing an item integrates, tests, deploys and pushes within the person's existing delivery permissions. A shared OS lock serializes simultaneous finishers without model calls: after the previous finisher releases it, the next syncs to that tested result and continues. Failed tests do not advance the tested sync target; the same agent corrects and re-tests the landed candidate. Failed deployment prevents pushing. Only the lead updates canonical plan status. Deployment/push commands are explicit, rather than inferred permissions for unrelated projects.

## Goals and human checkpoints

During the project's own Vision/path conversation, the agent proposes a few natural completed forms using existing milestones: what the person can use/read/see, a concise definition of done, included items and where approval or check-in is needed. Existing plans stay intact until agreement. `colony progress --define` records a boundary and `--start` deliberately releases it; Later never starts automatically.

Codex uses its native goal API. The role and bounded objective reach the intended console; the controller owns only goals it created or that the person/lead explicitly adopts. It persists mutation intent before RPC, reconciles uncertain outcomes by reading, and never blindly retries activation. Foreign user goals, manual pauses and native budget/usage/blocked stops remain intact. Hooks/watcher reconcile owned goals across sessions, pauses, context refresh and handoffs without a model call. Claude receives the same bounded instruction only when runnable; waiting does not generate repeated idle turns. Missing native control is visible on the board.

Routine `[?]` reviews inside the active version are collected at its checkpoint. They do not stop work on ordinary successors; a judgement the next work depends on remains an immediate gate. Open questions, costly decisions, usage pauses, handoffs, sync conflicts and version review stop continuation. The person can pause/resume the current scope from the board.

The completed candidate includes the exact tested commit and plan revision, or an existing coherent artifact and its hash, plus checks and inspection instructions. Running software requires a deployment receipt for that commit and an available URL. All included items and helper integrations must be finished. One candidate-bound review appears on the lead's board page. Approval, corrections and explicit release of a planned next version are distinct. Clear only hides the review; a stale/dismissed gate never approves the version. Changed plan/artifact/tested revision requires a fresh candidate.

## Commands

- `colony lead --pair AGENT`; `colony lead --switch AGENT --words "the person's direction"`.
- `colony lead --tested COMMIT --checks "checks passed" [--deployed]` records an already tested initial integration and optional delivery receipt.
- `colony item R4 --owner AGENT` (or `inherit`); `colony item R4 --start`, `--sync`, `--handback`.
- `colony item R4 --integrate "TEST COMMAND" [--deploy "COMMAND"] [--push]` is run by the completing item owner.
- `colony progress --define M2 --items R4,R5 --outcome "Usable version" --done "Both features work" --check "How to inspect it" --words "agreed direction"`.
- `colony progress --start M2-R4-R5`; `--ready ARTIFACT_OR_URL --commit COMMIT --checks "passed" [--deployed]`.
- `colony progress --approve CANDIDATE [--next CHECKPOINT]`, `--changes CANDIDATE --text "feedback"`, `--pause`, `--resume`, `--adopt-goal`, `--clear-goal` (explicit recovery; stops until resumed).

## Verification

Isolated Git checks cover ownership/inheritance/generations, one canonical plan, dirty helper checkpoints, tested-target engagement, conflict hashes/routing/abort, failed-test recovery and concurrent finishers. Candidate tests cover bundled review, stale artifacts and dismissal without approval. Native goal lifecycle checks use mocked RPC and existing local Responses fixtures, without account/model calls. Run those with the existing eight-module regression suite before restarting Colony.
