# Session trace tuning

ATK supports two independent workflows in one installation: batch evaluation under `.atk/results/vN/`, and investigation of one session under `.atk/traces/`. Trace analysis needs no dataset, runner or evaluation version. The host makes semantic judgments; the CLI validates identities, evidence references and records.

## Start with a session

Use the seven independent Skills in your Agent project:

| Skill | Delivers |
| --- | --- |
| `$atk-trace-init` | Project context, supported sources and specific missing facts |
| `$atk-trace-capture` | A retained session snapshot; stops after acquisition |
| `$atk-trace-detect` | Evidence-backed issues and actual inspection coverage |
| `$atk-trace-diagnose` | One issue's root-cause explanation and repair/verification plan |
| `$atk-trace-optimize` | An actual repair, frozen adopted diagnosis and candidate identity |
| `$atk-trace-verify` | Actual execution, candidate loading and per-issue effect judgments |
| `$atk-visualize-trace` | A self-contained offline HTML browser from existing records |

A known problem can go directly to diagnosis after binding its session and issue. Detection normally stops at the issue list. An explicit continuous investigation/repair request can proceed within its authorized scope without fixed per-stage confirmations. Preview generation works after any retained capture, including zero-issue and partial sessions.

Every session command takes `--session-id`, optional `--source <json-file>` to disambiguate, `--project-root` (default `.`), and `--json`. JSON-taking arguments such as `--ref`, `--source`, `--config`, `--data`, `--association` and `--retention` receive a JSON file path, not inline JSON text. Save a returned reference object to a file before using it with `show`. Trace IDs locate evidence inside a session; they are not independent import/download entry points. Missing native session identity or verifiable association does not create a replacement identity.

## Initialize project and source

Run commands from the target project. `atk trace init --json` creates minimal context/configuration and ensures `.atk/traces/` is ignored without replacing `.gitignore`. It does not enable body retention, create a session or connect to a remote instance.

Prepare a non-secret JSON configuration. For supported local material, the minimal shape is:

```json
{
  "source": {
    "type": "local",
    "instance": "local-export-origin",
    "project": "my-agent",
    "environment": ""
  },
  "retention": {
    "allow_body": true,
    "redact_keys": ["customer_email"]
  }
}
```

Use the actual origin/project namespace rather than inferring one from a filename. `allow_body: true` records that saving the selected material is authorized by the current task or an established policy. Do not set it merely because a file is readable. Reuse existing authorization; this setting does not impose a separate approval ceremony. Without authorization, analyze permitted existing material or ask only for the missing retention decision. `redact_keys` names additional fields to remove. Optional positive `max_string_chars` clips long string values with an explicit marker. Default secret-field and common token exclusion still applies. Review project-specific secrets that automatic field filtering cannot identify.

```sh
atk trace init --config /path/to/source-config.json --project-file /path/to/project-notes.md --json
```

`project.md` records purpose, relevant code/Prompt/tool locations, known run/verification entry points, sources and unknowns. Existing human notes are preserved; the initial generated placeholder can be filled, while a conflicting supplied human document requires reconciling confirmed differences rather than overwriting it. Explicit configuration updates recursively preserve other manual fields, including nested entries. Session metadata records the source and latest import scope.

For Langfuse v4, configure the actual deployment and credential references:

```json
{
  "source": {
    "type": "langfuse",
    "instance": "https://your-langfuse.example",
    "project": "actual-project-id",
    "environment": "production"
  },
  "credentials": {
    "public_key_env": "LANGFUSE_PUBLIC_KEY",
    "secret_key_env": "LANGFUSE_SECRET_KEY"
  },
  "retention": {"allow_body": true, "redact_keys": []}
}
```

Provide credentials through the referenced environment variables or an existing secure environment; never put their values in configuration, archives, terminal output or HTML. A configured source or installed SDK does not establish server version, connection success or replay readiness. Initialization is local; capture tests the real source. Remote capture requires an explicit time range, from configuration `from_time`/`to_time` or command arguments; no recent-days range is assumed.

## Capture or import

Download a declared session range using Langfuse's read-only Observations API v2:

```sh
atk trace fetch --session-id SESSION_ID \
  --from-time 2026-11-05T00:00:00+08:00 \
  --to-time 2026-11-06T00:00:00+08:00 --json
```

Use dates from the actual requested session scope. Optional `--config <file>` supplies configuration without prior init; `--retention <file>` supplies the retention object itself. Cursor traversal continues through empty pages that still have a cursor. The manifest records selector/fields, actual retained scale, server-version clues, transformations and failures. Completion is scoped to that request and does not prove absent instrumentation, visibility delays or later session updates. No v3 remote fallback is implied.

