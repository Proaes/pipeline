"use strict";

/* ── DOM 小工具：全模块共享 ── */

/** 按 id 取元素。拆模块前这里是 app.js 顶部的 `const $ = ...`。 */
export const $ = id => document.getElementById(id);

/** 往日志容器追加一行并滚到底。 */
export function logLine(container, text, cls) {
const div = document.createElement("div");
div.className = "log-line " + (cls || "");
div.textContent = text;
container.appendChild(div);
container.scrollTop = container.scrollHeight;
}

/** HTML 转义：树里文件名要高亮（innerHTML），必须先转义。 */
export function escapeHtml(s) { return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
