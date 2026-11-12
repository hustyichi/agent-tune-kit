---
name: atk-trace-init
description: Infer and save the project's tuning boundary, execution context and non-secret sources; use when initializing or updating Trace configuration without creating batch evaluation adapters.
metadata:
  compatibility: Requires the installed ATK CLI on PATH with trace and visualize trace commands.
---

# Agent Trace — Initialize

## Purpose

Understand the Agent project and automatically prepare its reusable tuning boundary, execution context and supported sources. Initialization requires no manually specified boundary, session, dataset, runner, remote connection or runnable Agent.

## Inputs

The current project, existing Trace files, optional `.atk/context.md`, project docs and the source needed to discover execution and verification entry points.

## Outputs

`.atk/traces/project.md` with the tuning goal, included components, exclusions with reasons, unknowns and sources; non-secret `config.json`, readiness and specific gaps. No session archive, download, issue, Agent execution or evaluation version. Initialization has no bound session and does not generate an empty preview; session stages generate it once retained material exists.

## Workflow

1. Read and apply the shared [tuning boundary](../../docs/trace-tuning.md#tuning-boundary) procedure. Read existing `.atk/traces/project.md`, optional `.atk/context.md`, the project registry and relevant project instructions/docs. Infer the default boundary from actual responsibilities and implementation entry points; distinguish reusable Agent mechanisms from the business artifacts they operate on. Identify known run/verification commands and sources. Keep unsupported scope judgments as unknowns; ask only about consequential ambiguity that the available material cannot resolve.
2. Inspect existing Trace files. Preserve human additions and working source settings. Local mode remains valid without remote credentials; do not guess or silently switch instance, project or environment.
3. Prepare supported configuration only: source instance/project/environment, version clues, credential environment-variable names and necessary retention settings. Do not request plaintext secrets. A present variable is not a verified connection.
4. Save the researched boundary and execution context using `atk trace init --project-file <markdown-file> --json`, with `--config <json-file>` when needed. Refer to stable project IDs in `projects.json` instead of duplicating its catalog. For existing human notes, reconcile only supported differences directly in `project.md`, preserving constraints and unrelated content; the CLI protects them from replacement. Bare `atk trace init --json` creates only a placeholder, so fill the available boundary before claiming initialization is complete. Record unavailable details as unknowns without requiring a separate user setup step. The first Trace writer preserves `.gitignore` and ensures `.atk/traces/` is ignored.
5. Check source paths and credential references without printing values. Perform a minimal read-only connection check only when requested; do not download session bodies as an initialization probe.
6. Report the saved default boundary, its basis and material unknowns so the user can correct it; do not require confirmation when evidence already supports it. Report local material availability, remote configuration readiness, actual connection checks and execution conditions separately. Stop unless further work was requested.

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

## Projects and scope

Use `atk trace project --list --json` to resolve existing project IDs, names, established abbreviations and workspace-relative paths. `--project-root` remains the archive workspace; it is not an attribution target. Prefer user-specified projects for investigation; follow cross-project evidence without silently broadening the effective tuning boundary. Never invent abbreviations. A caller, runtime location or proposed fix alone does not establish a project association.

Read the applicable project's README, AGENTS.md and CLAUDE.md (including actual filename case variants) before investigation or modification. Keep project IDs and workspace-relative paths consistent. State inspected and uninspected scope, proposed versus actual modifications, candidate versions and per-project loading evidence. When modifying another project, explain the tradeoff and remaining limits. Do not update parent gitlinks without explicit authorization.

Sessions contain issues; only individual issues own project associations. Do not store or display a session-level project union. `source.project` remains the trace source namespace. Project filters apply to the dashboard issue view and the issue list within a session; the session overview is unfiltered by project. Modification projects belong to repair records and details.

Associate a project when there is a reasonable, evidence-informed explanation of how its code, configuration, tool contract, Prompt, Skill or missing safeguard caused or contributed to the issue. These associations are for human review: historical runtime certainty, an implemented fix and successful verification are not prerequisites. Explain uncertainty in the reason and diagnosis. A mention, execution location or proposed modification alone is insufficient; explain the connection to this issue.

Issue `project_links` entries contain `project_id`, nonempty `reason`, retained `evidence` references and optional workspace-relative `source_paths`. Keep one entry per project; multiple projects are allowed. Save through `atk trace issue` with the current revision; omission preserves associations, `[]` clears them. Every associated project is displayed and filterable. If no project has a reasonable explanation, save `[]` and explain why in the diagnosis. An empty list means no associated project (无关联项目); it may reflect no applicable project, not necessarily an attribution gap or pending work. Describe an information gap only when one actually exists. Actual modification projects remain in repair records; verification outcomes do not automatically add or remove associations.

Save diagnoses with `--expected-revision <reviewed-issue-revision>`; retain project-specific source revision/dirty state in the diagnosis body. Each repair summary must identify the target project and files/behavior; verification must identify the candidate actually loaded for each changed project. Existing summary objects may carry `project_id`; do not claim an unimplemented alternative was changed. Refresh the session preview and dashboard after saving.

Run `atk trace project --discover --json` for local repository/submodule candidates, including unavailable checkouts. Read each relevant project's existing entrypoints to establish purpose, constraints and naming; retain sources in `entrypoints` and a concise `description`. Never initialize or align submodules as discovery. Register ordinary directories supplied by the user or confirmed workspace configuration as well. Merge with existing registry entries by path, retaining their stable IDs and human fields. Use `atk trace project --data <json> --json` with current registry `revision` and one entry (`id`, `name`, `path`, optional `parent_id`, `kind`, `abbreviation`, `aliases`, `description`, `entrypoints`, `deprecated`). Existing abbreviations require documentary or user provenance; otherwise use the common project name. Registry summaries do not replace original rules. Keep project.md as narrative context, referring to projects.json rather than duplicating the registry. Missing registry never blocks minimal initialization or capture.
