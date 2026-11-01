# Standing orders and wakes

A standing order from the person (from now on, always, never) is kept by colony, not your memory: `colony rule "the
rule" --words "their words"`. It comes back at every session start, after a compaction too; when they lift one,
`colony rule --retire ID "why"`.

To check back on something later, `colony wake --in 2h "what to check"` (or `--at HH:MM`, `--every 1d`): it comes as a note when you're idle in the person's hours (`--list`, `--cancel ID`).
