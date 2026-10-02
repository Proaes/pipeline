"use strict";

/* ════════════════════════════════════════════════════════════
 图片 → MD（OCR）：Ollama 环境检查与模型拉取（从 app.js 原样搬来）
 ════════════════════════════════════════════════════════════ */

import { $ } from "./dom.js";
import { api, ocrPost } from "./api.js";

export async function ocrCheck() {
const st = $("ocrstatus");
try {
  const d = await api("/api/ocr/check");
  let msg = d.message || "";
  if (d.pulling) msg += "（拉取中…"+(d.pull_lines?.length ? d.pull_lines[d.pull_lines.length-1] : "")+"）";
  st.textContent = "🔍 " + msg;
  st.style.color = d.running && d.model ? "#16a34a" : "#dc2626";
} catch (e) { st.textContent = "🔍 检查失败: " + e; st.style.color = "#dc2626"; }
}

function pollOcr() {
ocrCheck();
setTimeout(async () => { try { const d = await api("/api/ocr/check"); if (d.pulling) pollOcr(); else ocrCheck(); } catch(e){ocrCheck();} }, 5000);
}

$("ocrstart").onclick = async () => { $("ocrstatus").textContent="启动 Ollama 中…"; await ocrPost("/api/ocr/start"); setTimeout(ocrCheck,1000); };
$("ocrpull").onclick = async () => { $("ocrstatus").textContent="开始拉取模型（可能需几分钟）…"; const d=await ocrPost("/api/ocr/pull"); if(!d.started){$("ocrstatus").textContent="🔍 "+d.message;} pollOcr(); };
