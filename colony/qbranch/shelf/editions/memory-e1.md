title: Agent memory architecture
trigger: Before you design, extend or repair how an agent keeps, updates or recovers anything beyond its current context, and above all before adding a memory service: the native default is often enough, and this guide shows how to tell.
match: agent memory, memory across sessions, long-term memory, memory service, memory layer, remember across, memory recall, compaction, vector store, embeddings, transcript search, persistent notes
status: curated
outcomes: 0
updated: 2026-11-02
basis: measured three times as the guide that helps (once with two judge families), mostly by restraint
edition: 1
measured: 1

## Where to look

From how this guide was made (links across the research behind it, counted 2026-11-02):
- GitHub, by far the most: memory systems' code, and their issues for what broke (about 14,600 links).
- arXiv, for systems and benchmarks (about 5,500), with ACL Anthology and OpenReview for benchmark papers.
- The harnesses' own docs, for what the native default already does: Claude Code, ChatGPT/Codex, Devin, Cursor, Cline.
- Reddit, for practitioners' reports (about 265 links): useful leads, weighed as claims.

## Guide

# A Baby Guidebook to Agent Memory

Memory architecture begins with a job that is failing, not a database to install. Choose the smallest change that lets the agent recover the right evidence, apply it correctly and continue the intended work.

**Purpose fit is design inference unless a study measures that particular job.** A system can combine several shapes; no shape earns its cost merely by having a name.

## 1. Start with the harness. Often, add nothing.

Your baseline is the agent harness’s existing context, resume, compaction, instruction loading and ordinary project artifacts (**M00**). Current files, tests, version history, saved conversations and task notes may already provide durable memory.

Before adding anything:

1. Identify a concrete failure: a lost exception, stale preference, repeated task, missing argument or inappropriate disclosure.
2. Check the harness’s actual version, enabled features, loading rules and recovery paths.
3. Try recovering the needed information through existing context, files, resume and search.
4. Add a component only if it addresses the remaining gap at acceptable cost.

**Add nothing** when the needed material fits the reader’s effective context window, native continuation works, or existing artifacts already preserve and expose the evidence. Fitting inside an advertised window does not guarantee effective use; check the actual task.

A memory service is also unnecessary when a small project note or instruction-file correction solves the problem. Avoid duplicate status stores that must be synchronized.

## 2. Keep the evidence labels honest

The source material uses:

- **C — code read:** establishes an inspected mechanism, not its usefulness.
- **Cl — claim:** an assertion, not an independently established result.
- **M-a — author measurement:** measured by the system’s authors, under a particular setup.
- **M-i — independent measurement:** measured by people who did not build the winning system.

Attach labels to specific claims and configurations. Missing evidence remains unknown. Do not promote a code path into an outcome claim, or a recall score into proof of coherent continuation.

The supplied field guide also reports **local trials**. Keep those identified as local trials rather than assigning an unsupported evidence label.

Its strongest recommendations—always retain everything, always add search, reflection never helps—are broader than the accompanying evidence supports. Treat them as workload-specific leads, subject to retention, privacy, reader ability and cost.

## 3. The main memory shapes

The families below group related elements. The **M-numbers** preserve links to the supplied method index.

### A. Active context and resident core

**Elements:** recent window (**M39**), summary plus recent tail (**M11**), small editable core (**M01**), pinned verbatim constraints (**M13**).

**Strengths:** Immediate access without retrieval. Good for frequently needed preferences, current task state and short rules whose wording must survive compaction.

**Weaknesses:** Every resident token consumes context repeatedly. Windows lose earlier requirements; summaries lose detail; stale core entries repeatedly influence decisions. Pinning text does not ensure compliance.

**Consider:** Size limits, update authority, conflict resolution, reload behavior and what deserves repeated exposure. Keep exact exceptions outside lossy summaries when necessary, but first check whether native instruction loading already does this.

### B. Raw episodes and transcript archives

**Elements:** ordered journal (**M02**), transcript recall (**M03**), source-linked breadcrumbs (**M12**), context offloading (**M48**).

**Strengths:** Preserves wording, attribution, sequence and qualifiers that extraction can erase. A short orientation note can point into a much larger recoverable record.

