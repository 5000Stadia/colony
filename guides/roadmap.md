# The roadmap, and starting or finishing an item

The roadmap is your plan (the file your instructions name).

## Shaping the path

Reordering is yours; dropping a milestone is the person's call, and Later waits for them. The
person is the compass: they shape the vision and the path, and can say where quality or direction seems off and
steer it. Foresee the decisions an item will need and put them to the person together as gates (`asking.md`), each
with a default where the choice is reversible, and none where skipping would publish, send or spend. Each item's check and the roadmap as a whole trace to the acceptance criteria, and a criterion no item
serves is a gap in the path.

## Its format

Milestones `## M1 — name`, items `- [ ] R1 text`; `[~]` in progress, `[x]` done, `[?]` only for what the person asked
to sign off, then `colony ready R4 "what's ready" --check "how to see it"`. Work outside the milestone's purpose goes
under `## Later`.

The roadmap stays small: a finished item is its terse title; an open one is its title, one line of why, and
`(related: R5, R9)` for the 0-3 items that bear on it. Everything else about an item (the person's words, readings,
checks, findings) goes in `roadmap/R#.md`, written when the item is laid out and kept as it changes.

A milestone may name its kind after its title (`## M4 — Concepts · menu`): `menu` (options in any order), `lanes`
(roads side by side; each item says `(lane: name)`), `findings` (questions answered by experiment), `sections` (the
pieces of one whole); unnamed, it is a sequence, done in order.

A number that shows the vision coming true (for a marathon, the longest run; not activity, such as tests run) goes on
its own line in the part (or under `## Measures`, for the whole), with how colony takes it: `Measure: words / 80000 ·
count: wc -w chapters/*.md` (a command; the first number on its last line) or `· judge: what to read` (a small
separate model reads it); colony takes it at each push and finished milestone and shows it, so it is never your own
report.

## Starting an item

First decide the item's brief: its details file and its related items' — read those, nothing more.

Where an item first sets a look, sound or voice (shaders and textures, music, a fiction or marketing voice) whose
style the person hasn't yet described or shown, good can't be reached until you align on it, since taste is hard to
put in words: ask them for at least one real example of the quality and taste they want, kept as a link that sets
the bar for what they name in it, not a model to copy. Only the look waits: build what doesn't depend on it, and an agreed style carries to later
ones.

## Finishing an item

Before calling it done, see whether finishing it shows something the vision needs that has no place on the roadmap, and give it one.

Where only the person can judge, ask for their verdict in a form later work can be checked against (an approved
image, a graded set).

When a deliverable reaches a stage where it should be good, show it in the showcase: what it is and how to see or
try it, only when seeing it would tell them something about quality or direction, never for each small piece of a
spec. A deliverable worth their seeing: `colony show "title" --what "what it is" --try "how to see it" --link
URL-or-file --item R4`; their comment arrives as a note.
