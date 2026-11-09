"""Validate and resolve Issue segments against retained session nodes."""

from __future__ import annotations

from collections import defaultdict
from math import inf

from agent_tune_kit.errors import UserActionRequired

from . import query
from .store import validate_ref


def _node_pointer(pointer):
    if not isinstance(pointer, str) or not pointer:
        return "", ""
    for prefix in ("/trace/observations/", "/data/"):
        if pointer.startswith(prefix):
            rest = pointer[len(prefix) :]
            index, _, field = rest.partition("/")
            if index.isdigit():
                return f"{prefix}{index}", f"/{field}" if field else ""
    if pointer == "/trace" or pointer.startswith("/trace/"):
        return "/trace", pointer[len("/trace") :] or ""
    return pointer, ""


def _locate(nodes_by_ref, ref):
    path = ref.get("path")
    pointer = ref.get("pointer", "")
    line = ref.get("line")
    if line is not None:
        exact = nodes_by_ref.get((path, "", line))
        if exact is not None:
            return exact, ""
    node_pointer, field = _node_pointer(pointer)
    target = node_pointer
    while True:
        match = nodes_by_ref.get((path, target, None))
        if match is not None:
            return match, field if target == node_pointer else pointer[len(target) :]
        if not target:
            return None, field
        if target == "/trace":
            target = ""
            continue
        target = target.rpartition("/")[0]


def _index_nodes(session_dir):
    nodes = query.nodes(session_dir)
    by_ref = {}
    by_identity = {}
    children = defaultdict(list)
    for index, node in enumerate(nodes):
        ref = node.get("reference") or {}
        pointer = ref.get("pointer", "")
        node_pointer, _ = _node_pointer(pointer)
        key = (ref.get("path"), node_pointer or pointer, ref.get("line"))
        by_ref[key] = index
        identity = node.get("id")
        if isinstance(identity, (str, int)):
            slot = (node.get("trace_id"), identity)
            by_identity[slot] = index if slot not in by_identity else None
        at = query.timestamp_ms(node.get("start_time"))
        until = query.timestamp_ms(node.get("end_time"))
        if until is None or at is None or until < at:
            until = at
        node["at"] = at
        node["until"] = until
    for index, node in enumerate(nodes):
        parent = node.get("parent_id")
        if not isinstance(parent, (str, int)):
            continue
        parent_index = by_identity.get((node.get("trace_id"), parent))
        if parent_index is not None:
            children[parent_index].append(index)
    order = sorted(
        range(len(nodes)),
        key=lambda i: (
            nodes[i]["at"] is None,
            nodes[i]["at"] if nodes[i]["at"] is not None else inf,
            str(nodes[i].get("trace_id") or ""),
            i,
        ),
    )
    return nodes, by_ref, children, order


def _descendants(children, root):
    seen = set()
    stack = [root]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(children.get(current, ()))
    return seen


def _trace_root(nodes, index):
    trace_id = nodes[index].get("trace_id")
    path = (nodes[index].get("reference") or {}).get("path")
    for i, node in enumerate(nodes):
        ref_path = (node.get("reference") or {}).get("path")
        if node.get("type") == "TRACE" and node.get("trace_id") == trace_id and ref_path == path:
            return i
    return None


def _ancestors(nodes, children, index):
    parents = {}
    for parent, kids in children.items():
        for child in kids:
            parents[child] = parent
    chain = []
    seen = set()
    cursor = index
    while cursor is not None and cursor not in seen:
        seen.add(cursor)
        chain.append(cursor)
        cursor = parents.get(cursor)
    root = _trace_root(nodes, index)
    if root is not None and root not in seen:
        chain.append(root)
    return chain


def _coverage(nodes, indices):
    ordered = sorted(indices)
    timed = [i for i in ordered if nodes[i]["at"] is not None]
    start = min((nodes[i]["at"] for i in timed), default=None)
    end = max((nodes[i]["until"] for i in timed), default=None)
    first = min(timed, key=lambda i: nodes[i]["at"]) if timed else None
    last = max(timed, key=lambda i: nodes[i]["until"]) if timed else None
    return {
        "node_count": len(indices),
        "node_indexes": ordered,
        "trace_id": nodes[ordered[0]]["trace_id"] if ordered else None,
        "start_time": None if first is None else nodes[first]["start_time"],
        "end_time": None if last is None else (nodes[last]["end_time"] or nodes[last]["start_time"]),
        "duration_ms": None if start is None or end is None else end - start,
    }


def _require_ref(session_dir, ref, label):
    if not isinstance(ref, dict):
        raise UserActionRequired(f"Segment {label} must be a retained evidence reference.")
    validate_ref(session_dir, ref)
    return ref


