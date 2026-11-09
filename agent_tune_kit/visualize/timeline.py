"""Derive execution lanes and navigable evidence ranges from one retained capture."""

from collections import defaultdict
from math import inf

from agent_tune_kit.traces.query import timestamp_ms


def _branch_and_call_links(nodes, rows):
    """Use explicit dispatch identity and unambiguous call/result pairs, within a Trace."""
    parents = {}
    calls = defaultdict(lambda: defaultdict(list))
    for i in rows:
        node = nodes[i]
        identity = node.get("id")
        if isinstance(identity, (str, int)):
            parents[identity] = i if identity not in parents else None
    resolved = set()
    for i in rows:
        node = nodes[i]
        # Stop at missing, duplicated or cyclic ancestry. A generic AGENT is not a child task.
        path = []
        seen = set()
        cursor = i
        while cursor is not None and cursor not in seen and cursor not in resolved and not nodes[cursor].get("branch"):
            seen.add(cursor)
            path.append(cursor)
            parent = nodes[cursor].get("parent_id")
            cursor = parents.get(parent) if isinstance(parent, (str, int)) else None
        if cursor is not None and (branch := nodes[cursor].get("branch")):
            for child in path:
                nodes[child]["branch"] = branch
        resolved.update(path)
        event = node.get("output")
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        call_id = event.get("tool_use_id")
        if (
            kind in ("tool_use", "tool_result")
            and isinstance(call_id, str)
            and call_id
            and (node.get("name") == f"frontend.{kind}" or node.get("type") == "atk-event")
        ):
            calls[node.get("branch"), call_id][kind].append(i)
    for sides in calls.values():
        starts, ends = sides["tool_use"], sides["tool_result"]
        if len(starts) == len(ends) == 1:
            nodes[starts[0]]["paired"] = ends[0]
            nodes[ends[0]]["paired"] = starts[0]
    return parents


def build_timeline(capture):
    nodes = capture["nodes"]
    groups = {}
    for i, node in enumerate(nodes):
        trace_id = node.get("trace_id")
        if not isinstance(trace_id, (str, int)):
            trace_id = None
        file = node["reference"]["path"]
        key = ("trace", trace_id) if trace_id is not None else ("file", file)
        track = groups.setdefault(key, {"trace_id": trace_id, "file": file, "roots": [], "rows": []})
        start, end = timestamp_ms(node.get("start_time")), timestamp_ms(node.get("end_time"))
        if node.get("end_time") and (end is None or start is None or end < start):
            start = None
        node["at"] = start
        node["until"] = end if start is not None and end is not None else start
        track["roots" if node["type"] == "TRACE" else "rows"].append(i)

    def time_key(i):
        return nodes[i]["at"] if nodes[i]["at"] is not None else inf

    for track in groups.values():
        all_rows = track["roots"] + track["rows"]
        timed = [i for i in all_rows if nodes[i]["at"] is not None]
        track["start"] = min((nodes[i]["at"] for i in timed), default=None)
        track["end"] = max((nodes[i]["until"] for i in timed), default=None)
        track["rows"].sort(key=time_key)
    tracks = sorted(groups.values(), key=lambda t: t["start"] if t["start"] is not None else inf)
    order = []
    for number, track in enumerate(tracks):
        for i in track["roots"] + track["rows"]:
            nodes[i]["track"] = number
        rows = track["rows"]
        if track["trace_id"] is not None:
            _branch_and_call_links(nodes, rows)
        order.extend(rows or track["roots"])
    # Stable sort keeps Trace order and native record order for equal timestamps.
    capture["timeline"] = {"tracks": tracks, "order": sorted(order, key=time_key)}


