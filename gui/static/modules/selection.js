"use strict";

/* ════════════════════════════════════════════════════════════
 已选文件 — 分组显示（从 app.js 原样搬来）
 ════════════════════════════════════════════════════════════ */

import { $, escapeHtml } from "./dom.js";
import { S } from "./state.js";
import { renderTree } from "./browse.js";

export function renderSel() {
const ul = $("selected");
ul.innerHTML = "";

// 按目录分组
const groups = new Map(); // dir -> [file, ...]
for (const p of S.sel) {
  const idx = p.lastIndexOf("\\");
  const dir = idx > 0 ? p.slice(0, idx) : "";
  const name = idx > 0 ? p.slice(idx + 1) : p;
  if (!groups.has(dir)) groups.set(dir, []);
  groups.get(dir).push({ path: p, name });
}

// 排序：目录名升序
const sortedDirs = [...groups.keys()].sort((a, b) => a.localeCompare(b, "zh"));

for (const dir of sortedDirs) {
  const items = groups.get(dir).sort((a, b) => a.name.localeCompare(b.name, "zh"));

  // 组头
  const header = document.createElement("li");
  header.className = "sel-group-header";
  const dirLabel = dir || "（手动添加）";
  header.innerHTML = `<span>📁 ${escapeHtml(dirLabel)}（${items.length}）</span>`;
  // 组级删除
  const rmGroup = document.createElement("button");
  rmGroup.className = "rm-group";
  rmGroup.textContent = "✕";
  rmGroup.title = "移除此目录下所有已选文件";
  rmGroup.onclick = () => { for (const it of items) S.sel.delete(it.path); renderSel(); renderTree(); };
  header.appendChild(rmGroup);
  ul.appendChild(header);

  // 文件条目
  for (const item of items) {
    const li = document.createElement("li");
    li.className = "sel-item";
    const nm = document.createElement("span");
    nm.className = "nm";
    nm.textContent = item.name;
    nm.title = item.path;
    const rm = document.createElement("button");
    rm.className = "rm";
    rm.textContent = "✕";
    rm.onclick = () => { S.sel.delete(item.path); renderSel(); renderTree(); };
    li.appendChild(nm); li.appendChild(rm);
    ul.appendChild(li);
  }
}

$("selcount").textContent = S.sel.size ? `（${S.sel.size} 个）` : "";
}

$("clear").onclick = () => { S.sel.clear(); renderSel(); renderTree(); };
$("addpaths").onclick = () => {
for (const line of $("paths").value.split("\n")) {
  const p = line.trim();
  if (p) S.sel.add(p);
}
$("paths").value = "";
renderSel();
};
