"use strict";

/* ════════════════════════════════════════════════════════════
 实时监听（保存 md 即自动转换）（从 app.js 原样搬来）
 ════════════════════════════════════════════════════════════ */

import { $, logLine } from "./dom.js";
import { api } from "./api.js";

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
