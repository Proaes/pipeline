"use strict";

/* ════════════════════════════════════════════════════════════
 文件浏览 — 树形视图（从 app.js 原样搬来，只把共享状态改走 S）
 ════════════════════════════════════════════════════════════ */

import { $, escapeHtml } from "./dom.js";
import { api } from "./api.js";
import { S } from "./state.js";
import { isImageMode, isOkFile, IMG_EXTS } from "./options.js";
import { renderSel } from "./selection.js";

/** README 之类的元数据文件不进树、也不会被勾选。 */
const SKIP_NAMES = new Set(["readme.md", "readme.txt", "readme.rst", "readme"]);

export async function browse(dir) {
$("browserr").textContent = "";
const d = await api("/api/browse?dir=" + encodeURIComponent(dir) + "&depth=2");
if (d.error) { $("browserr").textContent = d.error; return; }
S.curDir = d.dir;
S.rootDir = d.root || "";
$("curdir").value = d.dir;
$("up").disabled = (S.curDir === S.rootDir);
S.treeData = d.entries || [];
renderTree();
}

/* ── 树渲染 ── */
export function renderTree() {
const container = $("browse");
container.innerHTML = "";
S.flatList = [];
const filter = $("search").value.trim().toLowerCase();
const imgMode = isImageMode();

for (const entry of S.treeData) {
  const path = S.curDir + "\\" + entry.name;
  buildTreeRows(container, entry, path, 0, filter, imgMode, null);
}
updateBrowseCount();
}

function buildTreeRows(parentEl, entry, path, depth, filter, imgMode, parentEntry) {
const isDir = entry.is_dir;

// 过滤 README 等元数据文件
if (!isDir && SKIP_NAMES.has(entry.name.toLowerCase())) return;

// 搜索过滤：如果过滤中且此节点（及子树）无匹配，跳过
if (filter && !matchesFilter(entry, filter)) return;

// 展开状态管理
if (entry._expanded === undefined) entry._expanded = false;

const node = document.createElement("div");
node.className = "tree-node";

const row = document.createElement("div");
row.className = "tree-row";
row.style.paddingLeft = (depth * 18 + 4) + "px";

// 复选框
const cb = document.createElement("input");
cb.type = "checkbox";
cb.className = "tree-cb";
cb.checked = isDir ? isDirFullyChecked(entry, path, imgMode) : S.sel.has(path);
cb.indeterminate = isDir ? isDirIndeterminate(entry, path, imgMode) : false;
row.appendChild(cb);

// 展开箭头
const arrow = document.createElement("span");
arrow.className = "tree-arrow" + (isDir ? (entry._expanded ? " expanded" : "") : " leaf");
arrow.textContent = "▶";
row.appendChild(arrow);

// 图标
const icon = document.createElement("span");
icon.className = "tree-icon";
if (isDir) {
  icon.textContent = entry._expanded ? "📂" : "📁";
} else {
  const ext = entry.name.split(".").pop().toLowerCase();
  if (ext === "md") icon.textContent = "📝";
  else if (ext === "tex") icon.textContent = "📐";
  else if (ext === "json") icon.textContent = "📋";
  else if (ext === "ppt" || ext === "pptx") icon.textContent = "📊";
  else if (IMG_EXTS.has(ext)) icon.textContent = "🖼️";
  else icon.textContent = "📄";
}
row.appendChild(icon);

// 文件名
const nameSpan = document.createElement("span");
nameSpan.className = "tree-name";
if (filter) {
  nameSpan.innerHTML = highlightMatch(entry.name, filter);
} else {
  nameSpan.textContent = entry.name;
}
if (!isDir && !isOkFile(entry.name, imgMode)) {
  nameSpan.classList.add("tree-muted");
}
row.appendChild(nameSpan);

// 大小
const sizeSpan = document.createElement("span");
sizeSpan.className = "tree-size";
sizeSpan.textContent = isDir ? "" : (entry.size || "");
row.appendChild(sizeSpan);

// 记录到 flatList
const flatItem = { row, entry, path, depth, expanded: entry._expanded, parentEntry };
S.flatList.push(flatItem);

// ── 事件 ──
// 复选框：切换选中
cb.addEventListener("click", (e) => {
  e.stopPropagation();
  if (isDir) {
    toggleDirCheck(entry, path, imgMode, cb.checked);
  } else {
    if (cb.checked) S.sel.add(path); else S.sel.delete(path);
  }
  renderTree();
  renderSel();
});

// 整行点击
let clickTimer = null;
row.addEventListener("click", (e) => {
  if (e.target === cb) return;
  // 双击检测
  if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; handleDblClick(entry, path, isDir, imgMode); return; }
  clickTimer = setTimeout(() => { clickTimer = null; handleClick(entry, path, isDir, imgMode, cb); }, 250);
});

