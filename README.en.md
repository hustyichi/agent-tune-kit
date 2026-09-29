# Agent Tune Kit

[简体中文](https://github.com/hustyichi/agent-tune-kit/blob/1.0.0/README.md) | English

[![PyPI](https://img.shields.io/pypi/v/agent-tune-kit.svg)](https://pypi.org/project/agent-tune-kit/)

Agent Tune Kit (ATK) is a **local Codex plugin** for evaluating an existing Agent, investigating failures, and checking whether changes to prompts, business Skills, code, or configuration improve its behavior.

Describe your goal in Codex. The current session analyzes evidence and edits your project; ATK records results, compares revisions, and manages recoverable local Git checkpoints.

## When to use it

- You have a working Agent and want to understand which tasks fail and what to change.
- You have batch results or Langfuse exports and want to investigate without running the Agent again.
- You want to test a change while checking that previously working tasks still pass.

ATK connects to your existing Agent without requiring a framework rewrite. Each candidate change keeps its evidence and decision, so you can review why it was accepted or rejected.

## Install

You need local Codex, Python 3.11+, and `uv`. Install the persistent CLI, then let it install the matching Skills:

```sh
uv tool install agent-tune-kit
atk install
atk status
```

To upgrade the CLI, run `uv tool upgrade agent-tune-kit`. Running `atk install` afterward to update the Skills is recommended, but can be deferred.
If the shell cannot find `atk`, run `uv tool update-shell` and open a new terminal.

Then enter this in Codex:

```text
/plugins
```

Enable **Agent Tune Kit** and open your own Agent project. The `$atk-*` examples below are **Skill calls entered in a Codex conversation**, not shell commands. Use them in your Agent project, not the ATK source repository.

See [PyPI](https://pypi.org/project/agent-tune-kit/) for package versions and downloads.

> **Upgrading from 0.x:** Version 1.0.0 changes the Skills and data layout. It does not migrate or delete old `.atk/results/vN/` data. Initialization stops if it finds a legacy `.atk` directory. Back up and decide how to preserve that data, or use a clean target project; do not overwrite the old directory.

## First run: evaluate and improve an Agent

Prepare the Agent's entry point, a few representative tasks, and criteria for judging the results. Formal evaluation and tuning require a clean Git working tree. Agents that write to databases, send messages, or perform other external writes also need a verified test environment or safeguards.

Replace `scripts/agent.py` and `data/eval.csv` below with your actual paths. Run each step in Codex and wait for it to finish before continuing.

### 1. Connect the project and prepare tasks

```text
$atk-init My Agent entry point is scripts/agent.py. Investigate how it runs and connect it. Allow changes to prompts/ only; protect other business files.
$atk-dataset Build an evaluation dataset from data/eval.csv. Confirm inputs, expected results, and judging criteria. Include tasks that should remain unchanged to check for regressions.
```

Codex inspects dependencies, inputs and outputs, actual business Skill loading, and external side effects, then creates a local runner. Each task is a Case. Include both Cases you want to improve and already working Cases that protect against regressions.

You can investigate without reference answers, but a dimension needs reliable criteria before it can receive a definite pass or fail. Do not treat the Agent's original answer as ground truth.

### 2. Run a baseline and investigate

```text
$atk-eval Run a baseline evaluation after establishing this Round's criteria, repeat count, timeout, and budget.
$atk-diagnose Investigate the failure evidence. Distinguish Agent behavior, tool, runtime, and data issues, and identify the priority issue.
```

The baseline captures behavior before changes. ATK saves actual outputs, individual judgments, and supporting evidence. Infrastructure failures such as model authentication errors cannot serve as valid evidence of Agent performance.

### 3. Change, validate, and decide

```text
$atk-optimize Prepare one candidate for the confirmed priority issue, within the allowed paths.
$atk-validate Compare the candidate with its parent. Check improvement on target tasks and regressions on protection tasks.
$atk-decide Use the validation result to keep, reject, or defer this candidate.
```

Only one candidate can be pending at a time. Normal acceptance requires passing validation and creates a local commit in your project. Rejection or deferral preserves the evidence and restores the candidate's parent file contents.

Repeat these three steps for additional issues. Before closing the Round, compare **all accepted changes against the original baseline**:

```text
$atk-validate Run final validation of the cumulative version against the original baseline, covering the full frozen task set and repeat plan.
$atk-decide Close the Round based on final validation. List accepted changes, the final commit, and unresolved issues.
```

ATK does not automatically push, publish, or deploy.

## Already have results? Start with analysis

Import CSV, JSON, or JSONL batch results, or Langfuse JSON/JSONL/CSV/`.gz` exports, without configuring an Agent command or Git:

```text
$atk-init Use analysis_only mode for existing results. Do not run the Agent.
$atk-eval Import data/traces.json. Confirm its format, field mapping, and redaction rules, then assess it against explicit criteria.
$atk-diagnose Investigate failures and identify missing evidence and explanations that still need testing.
$atk-decide Save the analysis and close this Round without adopting changes.
```

Importing does not rerun the Agent. External scores remain evidence rather than automatically becoming ATK pass rates. You can add runtime configuration and a Git baseline later while preserving existing evidence. See the [usage guide](https://github.com/hustyichi/agent-tune-kit/blob/1.0.0/docs/vnext-usage-guide.md) (Chinese) for formats.

## Skill reference

| Skill | What you want to do |
| --- | --- |
| `$atk-init` | Connect an existing Agent or initialize an import-only analysis project |
| `$atk-dataset` | Organize tasks, expected results, attachments, and regression protection Cases |
| `$atk-eval` | Run evaluations, import results, or reassess evidence without rerunning the Agent |
| `$atk-diagnose` | Investigate failures and record issues and competing explanations |
| `$atk-optimize` | Prepare one scoped candidate for an issue |
| `$atk-validate` | Validate a candidate, cumulative changes, or an external component repair |
| `$atk-decide` | Keep, reject, defer, roll back accepted changes, or close the Round |

For an external tool or service defect, use `$atk-diagnose` to save a local handoff and verification requirements. After repair, open a linked Round and check both the component directly and the Agent end to end. An Agent-side workaround does not establish that the external defect is fixed.

## Where to find results

Artifacts live under `.atk/` in **your Agent project**:

| Location | Contents |
| --- | --- |
| `.atk/runtime.md` | Integration details, runtime conditions, and known limitations |
| `.atk/datasets/<id>/` | Immutable task snapshots |
| `.atk/evidence/<batch-id>/` | Actual or imported results, sources, and execution status |
| `.atk/assessments/<id>/assessment.csv` | Per-record, per-dimension judgments and evidence references |
| `.atk/rounds/<id>/` | Plans, issues, candidates, validations, and decisions |
| `.atk/knowledge/<id>/` | Lessons with applicability conditions and evidence |

Ask Codex to identify the files for your Round or generate a local HTML view from the assessment CSV. Reassessment creates a new Assessment and preserves previous judgments.

To stop midway, ask Codex to pause the Round and record why. Resume only after checking the workspace and runtime configuration. After an interruption, preserve the files and recover through the operation record instead of deleting `.atk/` or force-resetting Git.

## Further reading

- [Usage guide](https://github.com/hustyichi/agent-tune-kit/blob/1.0.0/docs/vnext-usage-guide.md) (Chinese): attachments, budgets, concurrency, import mappings, recovery, and external repairs.
- [Shared Skill workflow](https://github.com/hustyichi/agent-tune-kit/blob/1.0.0/skills/WORKFLOW.md): operation contracts and internal interfaces.
- [Implementation and acceptance record](https://github.com/hustyichi/agent-tune-kit/blob/1.0.0/docs/vnext-implementation-report.md) (Chinese): implemented scope, evidence, and open acceptance work.
- [Design](https://github.com/hustyichi/agent-tune-kit/blob/1.0.0/docs/agent-tune-kit-vnext-refactor-plan.md) (Chinese): data model and validation protocol.

**Current limitations:** Real multi-Case gains, valid candidate acceptance commits, independent responsibility-layer diagnosis, and real external repair workflows still await acceptance. Passing offline tests does not prove improvement in your application; use your project's comparison results.

## Source development

Install the plugin from this repository:

```sh
uv run --frozen atk install
```

Run development checks:

```sh
uv run --frozen pytest -q
uv run --frozen ruff check .
python3 scripts/validate_skill_pack.py
uv build --no-sources
```

`atk internal ...` is an interface used by the Skills. Each Skill passes its plugin root. If versions differ, the CLI warns and continues; run `atk install` when you want to update the Skills.
