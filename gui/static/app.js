"use strict";
const $ = id => document.getElementById(id);

/* 全局错误可见化 */
window.addEventListener("error", e => {
const log = $("log");
if (log) { const d = document.createElement("div"); d.className = "log-line err";
  d.textContent = "[JS错误] " + (e.message || e); log.appendChild(d); }
});
window.addEventListener("unhandledrejection", e => {
const log = $("log");
if (log) { const d = document.createElement("div"); d.className = "log-line err";
  d.textContent = "[请求失败] " + (e.reason?.message || e.reason); log.appendChild(d); }
});

/* ── 状态 ── */
const sel = new Set();           // 已选文件路径集合
let curDir = "";
let rootDir = "";
let treeData = [];               // 当前目录的 entries（含 children）
let flatList = [];               // 展平后的可见行 [{row, entry, path, depth, expanded, parentEntry}]
let focusIdx = -1;               // 键盘焦点索引

/* 目标格式 → 可接受的输入扩展名（与 server.py 的 SUPPORTED_EXTS 保持一致） */
const TARGET_EXTS = {
pdf:  new Set(["md", "tex", "ppt", "pptx"]),
tex:  new Set(["md", "ppt", "pptx"]),
json: new Set(["md", "ppt", "pptx"]),
md:   new Set(["ppt", "pptx"]),
docx: new Set(["ppt", "pptx"]),
lint: new Set(["md"]),
audio: new Set(["md", "markdown", "txt"]),
};
const TARGET_HINTS = {
pdf:   "md/tex/ppt/pptx → PDF（latexmk，需 TeX Live）",
tex:   "md/ppt/pptx → LaTeX（ppt 走 slides 结构 article 模式）",
json:  "md/ppt/pptx → 结构化 JSON",
md:    "ppt/pptx → Markdown",
docx:  "ppt/pptx → Word",
lint:  "只检查 md 笔记写法（不改源文件）",
image: "选「含图片的目录」批量 OCR（需 Ollama + qwen2.5vl）",
audio: "md/txt → 语音（默认 GPT-SoVITS 微调，可切 GENIE；长文自动切句）",
};
const PPT_NOTE = "；.ppt 会先转 .pptx（需本机 PowerPoint 或 WPS）";
const IMG_EXTS = new Set(["jpg","jpeg","png","bmp","webp","tiff","tif","gif"]);
const SKIP_NAMES = new Set(["readme.md", "readme.txt", "readme.rst", "readme"]);
function currentTarget() { return document.querySelector("input[name=target]:checked").value; }
function isImageMode() { return currentTarget() === "image"; }
function okExts() { return TARGET_EXTS[currentTarget()] || new Set(); }
function updateTargetHint() {
const h = $("targethint");
if (!h) return;
const t = currentTarget();
const note = (t !== "image" && (TARGET_EXTS[t] || new Set()).has("ppt")) ? PPT_NOTE : "";
h.textContent = "ℹ " + (TARGET_HINTS[t] || "") + note;
}

async function api(path, opts) { return (await fetch(path, opts)).json(); }

/* ── 健康检查 ── */
api("/api/health").then(d => { $("health").textContent = d.ok ? "● 服务正常" : "● 异常"; })
.catch(() => $("health").textContent = "● 无法连接");

/* ════════════════════════════════════════════════════════════
 文件浏览 — 树形视图
 ════════════════════════════════════════════════════════════ */

async function browse(dir) {
$("browserr").textContent = "";
const d = await api("/api/browse?dir=" + encodeURIComponent(dir) + "&depth=2");
if (d.error) { $("browserr").textContent = d.error; return; }
curDir = d.dir;
rootDir = d.root || "";
$("curdir").value = d.dir;
$("up").disabled = (curDir === rootDir);
treeData = d.entries || [];
renderTree();
}

