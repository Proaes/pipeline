"use strict";

/* ════════════════════════════════════════════════════════════
 转换日志工具：复制到剪贴板 / 下载成 .txt
 （用户 261002 要求：sec-log 增加复制功能和下载日志）
 纯前端实现，不需要任何新接口。
 ════════════════════════════════════════════════════════════ */

import { $, logLine } from "./dom.js";

/** 取日志区当前全文（innerText 拿的是渲染后的文字，换行/缩进与看到的一致）。 */
function logText() {
const box = $("log");
if (!box) return "";
const t = (box.innerText !== undefined && box.innerText !== null) ? box.innerText : box.textContent;
return String(t || "").replace(/\s+$/, "");
}

/* ── 按钮旁的小反馈，1.8 s 后自己消失 ── */
let flashTimer = null;
function flash(msg) {
const st = $("logstate");
if (!st) return;
st.textContent = msg;
clearTimeout(flashTimer);
flashTimer = setTimeout(() => { st.textContent = ""; }, 1800);
}

/** 复制：优先异步剪贴板 API；它不可用（非安全上下文 / 被策略挡）就退回 execCommand。 */
async function copyToClipboard(text) {
if (navigator.clipboard && navigator.clipboard.writeText) {
  try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* 落到兜底 */ }
}
try {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.top = "-1000px";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(ta);
  return !!ok;
} catch (e) { return false; }
}

/** 下载文件名后缀：pipeline-log-20261002-233000.txt */
function stamp() {
const d = new Date();
const p = n => String(n).padStart(2, "0");
return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

$("logcopy").onclick = async () => {
const text = logText();
if (!text) { flash("日志是空的"); return; }
const ok = await copyToClipboard(text);
if (ok) { flash("已复制 ✓"); return; }
flash("复制失败");
logLine($("log"), "[日志] 自动复制失败，请手动选中日志后 Ctrl+C", "err");
};

$("logdownload").onclick = () => {
const text = logText();
if (!text) { flash("日志是空的"); return; }
try {
  const blob = new Blob([text + "\n"], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "pipeline-log-" + stamp() + ".txt";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  flash("已下载 ✓");
} catch (e) {
  flash("下载失败");
  logLine($("log"), "[日志] 下载失败：" + e, "err");
}
};
