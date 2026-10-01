# Rolling context (R68)

Colony refreshes context in the existing provider conversation. Project main agents refresh at an idle boundary after the last model request reaches 60% of the known context window. The monitor also keeps its 20-hour schedule. A refresh never deliberately replaces the conversation, restarts its console or changes its app identity.

The agent first replies with a short carry-over of transient intent, constraints, authorization, completed or uncertain actions, and rejected approaches absent from the current Vision, roadmap, notes and gates. Colony records the reply; the agent does not need file-write permission. Missing or oversized carry-over postpones scheduled compaction. Fifteen minutes without a valid reply is a failure, not permission to proceed.

Colony preserves up to four recent complete personal exchanges, including original whitespace and role labels. Tool output, setup messages, maintenance prompts and compaction's own summary response are excluded. Whole older exchanges may be omitted to fit; the newest exchange is never silently clipped. If it alone exceeds the budget, scheduled refresh waits. The carry-over is limited to 3,000 UTF-8 bytes, the exchange selection to 5,000 bytes including per-message allowance, and the whole restoration hook to 9,000 bytes. These conservative limits stay below Claude's installed 10,000-character hook-output cap. Codex's restoring hooks explicitly allow 12,000 tokens so its default preview substitution cannot shorten the bounded payload.

Native compaction retains its own summary. Colony then adds a separately labelled historical block before the next model request. It tells the agent to honor current instructions, use newer summary information over an older carry-over, and avoid repeating completed actions. This reduces the risk of a quoted old request becoming a new task; it does not make an LLM's memory perfect.

## Provider paths

Both installed providers support `SessionStart` with `source=compact`. Codex 0.159.3 queues that hook after history replacement and runs it before the next request, including continuation of a turn after automatic compaction. Claude 2.1.287 invokes it after `/compact`. Restoration has its own hook, separate from notes and helper instructions, so those do not consume its output allowance. Claude PreCompact prints nothing; Codex returns its required JSON. Codex restoration uses structured `additionalContext`, since a plain block beginning `[` would be mistaken for malformed JSON.

Scheduled Codex app conversations use their existing app-server socket and `thread/compact/start`, after checking native idle status. Local Codex and Claude use `/compact`. The same hooks restore either path. No callback RPC or `thread/inject_items` is needed. A later `UserPromptSubmit` can recover pending restoration after interruption, but only with transcript evidence that compaction actually completed.

Native automatic compaction remains the safety mechanism during long active turns. Colony does not start a new carry-over request in the middle of those turns. It restores earlier complete exchanges and the most recent dated carry-over; the provider handles the active input and newer progress. An oversized tail in this emergency path is retained by transcript pointer with an explicit reason, rather than blocking native compaction.

## Boundaries and recovery

Scheduling requires an idle, unattached console, no draft, no usage pause and no pending note, mail or monitor delivery. The checks run again before dispatch. Session identity is recorded from scoped provider hooks; helpers and another logical project sharing the working folder cannot replace it. The watcher processes main agents even when the monitor agent is disabled.

State, carry-over and history live in the logical project's `.board/context*.json` / `.jsonl` and `.board/context-carried.md`; the monitor has its own `.board` under its home. `colony context` shows the current phase and reason. An incremental transcript index under the board home survives hook processes, so later hooks do not reread months of history. Provider transcripts are never rewritten or pruned.

A definite failure before dispatch can retry after an hour. A dispatched request with an unknown outcome remains pending; Colony does not send it again on a timer. PreCompact alone is not proof of successful compaction. Printed restoration remains unconfirmed until transcript evidence or the subsequent completed turn; dropped output can be emitted again with the same historical marker. A restored generation waits for fresh usage and has a one-hour cooldown. If reconstructed context is still above the threshold, further scheduled refresh waits for a measured reduction, avoiding a costly repeat loop.

A wedged provider is not automatically killed to recover it. Inspect `colony context`, its history and the native console to diagnose a pending/unknown operation before resuming ordinary native work. This deliberately retains the person's approved rule: no successful fresh carry-over means no scheduled compaction. There is no guaranteed daily recovery from an unhealthy provider.

Claude context occupancy includes cache-read and cache-creation input. Its status line supplies the window size; a project with a custom status line that bypasses Colony may lack that telemetry, in which case the threshold is deferred rather than guessed. The monitor's existing 150k cap remains effective. Codex uses last-request usage and model window, never accumulated token spend.

## Evidence and rollout

The person accepted first consultation `ce22bd8` ($1.42), recorded on gate `g165e3f`. Checking round `cffe64a` cost $3.41 and identified hook output limits, hook trust-count validation, and the native SessionStart compaction path. The implementation addresses those findings; there is no third consultation.

Isolated tests cover carry-over failure, oversize/incomplete exchanges, exact whitespace, current usage, session scope, draft/busy/pause guards, monitor scheduling, lost hook output, uncertain dispatch, cache reuse and reduction telemetry. Native Codex Responses fixtures prove bounded restoration above the default hook allowance reaches the next model request intact and once, remains after a server restart under the same ID, and reaches continuation within a turn after automatic compaction. The normal remote tests also exercise attachment, helpers, two projects sharing a folder, and question delivery with the expanded hook set.

Minimal live-account scratch checks ran on 2026-10-01:

- Claude 2.1.287 / Sonnet 5.5: initial constraint, `/compact`, resume. Session ID stayed `f46155d2-4fc3-4ef8-bbbc-3fd45f5564c1`; the compaction hook attachment held the historical block. The reply retained “periwinkle” and correctly said not to resend an already-sent sample invoice. Three reported call costs total $0.7595274.
- Codex 0.159.3 / GPT-6.1 Sol: initial constraint, native same-thread compaction, continuation. Thread ID stayed `01a0f8fd-5c0c-7693-963f-7febc1d707ee`; restoration completed, and the reply was “The color is periwinkle, and the invoice should never be sent again.” No tools or real project work were requested.

The full regression suite passed 220 tests; 28 focused context and monitor checks passed after the final monitor-resume refinement.

These are path and continuity checks, not a broad paid model comparison or a proof of long-term project success. They did not restart or migrate any live project. Colony owns merge, live rollout, app continuity checks and deployment. Existing sessions acquire new hooks through the ordinary safe launch/reload path; their conversation IDs remain unchanged. The live Claude probe verifies manual compaction plus resume; automatic mid-turn Claude compaction was not separately forced.
