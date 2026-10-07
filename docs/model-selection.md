# Auto model choices

Every launcher uses `colony.selection` and an accepted exact model/effort pair:
main agents, helpers, consultants and monitor. Pins take
priority, including an explicit Ultra pin. Auto excludes Ultra and unsupported
model/effort pairs. Providers without effort controls use explicit null effort.

## Intelligence first

Artificial Analysis Intelligence Index is the only selection score. The ceiling
is the highest comparable score across runnable pairs from all enabled, installed
providers, including qualifying estimates. Quota usage never changes eligibility,
the ceiling, the slider or a pick. Safe pause handles exhausted usage separately.

At Balanced, role goals are these distances below the shared ceiling:

| Role | Distance |
| --- | ---: |
| Consultant, step-up | 0 |
| Monitor | 2 |
| Main agent | 4 |
| Routine helper | 10 |

For each provider, use the cheaper pair meeting the goal. Scores within **one
point** count as equal, including when the provider's best cannot reach the goal.
Concretely, eligible scores must be at least `min(goal, eligible family best) - 1`.
The tolerance is applied once, not chained between neighbouring pairs. Minimum
benchmark task cost wins; equal costs choose higher score, then stable model/effort
order. Family best is calculated after rejection and role-specific rollback;
these exclusions do not lower the shared capability ceiling. Shortfalls are shown
against the original goal, including a tolerated shortfall.

The seven-position Auto slider is available in colony Settings and each project's
settings. Position 0 is Intelligence, 3 Balanced, 6 Economy, with two intermediate
positions on either side. Each step changes every role's distance by two points;
distances never go below zero. Chores retain the explicitly requested value pick:
most Index points per benchmark-task dollar. The UI labels that exception.

A project inherits the colony slider unless it selects an override. Its main
and helpers then have their own accepted Auto seats. Monitor and
consultants use the colony slider. Previews show all positions using the same
selector, including current accepted choices, estimates, shortfalls and pending
new-model approval. Saving a slider does not remove explicit model pins.

With the 2026-10-01 measured snapshot, Balanced main chooses Opus high for Claude
and Sol 6.1 max for Codex. Sol's 51.83 is within one point of Astra max's 52.67,
at $0.724 versus $3.258 per benchmark task. The shared ceiling is Opus max's 57.62;
the Codex shortfall from the 53.62 main goal remains visible.

## Evidence and estimates

Cost per task is token volume per task × price, so that it never depends on a
page read succeeding. The free Artificial Analysis API gives each pair's
Intelligence Index and blended 3:1 token price, current every day. AA's public
model pages give the token volume: a page's cost per task over the price it states,
both kept with every reading. Each pair's cost says which kind it is:

- Measured: the page's own cost per task, read no earlier than today's API price.
- Carried: the page has not read since; its last measured volume at today's API
  price, dated, with its index from the API, which stays current. A page break
  therefore freezes only volumes, which change rarely, and accuracy degrades slowly.
- Estimated: no page has measured the pair. With at least two measured efforts,
  use the model's own average adjacent per-step gap, anchored at its nearest
  measured effort (ties to the lower effort); score steps are additive, volume
  steps positive ratios, divided over omitted levels. With exactly one, a
  same-provider donor whose measured score at that anchor is closest lends its
  percentage spread. A model no page has measured takes the API's index and the
  volume of the nearest measured pair of the same provider (the same effort first,
  then the nearest; among those, the closest index) at its own price today. Never
  another provider's volume, never a recursive estimate.

Measured evidence always replaces carried and estimated values once a page reads.
Volumes come only from page measurements of one benchmark version, so versions are
never spliced; the API's index (a tenth's precision, no version stated) is checked
against the pages that were read and flagged where it differs. Unknown costs cannot
win a cheapest or value comparison. If the qualifying pairs lack cost evidence,
retain the accepted choice and show the gap. The shipped snapshot is in
`colony/data/aa-pairs.json`; later local measurements supersede it.

Estimates retain their anchor, donor, steps and source measurements. Values outside
score bounds or nonpositive/nonfinite costs are invalid. An interior estimate
outside its measured neighbours is flagged, not capped: it still follows the
person's estimator. The Models page shows these provenance details and uses
Index/task cost rather than domain categories.

Daily upkeep reads the public AA pages right after the API fetch, without model
calls: every lineup pair the API lists, at the page its own slug names, so a new
model is read the day it appears; then pages known from earlier records. A page is
read by content: the object whose slug is the page's and whose effort is the one
asked for, wherever it sits in the payload, giving the index, its measured flag,
the cost per task and the price; never a comparison model. A page carrying nothing
usable is a failure with its reason, never a silent gap; a pair AA lists without a
cost yet is not a failure. Failed pages preserve prior measurements. When more than
half of a day's reads fail, or a lineup model has no cost at all, the Models page
and `colony doctor` say so, with how old the oldest carried volume is.
Reconciliation then follows the existing adoption policy. Dates are retrieval dates.

## Adoption and return

One colony-wide policy controls new models: switch automatically, or ask once per
model with affected roles grouped together. Already-approved models may change
effort through Auto. Qualifying estimates use that same policy, without an extra
approval step. Proposals appear in Needs you and Models/Settings, not project gates.
Approvals never overwrite pins.

The accepted ledger (`model-selection.json`) uses provider/role keys plus stable
project scope for overrides. New scoped seats inherit the accepted global pair
before any required new-model approval. Returning to inheritance resolves the
accepted global pair. A model return excludes it across Auto seats; an effort-only
return excludes that pair only from its specific global or project role.

Each transition retains evidence, slider position, goal, ceiling, task costs and
input/output token rates. Task costs and token rates are labelled separately;
actual project spend varies. Proposal tokens change when evidence, prices or
seats change, so stale approval cannot accept a different proposal. Rendering a
preview does not approve or launch it. Normal idle reload applies accepted changes.

The ledger is serialized with process/thread locks, atomic replacement and a
backup. Unavailable accepted models use approved replacements first, otherwise a
visible emergency proposal. With no evidence on first setup, freeze an exact
catalog choice labelled unmeasured. Existing pin migration remains unchanged.

R69 consultation: c8aafb9, then cccc136. Approved directions came through
m6ac2f1, md3ecb7 and m1ec0d2; gate g835a7b records the tolerance decision.
Run `python -m unittest discover -s tests` for the regression suite.
