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

Import-only projects can initialize with `analysis_only=true` without Git or a runner.
Rounds inherit this mode and can import, assess, diagnose, and close without B0. Later,
`configure_runtime=true` supplies runtime configuration; freezing binds a clean Git
baseline while retaining existing evidence. `transition_round` records pause/resume
reasons and checks operations, Git, and pending content before resuming. The first final
run enters `finalizing`, which blocks new candidates and incremental runs.
An unsealed draft can be released with `cancel_draft` after its workspace edits have
been restored and checked; its candidate budget remains spent.

## Evidence and artifacts

Evaluation accepts a runnable Case dataset, existing batch results, or Langfuse JSON/JSONL/CSV/`.gz` exports. Langfuse Trace bundles and Observation rows use explicit mapping profiles. For separate Trace, Observation, and Score files, declare each filename and its role in `file_roles`; declare nested CSV fields in `json_columns`. Map changed field names under `mapping.trace`, `mapping.observation`, or `mapping.score` as `{canonical_name: source_name}`; conflicting values block import. `mapping.root_observation_name` may supply missing Trace input or output only when it selects one root Observation. Missing parents, final answers, or export completeness stay unknown. External Scores are preserved as evidence without silently becoming ATK pass/fail judgments. Reassessment creates another immutable Assessment without running the Agent again.

The target runner uses the target project's Python environment and assigns one Execution to each planned attempt. Initialization requires an explicit `external_effects` declaration (`[]` after confirming no external writes); every declared write needs a recorded test environment, stub, or approved safeguard before replay. Freeze a positive integer `concurrency` (default 1) and a positive finite `timeout_seconds` (default 120); both must match each formal run and retry.
The primary assessment dimension is frozen. All specification dimensions are required
unless the plan explicitly selects a subset. Unknown or invalid required verdicts
block passage, and regression in a required dimension defeats a primary improvement.
Candidate target Cases come from its recorded primary Issue, not a caller-selected ID.
The default runner bounds active attempts, gives each a distinct output directory, and
persists completed, running, and unstarted identities. Attempt or batch timeouts stop
the relevant Agent process groups. Revisions remain serial; the target provides session
and external-write isolation. An adapter may declare `infrastructure_exit_codes` so failures such as model authentication become retryable `infra_error` evidence rather than an Agent result. An unknown Execution status leaves the batch `partial`. For business Skills, an available file is distinct from evidence that the Agent actually loaded or invoked it; unverified loading blocks normal Skill validation. Raw export files remain read-only. Common credential fields are masked in imported copies; project-specific sensitive fields require explicit redaction rules.

For a fixed remote component, project configuration may supply a read-only argv-array `version_command`, `expected_version`, and optional `version_timeout_seconds`. ATK probes the version before and after each batch and records the observed values, command fingerprint, and any identity failure. A changed or unverified version cannot support a formal improvement claim.

A frozen Round reserves both sides of final validation before candidate work. Authorized infrastructure retries create linked Executions without selecting the best attempt. Cost and tool-call gates require a frozen `metric_sources` declaration for an independent collector and a matching source on every Record; the default runner's Agent-written `metrics.json` values remain diagnostic only. Runner-measured duration may be used for duration gates. Independent holdout source groups are tracked across Rounds.
Interrupted local batches continue through `continue_batch_id`: confirmed records are
retained, unstarted slots may rerun, and unknown slots need recorded explicit user
authorization. Continuations consume attempt budget and preserve prior batches.

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
