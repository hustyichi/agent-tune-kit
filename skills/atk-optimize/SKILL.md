---
name: atk-optimize
description: Prepare and seal one scoped local Agent candidate on the current accepted Git checkpoint.
---

# Optimize one Issue

Read `../WORKFLOW.md`. If the Round is not frozen, call `create_round` and
`freeze_round` only after recording a clean B0, protected/allowed paths, component
identity, calibrated scoring, target/protection Cases, final repeats, finite budget,
commit authorization, and rollback rule. A blocked out-of-scope Issue cannot become
a local fix. Freeze Issue blockers and any authorized workaround IDs. A workaround is
a new Candidate with `change_kind=workaround`; it needs a passing gate and leaves the
external Issue open. Do not modify ATK workflow, judge, dataset, or runner to pass a gate.
Freeze `replay_preparation` before candidate work so old Revisions can be run in the
original directory with their caches and artifacts rebuilt.
The finite plan's execution budget must preserve both sides of final validation;
`budget.candidates` limits drafts and `max_retries_per_slot` is zero unless authorized.

Call `prepare_candidate` with the exact primary Issue and declared file paths.
Edit the existing Agent's business Skill, Prompt, code, or config in its original
worktree; keep one principal mechanism per candidate. Call `seal_candidate` after
editing. Check the returned changed paths and Revision. Do not continue editing after
seal; any further change requires a new seal/Validation. Do not submit, push, or stack
another candidate before `atk-decide` has resolved this one.
