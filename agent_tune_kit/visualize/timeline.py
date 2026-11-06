"""Derive execution lanes and navigable evidence ranges from one retained capture."""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime
from math import inf


def _milliseconds(value):
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
            if moment.utcoffset() is not None:
                return round(moment.timestamp() * 1_000_000) / 1000
        except (ValueError, OSError, OverflowError):
            pass
    return None


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


def _context_ranges(nodes, rows, parents):
    """Index a local reading window per cited action; long-lived execution shells add no time."""
    branches = defaultdict(list)
    for i in rows:
        if nodes[i]["at"] is not None:
            branches[nodes[i].get("branch")].append(i)
    windows = {}
    for branch, indices in branches.items():
        actions = [
            i
            for i in indices
            if nodes[i]["type"] in ("GENERATION", "TOOL")
            or "paired" in nodes[i]
            or nodes[i].get("level") == "ERROR"
            or nodes[i]["name"] in ("sdk.user", "sdk.result")
        ] or indices
        times = [nodes[i]["at"] for i in actions]
        for i in indices:
            node = nodes[i]
            if not node.get("issue_ids"):
                continue
            at = node["at"]
            pair = nodes[node["paired"]] if "paired" in node else None
            if pair and pair["at"] is not None:
                windows[i] = min(at, pair["at"]), max(node["until"], pair["until"])
                continue
            unit = node
            seen = set()
            while unit is not None and id(unit) not in seen:
                seen.add(id(unit))
                if unit.get("branch") != branch:
                    break
                is_unit = unit["type"] in ("TOOL", "GENERATION") or (
                    unit["type"] == "SPAN" and str(unit["name"]).startswith("agent.iteration #")
                )
                if (
                    is_unit
                    and unit["at"] is not None
                    and unit["at"] <= at <= unit["until"]
                    and unit["until"] > unit["at"]
                ):
                    windows[i] = unit["at"], unit["until"]
                    break
                parent = unit.get("parent_id")
                parent_index = parents.get(parent) if isinstance(parent, (str, int)) else None
                unit = nodes[parent_index] if parent_index is not None else None
            if i not in windows:
                # Padding is reading context. Do not join sparse actions across an idle gap.
                left = bisect_left(times, at)
                right = bisect_right(times, at)
                nearby = times[max(0, left - 2) : left] + times[right : right + 2]
                context = [at, *(t for t in nearby if abs(t - at) <= 30_000)]
                windows[i] = min(context), max(context)
    return windows


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
        start, end = _milliseconds(node.get("start_time")), _milliseconds(node.get("end_time"))
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
    windows = {}
    for number, track in enumerate(tracks):
        for i in track["roots"] + track["rows"]:
            nodes[i]["track"] = number
        rows = track["rows"]
        if track["trace_id"] is not None:
            parents = _branch_and_call_links(nodes, rows)
            windows.update(_context_ranges(nodes, rows, parents))
        else:
            # A shared file alone does not establish a shared execution context.
            for i in rows:
                if nodes[i].get("issue_ids") and nodes[i]["at"] is not None:
                    windows[i] = nodes[i]["at"], nodes[i]["at"]
        order.extend(rows or track["roots"])
    # Stable sort keeps Trace order and native record order for equal timestamps.
    capture["timeline"] = {"tracks": tracks, "order": sorted(order, key=time_key)}
    return windows


def issue_fragments(captures, windows, evidence):
    """Evidence positions reference the original issue array; never copy field bodies."""
    ranges = defaultdict(list)
    roots = defaultdict(list)
    other = []
    for pos, saved in enumerate(evidence):
        loc = saved.get("location")
        if not loc:
            other.append({"capture_id": saved.get("capture_id"), "kind": "file", "refs": [pos], "anchors": []})
            continue
        cid, i = loc["capture_id"], loc["node_index"]
        node = captures[cid]["nodes"][i]
        track = node["track"]
        fragment = {"capture_id": cid, "track": track, "refs": [pos], "anchors": [i]}
        if node["type"] == "TRACE":
            roots[cid, track].append((i, pos))
        elif i in windows[cid]:
            lo, hi = windows[cid][i]
            ranges[cid, track, node.get("branch")].append(
                {
                    **fragment,
                    "kind": "actions",
                    "start": lo,
                    "end": hi,
                    "branch": node.get("branch"),
                }
            )
        else:
            other.append({**fragment, "kind": "untimed"})
    merged = []
    for group in ranges.values():
        last = None
        for item in sorted(group, key=lambda f: f["start"]):
            if last and item["start"] <= last["end"]:
                last["end"] = max(last["end"], item["end"])
                last["refs"].extend(item["refs"])
                last["anchors"].extend(i for i in item["anchors"] if i not in last["anchors"])
            else:
                merged.append(item)
                last = item
    # Untimed fields on the same record are one destination too.
    undated = {}
    for item in other:
        key = (item["capture_id"], tuple(item["anchors"]))
        if item["anchors"] and key in undated:
            undated[key]["refs"].extend(item["refs"])
        else:
            undated[key] = item
            merged.append(item)
    for (cid, track), fields in roots.items():
        context = [pos for _, pos in fields]
        target = next((f for f in merged if f["capture_id"] == cid and f.get("track") == track), None)
        if target:
            target["context_refs"] = context
        else:
            merged.append(
                {
                    "capture_id": cid,
                    "track": track,
                    "kind": "trace",
                    "anchors": list(dict.fromkeys(i for i, _ in fields)),
                    "refs": context,
                }
            )
    capture_order = {cid: i for i, cid in enumerate(captures)}
    return sorted(merged, key=lambda f: (capture_order.get(f["capture_id"], inf), f.get("start", inf), f["refs"][0]))
