"use strict";

/* ════════════════════════════════════════════════════════════
 入口：只做组装 —— 全局错误可见化、健康检查、初始渲染。
 各模块的事件绑定在各自模块顶层完成（import 进来就生效）：
   browse.js    文件浏览树 / 全选反选 / 搜索 / 键盘导航 / 上级
   selection.js 已选文件分组列表 / 清空 / 手动粘路径
   options.js   目标格式切换 / 扩展名白名单 / 目标提示
   ocr.js       Ollama 检查与模型拉取
   tts.js       音色列表 / 起停服务 / /api/tts/status 轮询
   convert.js   开始转换 + 转换日志
   log.js       日志复制到剪贴板 / 下载成 .txt
   watch.js     实时监听
 ════════════════════════════════════════════════════════════ */

import { $ } from "./modules/dom.js";
import { api } from "./modules/api.js";
import { browse } from "./modules/browse.js";
import { updateTargetHint } from "./modules/options.js";
import "./modules/selection.js";
import "./modules/convert.js";
import "./modules/log.js";
import "./modules/watch.js";

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

/* ── 健康检查 ── */
api("/api/health").then(d => { $("health").textContent = d.ok ? "● 服务正常" : "● 异常"; })
.catch(() => $("health").textContent = "● 无法连接");

/* ── 初始 ── */
updateTargetHint();
browse("");
