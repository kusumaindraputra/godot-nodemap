"""nodemap.html: self-contained interactive graph viewer (vis-network)."""
from __future__ import annotations

import html
import json

from .graph import Graph

_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ - nodemap</title>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>
:root { --bg:#14161b; --panel:#1c1f26; --fg:#e6e6e6; --muted:#8b93a1; --line:#2c313b; --accent:#478cbf; }
* { box-sizing: border-box; }
body { margin:0; font:13px/1.45 system-ui,-apple-system,Segoe UI,sans-serif; background:var(--bg); color:var(--fg); display:flex; height:100vh; overflow:hidden; }
#side { width:340px; min-width:260px; background:var(--panel); border-right:1px solid var(--line); display:flex; flex-direction:column; }
#side header { padding:12px 14px; border-bottom:1px solid var(--line); }
#side h1 { font-size:15px; margin:0 0 4px; }
#side .meta { color:var(--muted); font-size:12px; }
#controls { padding:10px 14px; border-bottom:1px solid var(--line); display:flex; flex-direction:column; gap:8px; }
input[type=search] { width:100%; padding:7px 9px; border-radius:6px; border:1px solid var(--line); background:var(--bg); color:var(--fg); }
.row { display:flex; flex-wrap:wrap; gap:4px 10px; }
.row label { display:flex; align-items:center; gap:4px; cursor:pointer; color:var(--muted); }
.sw { width:10px; height:10px; border-radius:50%; display:inline-block; }
select { background:var(--bg); color:var(--fg); border:1px solid var(--line); border-radius:6px; padding:4px; }
#tabs { display:flex; border-bottom:1px solid var(--line); }
#tabs button { flex:1; background:none; border:0; color:var(--muted); padding:8px; cursor:pointer; border-bottom:2px solid transparent; }
#tabs button.on { color:var(--fg); border-bottom-color:var(--accent); }
#panel { flex:1; overflow:auto; padding:10px 14px; }
#panel h2 { font-size:14px; margin:4px 0 6px; word-break:break-all; }
#panel .k { color:var(--muted); }
#panel ul { padding-left:16px; margin:4px 0 10px; }
#panel li { margin:2px 0; word-break:break-all; }
#panel a { color:#8ec5ff; cursor:pointer; text-decoration:none; }
.sev-error { color:#ff7b72; } .sev-warning { color:#e3b341; } .sev-info { color:var(--muted); }
#net { flex:1; height:100vh; }
code { font-size:12px; color:#c9d1d9; }
@media (max-width: 700px) { body { flex-direction:column; } #side { width:100%; height:45vh; } #net { height:55vh; } }
</style>
</head>
<body>
<div id="side">
  <header><h1>__TITLE__</h1><div class="meta" id="meta"></div></header>
  <div id="controls">
    <input type="search" id="q" placeholder="Search scripts, scenes, signals...">
    <div class="row" id="kinds"></div>
    <div class="row"><label>Color by <select id="colorby"><option value="kind">kind</option><option value="community">community</option></select></label>
    <label><input type="checkbox" id="labels" checked> labels</label></div>
  </div>
  <div id="tabs"><button class="on" data-t="node">Node</button><button data-t="issues">Issues</button><button data-t="comm">Communities</button></div>
  <div id="panel"></div>
</div>
<div id="net"></div>
<script>
const DATA = __DATA__;
const KIND_COLORS = {script:"#478cbf", scene:"#e07a5f", node:"#f2cc8f", function:"#81b29a", signal:"#c77dff",
  autoload:"#ffd166", resource:"#9aa5b1", asset:"#5c6370", action:"#06d6a0", group:"#ef476f", missing:"#ff0000",
  doc:"#f4a261"};
const DEFAULT_ON = new Set(["script","scene","signal","autoload","resource","action","group","missing","doc"]);
const REL_COLORS = {emits:"#c77dff", connected_to:"#c77dff", calls:"#81b29a", extends:"#478cbf", instances:"#e07a5f",
  has_script:"#478cbf", node_path:"#f2cc8f", uses_autoload:"#ffd166", preloads:"#9aa5b1", loads:"#9aa5b1",
  mentions:"#f4a261"};
const byId = new Map(DATA.nodes.map(n => [n.id, n]));
const adj = new Map();
for (const e of DATA.edges) {
  if (!byId.has(e.source) || !byId.has(e.target)) continue;
  (adj.get(e.source) || adj.set(e.source, []).get(e.source)).push([e, "out"]);
  (adj.get(e.target) || adj.set(e.target, []).get(e.target)).push([e, "in"]);
}
const enabled = new Set(DEFAULT_ON);
const kinds = [...new Set(DATA.nodes.map(n => n.kind))].sort();
const kindsEl = document.getElementById("kinds");
for (const k of kinds) {
  const count = DATA.nodes.filter(n => n.kind === k).length;
  const l = document.createElement("label");
  l.innerHTML = `<input type="checkbox" ${enabled.has(k) ? "checked" : ""}> <span class="sw" style="background:${KIND_COLORS[k]||"#888"}"></span>${k} (${count})`;
  l.querySelector("input").onchange = ev => { ev.target.checked ? enabled.add(k) : enabled.delete(k); rebuild(); };
  kindsEl.appendChild(l);
}
const m = DATA.meta;
document.getElementById("meta").textContent =
  `${DATA.nodes.length} nodes · ${DATA.edges.length} edges · ${Object.keys(DATA.communities).length} communities · Godot ${(m.engine_features||[]).join(" ")}`;
const commColor = c => `hsl(${(c * 137.508) % 360},55%,58%)`;
// hidden node -> visible representative (functions/signals fold into their script, scene nodes into their scene)
function rep(n) {
  if (enabled.has(n.kind)) return n.id;
  const owner = n.script || n.scene;
  if (owner && byId.has(owner) && enabled.has(byId.get(owner).kind)) return owner;
  return null;
}
let network;
function rebuild() {
  const colorBy = document.getElementById("colorby").value;
  const showLabels = document.getElementById("labels").checked;
  const vis_ = [];
  const deg = new Map();
  const edges = [], ekeys = new Set();
  for (const e of DATA.edges) {
    const s = byId.get(e.source), t = byId.get(e.target);
    if (!s || !t) continue;
    const a = rep(s), b = rep(t);
    if (!a || !b || a === b) continue;
    const key = a + "|" + b + "|" + e.relation;
    if (ekeys.has(key)) continue;
    ekeys.add(key);
    deg.set(a, (deg.get(a) || 0) + 1); deg.set(b, (deg.get(b) || 0) + 1);
    edges.push({from:a, to:b, arrows:"to", title:e.relation + (e.signal ? "(" + e.signal + ")" : ""),
      color:{color:REL_COLORS[e.relation] || "#3a404c", opacity:0.55}, dashes:e.confidence !== "EXTRACTED", width:0.6});
  }
  for (const n of DATA.nodes) {
    if (!enabled.has(n.kind)) continue;
    const d = deg.get(n.id) || 0;
    const color = colorBy === "community" && n.community !== undefined ? commColor(n.community) : (KIND_COLORS[n.kind] || "#888");
    vis_.push({id:n.id, label: showLabels ? n.label : undefined, title:`[${n.kind}] ${n.label}\n${n.file||""}`,
      color:{background:color, border:color}, shape:"dot", size: 5 + Math.min(28, Math.sqrt(d) * 3),
      font:{color:"#d0d4dc", size:11, strokeWidth:3, strokeColor:"#14161b"}});
  }
  if (network) network.destroy();
  network = new vis.Network(document.getElementById("net"), {nodes:vis_, edges}, {
    physics:{solver:"forceAtlas2Based", forceAtlas2Based:{gravitationalConstant:-40, springLength:90}, stabilization:{iterations:250}},
    interaction:{hover:true, tooltipDelay:120}, edges:{smooth:false}});
  network.once("stabilizationIterationsDone", () => network.setOptions({physics:false}));
  network.on("click", p => { if (p.nodes.length) showNode(p.nodes[0]); });
}
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const loc = (f, l) => `${esc((f||"").replace("res://",""))}${l ? ":" + l : ""}`;
const link = id => `<a data-id="${esc(id)}">${esc(byId.get(id)?.label || id)}</a>`;
const panel = document.getElementById("panel");
panel.onclick = ev => { const a = ev.target.closest("a[data-id]"); if (a) focusNode(a.dataset.id); };
function focusNode(id) {
  const n = byId.get(id); if (!n) return;
  const r = rep(n);
  if (r && network) { network.selectNodes([r]); network.focus(r, {scale:1.1, animation:true}); }
  showNode(id);
}
function showNode(id) {
  setTab("node");
  const n = byId.get(id);
  let h = `<h2>${esc(n.label)}</h2><div class="k">${esc(n.kind)} · ${loc(n.file, n.line)}</div>`;
  if (n.sections) h += `<div class="k">sections</div><ul>${n.sections.map(x => "<li>" + esc(x) + "</li>").join("")}</ul>`;
  for (const k of ["class_name","extends","type","params","doc","declared_in","path"]) if (n[k]) h += `<div><span class="k">${k}:</span> ${esc(n[k])}</div>`;
  if (n.exports) h += `<div><span class="k">exports:</span> ${esc(n.exports.join(", "))}</div>`;
  const groups = {};
  for (const [e, dir] of adj.get(id) || []) {
    const other = dir === "out" ? e.target : e.source;
    const key = dir === "out" ? `${e.relation} →` : `← ${e.relation}`;
    (groups[key] = groups[key] || []).push(`${link(other)}${e.signal ? " <span class='k'>(" + esc(e.signal) + ")</span>" : ""}${e.section ? " <span class='k'>§ " + esc(e.section) + "</span>" : ""}${e.confidence !== "EXTRACTED" ? " <span class='k'>[" + e.confidence + "]</span>" : ""} <span class="k">@${loc(e.file, e.line)}</span>`);
  }
  for (const k of Object.keys(groups).sort()) h += `<div class="k">${esc(k)} (${groups[k].length})</div><ul>${groups[k].slice(0, 60).map(x => "<li>" + x + "</li>").join("")}</ul>`;
  const iss = DATA.issues.filter(i => i.file === n.file && (["script","scene","resource"].includes(n.kind) || i.line === n.line));
  if (iss.length) h += `<div class="k">issues</div><ul>${iss.map(i => `<li class="sev-${i.severity}">${esc(i.code)} @${loc(i.file, i.line)}: ${esc(i.message)}</li>`).join("")}</ul>`;
  panel.innerHTML = h;
}
function showIssues() {
  const order = {error:0, warning:1, info:2};
  const items = [...DATA.issues].sort((a, b) => order[a.severity] - order[b.severity]);
  panel.innerHTML = items.length ? `<ul>${items.map(i => `<li class="sev-${i.severity}"><b>${i.severity}</b> ${esc(i.code)} <a data-id="${esc(i.file)}">${loc(i.file, i.line)}</a><br>${esc(i.message)}${i.hint ? "<br><span class='k'>" + esc(i.hint) + "</span>" : ""}</li>`).join("")}</ul>` : "No issues.";
}
function showComms() {
  const cs = Object.entries(DATA.communities).sort((a, b) => b[1].nodes.length - a[1].nodes.length);
  panel.innerHTML = cs.map(([cid, c]) => {
    const files = c.nodes.filter(id => ["script","scene","autoload","resource","doc"].includes(byId.get(id)?.kind));
    return `<div><span class="sw" style="background:${commColor(+cid)}"></span> <b>C${cid}</b> ${esc(c.label)} <span class="k">(${files.length} files)</span><ul>${files.slice(0, 12).map(f => "<li>" + link(f) + "</li>").join("")}</ul></div>`;
  }).join("");
}
function setTab(t) {
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.t === t));
  if (t === "issues") showIssues(); else if (t === "comm") showComms();
}
document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => { setTab(b.dataset.t); if (b.dataset.t === "node") panel.innerHTML = "Click a node."; });
document.getElementById("colorby").onchange = rebuild;
document.getElementById("labels").onchange = rebuild;
document.getElementById("q").addEventListener("keydown", ev => {
  if (ev.key !== "Enter") return;
  const q = ev.target.value.toLowerCase().trim(); if (!q) return;
  const hit = DATA.nodes.find(n => n.label.toLowerCase() === q) || DATA.nodes.find(n => n.label.toLowerCase().includes(q)) || DATA.nodes.find(n => n.id.toLowerCase().includes(q));
  if (hit) focusNode(hit.id);
});
panel.innerHTML = "Click a node to see its connections. Dashed edges are INFERRED/AMBIGUOUS. Hidden kinds (functions, scene nodes) fold into their script/scene.";
rebuild();
</script>
</body>
</html>
"""


def to_html(g: Graph) -> str:
    data = g.to_dict()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    title = html.escape(g.meta.get("project") or "Godot project")
    return _TEMPLATE.replace("__TITLE__", title).replace("__DATA__", payload)
