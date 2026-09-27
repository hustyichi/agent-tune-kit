---
name: atk-init
description: Inspect and connect an existing local Agent to ATK v2 without changing its loading behavior.
---

# Connect an existing Agent

Read `../WORKFLOW.md`. Inspect the target project's source and instructions before
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
`allowed_paths`, `protected_paths`, and detailed `runtime_notes`. It creates
`.atk/project.json`, `.atk/runtime.md`, and `.atk/adapters/runner.py`, and adds `.atk/`
to the local Git exclude. Inspect the generated adapter and make only project-specific
invocation changes necessary to preserve its request/response contract. Do not create
a new Agent, run the full dataset, commit existing changes, or replace legacy `.atk`
data. If `.atk` already contains older data, stop and report the conflict.
