# Auto model choices

Every launcher uses `colony.selection`: project main agents, helper tiers, consultants,
monitor and unattended runtime. Auto is an accepted, exact model and effort pair, scoped
by provider and role. Model defaults and aliases are never passed through to a provider.
Providers without effort controls have an explicit null effort.

Judgement roles use the family's smartest measured model. Step-up helpers and
consultants run at max, the monitor at xhigh, and main agents, routine helpers and
the unattended runtime at the AA fixed-task cost knee. Auto never selects Ultra
(which enables delegation); unavailable levels map to the nearest supported level,
with ties downward. Chores retain their value-based model selection. Pins remain
unchanged, including an explicitly chosen Ultra.

Routine helpers retain R61's model rule: the cheapest model reaching within ten
points of the smartest model's lowest effort within five of its best, at high or
below. Their effort then uses that chosen model's knee. Main agents and runtime
use the smartest model at its knee; routine helpers need not use the same model.

The knee is the effort immediately before the largest proportional increase in
marginal USD per additional Intelligence Index point. It uses score and task-cost
pairs from one AA source, benchmark version and date, retained even when the API
supplies the current model ranking. Non-increasing, incomplete or missing curves
fall back to the lowest effort within five points of the model's best score,
labelled **Estimated knee** on Models. No colony-task telemetry or per-token price
is substituted for AA task costs. Accepted choices retain the curve, version, date,
computed marginal costs and policy revision for inspection and rollback.

Settings has one adoption policy: switch automatically, or ask. New-model proposals
appear in the board's Needs you area and on Models/Settings. They are not project gates
or notes, so the monitor's project-question machinery does not answer them. Each proposal
groups the affected roles and project seats, showing existing pins as comparisons; an
approval never replaces a pin. Recommendation changes, rather than releases, create
proposals. Imports, explicit data refresh and daily upkeep reconcile the recommendations.

Every accepted change retains the previous pair and input/output token rates from
`bench.token_price`. Unknown rates remain unknown. These comparisons are not task-cost
estimates: effort and token usage change actual spend. Models shows recent changes and
one-click return controls. A model return excludes that model from Auto across roles.
An effort-only return excludes that provider/role/pair, keeping Auto and its model eligible.
Rejected models are remembered across subsequent refreshes and restarts.

Choices, pending proposals, rejections and history live in the machine-local
`model-selection.json`, written under a thread/process lock with atomic replacement and
a previous snapshot. Launching or rendering a pending choice does not approve it. A
changed proposal receives a new token, so a stale approval cannot accept changed pairs,
prices or affected seats. Existing console fingerprints pick up accepted changes; the
normal idle reload mechanism applies them between turns.

If an accepted model disappears, an available approved choice is used first. If none
remains, the current exact recommendation is used immediately and an emergency proposal
says why the replacement is already running. An unreadable ledger is recovered from its
previous snapshot, or rebuilt from the current evidence, with a visible recovery proposal.
Before any evidence exists, Auto freezes a catalog model and labels it unmeasured; it
never asks the provider to follow an alias. A missing catalog cannot supply a runnable
model and is reported as unavailable.

Migration runs once per settings file. A legacy main-agent value equal to its global
setting, or to the provider's own default when that global value was blank, becomes Auto.
Floating aliases become Auto in the other roles too. Distinct exact pins survive, and
later deliberate pins equal to a default survive. Each changed project's old values are
recorded in `.board/model-migrations.jsonl`. Main-agent and monitor global pins are kept
per provider when the colony default provider changes.

PROVIDER: the unattended runtime currently executes only through Claude Code. The shared
selection state and the rest of the launchers are provider-neutral.

Run `python3 -m unittest tests.test_colony tests.test_board tests.test_selection`.