/* ── 树渲染 ── */
function renderTree() {
const container = $("browse");
container.innerHTML = "";
flatList = [];
const filter = $("search").value.trim().toLowerCase();
const imgMode = isImageMode();

for (const entry of treeData) {
  const path = curDir + "\\" + entry.name;
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
cb.checked = isDir ? isDirFullyChecked(entry, path, imgMode) : sel.has(path);
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
flatList.push(flatItem);

// ── 事件 ──
// 复选框：切换选中
cb.addEventListener("click", (e) => {
  e.stopPropagation();
  if (isDir) {
    toggleDirCheck(entry, path, imgMode, cb.checked);
  } else {
    if (cb.checked) sel.add(path); else sel.delete(path);
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
    if (sel.has(path)) sel.delete(path); else sel.add(path);
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
  for (const f of files) sel.add(f);
} else {
  for (const f of files) sel.delete(f);
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
return files.length > 0 && files.every(f => sel.has(f));
}

function isDirIndeterminate(entry, path, imgMode) {
const files = collectFiles(entry, path, imgMode);
if (files.length === 0) return false;
const checkedCount = files.filter(f => sel.has(f)).length;
return checkedCount > 0 && checkedCount < files.length;
}

function isOkFile(name, imgMode) {
if (imgMode) return false; // 图片模式只选目录
const ext = name.split(".").pop().toLowerCase();
return okExts().has(ext);
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

function escapeHtml(s) { return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

function updateBrowseCount() {
const total = flatList.filter(x => !x.entry.is_dir).length;
const checked = flatList.filter(x => !x.entry.is_dir && sel.has(x.path)).length;
$("browsecnt").textContent = `${checked}/${total} 文件已选`;
}

/* ── 全选 / 反选 ── */
$("btn-selall").onclick = () => {
for (const item of flatList) {
  if (!item.entry.is_dir && isOkFile(item.entry.name, isImageMode())) sel.add(item.path);
}
renderTree(); renderSel();
};
$("btn-invsel").onclick = () => {
for (const item of flatList) {
  if (!item.entry.is_dir && isOkFile(item.entry.name, isImageMode())) {
    if (sel.has(item.path)) sel.delete(item.path); else sel.add(item.path);
  }
}
renderTree(); renderSel();
};
$("btn-expand").onclick = () => { setAllExpanded(treeData, true); renderTree(); };
$("btn-collapse").onclick = () => { setAllExpanded(treeData, false); renderTree(); };
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
  if ($("search").value.trim()) setAllExpanded(treeData, true);
  renderTree();
}, 200);
});

/* ── 键盘导航 ── */
$("browse").addEventListener("keydown", (e) => {
if (!flatList.length) return;
if (e.key === "ArrowDown") { e.preventDefault(); moveFocus(1); }
else if (e.key === "ArrowUp") { e.preventDefault(); moveFocus(-1); }
else if (e.key === "Enter" || e.key === " ") {
  e.preventDefault();
  if (focusIdx >= 0 && focusIdx < flatList.length) {
    const item = flatList[focusIdx];
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
  if (focusIdx >= 0) { const item = flatList[focusIdx]; if (item.entry.is_dir && !item.entry._expanded) {
    item.entry._expanded = true; renderTree(); setFocus(focusIdx);
  }}
}
else if (e.key === "ArrowLeft") {
  // 左箭头折叠文件夹
  if (focusIdx >= 0) { const item = flatList[focusIdx]; if (item.entry.is_dir && item.entry._expanded) {
    item.entry._expanded = false; renderTree(); setFocus(focusIdx);
  }}
}
});

function moveFocus(delta) {
setFocus(Math.max(0, Math.min(flatList.length - 1, focusIdx + delta)));
}
function setFocus(idx) {
// 清除旧焦点
if (focusIdx >= 0 && focusIdx < flatList.length) flatList[focusIdx].row.classList.remove("focused");
focusIdx = idx;
if (focusIdx >= 0 && focusIdx < flatList.length) {
  flatList[focusIdx].row.classList.add("focused");
  flatList[focusIdx].row.scrollIntoView({ block: "nearest" });
}
}

/* ── 上级 / 选当前目录 ── */
$("up").onclick = () => {
if (curDir === rootDir) return;
const p = curDir.replace(/\\+$/, "");
const idx = p.lastIndexOf("\\");
browse(idx > 0 ? p.slice(0, idx) : rootDir);
};
$("pickdir").onclick = () => { sel.add(curDir); renderSel(); };

/* ════════════════════════════════════════════════════════════
 已选文件 — 分组显示
 ════════════════════════════════════════════════════════════ */

function renderSel() {
const ul = $("selected");
ul.innerHTML = "";

// 按目录分组
const groups = new Map(); // dir -> [file, ...]
for (const p of sel) {
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
  rmGroup.onclick = () => { for (const it of items) sel.delete(it.path); renderSel(); renderTree(); };
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
    rm.onclick = () => { sel.delete(item.path); renderSel(); renderTree(); };
    li.appendChild(nm); li.appendChild(rm);
    ul.appendChild(li);
  }
}

$("selcount").textContent = sel.size ? `（${sel.size} 个）` : "";
}

$("clear").onclick = () => { sel.clear(); renderSel(); renderTree(); };
$("addpaths").onclick = () => {
for (const line of $("paths").value.split("\n")) {
  const p = line.trim();
  if (p) sel.add(p);
}
$("paths").value = "";
renderSel();
};

/* ════════════════════════════════════════════════════════════
 转换 / OCR / Watch（逻辑不变，仅 UI 微调）
 ════════════════════════════════════════════════════════════ */

function logLine(container, text, cls) {
const div = document.createElement("div");
div.className = "log-line " + (cls || "");
div.textContent = text;
container.appendChild(div);
container.scrollTop = container.scrollHeight;
}

document.querySelectorAll("input[name=target]").forEach(r => {
r.onchange = () => {
  const img = isImageMode();
  const aud = (currentTarget() === "audio");
  $("ocropts").style.display = img ? "" : "none";
  $("ttsopts").style.display = aud ? "" : "none";
  $("ttscountwrap").style.display = aud ? "" : "none";  // 版本数只在 TTS 目标下出现
  $("pickdir").style.display = img ? "" : "none";
  if (img) ocrCheck();
  if (aud) ttsCheck();
  updateTargetHint();
  renderTree(); // 切换模式时刷新树（图片模式隐藏文件复选框；可选扩展名也随目标变化）
};
});

/* ── TTS 环境与音色 ── */
let ttsLoaded = false;
let ttsServePoll = null;
async function ttsCheck() {
const st = $("ttsstatus");
try {
  const d = await api("/api/tts/voices");
  if (!d.ok) { st.textContent = "⚠ " + (d.message || "TTS 环境未就绪"); $("ttsserve").disabled = false; return d; }
  const svc = d.service || {};
  if (svc.alive) {
    st.textContent = `● 就绪 · GPT-SoVITS 服务在跑（${svc.host}:${svc.port}）`;
    st.style.color = "#16a34a";
  } else {
    st.textContent = "● 就绪 · GPT-SoVITS 服务未启动（点「▶ 启动 GPT-SoVITS 服务」，或把引擎切到 GENIE）";
    st.style.color = "#e08b00";
  }
  $("ttsserve").disabled = !!svc.alive;
  if (!ttsLoaded) {
    const selv = $("tts_voice");
    const add = (label, names) => (names || []).forEach(n => {
      const o = document.createElement("option");
      o.value = n;
      o.textContent = n + "（" + label + "）";
      selv.appendChild(o);
    });
    add("预设角色", d.builtin);
    add("我的音色", d.registry);
    ttsLoaded = true;
  }
  return d;
} catch (e) {
  st.textContent = "⚠ 查询失败：" + e;
  return null;
}
}

/* ── GPT-SoVITS 服务：在界面里直接起停 ── */
$("ttsserve").onclick = async () => {
const st = $("ttsstatus");
st.textContent = "启动中…（模型加载要几十秒，先别关这个页面）";
st.style.color = "#e08b00";
try {
  const d = await api("/api/tts/serve/start", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  });
  st.textContent = (d.started ? "⏳ " : "ℹ ") + (d.message || "");
  if (!d.started) { st.style.color = d.alive ? "#16a34a" : "#dc2626"; return; }
  logLine($("log"), "[TTS] " + d.message, "dim");
  clearInterval(ttsServePoll);
  let n = 0;
  ttsServePoll = setInterval(async () => {
    n += 1;
    const r = await ttsCheck();
    if ((r && r.service && r.service.alive) || n > 40) {
      clearInterval(ttsServePoll); ttsServePoll = null;
    }
  }, 3000);
} catch (e) { st.textContent = "启动失败：" + e; st.style.color = "#dc2626"; }
};
$("ttsstop").onclick = async () => {
const st = $("ttsstatus");
try {
  const d = await api("/api/tts/serve/stop", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  });
  st.textContent = d.message || "";
  st.style.color = d.stopped ? "#666" : "#e08b00";
} catch (e) { st.textContent = "停止失败：" + e; st.style.color = "#dc2626"; }
clearInterval(ttsServePoll); ttsServePoll = null;
ttsCheck();
};

