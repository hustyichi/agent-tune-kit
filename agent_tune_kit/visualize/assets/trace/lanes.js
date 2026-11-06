"use strict";

window.createSwimlanes = function createSwimlanes({el, button, elapsed, onPick, onZoom, onRoot, onFragment}) {
  const chart = document.getElementById("lane-chart");
  const frame = document.getElementById("lane-scroll");
  const members = document.getElementById("lane-members");
  let capture, scope, selected, fragments = [], current, expanded = new Set();
  let marks = new Map(), cursors = [];

  function setSelected(index) {
    selected = index;
    for (const [mark, indices] of marks) {
      mark.setAttribute("aria-current", String(indices.includes(index) && !mark.classList.contains("cluster")));
    }
    const node = capture?.nodes[index];
    for (const {mark, number, branch, pct} of cursors) {
      mark.hidden = !node || node.at == null || node.track !== number || node.type === "TRACE" ||
        (branch !== null && node.branch !== branch);
      if (!mark.hidden) {
        mark.style.left = pct(node.at) + "%";
        mark.dataset.nodeIndex = index;
        mark.title = node.name || node.type;
        mark.setAttribute("aria-label", mark.title);
      }
      mark.setAttribute("aria-current", String(!mark.hidden));
    }
    for (const row of chart.querySelectorAll(".trace-overview")) {
      row.dataset.active = String(capture?.nodes[index]?.track === Number(row.dataset.track));
    }
  }
  function scrollSelected() {
    const mark = chart.querySelector('.current-tick:not([hidden])') || chart.querySelector('button[aria-current=true]:not(.lane-fragment)') ||
      chart.querySelector(".trace-overview[data-active=true] .lane-toggle");
    if (!mark) return;
    const box = frame.getBoundingClientRect(), rect = mark.getBoundingClientRect();
    const top = box.top + (chart.querySelector(".lane-axis")?.offsetHeight || 0);
    if (rect.top < top) frame.scrollTop += rect.top - top - 2;
    else if (rect.bottom > box.bottom) frame.scrollTop += rect.bottom - box.bottom + 2;
    return mark.getBoundingClientRect();
  }
  function listMembers(indices, trigger) {
    onPick(indices[0]);
    if (indices.length === 1) { members.hidden = true; return; }
    members.hidden = false;
    const close = button("收起", () => { members.hidden = true; trigger.focus({preventScroll:true}); });
    const list = el("div", undefined, "member-list");
    members.replaceChildren(list);
    list.append(el("strong", `${indices.length} 个动作`), close);
    let offset = 0;
    const more = button("更多动作", append);
    function append() {
      for (const i of indices.slice(offset, offset + 40)) {
        const n = capture.nodes[i];
        const link = button(n.name || String(n.id || "查看记录"), () => onPick(i));
        link.dataset.nodeIndex = i;
        list.append(link);
      }
      offset += 40;
      if (offset < indices.length) list.append(more); else more.remove();
    }
    append();
    close.focus({preventScroll:true});
  }
  function draw() {
    const top = frame.scrollTop;
    const tracks = capture?.timeline.tracks || [];
    const nodes = capture?.nodes || [];
    const scoped = scope === null ? tracks : tracks.slice(scope, scope + 1);
    const start = tracks.find(track => track.start !== null)?.start ?? 0;
    const lo = scoped.find(track => track.start !== null)?.start;
    const hi = scoped.reduce((last, track) => Math.max(last, track.end ?? -Infinity), -Infinity);
    const pct = t => Math.max(0, Math.min(100, (t - lo) * 100 / Math.max(1, hi - lo)));
    chart.replaceChildren();
    marks = new Map(); cursors = [];
    document.getElementById("legend-fragment").hidden = !fragments.length;
    document.getElementById("lane-reset").hidden = scope === null;
    if (!scoped.length) { chart.append(el("p", "此快照没有可浏览的执行轨迹。", "empty")); return; }
    const axis = el("div", undefined, "lane-row lane-axis");
    const ticks = el("div", undefined, "axis-ticks");
    axis.append(el("span", "执行泳道"), ticks); chart.append(axis);
    const width = Math.max(1, ticks.clientWidth);
    if (lo !== undefined) {
      const fractions = hi === lo ? [0] : width < 300 ? [0, .5, 1] : [0, .25, .5, .75, 1];
      for (const f of fractions) {
        const tick = el("span", elapsed(lo - start + (hi - lo) * f));
        tick.style.left = `${100*f}%`; ticks.append(tick);
      }
    } else ticks.append(el("span", "时间未知"));

    function rail(indices, bounds, number, branch = null) {
      const track = el("div", undefined, "lane-track");
      if (bounds.start != null) {
        const bar = el("span", undefined, "trace-envelope");
        bar.style.left = `${pct(bounds.start)}%`; bar.style.width = `${pct(bounds.end)-pct(bounds.start)}%`;
        track.append(bar);
      }
      for (const [index, part] of fragments) {
        if (part.track !== number || (branch !== null && part.branch !== branch) || part.start == null) continue;
        const mark = button("", () => onFragment(index), "lane-fragment");
        mark.dataset.fragment = index;
        mark.title = `片段 ${index + 1} · ${elapsed(part.start-start)} 至 ${elapsed(part.end-start)}`;
        mark.setAttribute("aria-label", mark.title);
        mark.setAttribute("aria-current", String(current === index));
        mark.style.left = `${pct(part.start)}%`; mark.style.width = `${pct(part.end)-pct(part.start)}%`;
        track.append(mark);
      }
      const timed = indices.filter(i => nodes[i].at !== null);
      let batch, previous = -Infinity;
      function tick() {
        const i = batch[0], n = nodes[i], indices = batch;
        const mark = button("", () => listMembers(indices, mark), indices.length > 1 ? "lane-mark cluster" : "lane-mark");
        mark.style.left = `${pct(n.at)}%`;
        mark.title = `${elapsed(n.at-start, true)} · ${indices.length === 1 ? n.name || n.type : indices.length + " 个动作"}`;
        mark.setAttribute("aria-label", mark.title); marks.set(mark, indices);
        track.append(mark);
      }
      for (const i of timed) {
        const x = pct(nodes[i].at) * width / 100;
        if (x - previous >= 9) { if (batch) tick(); batch = []; previous = x; }
        batch.push(i);
      }
      if (batch) tick();
      // A cluster can begin well before its selected member. Place the cursor at the exact native time.
      const cursor = button("", () => onPick(selected), "lane-mark current-tick");
      cursors.push({mark:cursor, number, branch, pct}); track.append(cursor);
      if (timed.length < indices.length) {
        const undated = indices.filter(i => nodes[i].at === null);
        const link = button(`时间未知 · ${undated.length}`, () => listMembers(undated, link), "untimed");
        marks.set(link, undated); track.append(link);
      }
      return track;
    }
    for (const [position, track] of scoped.entries()) {
      const number = scope === null ? position : scope;
      const root = nodes[track.roots[0]], file = !track.trace_id;
      const label = file ? "文件" : `T${number + 1}`;
      const title = root?.name || track.trace_id || track.file.split("/").pop();
      const row = el("div", undefined, "lane-row trace-overview");
      row.dataset.track = number;
      const left = el("div", undefined, "lane-heading");
      const pick = button("", () => onPick(track.rows[0] ?? track.roots[0]), "lane-toggle");
      pick.title = `定位 ${label} 首条记录 · ${title}`;
      pick.setAttribute("aria-label", pick.title);
      const text = el("span", undefined, "lane-text");
      const note = el("span", undefined, "lane-note");
      if (!file) note.append(el("span", String(track.trace_id).slice(0,8), "short-id"));
      note.append(el("span", track.rows.length + " 条", "lane-count"));
      text.append(el("span", title, "lane-name"), note);
      pick.append(el("span", label, "lane-id"), text); left.append(pick);
      if (root) {
        const info = button("ⓘ", () => onRoot(track.roots[0]), "root-info");
        info.title = `${label} · Trace 原始记录`; info.setAttribute("aria-label", info.title);
        marks.set(info, track.roots); left.append(info);
      }
      if (scope === null && tracks.length > 1) {
        const zoom = button("放大", () => onZoom(number), "lane-zoom");
        zoom.title = `只看此 Trace · ${label}`; zoom.setAttribute("aria-label", zoom.title); left.append(zoom);
      }
      for (const [index, part] of fragments) {
        if (part.track === number && part.start == null) {
          const label = part.kind === "trace" ? "执行级证据" : "未定时证据";
          const link = button(label, () => onFragment(index), "lane-evidence");
          link.dataset.fragment = index; link.setAttribute("aria-pressed", String(current === index)); left.append(link);
        }
      }
      const branches = new Map();
      if (scoped.length === 1) for (const i of track.rows) {
        const branch = nodes[i].branch;
        if (branch) {
          if (!branches.has(branch)) branches.set(branch, []);
          branches.get(branch).push(i);
        }
      }
      if (branches.size) {
        const toggle = button(expanded.has(number) ? "收起子任务" : `子任务 ${branches.size}`, () => {
          if (expanded.has(number)) expanded.delete(number); else expanded.add(number);
          draw(); chart.querySelector(".lane-branch-toggle")?.focus({preventScroll:true});
        }, "lane-branch-toggle");
        toggle.setAttribute("aria-expanded", String(expanded.has(number))); left.append(toggle);
      }
      row.append(left, rail(track.rows, track, number)); chart.append(row);
      if (expanded.has(number)) for (const [branch, indices] of branches) {
        const child = el("div", undefined, "lane-row child-lane");
        const title = el("span", `↳ ${branch}`, "lane-heading"); title.title = branch;
        const first = indices.find(i => nodes[i].at !== null);
        const end = indices.reduce((max, i) => Math.max(max, nodes[i].until ?? -Infinity), -Infinity);
        child.append(title, rail(indices, {start:nodes[first]?.at, end}, number, branch)); chart.append(child);
      }
    }
    setSelected(selected);
    frame.scrollTop = top;
  }
  let lastWidth;
  new ResizeObserver(() => {
    if (frame.clientWidth !== lastWidth) {
      lastWidth = frame.clientWidth;
      if (lastWidth) draw();
    }
  }).observe(frame);
  return {
    render(next, filter, index, parts, active) {
      if (capture !== next) { expanded.clear(); frame.scrollTop = 0; }
      capture = next; scope = filter; selected = index; fragments = parts; current = active;
      members.hidden = true; draw();
    },
    select: setSelected,
    scrollSelected,
    snapshot: () => ({top:frame.scrollTop, expanded:[...expanded]}),
    restore(state) { expanded = new Set(state.expanded); draw(); frame.scrollTop = state.top; },
  };
};
