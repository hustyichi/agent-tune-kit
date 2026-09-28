---
name: atk-eval
description: Run, import, or reassess Agent evidence under an explicit calibrated evaluation specification.
---

# Evaluate evidence

Read `../WORKFLOW.md`. Choose an explicit action: `run`, `import`, or `reassess`.
For run, configure a runtime first. Set `concurrency` to the positive integer frozen
in the Round plan (default 1). The default runner bounds active attempts and writes
results as they complete. Inspect `running_record_ids`, `completed_record_ids`, and
`not_started_record_ids` after interruption; legacy single-running adapters remain
supported. Each attempt has a distinct output directory, but session and external-write
isolation must still be provided by the target Agent.
For run, use `run_evaluation` with a dataset ID, Case IDs, Revision ID, round ID (if
frozen), repeat count, the frozen `timeout_seconds`, purpose, and `phase=incremental/final`. Final runs
must use both frozen sides once; their attempts are reserved before candidate work.
For a linked external fix Round, use `phase=external_fix` on the new B0 only; include
the original affected and protection Cases. Its direct component check is a separate
authorized `diagnostic_probe` with a passing Assessment, never the end-to-end run.
For an authorized infrastructure retry, pass `retry_batch_id` and the failed
`retry_execution_ids`; keep the same Round, Revision, dataset, purpose and phase.
The new batch retains every earlier Execution and adds linked retries. Never rerun a
completed Agent failure or branch from an already continued batch. The target project's Python runs its
`.atk/adapters/runner.py`; each attempt gets its own Execution ID. Inspect partial
or interrupted batches before using `continue_batch_id`. Confirmed records are carried
forward; only not-started slots rerun automatically. Unknown slots need an explicit
`unknown_execution_authorization` with `source=user`, the predecessor batch ID, exact
unknown record IDs, reason, and risk. Continuations consume new execution budget and
leave the predecessor evidence intact. Inspect partial
outputs, runner exit status, actual component hashes and pre/post service versions,
identity failures, and loading evidence. A diagnostic
probe needs a frozen `probe_authorization_id` matching the command, runner, Cases and
recorded isolation, plus separate probe budget. It is not formal effect evidence.
An analysis-only Round may run that probe with `revision_id=null` and no Git B0 when
the project has an explicit runtime. A sealed pending candidate may temporarily
replay its recorded parent or B0; recover an interrupted switch before another run.
If a process dies while replaying an older checkpoint, run
`inspect_or_recover_operation` with its recorded operation ID. Recovery only restores
known source content and re-runs the frozen preparation; unknown changes need inspection.

For import, call `import_evidence` with `source_kind=batch_results` and explicit field
mapping, or `source_kind=langfuse` with `adapter_profile` set to
`langfuse_trace_bundle` or `langfuse_observation_rows`. Include source namespace,
mapping version, filtered scope, and extra redaction keys. JSON, JSONL, CSV, and `.gz`
are supported; do not infer missing Trace parents, final output, or overall online
success rate. For separate Trace, Observation, and Score files, classify every file
with `file_roles` (`trace` / `observation` / `score`). Declare CSV nested fields in
`json_columns`; declare changed source field names under `mapping.trace`,
`mapping.observation`, or `mapping.score` as `{canonical_name: source_name}`.
Use `mapping.root_observation_name` only when it uniquely identifies
the final root Observation. Do not rerun the Agent when the request is reassess.

Define versioned `evaluation_spec` and `judger` before scoring. Use deterministic
checks for hard constraints and the current Codex session for semantic judgments.
For formal evaluation, record execution evidence trust, component contract or
artifact checks, and task effect as separate dimensions. Mark a dimension
`not_applicable` only under an explicit rule; mark missing evidence `unknown`.
Without Ground Truth, judge independently observable dimensions and mark any
unjudgeable task-success dimension `unknown`; output presence or imported scores
alone do not establish task success.
Calibrate new or changed rules with confirmed positive and negative examples; mark
uncalibrated if unresolved. Submit complete per-record, per-dimension rows to
`store_assessment`; each verdict cites `{batch_id,evidence_id}`. Its CSV is the sole
authoritative scoring detail. New standards create a new Assessment and invalidate
old comparisons; never edit an old Assessment in place. `render_assessment_html`
regenerates an escaped local view from the sealed CSV when needed; never use the
HTML as an independent scoring source.