parentEl.appendChild(node);
node.appendChild(row);

// 子节点
if (isDir && entry.children) {
  const childContainer = document.createElement("div");
  childContainer.className = "tree-children" + (entry._expanded ? "" : " collapsed");
  for (const child of entry.children) {
    buildTreeRows(childContainer, child, path + "\\" + child.name, depth + 1, filter, imgMode, entry);
  }
  node.appendChild(childContainer);
}
}

/* ── 单击：展开/折叠文件夹，或选中文件 ── */
function handleClick(entry, path, isDir, imgMode, cb) {
if (isDir) {
  entry._expanded = !entry._expanded;
  renderTree();
} else {
  if (isOkFile(entry.name, imgMode)) {
    if (S.sel.has(path)) S.sel.delete(path); else S.sel.add(path);
    renderTree();
    renderSel();
  }
}
}

/* ── 双击：进入文件夹 ── */
function handleDblClick(entry, path, isDir, imgMode) {
if (isDir) {
  browse(path);
}
}

/* ── 文件夹复选框语义：勾选 = 把所有可转换文件加入 sel ── */
function toggleDirCheck(entry, path, imgMode, checked) {
const files = collectFiles(entry, path, imgMode);
if (checked) {
  for (const f of files) S.sel.add(f);
} else {
  for (const f of files) S.sel.delete(f);
}
}

function collectFiles(entry, path, imgMode) {
const result = [];
if (!entry.children) return result;
for (const child of entry.children) {
  const cpath = path + "\\" + child.name;
  if (child.is_dir) {
    result.push(...collectFiles(child, cpath, imgMode));
  } else {
    if (!SKIP_NAMES.has(child.name.toLowerCase()) && isOkFile(child.name, imgMode)) result.push(cpath);
  }
}
return result;
}

function isDirFullyChecked(entry, path, imgMode) {
const files = collectFiles(entry, path, imgMode);
return files.length > 0 && files.every(f => S.sel.has(f));
}

function isDirIndeterminate(entry, path, imgMode) {
const files = collectFiles(entry, path, imgMode);
if (files.length === 0) return false;
const checkedCount = files.filter(f => S.sel.has(f)).length;
return checkedCount > 0 && checkedCount < files.length;
}

function matchesFilter(entry, filter) {
if (entry.name.toLowerCase().includes(filter)) return true;
if (entry.is_dir && entry.children) {
  return entry.children.some(c => matchesFilter(c, filter));
}
return false;
}

function highlightMatch(name, filter) {
const idx = name.toLowerCase().indexOf(filter);
if (idx < 0) return escapeHtml(name);
return escapeHtml(name.slice(0, idx)) + '<span class="highlight">' +
       escapeHtml(name.slice(idx, idx + filter.length)) + '</span>' +
       escapeHtml(name.slice(idx + filter.length));
}

function updateBrowseCount() {
const total = S.flatList.filter(x => !x.entry.is_dir).length;
const checked = S.flatList.filter(x => !x.entry.is_dir && S.sel.has(x.path)).length;
$("browsecnt").textContent = `${checked}/${total} 文件已选`;
}

