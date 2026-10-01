# Project lead, continuing work and human checkpoints

Draft requested by the person on 2026-10-01. First consultation R71 / cd37d08 is complete ($0.96); the substantive additions below await the person's decision. This draft does not migrate projects.

## What the person asked for

When several agents work on the same project, one switchable primary owns the whole roadmap by default. Each item has a primary through that default; explicit item assignments are needed only when agents take different work. The person wants colony-codex to take over Colony's primary role from the Anthropic agent.

The primary should establish a Codex goal automatically so work continues across items until a suitable completed milestone/version. The initial conversation about the Vision and path should also propose reasonable human checkpoints with little work for the person. These checkpoints present succinct completed forms of the work, rather than stop in the middle of an implementation.

## Ownership and switching

- A shared project has a declared identity, member agents and one current lead. Sharing a folder or a similar name is a suggestion for pairing, not sufficient authority to combine projects.
- One authoritative roadmap supplies the Vision, item identities and completion boundaries for all member agents. One shared record beside the board registry holds explicit member roots, lead, item exceptions, handoff state and the accepted plan revision. A lead change preserves that roadmap and the agreed stopping points.
- Only the lead edits the authoritative roadmap and allocates item identities; another agent proposes changes to it. For paired worktrees, a switch takes effect after the incoming branch has the reconciled current plan. The board then reads that lead's authoritative copy, rather than treating two branch copies as separate plans.
- Every open item shows its effective primary. An unset item assignment means the current project lead; an explicit assignment identifies another member and its responsibility. A split is recorded when agreed, rather than silently having two primaries take the same item.
- The board offers a lead selector and an item assignment where needed. Equivalent CLI commands let the person and an authorized agent record conversation decisions.
- Changing the lead tells both agents what changed and transfers the current objective, stopping point, relevant notes, decisions and unfinished work. The outgoing agent lands work already in flight; the incoming agent starts once the handoff is safe. Work already finished is not repeated.
- The handoff has an ownership generation and a recorded phase. An old callback or reconnected session cannot restore the previous lead. Explicit item owners have their own bounded assignments; the lead coordinates their hand-ins rather than taking over their work while waiting.
- Lead responsibility is distinct from permission to merge, deploy, publish or send externally. Colony's current implementation/deployment split remains the same unless the person changes it.

## Goals and continuation

- The current execution segment names a completed form of the work, its scoped roadmap items, what must be true when it is ready, and the human checkpoint at its end.
- An agent taking the primary role reads the current Vision and path, checks that this segment still makes sense, and establishes or resumes the corresponding goal. The default goal is bounded by the next suitable completed milestone/version, not the whole distant Vision or the Later list.
- Codex uses its native goal lifecycle. Session-start and lead-change wiring supplies the role, current segment and clear instruction to establish the goal when needed. The board records which native goal represents the current segment.
- Existing valid goals are reused. A manual pause, completed goal or changed human direction is not overwritten by periodic upkeep. Goal setup remains scoped to the intended project's console and conversation.
- The watcher reconciles only goals it owns with the current lead, plan revision and checkpoint. A new thread receives the same bounded segment after reconciliation. An unrelated goal set by the person is preserved and discussed rather than replaced.
- The goal includes the agreed stop condition explicitly. A human checkpoint, blocking question, unresolved costly decision, usage pause or outgoing handoff must prevent automatic continuation past that boundary.
- The person can pause or continue from the board. Automatic continuation never approves work, dismisses a gate, opens the next version, or broadens the agreed scope on their behalf.
- If native goal control is unavailable, the board explains the missing continuation capability and the agent receives the same bounded objective. A visible fallback is needed; an idle console must not be shown as continuing work.

## Completed versions and human checkpoints

