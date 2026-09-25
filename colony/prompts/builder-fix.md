Wave {wave} on row {row}: specialists and checks left these signals. A signal's strength is how
many agents found it independently.

{signals}

Fix what is real. For each signal, answer it with one line:
`python3 -m colony field resolve ID --fixed --text "what you changed"` or
`python3 -m colony field resolve ID --declined --text "why it stands"`.
Declining is right when a signal is wrong, or when the fix would make the work worse; say which.
When what you fix is a mistake later work could make again, leave what keeps it from coming back where
the project already looks: a test that fails if it returns, and a line in the project's own
conventions (its README, CLAUDE.md, or the equivalent for this kind of work).

End your final message with a line `STATUS: done`.
