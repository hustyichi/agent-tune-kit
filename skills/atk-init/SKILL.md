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

Call `initialize_project` with `python`, argv-array `command`, `components`,
`allowed_paths`, `protected_paths`, and detailed `runtime_notes`. It creates
`.atk/project.json`, `.atk/runtime.md`, and `.atk/adapters/runner.py`, and adds `.atk/`
to the local Git exclude. Inspect the generated adapter and make only project-specific
invocation changes necessary to preserve its request/response contract. Do not create
a new Agent, run the full dataset, commit existing changes, or replace legacy `.atk`
data. If `.atk` already contains older data, stop and report the conflict.