**Weaknesses:** Storage is insufficient without usable retrieval. Index gaps, clipped passages, missing surrounding exchanges and unavailable backing records can defeat recall.

**Consider:** Capture coverage, stable addresses, timestamps, speakers, retention and deletion, passage expansion and search access. An offloaded preview is not preservation of its full source.

Retain raw evidence where the job justifies it; “store everything forever” is not a universal requirement.

### C. Search and retrieval

**Elements:** lexical (**M14**), dense (**M15**), hybrid fusion (**M16**), agentic search (**M17**), reranking (**M69**), retrieval triggering (**M47**), compiled evidence-access programs (**M63**).

**Strengths:** Loads relevant evidence without carrying the whole archive. Lexical search suits identifiers and exact phrases; dense retrieval bridges paraphrases; hybrid search combines channels. A capable agent can investigate through successive queries.

**Weaknesses:** Similarity does not establish truth, validity or scope. Reranking cannot recover omitted candidates. Agentic search adds calls and latency and depends on reader ability. Compiled access plans need coverage and invalidation.

**Consider:** Compare against ordinary project search first. Inspect candidate coverage, query formulation, surrounding context, ranking, abstention and total lookup cost. Retrieval triggering and retrieval quality are separate decisions.

### D. Reconciled facts and temporal state

**Elements:** current fact store (**M18**), temporal graph (**M19**), stable entity identity (**M64**), accumulated entity state (**M34**).

**Strengths:** Compact access to recurring attributes, relationships and changing values. Explicit time and supersession can distinguish current truth from historical truth.

**Weaknesses:** Extraction can erase qualifications, confuse people or overwrite history. A stable ID does not resolve ambiguous references correctly. Graph construction and reconciliation add maintenance.

**Consider:** Preserve source links, versions, validity intervals and uncertainty. Define add, update, delete, supersede, alias and merge behavior. Separate “was true” from “is true.”

In the inspected babyseat update path, surprise residuals are diagnostic; they do not gate writes. Do not infer an admission policy from a state representation.

### E. Linked and hierarchical knowledge

**Elements:** associative notes (**M20**), relational graphs (**M70**), hierarchical summaries (**M21**), progressively disclosed topic files (**M22**), spreading activation (**M35**), vector-symbolic representations (**M32**).

**Strengths:** Supports navigation, relationships and broader orientation across a large collection. Selective loading can reduce irrelevant context.

**Weaknesses:** Links drift; upper-level summaries can hide relevant evidence; activation can amplify irrelevant associations. Structured representations may discard the argument behind a conclusion.

**Consider:** Backlinks to evidence, editable organization, navigation cost and maintenance. Distinguish persistent activation, query-time diffusion and decay. A graph or vector encoding does not supply an answer-admission policy.

These shapes plausibly support conceptual depth, but recall-QA results do not establish that an agent can resume and extend a difficult argument.

### F. Scoped instructions, maps and procedures

**Elements:** hierarchical instructions (**M09**), rule routing (**M10**), repository maps (**M23**), skill libraries (**M06**).

**Strengths:** Brings relevant conventions, code structure or reusable methods into context selectively. Often fits naturally into existing harness mechanisms.

**Weaknesses:** Incorrect triggers hide important rules. Maps become stale. Procedures can be obsolete or harmful. Code navigation does not preserve the user’s objective.

**Consider:** Actual precedence and loading rules, activation conditions, source versions, ownership and progressive disclosure. Distinguish instructions, navigation aids and executable procedures.

An available skill is not evidence that following it improves outcomes.

### G. Learned lessons and experience reuse

**Elements:** case retrieval (**M24**), reflective playbooks (**M25**), consolidation (**M07**), provenance-gated promotion (**M04**), review inbox (**M08**), learned memory manager (**M51**).

**Strengths:** Can reuse successful methods, reduce repeated derivation and keep tentative observations from immediately becoming permanent guidance.

**Weaknesses:** Similar cases can suggest the wrong solution. Reflection can turn mistaken interpretations into durable rules; task-specific regressions are documented. Review queues create interruptions and backlogs. Learned controllers add training and transfer risk.

