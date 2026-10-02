"use strict";

/* ════════════════════════════════════════════════════════════
 开始转换 + 日志渲染（从 app.js 原样搬来）
 ════════════════════════════════════════════════════════════ */

import { $, logLine } from "./dom.js";
import { api } from "./api.js";
import { S } from "./state.js";

$("convert").onclick = async () => {
const target = document.querySelector("input[name=target]:checked").value;
const log = $("log");
log.innerHTML = "";
if (S.sel.size === 0) {
  logLine(log, "请先在左侧选择文件或目录", "err");
  return;
}
const btn = $("convert");
btn.disabled = true;
$("convstate").textContent = "转换中…";
logLine(log, `[任务] 目标=${target} 文件/目录数=${S.sel.size}`, "dim");
try {
  const d = await api("/api/convert", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      files: [...S.sel],
      target, engine: $("engine").value,
      outdir: $("outdir").value.trim(),
      open_pdf: $("openpdf").checked,
      audio_format: $("tts_format").value,
      voice: $("tts_voice").value,
      tts_engine: $("tts_engine").value,
      tts_model: $("tts_model") ? $("tts_model").value : "",
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
