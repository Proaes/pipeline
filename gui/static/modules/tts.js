"use strict";

/* ════════════════════════════════════════════════════════════
 TTS：环境/音色查询 + 「启动模型」后的状态轮询
 轮询契约见 _shared/INTERFACES.md §2.3：
   点「启动 GPT-SoVITS 服务」后不锁死其他选项，用 GET /api/tts/status 轮询
   （2 s 一次，上限 120 s），按 stage ∈ {down,starting,ready,error} 分支显示。
   后端还没上线该端点时（404）自动降级用 /api/tts/voices 探活，上线后自动切回。
 ════════════════════════════════════════════════════════════ */

import { $, logLine } from "./dom.js";
import { api } from "./api.js";

const STATUS_POLL_MS = 2000;           // 契约建议 1.5–3 s
const STATUS_TIMEOUT_MS = 120000;     // 上限 120 s，之后提示查日志
const STATUS_RETRY_MS = 30000;        // 降级之后每 30 s 再探一次契约端点，后端上线就自动切回
const COLOR_READY = "#16a34a";
const COLOR_WAIT = "#e08b00";
const COLOR_ERR = "#dc2626";

/* 引擎展示名；只有 gptsovits 需要常驻服务 */
const ENGINE_LABELS = { gptsovits: "GPT-SoVITS", genie: "GENIE", qwen: "Qwen3-TTS" };

let ttsLoaded = false;       // 音色下拉是否已填充（只填一次）
let ttsServePoll = null;     // 启动后的轮询定时器
let ttsStatusOk = null;      // null=还没探过；true=后端有 /api/tts/status；false=探到没有，先用 voices 降级
let lastStatusProbe = 0;     // 上次探契约端点的时间
let lastStatus = null;       // 最近一次 status，切换引擎时据此重画状态栏，不额外发请求

function engineName() { return ENGINE_LABELS[$("tts_engine").value] || $("tts_engine").value; }
function engineNeedsService() { return $("tts_engine").value === "gptsovits"; }

function setStatus(text, color) {
const st = $("ttsstatus");
st.textContent = text;
st.style.color = color;
}

/** 降级路径：用 /api/tts/voices 里已有的 service.alive 拼一个同形状的 status。
   已经拿到 voices 应答的调用方把它传进来，省一次请求。 */
async function statusFromVoices(voicesData) {
const d = voicesData || await api("/api/tts/voices");
const svc = (d && d.service) || {};
const alive = !!svc.alive;
return {
  alive,
  ready: alive,
  stage: alive ? "ready" : "down",
  message: (d && d.message) || "",
  host: svc.host || "",
  port: svc.port || 0,
  pid: null,
  managed_by_gui: false,
};
}

/** 取一份 status：优先契约端点；探到 404 就先降级到 voices，之后每 30 s 再探一次，
    后端一上线就自动切回契约端点（降级提示只写一次，避免刷屏）。 */
async function fetchTtsStatus(voicesData) {
const now = Date.now();
if (ttsStatusOk !== false || now - lastStatusProbe > STATUS_RETRY_MS) {
  lastStatusProbe = now;
  try {
    const d = await api("/api/tts/status");
    if (d && typeof d.stage === "string") { ttsStatusOk = true; return d; }
    if (ttsStatusOk === null) {
      ttsStatusOk = false;
      logLine($("log"), "[TTS] 后端暂无 /api/tts/status，已降级用 /api/tts/voices 探活（后端上线后自动切回）", "dim");
    }
  } catch (e) {
    if (ttsStatusOk === null) {
      ttsStatusOk = false;
      logLine($("log"), "[TTS] /api/tts/status 查询失败，已降级用 /api/tts/voices 探活：" + e, "dim");
    }
  }
}
return await statusFromVoices(voicesData);
}

