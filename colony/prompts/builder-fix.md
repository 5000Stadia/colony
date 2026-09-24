Wave {wave} on row {row}: specialists and checks left these signals. A signal's strength is how
many agents found it independently.

{signals}

Fix what is real. For each signal, answer it with one line:
`python3 -m colony field resolve ID --fixed --text "what you changed"` or
`python3 -m colony field resolve ID --declined --text "why it stands"`.
Declining is right when a signal is wrong or not worth what it costs; say which.

End your final message with a line `STATUS: done`.