**Consider:** Evidence for each lesson, admission criteria, evaluation on later tasks, rollback and retirement. Background work still costs calls.

The supplied material does not justify a blanket claim that reflection never helps. Benefits and harms depend on the method, task and measured setup.

### H. Continuation and prospective memory

**Elements:** scratchpads and plans (**M49**), breadcrumbs (**M12**), pre-compaction flush (**M05**), prospective cues (**M26**), versioned workspaces (**M30**).

**Strengths:** Preserves objectives, completed work, dependencies, open questions and intentions that should activate later. Checkpoints support interrupted or branching work.

**Weaknesses:** Plans become stale; duplicated status conflicts; saving before compaction preserves only what gets selected. A remembered intention may no longer be authorized.

**Consider:** Record the current objective, evidence of completion, next authorized step, trigger and pending/satisfied state. Reconcile checkpoints with current files and current user intent before resuming.

A task list, explanatory narrative and procedure serve different needs.

### I. World state and situated viewpoints

**Elements:** event history and projections (**M28**), typed ledger (**M38**), temporal state (**M19**), stable identities (**M64**), character viewpoints (**M36**), rumor propagation (**M37**).

**Strengths:** Separates established state from generated prose, preserves consequences and supports characters with different knowledge or mistaken beliefs.

**Weaknesses:** Invalid writes corrupt authoritative state. Query-time interpretation of events does not guarantee deterministic reconstruction. Repeated reflection can drift without new evidence.

**Consider:** Write authority, validation, projection rules, unknown versus invented versus established detail, witness and hearsay provenance, belief revision and bounded updates.

The inspected RoleplayEngine includes witness/rumor provenance, belief chains and new-evidence admission. Its GM packet exposes several viewpoints together; this establishes neither isolated per-character execution nor a security boundary. Character coherence and immersion were not measured in that audit.

### J. Modality-preserving and spatial memory

**Elements:** image/audio/video archives (**M65**), object-centric scene graphs (**M66**), dense spatial feature maps (**M67**).

**Strengths:** Retains sensory details that text extraction can omit and supports object-level or spatial queries.

**Weaknesses:** Large evidence payloads, perception errors, identity mismatches, missing geometry and modality-access requirements.

**Consider:** Preserve original evidence where needed, with time, viewpoint, object identity and addressable retrieval. Text describing an image is a different representation from its pixels. Choose graphs for explicit objects and relations; choose spatial feature maps when position-linked features matter.

### K. Model-internal memory

**Elements:** recurrent state (**M52**), retrievable attention (**M53**), sparse KV retention (**M54**), KV offloading/quantization (**M55–M56**), soft compression (**M57**), latent slots (**M58**), fast weights (**M59**), parameter editing/fine-tuning (**M60–M61**), learned lookup (**M62**).

**Strengths:** Can address sequence length, model capacity or hardware residency within model computation.

**Weaknesses:** Often requires training or model/server control unavailable to ordinary CLI agents. Compression may lose detail; parameter changes raise interference, revision and traceability problems.

**Consider:** First establish access to the required implementation layer. Hardware-memory savings are a different objective from editable, sourced project memory.

## 4. Controls that compose with any shape

These are architectural obligations or policies, not substitutes for a representation.

| Control | Elements | Decision to make |
|---|---|---|
| Audience boundaries | **M27** | Enforce person, project and purpose scope during derivation, storage and retrieval—not merely through a scope field. |
| Grounding | Evidence gates **M29**, audits **M33** | Decide what evidence permits admission, what triggers uncertainty, and whether auditing is retrospective or repairs material before use. Neither guarantees grounded final answers. |
| Freshness | Dependency invalidation **M31**, expiry **M40** | Invalidate derived assertions when their sources change. Age alone does not prove obsolescence. |
| Capacity | Recency/frequency **M41**, utility retention **M42**, extractive compression **M68** | Protect rare requirements from eviction. Retained wording remains exact, but selection may discard a crucial qualifier. |
| Coordination | Shared blackboard **M50** | Specify ownership, visibility, write ordering and conflict handling. |
| Reuse | Exact memoization **M45**, semantic cache **M46** | Include changing dependencies in keys; inspect stale results, false similarity matches and side effects. |