def resolve_segments(session_dir, segments, *, assign_ids=True):
    """Validate segment scopes, assign S1.. ids, and report covered nodes."""
    if segments is None:
        return []
    if not isinstance(segments, list):
        raise UserActionRequired("Issue segments must be a list.")
    nodes, by_ref, children, order = _index_nodes(session_dir)
    resolved = []
    for offset, raw in enumerate(segments, 1):
        if not isinstance(raw, dict):
            raise UserActionRequired(f"Segment {offset} must be an object.")
        summary = raw.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise UserActionRequired(f"Segment {offset} requires a nonempty summary.")
        scope = raw.get("scope")
        if not isinstance(scope, dict):
            raise UserActionRequired(f"Segment {offset} requires a scope object.")
        evidence = raw.get("evidence", [])
        if not isinstance(evidence, list) or not evidence:
            raise UserActionRequired(f"Segment {offset} requires at least one evidence reference.")
        for ref in evidence:
            _require_ref(session_dir, ref, f"{offset} evidence")

        if "node" in scope:
            if set(scope) - {"node"}:
                raise UserActionRequired(f"Segment {offset} node scope cannot mix other keys.")
            endpoint = _require_ref(session_dir, scope["node"], f"{offset} node")
            located, _ = _locate(by_ref, endpoint)
            if located is None:
                raise UserActionRequired(f"Segment {offset} node is not a retained Trace observation.")
            if nodes[located].get("trace_id") is None:
                raise UserActionRequired(f"Segment {offset} node has no Trace identity.")
            covered = _descendants(children, located)
            kind = "node"
            scope_out = {"node": endpoint}
        elif "from" in scope or "to" in scope:
            if set(scope) != {"from", "to"}:
                raise UserActionRequired(f"Segment {offset} range scope requires both from and to.")
            start_ref = _require_ref(session_dir, scope["from"], f"{offset} from")
            end_ref = _require_ref(session_dir, scope["to"], f"{offset} to")
            start_i, _ = _locate(by_ref, start_ref)
            end_i, _ = _locate(by_ref, end_ref)
            if start_i is None or end_i is None:
                raise UserActionRequired(f"Segment {offset} from/to must resolve to retained observations.")
            if nodes[start_i].get("trace_id") != nodes[end_i].get("trace_id") or nodes[start_i].get("trace_id") is None:
                raise UserActionRequired(f"Segment {offset} from/to must belong to the same Trace.")
            start_at, end_at = nodes[start_i]["at"], nodes[end_i]["at"]
            if start_at is not None and end_at is not None and start_at > end_at:
                raise UserActionRequired(f"Segment {offset} from is later than to.")
            if start_at is None or end_at is None:
                rank = {index: position for position, index in enumerate(order)}
                if rank[start_i] > rank[end_i]:
                    raise UserActionRequired(f"Segment {offset} from is later than to.")
                low, high = rank[start_i], rank[end_i]
                covered = {
                    i for i in order[low : high + 1] if nodes[i].get("trace_id") == nodes[start_i].get("trace_id")
                }
            else:
                lo, hi = start_at, end_at
                covered = {
                    i
                    for i, node in enumerate(nodes)
                    if node.get("trace_id") == nodes[start_i].get("trace_id")
                    and node["at"] is not None
                    and lo <= node["at"] <= hi
                }
                covered.update({start_i, end_i})
            kind = "range"
            scope_out = {"from": start_ref, "to": end_ref}
        else:
            raise UserActionRequired(f"Segment {offset} scope must use node or from/to.")

        allowed = set(covered)
        for index in list(covered):
            allowed.update(_ancestors(nodes, children, index))
        for pos, ref in enumerate(evidence, 1):
            located, _ = _locate(by_ref, ref)
            if located is None or located not in allowed:
                raise UserActionRequired(
                    f"Segment {offset} evidence {pos} must resolve to a node inside the scope, an ancestor, or the Trace root."
                )

        supplied = raw.get("id")
        if supplied is not None and (not isinstance(supplied, str) or not supplied.strip()):
            raise UserActionRequired(f"Segment {offset} id must be a nonempty string when provided.")
        segment_id = supplied.strip() if isinstance(supplied, str) else (f"S{offset}" if assign_ids else None)
        resolved.append(
            {
                "id": segment_id,
                "summary": summary.strip(),
                "scope": scope_out,
                "kind": kind,
                "evidence": evidence,
                "coverage": _coverage(nodes, covered),
            }
        )
    if assign_ids:
        ids = [item["id"] for item in resolved]
        if len(ids) != len(set(ids)):
            raise UserActionRequired("Segment ids must be unique within an Issue.")
    return resolved


def segments_for_storage(resolved):
    """Drop derived coverage before writing issue.json."""
    return [
        {
            "id": item["id"],
            "summary": item["summary"],
            "scope": item["scope"],
            "evidence": item["evidence"],
        }
        for item in resolved
    ]
