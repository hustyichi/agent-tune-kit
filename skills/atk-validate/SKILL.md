---
name: atk-validate
description: Compare exact parent and candidate or B0 and cumulative Revision on frozen Cases and standards.
---

# Validate a Revision

Read `../WORKFLOW.md`. For incremental validation, evaluate the sealed Candidate and
its parent on the Issue target Cases, protection Cases, and previously fixed Cases.
The first final run enters `finalizing` automatically. If comparing existing final
evidence, first use `transition_round: start_finalizing` with a reason. No candidate
may remain pending, and no new candidate or incremental run is allowed in that stage.
For final validation, evaluate B0 and the exact current accepted Revision on the full
frozen set, using the repeat plan and `phase=final` on both sides. Use `run_evaluation` to replay known
commits in the original directory; it restores the starting checkpoint afterward.
Reassess existing executions only when fixed input/environment allows it; do not
select the best retry or omit unknowns.

Check candidate loading evidence for business Skills: both sides need the loaded
absolute path and fingerprint, or a staged copy linked to the declared source path.
Also check actual component identity, runner and fixed context, Case content,
judgement calibration, and paired repeat slots. Apply the frozen quality or
efficiency objective and metric limits; missing
required metrics yield `insufficient`. Cost includes all attempts, including retries.
Cost and tool-count gates require the frozen project's independent `metric_sources`
declaration and a matching source on every Record. The default runner's
Agent-written `metrics.json` sidecar remains untrusted for these gates.
Call `compare_and_gate` with exact Assessment IDs and parent/B0 commit.
The incremental target set comes from the sealed Candidate's primary Issue; a
different request Issue is rejected. Assess all frozen required dimensions on both
sides and keep the same formal timeout. Do not choose a different dimension during
comparison.
For a linked external fix Round, call `validate_external_fix` with the new B0
Assessment, passing direct-probe Assessment, and direct evidence refs. There is no
Agent Candidate or old-B0 comparison in this verification Round.
Report `pass/no_effect/regression/insufficient/invalid` with fixed, regressed, stable,
and unknown Cases. A single-run exploratory improvement is not final proof for a
stochastic or unknown Agent. Do not edit the Candidate or create a commit here.