/* ── 全选 / 反选 ── */
$("btn-selall").onclick = () => {
for (const item of S.flatList) {
  if (!item.entry.is_dir && isOkFile(item.entry.name, isImageMode())) S.sel.add(item.path);
}
renderTree(); renderSel();
};
$("btn-invsel").onclick = () => {
for (const item of S.flatList) {
  if (!item.entry.is_dir && isOkFile(item.entry.name, isImageMode())) {
    if (S.sel.has(item.path)) S.sel.delete(item.path); else S.sel.add(item.path);
  }
}
renderTree(); renderSel();
};
$("btn-expand").onclick = () => { setAllExpanded(S.treeData, true); renderTree(); };
$("btn-collapse").onclick = () => { setAllExpanded(S.treeData, false); renderTree(); };
function setAllExpanded(entries, val) {
for (const e of entries) {
  if (e.is_dir) { e._expanded = val; if (e.children) setAllExpanded(e.children, val); }
}
}

/* ── 搜索 ── */
let searchTimer = null;
$("search").addEventListener("input", () => {
clearTimeout(searchTimer);
searchTimer = setTimeout(() => {
  // 搜索时自动展开所有节点以便看到匹配
  if ($("search").value.trim()) setAllExpanded(S.treeData, true);
  renderTree();
}, 200);
});

/* ── 键盘导航 ── */
$("browse").addEventListener("keydown", (e) => {
if (!S.flatList.length) return;
if (e.key === "ArrowDown") { e.preventDefault(); moveFocus(1); }
else if (e.key === "ArrowUp") { e.preventDefault(); moveFocus(-1); }
else if (e.key === "Enter" || e.key === " ") {
  e.preventDefault();
  if (S.focusIdx >= 0 && S.focusIdx < S.flatList.length) {
    const item = S.flatList[S.focusIdx];
    if (e.key === " ") {
      // 空格 = 切换复选框
      const cb = item.row.querySelector(".tree-cb");
      cb.click();
    } else {
      // Enter = 展开/折叠或进入
      handleClick(item.entry, item.path, item.entry.is_dir, isImageMode(), null);
    }
  }
}
else if (e.key === "ArrowRight") {
  // 右箭头展开文件夹
  if (S.focusIdx >= 0) { const item = S.flatList[S.focusIdx]; if (item.entry.is_dir && !item.entry._expanded) {
    item.entry._expanded = true; renderTree(); setFocus(S.focusIdx);
  }}
}
else if (e.key === "ArrowLeft") {
  // 左箭头折叠文件夹
  if (S.focusIdx >= 0) { const item = S.flatList[S.focusIdx]; if (item.entry.is_dir && item.entry._expanded) {
    item.entry._expanded = false; renderTree(); setFocus(S.focusIdx);
  }}
}
});

function moveFocus(delta) {
setFocus(Math.max(0, Math.min(S.flatList.length - 1, S.focusIdx + delta)));
}
function setFocus(idx) {
// 清除旧焦点
if (S.focusIdx >= 0 && S.focusIdx < S.flatList.length) S.flatList[S.focusIdx].row.classList.remove("focused");
S.focusIdx = idx;
if (S.focusIdx >= 0 && S.focusIdx < S.flatList.length) {
  S.flatList[S.focusIdx].row.classList.add("focused");
  S.flatList[S.focusIdx].row.scrollIntoView({ block: "nearest" });
}
}

/* ── 上级 / 选当前目录 ── */
$("up").onclick = () => {
if (S.curDir === S.rootDir) return;
const p = S.curDir.replace(/\\+$/, "");
const idx = p.lastIndexOf("\\");
browse(idx > 0 ? p.slice(0, idx) : S.rootDir);
};
$("pickdir").onclick = () => { S.sel.add(S.curDir); renderSel(); };
