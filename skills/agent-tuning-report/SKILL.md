---
name: agent-tuning-report
description: Generate a current-version Markdown report with abnormal analysis and adjacent-version tuning validation when possible.
---

# Agent Tuning — Report and Cross-Version Validation

## Purpose

Generate `agent-tuning/results/vN/report.md` for the current Agent tuning version. This Skill maps to `docs/codex_agent_tuning_prd.md` sections 2.5, 4, 5, 6, and 7.

The report includes current-version statistics, abnormal-case analysis, root-cause hypotheses, and—when there is a previous version with `tuning_plan.md`—adjacent-version validation of whether the previous tuning goals were achieved.

Traceability note: section 2.5 defines report and cross-version validation, section 4 defines version management, and section 7 defines delivery requirements.

## Inputs

- Current version directory resolved from `agent-tuning/results/vN`.
- Required current files:
  - `results.csv`
  - `abnormal_cases.csv`
- Optional current file: `app.log`.
- Previous version files, when available:
  - `tuning_plan.md`
  - `report.md`
  - `results.csv`
  - `abnormal_cases.csv`
  - optional `app.log`
- Shared rules in `docs/shared-versioning-and-confirmation.md`.

## Outputs

- Current `agent-tuning/results/vN/report.md`.

## Workflow

1. Resolve current version with `resolve_current_version()` using `RESULTS_DIR = Path("agent-tuning/results")`.
2. Require current files with `require_current_file(current_dir, "results.csv")` and `require_current_file(current_dir, "abnormal_cases.csv")`.
3. Read optional current `app.log` if present.
4. Resolve previous version with `resolve_previous_version(current_dir)`.
5. If no previous version exists, generate a single-version report and explain that no previous version can be compared.
6. If previous version exists but lacks `tuning_plan.md`, degrade to a single-version or lower-confidence report with explicit explanation.
7. If previous `tuning_plan.md` exists, extract targets from `## 目标异常清单`, compare them with current `results.csv` and `abnormal_cases.csv`, and classify each target as `已解决`, `部分解决`, `未解决`, or `无法判断`.
8. Write `report.md` in the current version directory.

## Required report structure

Include these sections:

```markdown
# Agent Tuning Report - <current version>

## 执行摘要
## 测试结果统计
## 异常数据清单
## 归因分析
## 跨版本调优验证
## 建议下一步
```

The `## 跨版本调优验证` section must include when applicable:

- 对比版本：当前版本、上一版本
- 上一轮调优计划摘要（来源：上一版本 `tuning_plan.md`）
- 上一轮目标异常逐条复核：`已解决` / `部分解决` / `未解决` / `无法判断`
- 新增问题
- 观察性指标变化（异常总数、比例等，可选）
- 验证结论：上一轮调优是否符合预期，是否建议继续下一轮调优

## Matching guidance

Prefer stable identifiers from the dataset, such as `case_id`, `id`, `query`, or natural task keys. If no obvious ID exists, match by input content, expected result, previous report descriptions, and target symptoms. Only ask the user when semantic matching is unreliable enough to change validation status.

The main validation basis is whether previous `tuning_plan.md` target abnormalities still appear in the current abnormal cases. Abnormal-count changes are useful observations but not the primary success criterion.

## Shared version rules

Use the canonical helper names and semantics from `docs/shared-versioning-and-confirmation.md`:

- `RESULTS_DIR = Path("agent-tuning/results")`
- `resolve_current_version(results_dir=RESULTS_DIR)`
- `resolve_previous_version(current_dir, results_dir=RESULTS_DIR)`
- `require_current_file(current_dir, filename)`

Do not ask the user for current version. Do not fall back to older versions when current required files are missing.

## Confirmation triggers

Ask before producing a cross-version judgment when:

- target abnormalities in previous `tuning_plan.md` cannot be reliably matched to current rows;
- expected-result columns are ambiguous and affect root-cause conclusions;
- previous artifacts are inconsistent or appear manually edited;
- overwriting `report.md` might discard user-edited analysis.

## Failure behavior

- Require current `results.csv` and `abnormal_cases.csv`; if missing, stop and tell the user to run testing and abnormal filtering first.
- `app.log` is optional; if absent, explain that log-based attribution is unavailable.
- If previous version lacks `tuning_plan.md` or sample matching is unreliable, degrade to single-version or lower-confidence report with explicit explanation, not silent failure.
- If a previous version exists but is missing optional comparison files, include the limitation and continue only where evidence supports it.

## Handoff message

After writing the report, summarize:

- current version and previous version used, if any;
- counts of total and abnormal cases;
- cross-version validation status distribution: `已解决` / `部分解决` / `未解决` / `无法判断`;
- output path `agent-tuning/results/vN/report.md`;
- whether the next step is `agent-tuning-apply-tuning`.
