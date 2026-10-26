# Template: explicit local evidence importer

This adapter is trusted Python executed by the installed ATK interpreter. Install any parser dependency
there or use stdlib. Never load code, execute commands, contact remote services or change files named by
the source manifest. Read only the explicit `source_root`; ATK independently checks file references.

```python
ATK_IMPORT_VERSION = 1


def retain(role, value):
    from agent_tune_kit.errors import UserActionRequired
    raise UserActionRequired("TODO_AGENT_TUNING: declare safe imported content and metadata.")


EVIDENCE = {"name": "project-import-v1", "retain": retain}


def load_evidence(source_root):
    from agent_tune_kit.errors import UserActionRequired
    raise UserActionRequired("TODO_AGENT_TUNING: parse the native run without business calls or source writes.")
```

Return a dictionary with `source_system`, stable `source_run_id`, and `executions`. Each execution includes
a stable `source_id`, optional `sample_ids`, `input`, `output`, source `status` (unknown when unavailable),
`coverage`, `files` and `events`. File entries contain a source-root-relative `path`, `role`, `media_type`
and optional `phase` (`before`, `after`, `unknown`). Optional run `context_files` are copied into each execution.
Event entries contain `kind`, `attributes`, optional original `occurred_at`, `span_id`, `parent_span_id`,
and `source` record locator such as `{"file": "events.jsonl", "line": 1}`. Preserve native IDs and unknown fields
inside retained attributes; do not infer calls, retries or actors from timestamps or reports.

For Magic-style material, describe filtered frontend events, missing trace, native session/turn/tool identities
and before/after file evidence explicitly. A completed source turn is independent of a failed business check.
An optional `results` list must contain unique `atk_id` and `agent_output` for exactly the mapped samples;
its presence declares evaluation purpose before persistence. Omit it for evidence-only imports.

`retain` may return transformed safe values/bytes or raise to reject. For large files, an explicit
`retain_chunks(role, chunks)` callback may yield safe bytes while handling cross-chunk secrets; absent that
callback, file retention uses the ordinary serializer. `max_bytes` bounds captured content across a Run;
required failures stop the import. Metadata and indexes consume additional space. Do not silently truncate.
Repeated identical source/material/adapter fingerprints reuse a verified sealed package. Changed material,
an incomplete prior import or later trace requires a separate future supplementation design; no implicit resume.