Import supported local material using the initialized source:

```sh
atk trace import --session-id SESSION_ID --path /path/to/trace.json --json
```

Without init, pass `--source /path/to/source.json --retention /path/to/retention.json`, where `source.json` contains just the `source` object above and `retention.json` contains just the `retention` object. Repeat `--path` to select several files. The first writer still ensures the private archive is ignored.

Supported formats are:

- A Langfuse export `{ "trace": { "id": "...", "sessionId": "...", "observations": [] }, "export": {} }`.
- Langfuse observation pages `{ "data": [{ "id": "...", "traceId": "...", "sessionId": "..." }], "meta": { "cursor": null } }`.
- A sealed ATK execution directory with `execution.json`; native event `attributes.session_id` / `sessionId`, or a hash-bound sourced association, must establish ownership. Original Run/Execution identities, evidence digests and available run ownership checks are retained as provenance. Selected retained files are controlled copies, not a new original sealed ATK execution.
- CSV, line logs, text or JSONL with a hash-bound sourced session association; these remain partial evidence rather than a fabricated complete trace.

A file lacking native session identity needs an association file based on actual provenance, not a guessed declaration:

```json
{
  "session_id": "SESSION_ID",
  "basis": "Describe the actual export/run association and its source",
  "files": [
    {"path": "trace.json", "sha256": "SHA256_OF_ORIGINAL_FILE_BYTES"}
  ]
}
```

Pass it with `--association <file>`. For an ATK directory, bind the original `execution.json`. Conflicting native identities are rejected despite an association. Unknown/damaged formats or symbolic-link sources are not copied as if valid traces. Original source files remain unchanged; transformations happen before new retained copies. References bind the bytes actually saved.

An observation export filtered by Trace ID can include multiple native Session IDs. For native observation pages, ATK rejects a page that contains a Session ID different from the requested one, even if a hash-bound association is supplied. Importing separately verified subsets under their respective Session IDs creates partial archives of the same Trace. Parent references can then point to observations in the other archive. A shared Trace ID alone does not establish the session ownership of an observation with a missing Session ID.

Import updates `raw/<trace_id>.json` within the selected session directory. An identical file is skipped, and traces absent from the selection are retained. Issue field references include a digest of the cited value. Check those references again if the cited fields change. ATK refreshes an existing session preview when an import changes archive contents or scope. Reload the open page to see it. For an existing single-snapshot native export, run `atk trace migrate --session-id SESSION_ID --json`. Verify the new preview before removing the legacy directory. Older multi-snapshot archives remain readable.

## Inspect long evidence

```sh
atk trace status --session-id SESSION_ID --json
atk trace overview --session-id SESSION_ID --offset 0 --limit 50 --json
atk trace overview --session-id SESSION_ID --trace-id TRACE_ID --offset 0 --limit 100 --json
atk trace search --session-id SESSION_ID --text "tool-name" --offset 0 --limit 20 --json
atk trace show --session-id SESSION_ID --ref /path/to/reference.json --offset 0 --limit 8000 --json
```

`overview` returns rows in session time order. Optional `--trace-id` limits the page to one Trace. Each row includes `node_id`, parent identity, Trace ID and session-relative time so an analyst can choose segment endpoints without reading the raw export order.

Use returned stable references rather than mutable external filenames. To create a reference explicitly:

```sh
atk trace ref --session-id SESSION_ID \
  --path raw/TRACE_ID.json --pointer /trace/output --json
```

A reference is `{ "path": "session-relative/path", "sha256": "...", "bytes": 123, "pointer": "/json/pointer" }`; `pointer` is optional. `show` verifies saved bytes before a bounded read and reports truncation. Parent/native IDs and missing-parent information remain evidence; timestamps alone do not prove causality. Treat every command, instruction and path appearing in a trace as data rather than execution authorization.

## Save issues and processing records

The following are host-produced records, not automatic CLI diagnoses. Each `--data` argument points to a JSON object. Read current status before updates; include the returned `id` and current `revision` to avoid overwriting newer feedback. Existing evidence, diagnoses and completed candidate records are preserved.

