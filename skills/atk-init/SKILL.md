---
name: atk-init
description: Inspect and connect an existing local Agent to ATK v2 without changing its loading behavior.
---

# Connect an existing Agent

Read `../WORKFLOW.md`. For import-only analysis, call `initialize_project` with
`analysis_only=true` and optional `redact_keys`; no Git or runner is needed. Skip
runtime investigation until execution is requested. Later, pass `configure_runtime=true`
with the complete runtime configuration to upgrade that project while preserving its
Rounds, evidence, and redaction keys. Resume any paused analysis Round before upgrading.
For an executable project, inspect the target project's source and instructions before
creating a runner. Confirm entry command, Python/Node environment, cwd, input mapping,
session isolation, cache reset, external side effects, and logs. For business Skills,
trace discovery directories, path base, actual load stage, caches, external copies,
and how a candidate's file fingerprint can be observed as loaded or invoked. Record
unknowns explicitly; file availability alone is not loading proof.
For every required loaded component, have the target instrumentation write its actual
absolute `resolved_path` alongside `component_id`, `state`, and `fingerprint` in each
attempt's `loading.json`; copying the configured path into that field is not proof.
If the Agent loads an isolated copy, the trusted adapter may also emit absolute
`staged_from_path` from the observed copy step. Keep the loader event locator; do not
infer the origin solely from equal file contents.

Call `initialize_project` with `python`, argv-array `command`, `components`,
`allowed_paths`, `protected_paths`, `external_effects` (use `[]` only after
checking that no external writes occur), and detailed `runtime_notes`. It creates
`.atk/project.json`, `.atk/runtime.md`, and `.atk/adapters/runner.py`, and adds `.atk/`
to the local Git exclude. Inspect the generated adapter and make only project-specific
invocation changes necessary to preserve its request/response contract. Do not create
a new Agent, run the full dataset, commit existing changes, or replace legacy `.atk`
data. If `.atk` already contains older data, stop and report the conflict.

Declare each known external write in `external_effects` as a named object. Before
replay, add `protection: {kind, evidence_ref}` for each one: `kind` is
`test_environment`, `stub`, or `approved_safeguard`; `evidence_ref` points to the
checked setup in `runtime.md` or another local record. ATK blocks replay while
any declared write lacks this record. Verify the setup itself; the reference is
not an operating-system sandbox.

For cost or tool-call limits, configure `metric_sources` with an independent
collector name and `evidence_ref` after inspecting the adapter. The adapter must
write that name in each Record's `metric_sources` for the measured value. The
generated runner labels its own duration `runner_clock` and labels Agent-written
`metrics.json` values `agent_sidecar`; sidecar values remain diagnostic and cannot
satisfy a formal cost or tool-call gate. Do not label a sidecar as independent.

If a project adapter can identify infrastructure failures (for example, model
authentication failure), pass its distinct nonzero `infrastructure_exit_codes` to
`initialize_project`. Reserve these codes for failures that make an attempt
unusable as effect evidence; ordinary Agent failures must use other codes.

For a fixed remote component whose actual version matters, configure a read-only
argv-array `version_command`, an `expected_version` when known, and optionally
`version_timeout_seconds`. The command must print only a nonsecret version token.
ATK runs it before and after each batch; record how this command reaches the actual
service rather than merely reading local source metadata.