- During the project's own initial Vision/path conversation, the lead proposes a small number of natural completed forms appropriate to the project: for example, a useful first version, the complete intended version, and final handoff where these are distinct.
- Each proposed checkpoint has a short name, the included scope, what the person will be able to see/use/read, completion criteria and whether it is approval or a check-in before proceeding. The person can accept the proposed path in the normal conversation or edit the checkpoints on the board.
- Existing roadmaps are preserved. At the next suitable conversation, the lead proposes checkpoint boundaries using what is already planned; it does not insert approval stops into projects silently.
- A checkpoint is reached only after the included work performs its intended purpose and the agent has completed its own relevant checks. The agent presents a coherent deliverable, how to inspect it and any decision the next version needs.
- The checkpoint refers to the integrated result and its plan revision/commit or artifact identity. For a software checkpoint about running behavior, the intended version must be deployed and available for the person to inspect; an unmerged branch is not that candidate. For a document, the coherent readable artifact is the candidate; a checkpoint does not itself authorize publishing it.
- At that point the segment stops and the board shows it waiting for the person. Approval or a direction to proceed is recorded before the next segment starts. If the person asks for corrections, those stay in the current segment until it is ready again.
- An ordinary item completion is not itself an additional human checkpoint. Existing gates still handle decisions that arise during work; a planned version checkpoint must never delay fixing a current problem or obtaining a decision the work already depends on.
- Proposed consultation change: routine item-level `[?]` reviews are gathered into the version's checkpoint rather than each stopping continuation. A judgement that subsequent work depends on remains an immediate gate. Checkpoints use the existing milestone boundaries and one concrete line describing the completed form, rather than a separate competing version plan.

## Compatibility and rollout

- A single-agent project has itself as the default primary; no pairing setup is needed.
- Several board agents may share one working folder or use separate worktrees. Their provider settings, conversations and local files stay scoped to the correct logical agent.
- Pairing existing projects must preserve their notes/gates and reconcile divergent roadmap content deliberately. It must not discard a branch's unfinished work or infer that similar item numbers mean the same item.
- Safe handoff uses the existing turn, draft, context-refresh and usage-limit protections. Starting a new session must restore the role and segment without repeating completed work.
- Proposed consultation change: concurrent item owners use separate worktrees/branches and hand in their work for integration. If worktrees do not apply, edits in a shared working folder are serialized; two owners do not commit/test each other's unfinished changes.
- Rollout should explicitly pair colony and colony-codex and set colony-codex as lead, as requested. Other projects keep their current role/scope until their own pairing or checkpoint conversation.

## Verification before handoff

1. A single-agent project inherits its own lead for every item; explicit item assignment overrides that default; switching the project lead changes inherited assignments while preserving explicit ones.
2. Both member agents read the same roadmap and execution segment across a shared folder and across separate worktrees; unrelated projects remain separate.
3. A lead switch with work in flight drains safely and does not start competing goals. The new lead receives the unfinished state and honors the same checkpoint.
4. A Codex native goal is established once for the active segment, persists through restart/context refresh, and stops at human review, a gate, explicit pause, usage pause or handoff.
5. A completed checkpoint creates one durable waiting moment with a concrete deliverable; repeated watcher runs do not restart work or create duplicate questions. Human approval, corrections and deliberate continuation have distinct recorded effects.
6. Initial conversation guidance proposes useful completed versions concisely and preserves existing plans until agreed.
7. Native goal verification uses the existing local Responses fixture with no account calls. The normal regression suite checks hooks, notes, waiting status, providers and roadmap behavior.

## Facts checked

- Peer mail m678ffb confirms colony and colony-codex are paired worktrees, there is no durable lead record, and no overlapping implementation is under way.
- Codex 0.160.0 exposes `thread/goal/set`, `thread/goal/get` and `thread/goal/clear`; installed feature `goals` is stable and enabled.
- An isolated native probe set and read a paused goal, resumed the same thread after a server restart, and cleared the goal. All operations succeeded with zero model requests.
- A second native probe activated a goal against the local Responses fixture. It started an automatic turn; pausing while that turn was in flight let it complete, left the goal paused and produced exactly one fixture request. No account/model calls were made. Full Colony checkpoint/handoff integration remains to be verified during implementation.
- First consultation cd37d08: Opus 5.5/max and GPT-6.1 Sol/max; total $0.96. Both recommend explicit shared identity, safe exclusive handoff, bounded goal reconciliation and approval of an integrated candidate. Opus additionally recommends consolidating routine item reviews into milestone checkpoints and isolating or serializing shared-folder edits. The person must settle those changes before the one allowed checking round and implementation.
