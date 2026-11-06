"""Read-only Langfuse v4 Observations API v2; no SDK or company-specific gateway."""

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.client import HTTPException

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.results.evidence import json_bytes, now

from . import archive, store
from .capture import load_config, retain, retention_policy, validate_document

FIELDS = "core,basic,io,metadata,trace_context,model,usage,time"
ENDPOINT = "/api/public/v2/observations"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _instance(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError
        return value.rstrip("/")
    except (TypeError, ValueError):
        raise UserActionRequired(
            "Langfuse instance must be an HTTPS base URL without credentials, query or fragment (localhost HTTP is allowed)."
        ) from None


def _time_bounds(from_time, to_time):
    try:
        start, end = (datetime.fromisoformat(value.replace("Z", "+00:00")) for value in (from_time, to_time))
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise UserActionRequired(
            "Provide explicit from_time and to_time with timezone offsets; no recent-days window is assumed."
        ) from None


def _credentials(config):
    refs = config.get("credentials", {})
    if not isinstance(refs, dict):
        raise UserActionRequired("Langfuse credentials must reference environment variable names.")
    values = []
    for name in ("public_key_env", "secret_key_env"):
        key = refs.get(name)
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise UserActionRequired(f"Configure credentials.{name} with an environment variable name.")
        value = os.environ.get(key)
        if not value:
            raise UserActionRequired(f"Langfuse credential environment reference {name} is unavailable.")
        values.append(value)
    return tuple(values)


def _get_page(instance, params, credentials):
    auth = base64.b64encode(":".join(credentials).encode()).decode("ascii")
    request = urllib.request.Request(
        instance + ENDPOINT + "?" + urllib.parse.urlencode(params),
        headers={"Authorization": "Basic " + auth, "Accept": "application/json"},
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirect())
    for attempt in range(3):
        try:
            with opener.open(request, timeout=30) as response:
                raw = response.read()
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(0.25 * (attempt + 1))
                continue
            raise UserActionRequired(
                f"Langfuse read failed with HTTP {code}; response body and credentials were omitted."
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError, HTTPException):
            if attempt < 2:
                time.sleep(0.25 * (attempt + 1))
                continue
            raise UserActionRequired(
                "Langfuse read failed after bounded retries; connection details were omitted."
            ) from None
        except (ValueError, UnicodeError):
            raise UserActionRequired("Langfuse returned invalid JSON; response body was not retained.") from None
    raise AssertionError("unreachable")


def _prepare(config, session_id, from_time, to_time):
    if not isinstance(session_id, str) or not session_id.strip():
        raise UserActionRequired("An explicit Session ID is required; Trace ID alone cannot start capture.")
    if not isinstance(config, dict) or not isinstance(config.get("source"), dict):
        raise UserActionRequired("Configure a Langfuse source before downloading a session.")
    source = store.normalize_source(config["source"])
    if source.get("type") != "langfuse":
        raise UserActionRequired("Remote capture supports the Langfuse v4 source only.")
    source["instance"] = _instance(source.get("instance"))
    if not source.get("project"):
        raise UserActionRequired("Configure the Langfuse project identity associated with the read-only credentials.")
    from_time = from_time or config.get("from_time")
    to_time = to_time or config.get("to_time")
    _time_bounds(from_time, to_time)
    credentials = _credentials(config)
    version = config.get("server_version")
    if version is not None and not isinstance(version, str):
        raise UserActionRequired("Langfuse server_version must be a version hint string when supplied.")
    version, _ = retain(version, {}, secret_values=credentials)
    params = {
        "sessionId": session_id,
        "fromStartTime": from_time,
        "toStartTime": to_time,
        "fields": FIELDS,
        "limit": 1000,
    }
    if source.get("environment"):
        params["environment"] = source["environment"]
    scope = {
        "kind": "langfuse-v4-download",
        "source": source,
        "endpoint": ENDPOINT,
        "parameters": params.copy(),
        "requested_at": now(),
        "api_version": "observations-v2",
        "server_version": version,
        "sdk_version": None,
    }
    return source, credentials, params, scope


def _download(source, credentials, params, scope, policy):
    files, seen, cursors, traces, changes, errors = {}, {}, set(), set(), set(), []
    page_number, finished, interrupted = 0, False, False
    while True:
        try:
            page = _get_page(source["instance"], params, credentials)
            details = validate_document(page, params["sessionId"], source=source)
            if not isinstance(page.get("meta"), dict):
                raise UserActionRequired("Langfuse observation page is missing pagination metadata.")
            cursor = page["meta"].get("cursor")
            if cursor is not None and (not isinstance(cursor, str) or not cursor):
                raise UserActionRequired("Langfuse returned an invalid pagination cursor.")
            # Validate conflicts before accepting a whole page; do not retain mixed-session rows.
            for row in page["data"]:
                identity = (row["traceId"], row["id"])
                if identity in seen and seen[identity] != row:
                    raise UserActionRequired("Langfuse returned conflicting duplicate observation rows.")
            retained, redactions = retain(page, policy, secret_values=credentials)
            page_number += 1
            files[f"pages/{page_number:06d}.json"] = json_bytes(retained)
            changes.update(redactions)
            for row in page["data"]:
                seen[row["traceId"], row["id"]] = row
            traces.update(details["trace_ids"])
            if cursor is None:
                finished = True
                break
            if cursor in cursors:
                raise UserActionRequired("Langfuse pagination repeated a cursor; partial material was preserved.")
            cursors.add(cursor)
            params["cursor"] = cursor
        except KeyboardInterrupt:
            interrupted = True
            errors.append("Capture interrupted; already retained pages were preserved.")
            break
        except UserActionRequired as exc:
            errors.append(str(exc))
            break
    gaps = [
        "Coverage is limited to the declared time range and requested fields; ingestion delays or missing spans may remain."
    ]
    if not seen:
        gaps.append("No visible observations were returned; this does not prove an empty or complete session.")
    if changes:
        gaps.append("Saved material was transformed by the declared retention policy.")
    if not finished:
        gaps.append("Pagination did not complete.")
    completeness = {
        "status": "declared-range" if finished and seen else "empty" if finished else "partial",
        "pagination_completed": finished,
        "trace_count": len(traces),
        "observation_count": len(seen),
        "trace_ids": sorted(traces),
        "page_count": page_number,
        "gaps": gaps,
        "transformations": sorted(changes),
    }
    return {
        "files": files,
        "scope": scope,
        "completeness": completeness,
        "errors": errors,
        "status": "interrupted" if interrupted else "completed" if finished else "partial" if files else "failed",
        "source": source,
    }


def download_session(config, session_id, *, from_time=None, to_time=None, retention=None):
    """Return retained material in memory for verification evidence, without creating another archive."""
    if not isinstance(config, dict):
        raise UserActionRequired("Configure a Langfuse source before downloading a session.")
    policy = retention_policy(".", retention if retention is not None else config.get("retention"))
    source, credentials, params, scope = _prepare(config, session_id, from_time, to_time)
    return _download(source, credentials, params, scope, policy)


def fetch_session(project_root, session_id, config=None, *, from_time=None, to_time=None, retention=None):
    config = load_config(project_root) if config is None else config
    if not isinstance(config, dict):
        raise UserActionRequired("Configure a Langfuse source before downloading a session.")
    policy = retention_policy(project_root, retention if retention is not None else config.get("retention"))
    source, credentials, params, scope = _prepare(config, session_id, from_time, to_time)
    downloaded = _download(source, credentials, params, scope, policy)
    result = archive.save(
        project_root,
        session_id,
        source,
        downloaded["files"],
        scope=scope,
        completeness=downloaded["completeness"],
        status=downloaded["status"],
        errors=downloaded["errors"],
    )
    result.update(
        trace_count=downloaded["completeness"]["trace_count"],
        observation_count=downloaded["completeness"]["observation_count"],
    )
    return result