Reservoir and prioritized replay (**M43–M44**) manage training experience. Their value for prompt-time conversation memory requires separate justification.

## 5. What each memory job needs

All candidate choices below are **design inference**, unless evidence tests the stated outcome.

| Job | Required result | Candidate elements and critical check |
|---|---|---|
| **Personal assistance** | Current preferences, relationships and commitments surface for the right person at the right moment. | Small core, reconciled/temporal facts, reviewed promotion, transcript evidence, prospective cues and audience controls. Separate recurring preferences from one-time remarks. |
| **Coding** | Recover current code, applicable rules, change rationale and intended next work. | Native files/history, scoped rules, maps, skills, search and invalidation. Separate navigation, task intent and procedural learning. |
| **Literal fidelity** | Recover and apply exact wording or values with source, scope, time and uncertainty. | Verbatim rails, original episodes, lexical recall and evidence gates. Test capture, retrieval and faithful use separately. |
| **Conceptual depth** | Reconstruct alternatives, assumptions, tensions, reasons and open questions well enough to extend them. | Source-linked orientation, raw evidence, linked notes and versioned explorations. Recall scores leave this outcome unproven. |
| **Project sequencing** | Resume the correct objective without repeating completed work or pursuing stale plans. | Native resume first; then checkpoints, concise state, exact constraints and prospective cues. Recheck authorization. |
| **Operational/domain knowledge** | Reuse valid corrections, exceptions and methods across tasks. | Skills, cases, playbooks, sourced facts and invalidation. Evaluate whether distilled lessons improve later work. |
| **World/object continuity** | Preserve identities, attributes and consequences while representing changes explicitly. | Stable identities, events, projections and validated state ledger. Separate established, unknown and invented detail. |
| **NPC viewpoints** | Preserve each character’s experience, hearsay and intelligible belief changes. | Situated viewpoints and rumor provenance. Keep belief separate from canon; repeated reflection is not new evidence. |
| **Conversation evidence** | Refind what was said, by whom, with enough context to interpret it. | Ordered episodes, transcript recall and breadcrumbs. Extracted facts must not silently replace qualified words. |
| **Identity/audience separation** | Admit only information appropriate to the current people and purpose. | Enforced boundaries throughout every path, including derived summaries and shared stores. Recall quality is insufficient. |
| **Sensory/spatial continuity** | Recover the needed sensory or embodied evidence with identity, time and viewpoint. | Original modality archives, scene graphs or spatial maps. Check what each transformation loses. |

## 6. Use the reported results within their limits

The supplied draft reports useful leads, not universal guarantees:

- **Local test, one project's records, 62 questions:** records alone scored 60%; adding transcript search reached 67%, with cheaper and faster lookups.
- **Local test, four projects, 56 questions:** 59% became 64%, at 24% lower cost. The longest history gained nothing; finding information absent from existing records remained weak.
- **Local test, 45 questions:** a BM25 agent over raw logs scored 88% using 40% of a structured memory system's tokens. This tested detail access, **not conceptual cohesion**.
- **Local test:** durable items in successive summaries fell to 25% fully retained after one compaction and 0% after eight. This supports preserving recoverable evidence beyond summaries.
- **Reported Hermes comparison:** breadcrumb-style recall reached 68% versus 46% for a summary three times longer. Keep the result tied to that test; the supplied passage does not assign an evidence label.

Measure the cost that matters in your deployment: repeated context, write-time extraction, searches, reflection, latency, storage and maintenance. Cached input still occupies context and may carry cost.

## 7. Your minimum design record

Before adding a component, write down:

1. **Failure and success criterion:** what must improve?
2. **Writer and trigger:** who may save or change memory, and when?
3. **Representation and provenance:** what survives, with which sources and scope?
4. **Read path:** how is it found, expanded and admitted into context?
5. **Lifecycle:** how are changes, conflicts, invalidation and deletion handled?
6. **Evidence and cost:** what is inspected, claimed, measured or still unknown?

Prefer one justified addition at a time. Keep it only if it improves the actual memory job beyond the native baseline.

## Lessons

## Sources

