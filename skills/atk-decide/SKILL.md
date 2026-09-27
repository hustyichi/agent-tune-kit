---
name: atk-decide
description: Commit a validated candidate, restore a failed one, roll back an accepted suffix, or finish a Round.
---

# Decide and checkpoint

Read `../WORKFLOW.md`. Identify the exact Round, Candidate, Validation, and frozen
authorization. `decide_candidate` with `keep` requires a matching passing incremental
Validation (or explicit recorded trial override with user source, action, reason,
and risk); it stages only sealed paths and checks the commit parent, changed paths,
trailers, and content. `reject` or `defer` preserves evidence and restores the
Candidate's parent without making a failure commit. Inspect any interrupted operation
with `inspect_or_recover_operation` before retrying.
For an unsealed draft with no remaining workspace edits, use `cancel_draft` with a
reason; it records the cancellation and releases the pending slot without refunding
candidate budget. Never use `decide_candidate` on a draft.

After all candidates, require a passing final Validation for `finish_round: complete`.
It must match this Round, frozen plan, B0, current Revision, and final commit.
`complete_with_override` records an explicit user exception and keeps the original
failing or absent Validation visible; it never reports ordinary completion.
If the final gate fails, follow the frozen rule: `rollback_to` B0 or an earlier recorded
checkpoint, or pause for an explicit human exception. Rollback creates a normal
restoration commit and withdraws the affected suffix; it never rewrites Git history.
For a pause, call `transition_round` with `action=pause` and a reason; later recover
any interrupted operation and use `action=resume`. This preserves the candidate and
blocks further work until workspace checks pass. Do not manually rewrite Round state.
No adopted Candidate means `close_without_adoption`, with no empty commit. This also closes an unfrozen analysis-only Round, even without
Git or B0. Report B0 when present,
all accepted/rejected candidates, final commit, and limitations. Never push or deploy.
For a linked external fix Round, use `finish_round: complete_external_fix` only with a
passing external-fix Validation. Then append a resolved revision to the original Issue
with the linked Round, component identity, direct evidence refs, and Validation ID.
