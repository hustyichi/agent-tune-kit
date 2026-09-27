---
name: atk-diagnose
description: Investigate evidence, distinguish responsibility layers, and record Issues with bounded checks.
---

# Diagnose Issues

Read `../WORKFLOW.md`. Work from exact evidence locations. Separate the first
observable fault from downstream symptoms; check actual component identity, input,
contract, raw response, and runtime handoff. Compare credible explanations that would
change the repair location. Use source/contract inspection first; run a bounded
`diagnostic_probe` only with an approved command, isolation, and budget. Never treat a
probe as an original execution or as effect validation.
Record each probe permission in the frozen plan (or an `analysis_plan` before freeze):
ID, configured command hash, runner hash, allowed Case IDs and isolation reference.
For a post-fix direct check also freeze `kind=direct_component`, the repaired
`component_identity`, and its `evaluation_spec_hash` in that permission.
Set a separate `budget.probes`; zero is the default. The reference records a checked
setup and does not create an OS sandbox for the target Agent.

Use `store_diagnosis` to record one or more Issues. Include symptom, supporting and
contrary evidence, checks with `not_run/completed/failed/inconclusive`, mechanism refs,
intervention Validation refs, responsibility component, layer (`skill`, `prompt`,
`agent_code`, `tool`, `retrieval`, `runtime`, `dataset_or_judge`, or `unknown`), Case
links, disposition, and next action. A `supported` mechanism requires direct evidence
and addressed competing explanations; an effective intervention is a separate claim.

For out-of-scope components, record a local `external_handoff` with trigger,
expected/actual, contract, component version, minimal reproduction and post-fix
end-to-end regression. Do not send it or edit another repository. A fixed service
requires a linked new Round and fresh verification before resolving its Issue.
