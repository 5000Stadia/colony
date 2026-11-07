# Q Branch

Before an agent designs a roadmap item that needs research, Q Branch hands it one short, restrained guide for the
item's domain: the library's curated entry where one fits, otherwise one research pass written in the same form.
After the item is built, what the agent found that the guide lacked can be woven back into a curated guide, so guides
are kept by what happened when they were used. Q Branch recommends nothing: the agent doing the work makes the call.

This is Q Branch's core, copied into colony from Memory Research's `qbranch/` at commit 40cf24b, with its later name-matching and hand-over changes through 8495595. That project owns the
method: the prompts, the curation reviews and the studies behind them; changes to it come to colony from there.
Colony's seam (`colony/qdept.py`) decides when it runs, delivers what it makes and keeps its settings; its runners
(`colony/qroom.py`) send every model call through colony's research room. The core itself knows nothing of colony's
projects: it is handed generic text and returns generic text.

```
python3 -I qbranch.py brief < input.json    # a curated entry's hand-over, or one research pass: OUT/brief.md, note.md, log.json
python3 -I qbranch.py decision < input.json # one added decision: OUT/addendum.md and OUT/log.json
python3 -I qbranch.py weave < input.json    # rewrites one curated guide section; makes a new edition
python3 -I qbranch.py learn < input.json    # an item's outcome and candidates into the domain's library entry
```

Run it with `python3 -I`: it reads text that came from web pages. Its tests are colony's
`tests/test_qbranch_decision.py` and `tests/test_qbranch_weave.py`, with fake runners.

## Input

One JSON object on stdin.

| Key | Used by | What it is |
|---|---|---|
| `subject` | brief, decision | the research question in generic words, with no project details |
| `requirements` | brief, decision | list of strings: the work's constraints in generic words; may be empty |
| `entry` | brief | a curated shelf entry's id: hand it over instead of running a pass |
| `shelf` | brief | library entries, as `[{"id", "path"}]` |
| `sourced` | brief | true: the full brief with its source plan; false (default): the plain pass |
| `read` | brief | false drops the plain pass's search-and-read paragraph (default true; Claude needs it) |
| `decision`, `default` | decision | the one decision to research and the agent's default, each a single line |
| `size` | decision | `small` (default), `standard`, or `deep`: caps of 8, 16 or 32 tool calls |
| `guide` | decision | readable path to the existing guide, passed as untrusted data |
| `out` | brief, decision, learn | output folder; `pages/` in it holds what the pass read |
| `entry`, `candidate`, `private`, `extra` | weave | see Guide upkeep |
| `shelf_dir`, `outcome`, `label`, `candidates`, `private` | learn | see Guide upkeep |
| `runner` | all | the command for each step: `research`, `check` (the screen's model), `weave`, `learn` |
| `source_index`, `source_ledger` | brief, decision, learn | the source index and its verdict ledger (none by default) |
| `screen` | brief, decision, weave | `true` also uses `runner.check` for the model screen; the deterministic screen always applies |
| `cwd` | all | the working folder for every pass; use an empty one |

## What it does

- **brief:** with `entry`, the curated guide with its hand-over, screened. Otherwise one research pass gets the subject
  and requirements alone. The plain pass (the default) is the brief without its source plan and reading sections, plus
  one generic paragraph telling it to search and read before it writes: without it, a Claude pass wrote from memory
  with citations it never opened. It writes a 1,500 to 2,500-word guide in the restrained form; the reply is parsed
  into the guide, the sources that fed it and its source verdicts, and the guide is screened once.
- **decision:** one research pass investigates only a decision the existing guide does not cover. It stops at evidence
  for a better option, two independent good sources confirming the default, two searches adding nothing, or the size
  cap. A better option yields a screened `### decision` addendum; either default outcome yields exactly
  `- decision: the default holds (default).`, made by code. The guide itself is not changed: the caller appends.
- **learn:** one cheap call compares the guide with what was built and adds dated lessons to the domain's entry; with
  candidates on a curated entry, it keeps those that would change what the next agent decides after reading the
  section they belong in, and weaves each kept one in.

## The library

One Markdown file per domain, in `shelf/`: header lines (`title`, `trigger`, `match`, `status: draft|curated`,
`outcomes`, `updated`, `basis`, optional `edition` and `measured`), then the sections `Where to look`, `Guide`,
`Lessons` and `Sources`. `match` (curated entries) is a comma-separated list of domain words and phrases: colony
matches them by code, case-insensitive, whole words or phrases, against each item's line and details, and hands a
match over at the item's start whether or not the item is flagged. `shelf/memory.md` is the first curated entry, the
memory guide as measured, with project labels taken out; `shelf/editions/memory-e1.md` is that measured edition,
pinned. Colony seeds its living shelf (in the board's home) from here.

## Guide upkeep

`weave` takes `{"entry": "shelf/memory.md", "candidate": {"text", "url"}, "runner": {"weave", "check"}, "screen": true,
"cwd", "private": ["names"], "extra": 80}`. The light model first sees only the numbered guide headings and the
candidate and picks one (or proposes a new one), then only that section, which it rewrites whole within its old word
count plus `extra`, keeping its other points and citing the URL. Code checks the cap, the URL, no headings, at least
70% of the words kept and no private string; the screen must leave it unchanged. Each success is a new edition: the
old bytes are kept in `editions/{entry}-e{n}.md`, `editions/{entry}.jsonl` records the change and its tokens, and
`measured` stays pinned to the edition actually measured. Weaves of one guide take its `editions/{entry}.lock`.

`learn` takes `candidates: [{"text", "url"}]` (at most three, each up to 80 words with a source URL) and `private`
(defaults to `[label]`); it weaves kept candidates in order with the same runners, and appends every verdict on a
curated guide's candidates to `editions/{entry}.candidates.jsonl`, with the text only when it passed the privacy check.

## Runners

A runner is a command that takes the prompt on stdin and prints only the final answer on stdout. `{model}` and
`{effort}` in the command are filled in from the runner's entry, and `timeout` (seconds) is optional. A runner may
append JSON lines to `$QBRANCH_RUNLOG` (`{"tool", "input"}` per call, `{"usage"}` at the end), and keep the pages it
opens in `$QBRANCH_WORKSPACE`. The core calls no model API and installs nothing.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | done |
| 2 | bad input |
| 3 | a runner or the screen failed (only `log.json` reaches OUT) |

On SIGTERM it stops its runners and writes nothing.
