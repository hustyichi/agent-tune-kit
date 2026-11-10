---
name: atk-trace-init
description: Prepare project context and non-secret sources for session trace tuning; use when initializing or updating Trace configuration without creating batch evaluation adapters.
metadata:
  compatibility: Requires the installed ATK CLI on PATH with trace and visualize trace commands.
---

# Agent Trace — Initialize

## Purpose

Understand the Agent project and prepare reusable context and supported sources. Initialization requires no session, dataset, runner, remote connection or runnable Agent.

## Inputs

The current project, existing Trace files, optional `.atk/context.md`, project docs and the source needed to discover execution and verification entry points.

## Outputs

`.atk/traces/project.md`, non-secret `config.json`, readiness and specific gaps. No session archive, download, issue, Agent execution or evaluation version. Initialization has no bound session and does not generate an empty preview; session stages generate it once retained material exists.

## Workflow

1. Read project instructions/docs. Identify Agent purpose, relevant code/Prompt/tool locations, known run/verification commands, supporting sources and unknowns. Reference confirmed standards in optional `.atk/context.md`; do not replace it with run notes.
2. Inspect existing Trace files. Preserve human additions and working source settings. Local mode remains valid without remote credentials; do not guess or silently switch instance, project or environment.
3. Prepare supported configuration only: source instance/project/environment, version clues, credential environment-variable names and necessary retention settings. Do not request plaintext secrets. A present variable is not a verified connection.
4. Run `atk trace init --json` for minimal local initialization, or `atk trace init --config <json-file> --project-file <markdown-file> --json` with researched input. Existing human context is preserved; inspect reported differences rather than overwriting it. The first Trace writer preserves `.gitignore` and ensures `.atk/traces/` is ignored.
5. Check source paths and credential references without printing values. Perform a minimal read-only connection check only when requested; do not download session bodies as an initialization probe.
6. Report local material availability, remote configuration readiness, actual connection checks, known execution conditions and gaps separately. Stop unless further work was requested.

## Minimal local configuration

A bare init leaves `{}` and does not enable capture. For an authorized local import, prepare the actual source namespace and retention decision, then pass this JSON through `--config`:

```json
{
  "source": {"type": "local", "instance": "actual-export-origin", "project": "actual-project", "environment": ""},
  "retention": {"allow_body": true, "redact_keys": []}
}
```

Set `allow_body` only when saving the selected material is authorized by the current task or an established policy. Reuse that authorization without asking again. Use project-specific sensitive field names in `redact_keys`; optional `max_string_chars` is a positive integer. Without permission to save bodies, leave retention disabled and state the precise gap. Local mode needs no Langfuse credentials or time range. Source names describe verified provenance rather than guessed session ownership.

## Failure behavior

Keep existing files on invalid input or conflict. Missing remote settings never block local mode. Ask only for necessary facts unavailable from project/approved sources. Do not invent runtime versions, readiness or historical reproducibility.

## CLI dependency

Use the installed `atk` CLI from the target project directory. Check `atk --version` and the required command's `--help` when compatibility is uncertain. If unavailable, guide installation with `uv tool install agent-tune-kit`, then `atk install`; do not recreate the fixed CLI or frontend. CLI and Skill contracts must agree. `atk status` checks installation; `atk trace status` reads session records. Installation does not prove the current host session loaded updated Skills.

For source/configuration and record fields, locate the plugin root by walking upward from this file to `.codex-plugin/plugin.json`, then read its `docs/trace-tuning.md` as needed.
