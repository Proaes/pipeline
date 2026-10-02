"use strict";

/* ════════════════════════════════════════════════════════════
 目标格式 / 引擎 / 可转换扩展名（从 app.js 原样搬来）
 ════════════════════════════════════════════════════════════ */

import { $ } from "./dom.js";
import { S } from "./state.js";
import { renderTree } from "./browse.js";
import { ocrCheck } from "./ocr.js";
import { ttsCheck } from "./tts.js";

/* 目标格式 → 可接受的输入扩展名（与 server.py 的 SUPPORTED_EXTS 保持一致） */
export const TARGET_EXTS = {
pdf:  new Set(["md", "tex", "ppt", "pptx"]),
tex:  new Set(["md", "ppt", "pptx"]),
json: new Set(["md", "ppt", "pptx"]),
md:   new Set(["ppt", "pptx"]),
docx: new Set(["ppt", "pptx"]),
lint: new Set(["md"]),
audio: new Set(["md", "markdown", "txt"]),
};
export const TARGET_HINTS = {
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
export const IMG_EXTS = new Set(["jpg","jpeg","png","bmp","webp","tiff","tif","gif"]);

export function currentTarget() { return document.querySelector("input[name=target]:checked").value; }
export function isImageMode() { return currentTarget() === "image"; }
export function okExts() { return TARGET_EXTS[currentTarget()] || new Set(); }
export function updateTargetHint() {
const h = $("targethint");
if (!h) return;
const t = currentTarget();
const note = (t !== "image" && (TARGET_EXTS[t] || new Set()).has("ppt")) ? PPT_NOTE : "";
h.textContent = "ℹ " + (TARGET_HINTS[t] || "") + note;
}

/** 图片模式只选目录，不选文件；其余模式看当前目标的扩展名白名单。 */
export function isOkFile(name, imgMode) {
if (imgMode) return false;
const ext = name.split(".").pop().toLowerCase();
return okExts().has(ext);
}

/* ── 切目标格式：显示/隐藏该模式专属的选项 ── */
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