def _evidence_fragment(captures, saved, pos):
    loc = saved.get("location")
    if not loc:
        return {"capture_id": saved.get("capture_id"), "kind": "file", "refs": [pos], "anchors": [], "derived": False}
    cid, index = loc["capture_id"], loc["node_index"]
    node = captures[cid]["nodes"][index]
    fragment = {
        "capture_id": cid,
        "track": node["track"],
        "refs": [pos],
        "anchors": [index],
        "derived": False,
    }
    if node["type"] == "TRACE":
        return {**fragment, "kind": "trace"}
    if node["at"] is None:
        return {**fragment, "kind": "untimed"}
    return {**fragment, "kind": "point", "start": node["at"], "end": node["until"]}


def legacy_point_fragments(captures, evidence):
    """Older Issues without segments: show each located field as a single point."""
    merged = []
    points = {}
    for pos, saved in enumerate(evidence):
        fragment = _evidence_fragment(captures, saved, pos)
        if fragment["kind"] == "file":
            merged.append(fragment)
            continue
        key = (fragment["capture_id"], tuple(fragment["anchors"]), fragment["kind"])
        if key in points:
            points[key]["refs"].extend(fragment["refs"])
        else:
            points[key] = fragment
            merged.append(fragment)
    capture_order = {cid: i for i, cid in enumerate(captures)}
    return sorted(
        merged,
        key=lambda f: (capture_order.get(f["capture_id"], inf), f.get("start", inf), f["refs"][0]),
    )


def recorded_segments(captures, issue, resolved, evidence, evidence_keys):
    """Render analyst-authored segments; coverage indexes match the current archive nodes."""
    current = next((capture for capture in captures.values() if capture.get("current")), None)
    if current is None and len(captures) == 1:
        current = next(iter(captures.values()))
    fragments = []
    used = set()
    for item in resolved:
        coverage = item["coverage"]
        indexes = coverage["node_indexes"]
        if current is None:
            continue
        nodes = current["nodes"]
        if any(index >= len(nodes) for index in indexes):
            continue
        track_ids = {nodes[index]["track"] for index in indexes}
        if len(track_ids) != 1:
            continue
        track = next(iter(track_ids))
        timed = [index for index in indexes if nodes[index]["at"] is not None]
        refs = []
        for ref in item["evidence"]:
            key = evidence_keys(ref)
            for pos, saved in enumerate(evidence):
                if evidence_keys(saved["reference"]) == key:
                    refs.append(pos)
                    used.add(pos)
                    break
        fragment = {
            "id": item["id"],
            "summary": item["summary"],
            "kind": "segment",
            "scope_kind": item["kind"],
            "capture_id": current["id"],
            "track": track,
            "anchors": indexes,
            "refs": refs,
            "node_count": coverage["node_count"],
            "duration_ms": coverage["duration_ms"],
            "derived": False,
        }
        if timed:
            fragment["start"] = min(nodes[index]["at"] for index in timed)
            fragment["end"] = max(nodes[index]["until"] for index in timed)
        else:
            fragment["kind"] = "untimed"
        fragments.append(fragment)
    # Top-level evidence that never entered a segment stays as points/files.
    leftovers = [pos for pos, _ in enumerate(evidence) if pos not in used]
    if leftovers:
        leftover_evidence = []
        remap = {}
        for new_pos, old_pos in enumerate(leftovers):
            leftover_evidence.append(evidence[old_pos])
            remap[new_pos] = old_pos
        for fragment in legacy_point_fragments(captures, leftover_evidence):
            fragment["refs"] = [remap[pos] for pos in fragment["refs"]]
            if "context_refs" in fragment:
                fragment["context_refs"] = [remap[pos] for pos in fragment["context_refs"]]
            fragments.append(fragment)
    capture_order = {cid: i for i, cid in enumerate(captures)}
    return sorted(
        fragments,
        key=lambda f: (capture_order.get(f["capture_id"], inf), f.get("start", inf), f.get("refs", [0])[0]),
    )


def issue_fragments(captures, evidence):
    """Compatibility wrapper for callers that only have flat evidence."""
    return legacy_point_fragments(captures, evidence)
