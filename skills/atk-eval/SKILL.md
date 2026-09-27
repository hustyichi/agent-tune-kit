---
name: atk-eval
description: Run, import, or reassess Agent evidence under an explicit calibrated evaluation specification.
---

# Evaluate evidence

Read `../WORKFLOW.md`. Choose an explicit action: `run`, `import`, or `reassess`.
For run, use `run_evaluation` with a dataset ID, Case IDs, Revision ID, round ID (if
frozen), repeat count, timeout, and purpose. The target project's Python runs its
`.atk/adapters/runner.py`; each attempt gets its own Execution ID. Inspect partial
outputs, runner exit status, actual component hashes, and loading evidence. A diagnostic
probe is not formal effect evidence.

For import, call `import_evidence` with `source_kind=batch_results` and explicit field
mapping, or `source_kind=langfuse` with `adapter_profile` set to
`langfuse_trace_bundle` or `langfuse_observation_rows`. Include source namespace,
mapping version, filtered scope, and extra redaction keys. JSON, JSONL, CSV, and `.gz`
are supported; do not infer missing Trace parents, final output, or overall online
success rate. Do not rerun the Agent when the request is reassess.

Define versioned `evaluation_spec` and `judger` before scoring. Use deterministic
checks for hard constraints and the current Codex session for semantic judgments.
Calibrate new or changed rules with confirmed positive and negative examples; mark
uncalibrated if unresolved. Submit complete per-record, per-dimension rows to
`store_assessment`; each verdict cites `{batch_id,evidence_id}`. Its CSV is the sole
authoritative scoring detail. New standards create a new Assessment and invalidate
old comparisons; never edit an old Assessment in place.
