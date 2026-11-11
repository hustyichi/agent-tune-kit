# Agent Tune Kit

English | [简体中文](README.md)

[![PyPI](https://img.shields.io/pypi/v/agent-tune-kit.svg)](https://pypi.org/project/agent-tune-kit/)

Agent Tune Kit is a **local Codex plugin** for tuning an Agent's prompts, code, parameters, or tool configuration. Install it once and use either workflow independently.

- [Evaluation-based tuning](#evaluation-based-tuning) starts with a set of samples and criteria. Run the Agent in batches, compare rounds, and check for regressions.
- [Continuous tuning from session traces](#continuous-tuning-from-session-traces) starts with an existing session. Follow its execution trace to identify issues, then fix and verify them individually.

## Install and Enable

Run these commands in your terminal to install ATK and its bundled Skills.

```sh
uv tool install agent-tune-kit
atk install
```

In Codex, enter `/plugins`, select and enable `Agent Tune Kit`, then restart Codex and open a new session.

<details>
<summary>Install with pipx or from local source</summary>

To install with pipx, run the following commands.

```sh
pipx install agent-tune-kit
atk install
```

To install from local source, update your chosen branch and run these commands from the **ATK repository root**. Rerun them after source updates, including when the version stays the same.

```sh
uv tool install --reinstall .
atk install
```

</details>

Finish active ATK tasks before reinstalling. `atk install` directly updates and verifies the Codex ATK cache, so no cache rebuild is needed afterward. Start a new session to replace instructions already read into a conversation; restart Codex if the skill list has not refreshed. If you have installation problems, run `atk status` and confirm the plugin is enabled in Codex. [Installation and cache notes](docs/skill-template-pack-usage.md#local-plugin-install-and-smoke)

## Evaluation-based tuning

![Evaluation-based tuning architecture showing data preparation and Agent setup, batch evaluation, diagnosis, cross-round reporting, targeted tuning, and reruns.](docs/assets/agent_tune_kit_evaluation.png)

Use a set of evaluation samples to find Agent failures and verify changes in later rounds. Prepare your Agent's entry point and a set of samples, preferably in CSV format. Five to twenty samples are enough to get started. Add expected answers, labels, or acceptance criteria where available.

Open your Agent project in Codex and send the following `$atk-…` instructions in the conversation.

### 1. Prepare Data as Needed

If you already have suitable data, go straight to connecting your Agent. To organize data or fill gaps in the evaluation criteria, send the following instruction.

```text
$atk-dataset Prepare data/eval.csv for evaluation. Preserve existing fields and answers. Fill gaps in the judgment criteria where the evidence supports an answer.
```

You can also start with business examples or request a correction, such as changing the expected result for atk_id 7. Keep your existing business column names. Answers without trustworthy support stay blank.

Use `$atk-visualize-dataset` when you want a local page for browsing and reviewing data, then give feedback to `$atk-dataset`.

### 2. Connect Your Agent

Tell Codex where to find your Agent entry point and evaluation data.

```text
$atk-init My Agent entry point is scripts/agent.py and the evaluation dataset is data/eval.csv
```

Codex reads the project, prepares the adapter, and organizes a runnable dataset. If your Agent is already connected through a compatible adapter, go straight to evaluation.

Evaluation appends five reserved columns called `agent_output`, `output_status`, `error_type`, `error_message`, and `log_path`. If evaluation reports a name collision, confirm a new name for the business column and update references in the adapter and judgment rules. [Reserved columns](docs/shared-versioning-and-confirmation.md#reserved-evaluation-output-columns)

### 3. Evaluate, Diagnose, and Read the Report

Send these instructions in order.

```text
$atk-eval
$atk-diagnose
$atk-report
```

| Instruction | What you get |
| --- | --- |
| `$atk-eval` | Actual Agent outputs and execution records for each sample |
| `$atk-diagnose` | Passed, failed, or unknown judgments with reasons and an initial investigation of failures |
| `$atk-report` | Main issues, cause analysis, and tuning recommendations |

Use `$atk-visualize-failures` for a local offline page to search, filter, and review individual failures.

Check judgment coverage when comparing pass results. Samples with insufficient evidence are marked unknown. Reported technical failures such as timeouts are counted separately and can be reviewed with `atk results inspect`.

### 4. Tune from the Results

```text
$atk-optimize
```

Codex uses the report and failures to edit your Agent. It records why it made changes and what to verify next. You can save your current code with Git before tuning so you can compare and restore it.

### 5. Rerun the Evaluation and Verify Changes

After tuning, you can first rerun the previous round's confirmed failures.

```text
$atk-eval --only-failures
$atk-diagnose
$atk-report
```

A new evaluation does not overwrite existing nonempty result directories. From the second round onward, when prior tuning notes exist and conditions are comparable, the report identifies target issues as resolved, partially resolved, unresolved, or impossible to judge.

To revisit unknown samples, technical failures, or other selected samples, use `$atk-eval --ids 7 9` separately. To check the effect on the full dataset, run `$atk-eval` without selection flags, then diagnose and generate the report.

### Where to Find Evaluation Outputs

Each round saves results under `.atk/results/vN/` in your Agent project. Files appear as you run the corresponding steps.

| File | What it shows |
| --- | --- |
| `eval_results.csv` | Actual outputs and execution status. Follow `log_path` to find a sample's log |
| `judgments.csv`, `failure_cases.csv` | Individual judgments and confirmed failures. Use `failure_cases.html` to browse failures |
| `report.md`, `tuning_plan.md` | Issues, changes, and verification targets for the round. Later reports revisit those targets when conditions are comparable |

[More batch evaluation options and outputs](docs/skill-template-pack-usage.md#manual-22--26-loop)

## Continuous tuning from session traces

![Trace tuning architecture showing session capture, issue detection, root-cause diagnosis, targeted fixes, and verification.](docs/assets/agent_tune_kit_trace.png)

Start with an existing session and use its execution trace to check whether the Agent completed the task. Save diagnoses, fixes, and verification results by issue. Get the native Session ID, its source, and access to the trace, then open the Agent project in Codex. You can start without an evaluation dataset or runner.

Run `atk trace --help` to check whether your CLI supports trace tuning. If the command is unavailable, follow the local source installation steps above with a version that supports trace tuning, then open a new Codex session.

### 1. Prepare the Session Trace

- **Langfuse v4**. Use `$atk-trace-init` to record project settings and configure read-only access on first setup. Configuration references credentials by environment-variable name. Then send `$atk-trace-capture Download session <session-id> from <start time> to <end time>`.
- **Local files**. Send `$atk-trace-capture Import traces for session <session-id> from <file path>, with source <source>`. You can also provide files with verifiable ties to the session during analysis and let detection import them. Analyze an already archived session directly.

The capture step downloads or imports data. Before saving, decide which content to retain and which fields to redact. ATK adds `.atk/traces/` to the project's `.gitignore`.

### 2. Analyze and Review Evidence

```text
$atk-trace-detect Check whether session <session-id> completed its task correctly. List issues, evidence, and what you did not inspect.
$atk-visualize-trace Open the execution lanes for session <session-id> and jump from issues to the relevant trace segments and evidence fields.
```

Detection retains issues with serious impact, recurrence, direct relevance to your stated focus, or a concrete implementation improvement lead, and explains the retention reason, evidence and inspection scope. Implementation leads include code, configuration, tool contracts, Prompt and Skill; an unresolved cause can proceed to diagnosis. Minor observations with none of these reasons are not saved separately. No retained issues does not prove the session was correct.

The capture, detect, diagnose, optimize and verify Skills generate or update `.atk/traces/<session_id>/preview.html` at stage end without opening a browser. Use `$atk-visualize-trace` when you want to open it; refresh an already open page after the file changes. Initialization has no session trace and does not generate an empty page.

These stages also refresh the project dashboard `.atk/traces/index.html`, which shows issues and actual progress across all sessions by session or by issue category and links back to each session page. To open it, send `$atk-visualize-trace Open the trace dashboard`. Issues recorded before categories existed show as uncategorized until their session is analyzed again.

Use Trace IDs to locate evidence within the session and open issue details to read the retention reason. Review any retained content in the HTML before sharing it.

### 3. Investigate a Session or Specific Issues, Then Fix and Verify as Needed

```text
$atk-trace-diagnose Investigate session <session-id> and propose repair and verification plans.
$atk-trace-diagnose Investigate ISSUE-001 in session <session-id> and propose a repair and verification plan.
$atk-trace-optimize Implement the selected plan for ISSUE-001 in session <session-id>.
$atk-trace-verify Verify the candidate for ISSUE-001 in session <session-id> and check relevant behavior that should still work.
```

Without specified issues, diagnosis reviews all available session traces and works through existing and newly found issues, prioritizing causes directly related to project code, configuration, tool contracts, Prompt or Skill. Findings established as unrelated to the existing implementation may be skipped with an explanation. Findings with uncertain relevance remain open for investigation. Specified issues or concrete symptoms limit the investigation scope. You can start directly with diagnosis without a separate detection invocation.

Diagnoses and verification conclusions are saved per issue. If the evidence cannot establish which candidate loaded or whether key verification conditions were met, the verification outcome is `inconclusive`. Investigation results identify unread ranges and evidence gaps. As new issues surface, work through them individually or start another analysis with a new session trace.

### Where to Find Trace Outputs

Each session archive is stored under `.atk/traces/<session_id>/` in your Agent project. Files appear as you run the corresponding steps. The project dashboard `index.html` and the category list `categories.json` sit directly under `.atk/traces/`.

| File or directory | What it shows |
| --- | --- |
| `session.json`, `raw/` | Session source, capture scope, inspection coverage, and retained traces |
| `preview.html` | Offline browse: issue rail, swimlanes, Event list; jump by Issue segments |
| `issues/<issue-id>/` | Issue evidence, diagnoses, and changes. Each verification's `result.json` records its status, conclusion, and evidence references |

[Trace tuning guide for source configuration, supported formats, and archive layout](docs/trace-tuning.md)

[Multi-project attribution and filtering details (Chinese)](docs/trace-project-attribution-design.md)

Use `atk trace project` to register workspace projects and submodules. Record associated projects with reasonable causal explanations for human review, and filter them in the dashboard and session issue list. Associations do not require a completed or verified fix; reasons describe uncertainty, and repair details describe modification locations.
