---
name: atk-optimize
description: Prepare and seal one scoped local Agent candidate on the current accepted Git checkpoint.
---

# Optimize one Issue

Read `../WORKFLOW.md`. If the Round is not frozen, call `create_round` and
`freeze_round` only after recording a clean B0, `dataset_id`, protected/allowed paths, component
identity, calibrated scoring, target/protection Cases, final repeats, finite budget,
commit authorization, and rollback rule. A blocked out-of-scope Issue cannot become
a local fix. Freeze Issue blockers and any authorized workaround IDs. A workaround is
a new Candidate with `change_kind=workaround`; it needs a passing gate and leaves the
external Issue open. Do not modify ATK workflow, judge, dataset, or runner to pass a gate.
Freeze `replay_preparation` before candidate work so old Revisions can be run in the
original directory with their caches and artifacts rebuilt.
The finite plan's execution budget must preserve both sides of final validation;
`budget.candidates` limits drafts and `max_retries_per_slot` is zero unless authorized.

Record the primary Issue and its target Cases before `prepare_candidate`; the frozen
target set must be included in that Issue. Related Issue IDs must also have recorded
revisions. Call `prepare_candidate` with the exact primary Issue and declared file paths.
Edit the existing Agent's business Skill, Prompt, code, or config in its original
worktree; keep one principal mechanism per candidate. Call `seal_candidate` after
editing. Check the returned changed paths and Revision. Do not continue editing after
seal. Do not submit, push, or stack another candidate before `atk-decide` has resolved
this one. To revise the same Issue, create a new Candidate with `supersedes` pointing to
the sealed Candidate; seal and validate the new content separately.
If an unsealed draft is abandoned, first restore its files to the parent checkpoint
and inspect any partial seal artifacts, then use `cancel_draft` with a reason. This
releases the pending slot while retaining the draft and its consumed candidate budget.
