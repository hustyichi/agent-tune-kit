# ATK Local Context

`./.atk/context.md` is a local, private tuning-consensus document for one target project.

It records user-confirmed standards and tuning decisions that cannot be reliably inferred from datasets, runners, or
result files. It is not a dataset metadata registry, run log, or team collaboration file.

## Recommended Template

```markdown
# ATK Local Context

## Tuning Objective

<!-- User-confirmed goal for the current tuning loop. -->

## Agent Behavior Standard

<!-- User-confirmed expectations for the Agent's output and boundaries. -->

- Should:
  -
- Should not:
  -
- Acceptable variations:
  -
- Hard failures:
  -

## Ground Truth Standard

<!-- User-confirmed standard for writing and judging ground_truth. -->

- Ground truth represents:
  -
- Must include:
  -
- May omit:
  -
- Acceptable variations:
  -
- Rejection criteria:
  -
- Edge cases:
  -

## User Feedback

<!-- Durable user judgments from conversation, manual review, or evaluation feedback. -->

- Date:
  - Feedback:
  - Implication:

## Tuning Decisions

<!-- Decisions future tuning should preserve unless the user changes direction. -->

- Date:
  - Decision:
  - Reason:
  - Applies to:
```

## What Belongs Here

Write only durable, user-confirmed or human-reviewed information:

- the current tuning objective and non-obvious success criteria;
- Agent behavior standards, output preferences, and hard failure boundaries;
- `ground_truth` writing and judgment standards;
- accepted variations, rejection criteria, and edge-case rulings;
- user feedback that changes evaluation or tuning interpretation;
- tuning decisions that should prevent repeated debate or drift.

## What Does Not Belong Here

Do not record information that is easy to recover from project artifacts:

- dataset path, row count, headers, field types, or inferred column roles;
- result version, latest run path, metrics snapshots, or routine execution logs;
- large failure lists that already live in `failure_cases.csv` or `report.md`;
- Agent command, runtime, or file paths unless the user explicitly turns them into a behavior standard;
- automatically generated statistics or observations that have not become user-confirmed tuning guidance.

Before writing, ask: "Is this a user-confirmed standard, feedback item, or tuning decision that future ATK steps should
preserve?" If not, leave it out.

## Skill Read/Write Contract

- `atk-build-dataset`: may read the local context as optional business and behavior guidance; should not write it.
- `atk-build-ground-truth`: reads `Ground Truth Standard` when present; may create or update only that section when the
  user confirms dataset-wide `ground_truth` semantics.
- `atk-tune-ground-truth`: reads `Ground Truth Standard`; updates it when review feedback changes the confirmed
  standard, and records the decision in `Tuning Decisions`.
- `atk-find-failures`: reads `Tuning Objective`, `Agent Behavior Standard`, and `Ground Truth Standard` before judging
  failures; should not write the local context.
- `atk-report`: reads the whole document when present; may append durable `User Feedback` or `Tuning Decisions` only
  when the report conclusion reflects user-confirmed or human-reviewed guidance rather than routine run facts.
- `atk-tune`: reads `Tuning Objective`, `Agent Behavior Standard`, `Ground Truth Standard`, and `Tuning Decisions`
  before editing the Agent; should not modify `Ground Truth Standard`.

The file is optional. Missing `.atk/context.md` must never block a Skill.