/** 按 stage 四值分支画状态栏；down 时再分「当前引擎要不要服务」。 */
function renderStage(stt) {
lastStatus = stt;
const stage = stt.stage || "down";
const where = (stt.host && stt.port) ? `（${stt.host}:${stt.port}）` : "";
if (stage === "ready") { setStatus(`● 就绪 · GPT-SoVITS 服务在跑${where}`, COLOR_READY); return; }
if (stage === "starting") { setStatus(`⏳ 正在加载模型…${where}（可以继续改下面的选项，不用等）`, COLOR_WAIT); return; }
if (stage === "error") { setStatus("⚠ " + (stt.message || "GPT-SoVITS 服务启动失败"), COLOR_ERR); return; }
// stage === "down"
if (engineNeedsService()) {
  setStatus("● 就绪 · GPT-SoVITS 服务未启动（点「▶ 启动 GPT-SoVITS 服务」，或把引擎切到 GENIE）", COLOR_WAIT);
} else {
  setStatus(`● 就绪 · 当前引擎（${engineName()}）不需要 GPT-SoVITS 服务`, COLOR_READY);
}
}

function stopServePoll() { clearInterval(ttsServePoll); ttsServePoll = null; }

/**
 * 启动后轮询：不 disable 任何选项，只画状态栏。
 * 120 s 还没就绪就停下并提示查日志（message 里已带日志路径）。
 */
function startServePoll() {
stopServePoll();
const t0 = Date.now();
ttsServePoll = setInterval(async () => {
  let stt;
  try { stt = await fetchTtsStatus(); }
  catch (e) { stt = { stage: "error", message: "查询状态失败：" + e }; }
  // 降级路径只能给出 alive，等待期间把 down 显示成 starting，避免误导成"没启动"
  const view = (ttsStatusOk === false && stt.stage === "down") ? Object.assign({}, stt, { stage: "starting" }) : stt;
  renderStage(view);
  if (view.stage === "ready" || view.stage === "error") { stopServePoll(); return; }
  if (Date.now() - t0 > STATUS_TIMEOUT_MS) {
    stopServePoll();
    const hint = view.message ? ("，日志：" + view.message) : "，日志：" + "output/tts/p10_serve_si.log";
    setStatus(`⚠ 超过 ${STATUS_TIMEOUT_MS / 1000} s 仍未就绪，请查启动日志${hint}`, COLOR_ERR);
  }
}, STATUS_POLL_MS);
}

/* ── TTS 环境与音色 ── */
export async function ttsCheck() {
const st = $("ttsstatus");
try {
  const d = await api("/api/tts/voices");
  if (!d.ok) { st.textContent = "⚠ " + (d.message || "TTS 环境未就绪"); $("ttsserve").disabled = false; return d; }
  const svc = d.service || {};
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
  // 状态栏优先用 /api/tts/status（契约端点），拿不到就用上面的 service 字段兜底
  let stt = null;
  try { stt = await fetchTtsStatus(d); } catch (e) { stt = null; }
  if (!stt) stt = { alive: !!svc.alive, ready: !!svc.alive, stage: svc.alive ? "ready" : "down", message: d.message || "", host: svc.host || "", port: svc.port || 0 };
  renderStage(stt);
  $("ttsserve").disabled = !!stt.alive;
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
st.style.color = COLOR_WAIT;
try {
  const d = await api("/api/tts/serve/start", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  });
  st.textContent = (d.started ? "⏳ " : "ℹ ") + (d.message || "");
  if (!d.started) { st.style.color = d.alive ? COLOR_READY : COLOR_ERR; return; }
  logLine($("log"), "[TTS] " + d.message, "dim");
  logLine($("log"), "[TTS] 加载期间可以继续改引擎/输出目录/格式/版本数，不用等", "dim");
  startServePoll();
} catch (e) { st.textContent = "启动失败：" + e; st.style.color = COLOR_ERR; }
};
$("ttsstop").onclick = async () => {
const st = $("ttsstatus");
try {
  const d = await api("/api/tts/serve/stop", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  });
  st.textContent = d.message || "";
  st.style.color = d.stopped ? "#666" : COLOR_WAIT;
} catch (e) { st.textContent = "停止失败：" + e; st.style.color = COLOR_ERR; }
stopServePoll();
ttsCheck();
};

/* 切引擎时重画状态栏（gptsovits 需服务，genie/qwen 不需要），不额外发请求 */
$("tts_engine").addEventListener("change", () => { if (lastStatus) renderStage(lastStatus); });
