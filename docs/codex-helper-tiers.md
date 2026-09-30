# Exact helper tiers in installed Codex 0.154.0

Verified locally on 2026-09-30 with `codex-cli 0.154.0`. Colony owns tier selection and integration; this document covers the Codex configuration and audit interface.

## Project definition

Register the role in the trusted project's `.codex/config.toml`:

```toml
[agents.colony-chores]
description = "Use for the colony chores tier."
config_file = "agents/colony-chores.toml"
```

Then `.codex/agents/colony-chores.toml` contains a configuration overlay:

```toml
model = "gpt-5.6-luna"
model_reasoning_effort = "low"
developer_instructions = "Use the chores tier for the bounded task assigned by the parent."
```

Register `colony-routine` and `colony-stepup` the same way, each referencing its own overlay and setting both fields. These are TOML files, not Markdown frontmatter. The registration key is the name used to invoke the role. Paths above are relative to `.codex/config.toml`.

For this version, do not rely on directory autodiscovery alone. A file with `name` and `description` in `.codex/agents` was not exposed as a named role in the initial probe. Explicit `[agents.NAME]` registration worked. The [current subagent documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents) describes newer standalone named files, so the installed-version test takes precedence for this machine.

Project configuration must be trusted. The successful probe used a temporary `CODEX_HOME` with this persisted user configuration:

```toml
[projects."/tmp/colony-tier-probe"]
trust_level = "trusted"

[features]
hooks = false # probe isolation only; do not disable colony's production hooks
multi_agent = true
```

Another successful probe supplied registration through session `-c agents.colony-chores.description=...` and `-c agents.colony-chores.config_file="/absolute/path/to/overlay.toml"` overrides. This offers an alternative when colony already resolves defaults and project overrides at launch. Do not replace the person's whole config to add these entries.

## Invocation

The agent chooses the role in its native spawn call, rather than merely putting the tier name into the task text:

```json
{
  "task_name": "tier_report",
  "agent_type": "colony-chores",
  "fork_turns": "none",
  "message": "Own only the requested identity report and end there. The parent verifies evidence; colony implements the tier system separately."
}
```

This exact shape was observed in `collaboration.spawn_agent` in a successful local run. Another local run exposed the older `multi_agent_v1__spawn_agent` tool with `agent_type`, `fork_context: false`, and `message`. Tool naming depends on the runtime; the important selector is `agent_type`.

Codex also supports explicit per-call `model` and `reasoning_effort`. For colony's named tiers, set both in the role overlay and omit both from calls. Use a fresh or partial fork when supplying per-call overrides: the full-history fork interface does not accept them. A session that was launched without registered roles may not expose `agent_type`; start a newly configured session and inspect its available spawn tool. A task name is not a role selection.

## Audit evidence

Use `CODEX_HOME`, falling back to `~/.codex`. With persisted sessions, the observed storage is:

```text
sessions/YYYY/MM/DD/rollout-<timestamp>-<thread-id>.jsonl
```

The child `session_meta.payload` identifies `id`, `agent_role`, and `source.subagent.thread_spawn.parent_thread_id` (also `depth` and sometimes `agent_path`). Read each child's **per-turn** `turn_context.payload.model` and `.effort`; these are the effective client execution settings for that turn. They are stronger evidence than requested spawn fields or a helper's self-description. They do not attest the remote server's internal model implementation.

The generated local app-server schema also exposes `thread/read` model and `reasoningEffort`, but explicitly describes these as current configuration or latest persisted values, not per-turn execution telemetry. Spawn events' model/effort fields describe requests, not the resolved role overlay. Do not rely on either alone for historical audits. `--ephemeral` does not persist the session evidence.

## Successful project-level probe

The parent ran at `gpt-5.6-terra`, `medium`, with no per-call child model/effort overrides. Its registered `colony-chores` child ran at `gpt-5.6-luna`, `low`:

- Parent: `01a0f1bc-cd76-71a3-81d7-a6eb6f32d485`.
- Child: `01a0f1bc-e74d-79c3-99e8-dcc8356c756f`, role `colony-chores`, path `/root/tier_report`.
- Child evidence: `/tmp/colony-tier-home/sessions/2026/09/30/rollout-2026-09-30T02-54-44-01a0f1bc-e74d-79c3-99e8-dcc8356c756f.jsonl`.

The child's initial unaided response was “Runtime model: GPT-5. Reasoning effort: unknown/not exposed.” Its session record contained the exact selected Luna/low settings. Therefore, a useful identity-test helper reads its own saved `turn_context` using `CODEX_THREAD_ID` and `CODEX_HOME`, rather than guessing from its instructions. No production project settings or user configuration were changed by these probes.

That read-only identity test also passed. Parent `01a0f1bd-796d-70d3-be78-4770e691473c` invoked child `01a0f1bd-8d95-7382-901f-61a8e3921074`, which returned:

```text
thread_id: 01a0f1bd-8d95-7382-901f-61a8e3921074
model: gpt-5.6-luna
effort: low
```

Independently checked against `/tmp/colony-tier-home/sessions/2026/09/30/rollout-2026-09-30T02-55-26-01a0f1bd-8d95-7382-901f-61a8e3921074.jsonl`. Temporary auth/catalog symlinks were removed after the test; saved test sessions and configuration remain for inspection.