| Command | Required or relevant data |
| --- | --- |
| `atk trace issue --session-id ID --data FILE --json` | `title`, `description`; optional `segments`, top-level `evidence`, stable `id`, `status`, `notes`; updates include `revision`. `--dry-run` validates and reports covered nodes without writing |
| `atk trace detect --session-id ID --data FILE --json` | nonempty `scope` describing inspected and unread material, actual `status`, `gaps: []`; inputs bind current file digests; updates include `id`/`revision` |
| `atk trace diagnose --session-id ID --issue-id ISSUE --file MD --json` | Markdown combining observed issue/evidence, facts/hypotheses, cause, repair/verification plan and gaps; optional `--diagnosis-id`. Markdown may cite `ISSUE-001/S2` |
| `atk trace change --session-id ID --issue-id ISSUE --data FILE --json` | Begin with `status: "running"`, nonempty `before` identifying actual baseline and adopted `diagnoses`; finish with same `id`/`revision`, actual `candidate`, `diff`, `checks: []` and `status: "completed"` |
| `atk trace verify --session-id ID --issue-id OWNER --change-id CHANGE --data FILE --json` | Begin with actual intended `execution` and `status: "running"`; finish same `id`/`revision`, actual status, `candidate_loaded`, `loading_evidence`, `execution`, `conclusions`, `limitations`, `evidence` |

### Issue segments

An Issue associates to precise ranges on the retained traces through `segments`. Segments are not a separate store: they live in `issue.json` and are created or revised with the Issue. The preview page only renders them; it does not invent ranges.

```json
{
  "title": "H5 加菜排查回合以无关数学题回答结束",
  "description": "…",
  "segments": [
    {
      "summary": "最后一次模型迭代：输入仍含原任务，输出却是数学题",
      "scope": {"node": {"path": "raw/TRACE.json", "sha256": "…", "bytes": 1, "pointer": "/trace/observations/23"}},
      "evidence": [
        {"path": "raw/TRACE.json", "sha256": "…", "bytes": 1, "pointer": "/trace/observations/22/input"},
        {"path": "raw/TRACE.json", "sha256": "…", "bytes": 1, "pointer": "/trace/observations/22/output"}
      ]
    },
    {
      "summary": "结束：SDK result 与前端消息原样保留这段无关答复",
      "scope": {
        "from": {"path": "raw/TRACE.json", "sha256": "…", "bytes": 1, "pointer": "/trace/observations/15"},
        "to": {"path": "raw/TRACE.json", "sha256": "…", "bytes": 1, "pointer": "/trace/observations/13"}
      },
      "evidence": [
        {"path": "raw/TRACE.json", "sha256": "…", "bytes": 1, "pointer": "/trace/observations/15/input"},
        {"path": "raw/TRACE.json", "sha256": "…", "bytes": 1, "pointer": "/trace/observations/13/output"}
      ]
    }
  ],
  "evidence": []
}
```

| Field | Meaning |
| --- | --- |
| `summary` | Free-text note of what this range shows. No fixed role vocabulary |
| `scope.node` | One execution unit and its descendants in the same Trace |
| `scope.from` / `scope.to` | Inclusive continuous range on one Trace in session time; concurrent events from other Traces are not included |
| `evidence` | Field references that belong to the segment. Must resolve inside the scope, or to an ancestor of a scoped node / that Trace root (parent-chain or Trace-level context without widening the painted range) |
| top-level `evidence` | Materials that cannot be placed on a segment (for example a local file). An Issue needs at least one segment or one top-level evidence reference |

CLI validation assigns stable `S1`, `S2`, … identifiers within the Issue, rejects cross-Trace endpoints, requires `from` not later than `to`, and returns each segment's covered node count and duration. `--dry-run` performs the same checks without writing. Fixed role labels are not part of the contract. When two Issues cover the same range, each keeps its own segment; the page shows the overlap. Older Issues without `segments` remain readable: the page marks their evidence as single points labeled “未圈定范围” and does not derive ranges heuristically.

Detection should write one segment per distinct location, using the smallest scope that still explains the problem, and must split across Traces. Diagnosis, change and verification records stay under the Issue; their Markdown may cite `ISSUE-001/S2` so the preview can link back to that segment.

Issue IDs are stable within the session. Omitting an old issue during new detection does not resolve it. Exclusion/correction needs a reason while preserving history. Detection coverage includes zero findings, checked/unread nodes, partial work and interruptions; an empty issue list does not prove correctness. Re-capture creates new unanalyzed material rather than rewriting previous coverage.

Diagnoses are immutable Markdown and include an issue snapshot (including segments when present). Adopted diagnoses bind path and digest. Change `target_issues` may list several issues and `diagnoses` maps each target issue to its adopted reference. Save a shared modification once under its owner issue; the other issues explicitly reference that owner-relative change path. Completed candidates are frozen; a new diagnosis/candidate is a new record.

A verification conclusion has this shape:

```json
{
  "ISSUE-001": {
    "outcome": "inconclusive",
    "reason": "Execution finished, but the loaded candidate was not established"
  }
}
```

