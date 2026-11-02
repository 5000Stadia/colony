# Outside services

Connectors (the person's Gmail, Drive, Chrome, plugins' servers) are off here until switched on: `colony connect --list`
shows what exists; `colony connect --on NAME` switches one on.

Outside services (an API, a mailbox) reach you as connections, set by the person per project. Whatever needs an
account or a key, or sends, posts or spends, goes through one, even for this project alone (it can have its own
account there), never wired into the project with its keys in a file; public data needing neither, your own code may
fetch. One the work needs and you lack: `colony connect --add NAME --command "…" --key KEY`, an existing MCP server
with its version pinned (no `--command` for keys your own script uses); they enter its keys on the board, never in a
chat.
