# Codex hook discovery in worktrees

Verified on 2026-09-28 with Codex CLI 0.154.0. This describes observed behavior in that release, not a promise about later versions.

The CLI's `config/read` reports a linked worktree's `.codex` layer, but `hooks/list` discovers project `hooks.json` in the main checkout. A temporary repository and linked worktree reproduced this without resuming a conversation. Explicitly trusting the worktree, creating a project `config.toml`, adding inline project hooks, and overriding `project_root_markers` did not restore worktree hook discovery. The main-repository trust entry was therefore not the whole problem.

Colony supplies its three guarded lifecycle hooks through `-c hooks.EVENT=...` in the console launch command, for both fresh and resumed sessions. These appear as `sessionFlags` in Codex's hook browser. They still require normal `/hooks` trust review; colony does not use the hook-trust bypass flag. This avoids edits to another checkout or the person's user-level configuration.

`wire()` installs AGENTS.md and removes only colony's recognized old hook commands from the project's `.codex/hooks.json`. Personal commands, groups containing personal commands, metadata and `config.toml` are retained. Re-track previously wired projects before launching with the new command to avoid duplicate file and session registrations. If a worktree inherits old colony file hooks from a separately tracked main checkout, that main project's owner must re-track it too; wiring a worktree never edits its main checkout.

Verification used the real CLI's stdio app-server with only `initialize`, `config/read` and `hooks/list`; it started no agent turn or inference request. A normal checkout and linked worktree each exposed exactly three colony session hooks, all awaiting trust, while retaining a personal project hook. The ordinary test suite also checks TOML launch arguments, fresh/resumed commands, migration, and notes/mail/question isolation with a stand-in.

Live turn delivery and Stop still need to be verified in the board console after it restarts with the new command and its hooks are trusted.

[Official hook configuration and trust documentation](https://developers.openai.com/codex/hooks)
