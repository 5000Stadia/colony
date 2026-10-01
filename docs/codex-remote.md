# Codex in the ChatGPT app

PROVIDER: this integration is specific to Codex 0.159.3 on Linux. `remote` is on
by default. An existing conversation moves when its console next closes idle and
reopens; an active writer defers the move. The console and ChatGPT then attach to
the same thread in a project-owned app server.

First open the project's console with Remote Control on. On the board, go to
**Settings → Agent programs → Pair with ChatGPT** for that project. Enter the
fresh code in ChatGPT's Remote pairing screen. The board shows the expiry and
checks whether the code has been accepted; request a new code if it expires.
Pairing uses the running project's socket without restarting or interrupting it.
The console works even before pairing. First-time setup includes this step.

In ChatGPT, open **Remote**, select the machine host, then the conversation named
after the project (first rollout: **colony-codex** on **Box**). Sign in to the same
ChatGPT account used by Codex. Codex fixes host names to the OS hostname; project
names belong to the conversations. Account/workspace access to Remote is also
required. Settings → Agent programs shows daemon counts, connection state and any
local fallback reason. A connecting host can take a moment to enroll.

Each owned home needs its own pairing. Native Codex stores an `installation_id`
UUID per home, uses it for enrollment and refresh, and binds pairing to a specific
server and environment. There is no verified multi-server contract for sharing
that identity: Colony keeps installation IDs and enrollment records separate.
Only sign-in credentials are shared. Pairing codes are returned only to the
requesting board page, never persisted in Colony state or put in URLs or logs.

Changing `remote` to off keeps the conversation in its owned home and starts the
embedded local CLI. A running app turn defers that change. Colony sends SIGHUP to
drain an idle host; Codex rejects new admissions while letting any racing admitted
turn finish. Colony never escalates to a forced shutdown and never retries input
whose completion is uncertain. There is no independent Codex daemon updater.

Each logical project has its own home under the board's `codex-remote` directory,
even when projects share a working folder. Project identity is explicit in the
daemon environment. Persistent CLI overrides keep model, effort, permissions,
hooks and immutable helper definitions above shared-folder configuration. The
returned session settings are checked before attaching the TUI. Native hook trust
is persisted using the same API as Codex's hook picker, for Colony's five hooks
only, when the board's startup-trust setting permits it.

App input runs the same delivery and Stop hooks as console input: notes/mail are
delivered, person words and agent replies reach `board.said`, and closing questions
reach the board. Pre/PostToolUse hooks also record interactive questions before
Stop and capture their answers. Helper sessions cannot replace the main thread's
conversation mapping or consume its notes.

Sign-in uses a symlink to the original `auth.json`, never a token copy. Native
refresh writes through that link. Keyring-only credentials require a supported
file sign-in; missing credentials keep a labelled local console. Credential
contents are never printed or saved to Colony's state. The native refresh check
uses synthetic credentials and a local authority, including simultaneous refresh
requests from two owned homes and the original home.

Codex cannot import an existing paginated transcript into another home. Transfer
therefore accepts only the verified native database schemas, takes Codex's own
thread-family writer locks, copies only that family's indexed state and rollouts,
and changes its absolute rollout paths. The source becomes archived before the
destination is published. Its content remains intact; no second unarchived copy
can resume accidentally. A journal and hashes recover the archive/publication
crash boundary. Unknown schemas, a live writer, missing history or forked history
retain a visible local fallback. An operator can recover the retained source
archive after ensuring the owned destination has stopped; never run both copies.

Validation: `python3 -m unittest tests.test_codex_remote` uses installed Codex with
a local mock Responses server and no real model calls. It checks two projects in
one folder, different settings/helpers, cold resume, board hooks, live questions,
history transfer with source history unavailable, active-turn deferral, remote-off
continuity and shared-auth refresh. Native tests skip on unsupported Codex versions.
The personal account's actual app visibility is a deployment check.

Protocol/lifecycle behavior was checked against the official
[Codex rust-v0.159.3 source](https://github.com/openai/codex/tree/rust-v0.159.3/codex-rs),
including app-server shutdown, hook trust, local writer locks and auth storage.