async function ocrCheck() {
const st = $("ocrstatus");
try {
  const d = await api("/api/ocr/check");
  let msg = d.message || "";
  if (d.pulling) msg += "（拉取中…"+(d.pull_lines?.length ? d.pull_lines[d.pull_lines.length-1] : "")+"）";
  st.textContent = "🔍 " + msg;
  st.style.color = d.running && d.model ? "#16a34a" : "#dc2626";
} catch (e) { st.textContent = "🔍 检查失败: " + e; st.style.color = "#dc2626"; }
}
function ocrPost(path) {
return api(path, { method:"POST", headers:{"Content-Type":"application/json"}, body:"{}" });
}
function pollOcr() {
ocrCheck();
setTimeout(async () => { try { const d = await api("/api/ocr/check"); if (d.pulling) pollOcr(); else ocrCheck(); } catch(e){ocrCheck();} }, 5000);
}
$("ocrstart").onclick = async () => { $("ocrstatus").textContent="启动 Ollama 中…"; await ocrPost("/api/ocr/start"); setTimeout(ocrCheck,1000); };
$("ocrpull").onclick = async () => { $("ocrstatus").textContent="开始拉取模型（可能需几分钟）…"; const d=await ocrPost("/api/ocr/pull"); if(!d.started){$("ocrstatus").textContent="🔍 "+d.message;} pollOcr(); };

