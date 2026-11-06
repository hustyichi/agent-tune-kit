"use strict";

window.createSwimlanes = function ({issuesForNode, onNode}) {
  const $ = id => document.getElementById(id);
  const chart = $("lane-chart"), members = $("lane-members"), reset = $("lane-reset");
  const el = (tag, cls, text) => {
    const item = document.createElement(tag);
    if (cls) item.className = cls;
    if (text !== undefined) item.textContent = text;
    return item;
  };
  const button = (cls, text, handler) => {
    const b = el("button", cls, text); b.type = "button"; b.onclick = handler;
    return b;
  };
  const roles = {AGENT: "Agent", GENERATION: "模型", TOOL: "工具", SPAN: "跨度", EVENT: "事件"};
  const name = n => String(n.name || n.id || "未命名节点");
  const time = value => typeof value === "string" && value.trim() ? Date.parse(value) : NaN;
  const seconds = ms => `${Number((ms / 1000).toFixed(2))} s`;
  let capture = null, selected = null, visible = null, zoom = null, open = new Set();
  let groups = [], entries = new Map(), clustered = null;

  function prepare(next) {
    const traces = new Map();
    entries = new Map(); selected = null; zoom = null; open = new Set(); clustered = null;
    for (const [index, node] of (next?.nodes || []).entries()) {
      const traceId = node.trace_id;
      const file = node.reference?.path || "";
      const key = traceId ? "trace:" + traceId : "file:" + file;
      if (!traces.has(key)) traces.set(key, {key, traceId, file, list: [], roots: [], ids: new Map(), lo: Infinity, hi: -Infinity});
      const group = traces.get(key), start = time(node.start_time), end = time(node.end_time);
      // A malformed or reversed endpoint belongs in the untimed list; it cannot place a duration.
      const timed = Number.isFinite(start) && (!node.end_time || Number.isFinite(end) && end >= start);
      const entry = {node, index, group, start, end, timed};
      entries.set(node, entry);
      if (node.type === "TRACE") group.roots.push(entry);
      else {
        group.list.push(entry);
        if (node.id != null) group.ids.set(node.id, group.ids.has(node.id) ? null : entry);
      }
      if (timed) {
        group.lo = Math.min(group.lo, start);
        group.hi = Math.max(group.hi, Number.isFinite(end) ? end : start);
      }
    }
    groups = [...traces.values()].sort((a,b) => a.lo - b.lo);
  }

  function issues(entry) { return issuesForNode?.(entry.node) || entry.node.issue_ids || []; }
  function label(entry) {
    const n = entry.node, when = entry.timed ? n.start_time : "无可用时间";
    const length = Number.isFinite(entry.end) && entry.timed ? "持续 " + seconds(entry.end - entry.start) : "无结束时间";
    return [name(n), when, length, ...issues(entry), n.level === "ERROR" ? "运行时错误" : ""].filter(Boolean).join(" · ");
  }
  function select(entry) { onNode(entry.node); }
  function identify(mark, entry) {
    mark.dataset.nodeIndex = entry.index;
    if (entry.node === selected) { mark.setAttribute("aria-current", "true"); mark.classList.add("selected-mark"); }
    mark.title = label(entry); mark.setAttribute("aria-label", mark.title);
  }
  function keep(entries) { return visible ? entries.filter(e => visible.has(e.node)) : entries; }
  function showMembers(items) {
    clustered = items; members.hidden = false;
    const header = el("div","flow-heading"); header.append(el("h3","",`${items.length} 个节点`),
      button("","收起",() => { clustered = null; members.hidden = true; chart.focus(); }));
    members.replaceChildren(header);
    const list = el("div","member-list"); members.append(list);
    for (const e of items) {
      const b = button("member-button", name(e.node), () => select(e));
      identify(b,e);
      if (issues(e).length) b.append(el("span","member-flag"," ⚑ " + issues(e).join("、")));
      if (e.node.level === "ERROR") b.append(el("span","member-error"," ! 运行时错误"));
      list.append(b);
    }
    list.firstElementChild?.focus();
  }
  function lineage() {
    const related = new Set(), entry = entries.get(selected);
    if (!entry?.group.traceId) return related;
    let e = entry;
    while (e.node.parent_id != null) {
      e = entry.group.ids.get(e.node.parent_id);
      if (!e || related.has(e) || e === entry) break;
      related.add(e);
    }
    return related;
  }

  function draw() {
    const scoped = zoom ? groups.filter(g => g.key === zoom) : groups;
    const timedGroups = scoped.filter(g => Number.isFinite(g.lo));
    let lo = Infinity, hi = -Infinity;
    for (const g of timedGroups) { lo = Math.min(lo,g.lo); hi = Math.max(hi,g.hi); }
    const span = Math.max(1, hi - lo), pct = t => Math.max(0, Math.min(100,(t - lo) * 100 / span));
    reset.hidden = zoom == null;
    const start = groups.find(g => Number.isFinite(g.lo))?.lo ?? 0;
    $("lane-scale-title").textContent = !timedGroups.length ? "没有可用时间轴 · 按来源浏览节点" :
      `${zoom ? "当前 Trace" : "全会话"} · ${seconds(hi-lo)} 观测跨度`;
    const picked = entries.get(selected), caption = $("lane-selected");
    caption.hidden = !picked;
    if (picked) {
      const elapsed = !picked.timed ? "无可用时间" : Number.isFinite(picked.end)
        ? "持续 " + seconds(picked.end - picked.start) : "无结束时间";
      caption.textContent = `已定位 · ${name(picked.node)} · ${elapsed}`;
    }
    chart.replaceChildren(); chart.tabIndex = -1;
    if (!clustered) members.hidden = true;
    let width = 0;
    const related = lineage();
    if (timedGroups.length) {
      const axis = el("div","lane-row lane-axis"); axis.append(el("span","axis-caption","距会话起点"));
      const ticks = el("div","axis-ticks");
      for (const fraction of hi === lo ? [0] : [0,.25,.5,.75,1]) {
        const tick = el("span", "", seconds(lo-start+(hi-lo)*fraction));
        tick.style.left = 100 * fraction + "%";
        ticks.append(tick);
      }
      axis.append(ticks); chart.append(axis);
      width = ticks.getBoundingClientRect().width;
    }
    function bucket(items) {
      // Anchor each cluster to its earliest event. Leave room for the count, flag and error glyph.
      const gap = 17 + 7 * String(items.length).length;
      const batches = [];
      let anchor = -Infinity;
      for (const e of [...items].sort((a, b) => a.start - b.start)) {
        const x = pct(e.start) * width / 100;
        if (x - anchor >= gap) {
          batches.push([]);
          anchor = x;
        }
        batches[batches.length - 1].push(e);
      }
      return batches;
    }
    function mark(items, overview) {
      const e = items[0], interval = items.length === 1 && !overview && e.end > e.start;
      const kind = items.length > 1 ? "cluster" : interval ? "interval" : "point";
      const glyph = button("lane-mark " + kind, "", () => items.length === 1 ? select(e) : showMembers(items));
      glyph.style.left = pct(e.start) + "%";
      if (interval) glyph.style.width = Math.max(0,pct(e.end)-pct(e.start)) + "%";
      if (items.length === 1) {
        identify(glyph,e);
        if (overview && open.has(e.group.key)) {
          glyph.removeAttribute("aria-current");
          glyph.classList.remove("selected-mark");
        }
      }
      else {
        const active = items.find(x => x.node === selected);
        if (active) { glyph.classList.add("selected-cluster"); }
        glyph.textContent = items.length;
        glyph.title = `${items.length} 个相邻节点 · 距会话起点 ${seconds(e.start - start)} 至 ${seconds(items.at(-1).start - start)} · 查看全部`;
      }
      const tagged = items.filter(x => issues(x).length), errors = items.filter(x => x.node.level === "ERROR");
      if (tagged.length) {
        glyph.classList.add("has-issue");
        glyph.title += " · ⚑ " + [...new Set(tagged.flatMap(issues))].join("、");
      }
      if (errors.length) {
        glyph.classList.add("has-error");
        glyph.title += ` · ${errors.length} 个运行时错误`;
      }
      if (!overview && items.some(x => related.has(x))) glyph.classList.add("related-mark");
      glyph.setAttribute("aria-label",glyph.title);
      return glyph;
    }
    let count = 0;
    for (const group of scoped) {
      const rows = keep(group.list), roots = keep(group.roots);
      if (!rows.length && !roots.length) continue;
      count++;
      const expanded = open.has(group.key), timed = rows.filter(e => e.timed);
      const title = group.roots[0]?.node.name || group.traceId || group.file.split("/").pop() || "未归属材料";
      const row = el("div","lane-row trace-overview"), left = el("div","lane-heading");
      const toggle = button("lane-toggle", "", () => {
        if (expanded) open.delete(group.key); else open.add(group.key);
        clustered = null; draw();
        [...chart.querySelectorAll(".lane-toggle")].find(b => b.dataset.lane === group.key)?.focus();
      });
      toggle.dataset.lane = group.key; toggle.setAttribute("aria-expanded", String(expanded));
      toggle.setAttribute("aria-label", (expanded ? "收起 " : "展开 ") + title);
      toggle.append(el("span","lane-chevron",expanded ? "▾":"▸"), el("strong","",title));
      left.append(toggle);
      const info = el("span","lane-note", `${group.traceId ? String(group.traceId).slice(0,8) : "文件"} · ${visible ? rows.length + "/" : ""}${group.list.length} 节点`);
      info.title = group.traceId || group.file; left.append(info);
      const track = el("div","lane-track lane-overview-track");
      if (Number.isFinite(group.lo)) {
        const envelope = el("span","trace-envelope");
        envelope.style.left = pct(group.lo)+"%"; envelope.style.width = (pct(group.hi)-pct(group.lo))+"%";
        track.append(envelope);
      }
      for (const items of bucket(timed)) track.append(mark(items,true));
      row.append(left,track); chart.append(row);
      for (const root of roots) {
        if (issues(root).length || selected === root.node) {
          const rootButton = button("root-evidence", issues(root).length ? "⚑ Trace 证据" : "Trace 记录", () => select(root));
          identify(rootButton, root); left.append(rootButton);
        }
      }
      if (!expanded) continue;
      if (Number.isFinite(group.lo) && zoom !== group.key) {
        left.append(button("zoom-trace","聚焦此 Trace",() => { zoom = group.key; clustered = null; draw(); reset.focus(); }));
      }
      const categories = new Map();
      for (const e of timed) {
        const role = roles[e.node.type] || String(e.node.type || "其他");
        if (!categories.has(role)) categories.set(role,[]);
        categories.get(role).push(e);
      }
      for (const [role, items] of categories) {
        const r = el("div","lane-row role-row"); r.append(el("span","role-name",role + " · " + items.length));
        const rail = el("div","lane-track"), ends = [];
        // Bin nearby points. Keep the selected node distinct so keyboard navigation can land on it.
        const intervals = items.filter(e => e.end > e.start).map(e => [e]);
        const points = bucket(items.filter(e => !(e.end > e.start))).flatMap(batch => {
          const picked = batch.find(e => e.node === selected);
          return picked && batch.length > 1 ? [batch.filter(e => e !== picked), [picked]] : [batch];
        });
        for (const batch of intervals.concat(points).sort((a,b) => a[0].start-b[0].start)) {
          const entry = batch[0], startPx = pct(entry.start) * width / 100;
          let slot = ends.findIndex(x => x + 3 <= startPx);
          if (slot === -1) slot = ends.length;
          ends[slot] = Math.max(startPx+(batch.length>1?23:12), (entry.end>entry.start?pct(entry.end)*width/100:0));
          const glyph = mark(batch,false); glyph.style.top = (11 + slot*23)+"px"; rail.append(glyph);
        }
        rail.style.height = (22+Math.max(0,ends.length-1)*23)+"px";
        r.append(rail); chart.append(r);
      }
      const undated = rows.filter(e => !e.timed);
      if (undated.length) {
        const r = el("div","lane-row undated-row"); r.append(el("span","role-name","未定时"));
        const content = el("div","undated-values");
        content.append(button("untimed-button", `查看 ${undated.length} 个节点`,() => showMembers(undated)));
        const chosen = undated.find(e => e.node === selected);
        if (chosen) { const b = button("untimed-button",name(chosen.node),() => select(chosen)); identify(b,chosen); content.append(b); }
        r.append(content); chart.append(r);
      }
    }
    if (!count) chart.append(el("p","empty", capture?.error ? "快照不可读取" : "没有符合当前条件的轨迹节点"));
  }
  function reveal(node) {
    const e = entries.get(node); if (!e) return;
    selected = node;
    if (visible && !visible.has(node)) visible = null;
    if (zoom && zoom !== e.group.key) zoom = null;
    open.add(e.group.key); clustered = null; draw();
  }
  $("trace-search-clear").onclick = () => {
    const search = $("trace-search");
    search.value = ""; search.dispatchEvent(new Event("input")); search.focus();
  };
  reset.onclick = () => { zoom = null; clustered = null; draw(); chart.focus(); };
  function selectedGlyph() {
    return chart.querySelector(".role-row [aria-current=true], .undated-row [aria-current=true], .root-evidence[aria-current=true]")
      || chart.querySelector("[aria-current=true]");
  }
  function scrollSelected() {
    const glyph = selectedGlyph();
    if (!glyph) return;
    const frame = $("lane-scroll"), rect = frame.getBoundingClientRect(), axis = chart.querySelector(".lane-axis");
    const top = rect.top + (axis?.offsetHeight || 0) + 5;
    let pos = glyph.getBoundingClientRect();
    if (pos.top < top) frame.scrollTop += pos.top - top;
    else if (pos.bottom > rect.bottom - 6) frame.scrollTop += pos.bottom - rect.bottom + 6;
    if (pos.left < rect.left + 6) frame.scrollLeft += pos.left - rect.left - 6;
    else if (pos.right > rect.right - 6) frame.scrollLeft += pos.right - rect.right + 6;
    pos = glyph.getBoundingClientRect();
    const safeTop = document.querySelector(".topbar").getBoundingClientRect().bottom + 12;
    const drawer = $("drawer");
    const safeBottom = (innerWidth <= 800 && !drawer.hidden
      ? drawer.getBoundingClientRect().top : innerHeight) - 14;
    if (pos.top < safeTop) window.scrollBy(0, pos.top - safeTop);
    else if (pos.bottom > safeBottom) window.scrollBy(0, pos.bottom - safeBottom);
  }
  let lastWidth = 0;
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(records => {
    const width = Math.round(records[0].contentRect.width);
    if (width > 0 && lastWidth !== width) {
      lastWidth = width;
      const active = chart.contains(document.activeElement) ? document.activeElement : null;
      const lane = active?.dataset.lane, index = active?.dataset.nodeIndex;
      draw();
      if (index != null) {
        const target = chart.querySelector(`[aria-current=true][data-node-index="${index}"]`)
          || chart.querySelector(`[data-node-index="${index}"]`);
        target?.focus({preventScroll:true});
      }
      else if (lane != null) [...chart.querySelectorAll(".lane-toggle")].find(b => b.dataset.lane === lane)?.focus({preventScroll:true});
    }
    if (selected && !$("drawer").hidden && $("drawer").classList.contains("node-drawer")) scrollSelected();
  }).observe($("lane-scroll"));
  return {
    render(next, filteredNodes = next?.nodes, selectedNode) {
      if (next !== capture) { capture = next; prepare(next); }
      if (selectedNode !== undefined) selected = selectedNode;
      visible = filteredNodes && filteredNodes !== next?.nodes && filteredNodes.length !== next?.nodes?.length ? new Set(filteredNodes) : null;
      $("trace-search-clear").hidden = !$("trace-search").value;
      clustered = null; draw();
    },
    reveal,
    snapshot: () => ({zoom,open:[...open],selected}),
    restore(state) {
      if (!state) return;
      zoom = state.zoom; open = new Set(state.open); selected = state.selected; clustered = null; draw();
    },
    scrollSelected,
    focusSelected() {
      const glyph = selectedGlyph();
      glyph?.focus({preventScroll:true}); scrollSelected(); return glyph;
    }
  };
};
