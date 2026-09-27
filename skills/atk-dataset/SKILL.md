---
name: atk-dataset
description: Build or revise versioned evaluation Cases and Ground Truth without running the Agent.
---

# Prepare Cases

Read `../WORKFLOW.md`. Use confirmed examples, criteria, and reviewer feedback to
prepare a CSV or JSONL with stable Case IDs, business input, optional expected result,
source group, usage, and replay requirements. Do not copy an Agent's answer into Ground
Truth without a confirmed standard. Keep optimization, protection, and independent
holdout groups separate by source task/session; unknown origin is not independent.
Holdout runs need a frozen milestone ID. ATK records exposed source groups across
Rounds and blocks future independent reuse or further candidate editing in that Round.

Call `store_dataset` with explicit column mapping. It writes an immutable
`.atk/datasets/<dataset-id>/cases.jsonl` and manifest. A correction creates another
dataset ID; old Assessments and Validations stay bound to their original Case content.
Report gaps in environment or attachments before calling a Case replayable.
