"use strict";

/* ════════════════════════════════════════════════════════════
 开始转换 + 日志渲染

 261002 七次（用户要求）：转换入口有两个 ——
   ① 「转换选项」栏里那个（#convert，原来就有）
   ② 「📋 已选文件」栏里那个（#convert2，新加）
 两者**共用同一个 startConvert()**：选完文件就在原地能按，不用再滚回上面去；
 运行时两个一起禁用，避免连点两次并发转换。
 ════════════════════════════════════════════════════════════ */

import { $, logLine } from "./dom.js";
import { api } from "./api.js";
import { S } from "./state.js";

const CONVERT_BUTTONS = ["convert", "convert2"];

/** 两个入口共用：跑一次转换，结果写进日志。 */
export async function startConvert() {
const target = document.querySelector("input[name=target]:checked").value;
const log = $("log");
log.innerHTML = "";
if (S.sel.size === 0) {
  logLine(log, "请先在左侧选择文件或目录", "err");
  return;
}
const btns = CONVERT_BUTTONS.map($).filter(Boolean);
btns.forEach(b => { b.disabled = true; });
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
btns.forEach(b => { b.disabled = false; });
}

for (const id of CONVERT_BUTTONS) {
  const b = $(id);
  if (b) b.onclick = startConvert;
}
