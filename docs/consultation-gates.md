# Consultant points on decision gates

A gate can carry the few consultant recommendations that would change the approach.
Each point shows who recommended it, with the complete answers available underneath.
The person chooses **Accept** or **Reject** for each point and can add a comment.
The same controls appear in Needs you, the project's Waiting on you, and its item page.

This reuses answers already produced. Displaying recommendations and recording choices
make no model calls. The project's agent curates the relevant points; there is no extra
extraction call and no automatic interpretation of the person's reply.

## Creating a gate

After a first-round consultation, write a JSON list of meaningful recommendations:

```json
[
  {"text": "Retain the existing export format.", "consultants": [1, 2]},
  {"text": "Provide a migration before changing the stored format.", "consultants": [2]}
]
```

Source numbers are the consultant numbers printed by `colony consult`. A shared
recommendation can cite both. The gate snapshots their provider, model and effort.
Failed or empty answers cannot be cited. A single available family works the same way.

```sh
colony gate "Which changes should we take?" --item R4 --why "The export depends on these choices" --consult c123456 --points points.json
```

Link a first-round consultation belonging to this project and this decision. Each
consultation gets one gate; each point receives a stable name, P1, P2, and so on.
Pass `--points -` to read the JSON from stdin. Bring only substantial changes, not
wording or all the material from both answers.

## Decisions and checking

Saving requires a choice for every point; blanks, duplicate choices and unknown
points cannot grant approval. The comment is optional. Accepted points automatically
become the consultation's adoption record. Rejecting every point closes the gate
without authorising a checking round.

If the person settles it in conversation, record the actual choices without sending
a receipt back to yourself:

```sh
colony gate "Keep the format; no migration yet" --answered g123456 --accept P1 --reject P2
```

For an accepted change, the existing `colony consult R4 "Check the revised approach"
--digest facts.txt --plan revised.txt` runs the one permitted checking round. Its
brief includes the person's choices, and its record preserves which accepted points
were checked. There is no third round.

The person can revise choices from the answered gate or in conversation. Revisions
append history and update adoption; they never rewrite a checking round already
performed. Exact retries create no duplicate decision or note and can repair a
notification whose first write failed.

Ordinary gates still accept a plain answer. Past consultations and their recorded
`--adopt` decisions remain readable and usable; they require no migration. For a linked
gate, manual adoption cannot override unanswered or rejected points.