$("convert").onclick = async () => {
const target = document.querySelector("input[name=target]:checked").value;
const log = $("log");
log.innerHTML = "";
if (sel.size === 0) {
  logLine(log, "请先在左侧选择文件或目录", "err");
  return;
}
const btn = $("convert");
btn.disabled = true;
$("convstate").textContent = "转换中…";
logLine(log, `[任务] 目标=${target} 文件/目录数=${sel.size}`, "dim");
try {
  const d = await api("/api/convert", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      files: [...sel],
      target, engine: $("engine").value,
      outdir: $("outdir").value.trim(),
      open_pdf: $("openpdf").checked,
      audio_format: $("tts_format").value,
      voice: $("tts_voice").value,
      tts_engine: $("tts_engine").value,
      tts_draw: $("tts_draw").checked,
      tts_count: $("tts_count").value,
      audio_tail_pad: $("tts_tail_pad").value,
      audio_srt: $("tts_srt").checked,
      recursive: $("ocr_recursive").checked,
      force: $("force").checked,
    }),
  });
  for (const res of (d.results || [])) {
    logLine(log, res.ok ? "✓ " + res.file : "✗ " + res.file, res.ok ? "ok" : "err");
    for (const s of res.steps) {
      const skipped = (s.out || "").includes("[跳过]");
      logLine(log, `   [${s.step}] ${s.ok ? (skipped ? "跳过（产物已存在，本次没重新生成）" : "成功") : "失败"}`,
              s.ok ? (skipped ? "dim" : "ok") : "err");
      if (s.out) logLine(log, s.out.trimEnd(), "dim");
      if (s.err) logLine(log, s.err.trimEnd(), "err");
    }
  }
} catch (e) { logLine(log, "请求失败: " + e, "err"); }
$("convstate").textContent = "完成";
btn.disabled = false;
};

/* ── watch ── */
async function refreshWatch() {
try {
  const d = await api("/api/watch");
  $("wstatus").textContent = d.running ? `● 监听中：${d.file}` : "○ 已停止";
  if (d.running && d.lines.length) {
    $("wlog").innerHTML = "";
    const wl = $("wlog");
    for (const l of d.lines.slice(-30)) logLine(wl, l, l.includes("✗") ? "err" : "dim");
  }
} catch (e) { /* 忽略 */ }
}
$("wstart").onclick = async () => {
const file = $("watchfile").value.trim();
if (!file) { alert("请填写要监听的 md 文件路径"); return; }
await api("/api/watch", {
  method:"POST", headers:{"Content-Type":"application/json"},
  body: JSON.stringify({ action:"start", file, outdir:$("outdir").value.trim() }),
});
refreshWatch();
};
$("wstop").onclick = async () => {
await api("/api/watch", { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({action:"stop"}) });
refreshWatch();
};
setInterval(refreshWatch, 2000);

/* ── 初始 ── */
updateTargetHint();
browse("");
