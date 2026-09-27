# Agent Tune Kit

[简体中文](README.md) | English

Agent Tune Kit (ATK) is a local Codex plugin for **existing Agents**. The current Codex session investigates evidence, makes semantic judgments, and edits the target. Deterministic Python operations import or run evidence, store assessments, compare revisions, and manage Git checkpoints. A target may be a business Skill, prompt, Agent code, or configuration.

> This checkout is implementing the [vNext design](docs/agent-tune-kit-vnext-refactor-plan.md). It is a breaking, unpublished development state. Old Skills and `.atk/results/vN/` data are not migrated or deleted. Initialization stops when it finds an existing legacy `.atk` directory.

## Install and workflow

The command for the currently published package remains:

```sh
uvx --from agent-tune-kit atk install
```

To try this checkout, run `uv run --frozen atk install` here and enable Agent Tune Kit in Codex `/plugins`. vNext exposes exactly seven Skills:

| Skill | Responsibility |
| --- | --- |
| `atk-init` | Inspect the existing Agent's entry point, runtime, side effects, and business Skill loading; create a local runner |
| `atk-dataset` | Save versioned Cases, expected results, replay conditions, and source groups |
| `atk-eval` | Explicitly run, import, or reassess local/batch/Langfuse evidence |
| `atk-diagnose` | Investigate competing explanations, record Issues and local handoffs |
| `atk-optimize` | Prepare and seal one scoped candidate on a clean Git baseline |
| `atk-validate` | Compare each candidate with its parent and the final cumulative version with B0 |
| `atk-decide` | Commit a passing candidate, restore a rejected one, roll back an accepted suffix, or close the Round |

Flow: `atk-init → atk-dataset (as needed) → atk-eval → atk-diagnose → [atk-optimize → atk-validate → atk-decide]×N → final atk-validate → atk-decide`. Only one candidate is pending at a time. Passing candidates become local commits in the original worktree; rejected candidates retain evidence and restore their parent. ATK does not push, deploy, rewrite history, or edit another repository.

## Evidence and artifacts

Evaluation accepts a runnable Case dataset, existing batch results, or Langfuse JSON/JSONL/CSV/`.gz` exports. Langfuse Trace bundles and Observation rows use explicit mapping profiles. Missing parents, final answers, or export completeness stay unknown. External Scores are preserved as evidence without silently becoming ATK pass/fail judgments. Reassessment creates another immutable Assessment without running the Agent again.

The target runner uses the target project's Python environment and assigns one Execution to each planned attempt. An attempt timeout stops its Agent process group before the next Case starts. An adapter may declare `infrastructure_exit_codes` so failures such as model authentication become retryable `infra_error` evidence rather than an Agent result. An unknown Execution status leaves the batch `partial`. For business Skills, an available file is distinct from evidence that the Agent actually loaded or invoked it; unverified loading blocks normal Skill validation. Raw export files remain read-only. Common credential fields are masked in imported copies; project-specific sensitive fields require explicit redaction rules.

For a fixed remote component, project configuration may supply a read-only argv-array `version_command`, `expected_version`, and optional `version_timeout_seconds`. ATK probes the version before and after each batch and records the observed values, command fingerprint, and any identity failure. A changed or unverified version cannot support a formal improvement claim.

A frozen Round reserves both sides of final validation before candidate work. Authorized infrastructure retries create linked Executions without selecting the best attempt. Frozen gates require complete data for configured metric limits; independent holdout source groups are tracked across Rounds. The default runner can read cost and tool counts from a task sidecar, whose provenance must be checked before treating those values as trusted.

ATK stores project configuration and runner under `.atk/`, immutable datasets under `datasets/<id>/`, evidence under `evidence/<batch-id>/`, the authoritative scoring CSV under `assessments/<id>/`, and Round plans, Issues, Candidates, Validations, Decisions, and recovery operations under `rounds/<id>/`. Objects are addressed by explicit IDs, never by a “latest vN” folder. The seven Skills call `atk internal <operation> --request <JSON> --output <JSON>`; it is an implementation interface, not an additional public tuning workflow.

If a process exits during replay of an older checkpoint, the recorded operation can restore the current source and rerun the frozen cache or artifact preparation, provided no unknown file content has appeared.

## Development checks

```sh
uv run --frozen pytest -q
uv run --frozen ruff check .
python3 scripts/validate_skill_pack.py
uv build --no-sources
```

Offline tests use tiny Git repositories and a fake Agent. One real Magic Workspace Trace import and business Skill load have separate acceptance evidence; known-cause diagnosis, post-service-fix verification, and multi-Case evaluation remain open. An Agent with unisolated external writes must not be replayed as a formal local evaluation.
