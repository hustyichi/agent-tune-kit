# Template: .atk/runner/eval_runner.py

Generate this project adapter after inspecting Agent invocation, input fields, runtime and logs.
Preserves all original dataset columns through the shared CLI engine. The engine reads
`.atk/datasets/dataset.csv`, requires `atk_id`, appends `agent_output` and `log_path`, and
writes `.atk/results/vN/eval_results.csv` plus independent `executions/<id>/` evidence.
Version selection, retention, observation and sealing are implemented in ATK.
Managed V1/V2 reserve `agent_output`, `output_status`, `error_type`, `error_message`, and `log_path` for output.
Evaluation and `--check` reject source-column collisions before Agent calls or version allocation. Confirm explicit
business-field renames and update adapter/rule references; do not implement automatic renaming in this adapter.
Direct execution remains supported with `--limit`, `--offset`, `--concurrency`,
`--only-failures` and `--no-progress`. Replace all TODO_AGENT_TUNING blocks.

```python
#!/usr/bin/env python3
"""Project-specific Agent adapter; fixed execution behavior belongs to ATK."""
import logging
import shutil
import subprocess
import sys
from pathlib import Path

ATK_MANAGED_RUNNER = True
ATK_ADAPTER_VERSION = 2
REPO_ROOT = Path(__file__).resolve().parents[2]

if __name__ == "__main__":
    atk = shutil.which("atk")
    if not atk:
        print("Install ATK first: uv tool install agent-tune-kit; atk install", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(subprocess.call([atk, "eval", "--python", sys.executable, *sys.argv[1:]]))
else:
    from agent_tune_kit.errors import AgentExecutionError, UserActionRequired

# TODO_AGENT_TUNING_IMPORT_PATHS: retain project-specific imports and runtime setup.
for root in (REPO_ROOT / "src", REPO_ROOT):
    if root.is_dir():
        sys.path.insert(0, str(root))


def build_agent_input(row):
    # TODO_AGENT_TUNING: exclude answer fields and ATK metadata from Agent inputs.
    raise UserActionRequired("TODO_AGENT_TUNING: implement build_agent_input(row).")


def call_agent(agent_input, *, execution):
    # TODO_AGENT_TUNING: implement the project's sync or async Agent invocation.
    # Raise AgentExecutionError only for known row failures; propagate setup errors.
    raise UserActionRequired("TODO_AGENT_TUNING: implement call_agent(agent_input).")


def retain(role, value):
    # TODO_AGENT_TUNING: approve or redact each role; never silently retain unknown secrets.
    raise UserActionRequired("TODO_AGENT_TUNING: declare safe evidence retention.")


EVIDENCE = {
    "name": "project-retention-v1",
    "retain": retain,
    "logging": [],  # Explicit source logger names; ATK does not raise their levels.
    "loguru": False,  # Optional dependency from the project interpreter.
    "snapshots": [],  # Project-relative source, Prompt and effective configuration paths.
    "sources": [],  # Source, scope, filtering and closure limitations; unknown stays partial.
}
```

Keep module imports and `build_agent_input` free of business side effects; initialize resources inside
`call_agent`. Exclude answers and ATK metadata from actual Agent inputs. `atk_truth` is removed by ATK,
but project-specific answer columns still require explicit input mapping and filesystem isolation.

Use `with execution.span("model", {"request": request}): ...`, `execution.event(kind, attributes)` and
`execution.artifact(role, value, media_type="application/json")` around existing public calls. Observe only
stream fragments the business actually consumes; do not add retries. Copy `contextvars.copy_context()` at
each internal thread submission, and join managed work before returning. Loguru capture uses its own synchronous
sink; third-party queues, hidden SDK retries, child processes and late background work remain explicit gaps.

For grouping, implement `plan_executions(selected_rows, all_rows)` returning dictionaries with `sample_ids`,
`source`, and optional `context_ids`. Implement `map_agent_output(raw_output, item)` returning
`{atk_id: {"output": complete_sample_output, "location": json_pointer}}` for exactly the selected IDs.
Extra Case inputs are frozen context, not scored rows. Preserve V1 adapters explicitly; do not silently replace them.

`optional_roles` may list nonessential artifact roles: rejection leaves an explicit gap. Input/output, scoring
fields and lifecycle events remain required. `max_bytes` is a shared Run content budget (metadata/index overhead
is additional). `allow_direct_artifacts=True` is only for project outputs already safe before writing; use
`execution.artifact_directory`, finish writes before returning, and ATK will register independent files.
Otherwise submit through `artifact`. For explicitly safe streaming files, `retain_chunks(role, chunks)` must
handle secrets across boundaries before yielding bytes. Source closure and SDK internals remain limitations;
successful sealing alone does not promote coverage to complete.
After actually closing a declared source, emit `execution.event("source.closed", {"source": name})`.
Only closed declared sources with no known capture gaps can become `complete_declared`; this records the
adapter's closure claim and never asserts completeness of unobserved SDK/network/background activity.
