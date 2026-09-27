---
name: atk-validate
description: Compare exact parent and candidate or B0 and cumulative Revision on frozen Cases and standards.
---

# Validate a Revision

Read `../WORKFLOW.md`. For incremental validation, evaluate the sealed Candidate and
its parent on the Issue target Cases, protection Cases, and previously fixed Cases.
For final validation, evaluate B0 and the exact current accepted Revision on the full
frozen set, using the repeat plan and `phase=final` on both sides. Use `run_evaluation` to replay known
commits in the original directory; it restores the starting checkpoint afterward.
Reassess existing executions only when fixed input/environment allows it; do not
select the best retry or omit unknowns.

Check candidate loading evidence for business Skills, actual component identity,
runner and fixed context, Case content, judgement calibration, and paired repeat
slots. Apply the frozen quality or efficiency objective and metric limits; missing
required metrics yield `insufficient`. Cost includes all attempts, including retries.
Verify the metric source before trusting cost or tool counts; the default runner's
`metrics.json` sidecar alone does not prove that the target Agent could not edit them.
Call `compare_and_gate` with exact Assessment IDs and parent/B0 commit.
Report `pass/no_effect/regression/insufficient/invalid` with fixed, regressed, stable,
and unknown Cases. A single-run exploratory improvement is not final proof for a
stochastic or unknown Agent. Do not edit the Candidate or create a commit here.