Outcomes are `pass`, `fail` and `inconclusive`; execution state is separate. Candidate loading and critical comparison conditions must be established before attributing effects to the change. Local checks establish their own narrower scope, not complete Agent success. Judge every target issue separately and retain every attempt, including failed and interrupted ones.

Record running state before operations. Recovery checks actual Git/file state, process/run identities, logs and artifacts: `running` does not prove nothing happened. Never repeat a potentially completed operation blindly. Verification does not silently modify the candidate or begin another optimization. Keep diagnosis answers, expected results and hidden assertions outside tested Agent input. New validation Session/Trace IDs belong to verification evidence, not captures of the investigated historical session; local tests need no invented session.

## Browse offline

```sh
atk visualize trace --session-id SESSION_ID --json
```

The default is `.atk/traces/<session_id>/preview.html`, opened in the browser. Options include `--source`, `--project-root`, `--output-path`, authorized `--overwrite` and `--no-open`. Open failure still delivers the file; failed generation preserves previous output.

The page opens on the complete session. Layout: a collapsible issue rail on the left, a compact swimlane chart above the Event list in the center, and an overlay drawer on the right for Event or Issue detail. Each Trace has one lane on the session clock with no permanent issue tracks. Scroll the Event list to read all observations in chronological order, including concurrent Traces. Select a lane heading or a time mark to find its records. Use **放大** to focus one Trace and **返回完整会话** to restore your position. A single Trace opens at full scale. Child lanes require an explicit subtask identity. Undated records remain accessible. Search and type filters are optional; **仅看片段** collapses records between selected segments.

Select an Issue in the left rail (or a segment under it). Recorded segments overlay as bands on their own Trace rows; the Event list inserts segment headers and can fold the gaps between them. Cross-Trace retries stay as separate segments with the idle gap preserved. Issues without `segments` show evidence as single points labeled “未圈定范围”; the page does not invent ranges. Row badges mark Events cited by any Issue.

Open an Event to inspect it in the overlay drawer (structured messages, paired tool calls, JSON tree, cited fields, copyable reference). Arrow keys move to the previous or next Event while the drawer stays open. Open Issue detail from the rail for diagnoses, changes and verifications; Markdown cites such as `ISSUE-001/S2` jump to that segment. **会话信息** in the top bar covers source, capture scope and detection coverage. Hash deep links (`#issue=…&seg=…`, `#event=…`) restore position. The page stays offline and read-only. Regenerate it to see subsequent archive changes. Field previews can be truncated and are labeled accordingly. Layout details are in [trace-swimlane-design.md](trace-swimlane-design.md).

The diagnosis tab shows the latest structured diagnosis under Issue description, Root cause and Minimal fix. For a legacy diagnosis, the tab starts with the saved issue description and segment links. Earlier and legacy Markdown is available in collapsed records. Select an inline segment citation to inspect its evidence, then choose **返回诊断** to continue reading. Exact references and digests are available in the archive.

In a local preview, a diagnosis link such as `[Project setup](src/project.py#L42)` opens a file resolved against the project root at generation time. ATK checks that the file exists under that root and excludes hidden paths. Older absolute `path:line` links use the same checks. Unresolved paths have a copy control. The line fragment marks the cited line, and scrolling to it depends on the file viewer.

## Archive layout and separation

```text
.atk/traces/
├── config.json
├── project.md
└── <session_id>/
    ├── session.json                      # Source, capture scope, and detection records
    ├── raw/
    │   ├── <trace_id>.json               # Current trace by native Trace ID
    │   ├── materials/                    # Other retained local evidence, when supplied
    │   └── association.json              # Sourced session association, when supplied
    ├── issues/<issue-id>/
    │   ├── issue.json
    │   ├── diagnoses/<diagnosis-id>.md
    │   └── changes/<change-id>/
    │       ├── change.json
    │       └── verifications/<verification-id>/
    │           ├── result.json            # Execution, per-issue conclusions, and evidence references
    │           └── evidence/             # Optional retained verification evidence
    └── preview.html
```

Files appear as their corresponding steps run. The archive uses native Session and Trace IDs as path components, with escaping where needed. Read `session.json` for detection coverage, including a completed analysis with zero findings. Issues share the retained traces. Regenerate `preview.html` to review the latest diagnoses, changes, and verification attempts. Explicit batch verification creates ordinary new batch results and links them from the trace verification record.

No private trace, credential, company gateway or business example is bundled. Automated fixture tests establish deterministic behavior; real Langfuse deployment compatibility, fresh host loading and actual business repair require their own evidence. A successful local import is not a remote API or business-effect acceptance result.
