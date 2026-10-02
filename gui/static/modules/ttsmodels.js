"use strict";

/* ════════════════════════════════════════════════════════════
 语音引擎 / 模型下拉（数据源 = 后端 GET /api/tts/models，契约 §2.7）

 为什么要有这一层：一个「模型」= 一个引擎 + 一套权重。用户要能一眼看出
 「祀-G」是微调权重还是原版权重，所以引擎名与模型名后面都跟着一句简要描述。
 引擎下拉决定模型下拉的内容（选 GENIE 就只能选 ONNX 那条），反过来选某个模型
 也会把引擎切回它所属的那一支 —— 两处永远自洽，不会出现「GENIE + PyTorch 权重」。

 模型权重文件缺失（注册表里有、磁盘上没有）时该条置灰并写明「缺失」，
 免得点了半天才发现服务切不过去。
 ════════════════════════════════════════════════════════════ */

import { $, logLine } from "./dom.js";
import { api } from "./api.js";

let modelsData = null;      // 最近一次 /api/tts/models 的应答
let loading = false;

/** 引擎下拉的 value → 描述。描述直接来自后端（注册表里每支引擎的 summary）。 */
function engineSummary(id) {
  const e = ((modelsData || {}).engines || []).find(x => x.id === id);
  return e ? (e.summary || "") : "";
}

/** 按 id 取引擎对象。 */
function engineOf(id) {
  return ((modelsData || {}).engines || []).find(x => x.id === id) || null;
}

/** 当前选中模型（取不到就返回 null）。 */
export function currentModel() {
  if (!modelsData) return null;
  const id = $("tts_model") && $("tts_model").value;
  if (!id) return null;
  for (const e of modelsData.engines) {
    for (const m of e.models) if (m.id === id) return m;
  }
  return null;
}

/** 当前选中模型是不是别的语言/音色要用的引擎（切引擎时用）。 */
export function modelEngineId() {
  const m = currentModel();
  return m ? m.engine : ($("tts_engine") ? $("tts_engine").value : "");
}

/** 引擎下拉的 option 文案：`<名字>（<一句描述>）` —— 描述不丢。 */
function engineOptionText(e) {
  return e.summary ? `${e.label}（${e.summary}）` : e.label;
}

function renderEngines(sel) {
  sel.innerHTML = "";
  for (const e of modelsData.engines) {
    const o = document.createElement("option");
    o.value = e.id;
    o.textContent = engineOptionText(e);
    sel.appendChild(o);
  }
}

/** 模型下拉：只列当前引擎的模型；缺文件的置灰。 */
export function renderModels(preferEngine) {
  const sel = $("tts_model");
  if (!sel || !modelsData) return;
  const eid = preferEngine || ($("tts_engine") ? $("tts_engine").value : "");
  const eng = engineOf(eid) || modelsData.engines[0];
  if (!eng) return;
  sel.innerHTML = "";
  for (const m of eng.models) {
    const o = document.createElement("option");
    o.value = m.id;
    const missing = (m.exists === false);
    o.textContent = missing ? `${m.label}（⚠ 权重文件缺失）` : m.label;
    o.disabled = missing;
    sel.appendChild(o);
  }
  if (sel.options.length && !sel.value) sel.selectedIndex = 0;
  renderModelNote();
}

/** 模型名下面那行说明：模型自己的备注 + 引擎描述；服务在跑时补一句当前权重。 */
export function renderModelNote() {
  const el = $("tts_model_note");
  if (!el) return;
  const m = currentModel();
  if (!m) { el.textContent = modelsData ? "（没有可用模型）" : "（模型清单加载中…）"; return; }
  const parts = [];
  if (m.note) parts.push(m.note);
  const sum = engineSummary(m.engine);
  if (sum) parts.push(sum);
  const cur = (modelsData.current || null);
  if (cur) {
    const name = (function () {
      for (const e of modelsData.engines) for (const x of e.models) if (x.id === cur.model) return x.label;
      return "（注册表里没登记）";
    })();
    parts.push(`服务当前加载：${name}`);
  }
  el.textContent = parts.join(" ｜ ");
}

/** 拉一次模型清单并铺开两个下拉。失败时把原因写进日志，不抛。 */
export async function loadTtsModels() {
  if (loading) return modelsData;
  loading = true;
  try {
    const d = await api("/api/tts/models");
    if (!d || !d.ok) {
      const log = $("log");
      if (log) logLine(log, "[TTS] 模型清单不可用：" + ((d && d.message) || "未知原因"), "err");
      const sel = $("tts_model");
      if (sel) sel.innerHTML = '<option value="">（清单不可用）</option>';
      return null;
    }
    modelsData = d;
    const esel = $("tts_engine");
    if (esel) {
      const keep = esel.value;
      renderEngines(esel);
      if ([...esel.options].some(o => o.value === keep)) esel.value = keep;
      renderModels(esel.value);
    }
    return d;
  } catch (e) {
    const log = $("log");
    if (log) logLine(log, "[TTS] /api/tts/models 查询失败：" + e, "err");
    return null;
  } finally {
    loading = false;
  }
}

/* ── 事件绑定：切引擎换模型清单 / 点按钮热切权重 ── */
const esel = $("tts_engine");
if (esel) {
  esel.addEventListener("change", () => {
    // 引擎是模型清单的主：切引擎就把模型切到该引擎的第一条，两处保持自洽
    renderModels(esel.value);
    const apply = $("ttsmodelapply");
    if (apply) apply.textContent = "⟳ 切到该模型";
  });
}
const msel = $("tts_model");
if (msel) {
  // 选到别的引擎的模型（正常路径下不会发生）也把引擎跟着切过去
  msel.addEventListener("change", () => {
    const m = currentModel();
    if (m && esel && esel.value !== m.engine) {
      esel.value = m.engine;
      renderModels(m.engine);
      if (msel) msel.value = m.id;
    }
    renderModelNote();
  });
}
const applyBtn = $("ttsmodelapply");
if (applyBtn) {
  applyBtn.onclick = async () => {
    const m = currentModel();
    if (!m) return;
    const log = $("log");
    applyBtn.disabled = true;
    applyBtn.textContent = "⟳ 切换中…";
    let ok = false;
    try {
      const d = await api("/api/tts/model/apply", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: m.id }),
      });
      ok = !!d.ok;
      if (log) {
        logLine(log, "[TTS] " + (d.message || d.error || "已请求切换"),
                d.ok ? (d.switched ? "ok" : "dim") : "err");
        for (const s of d.steps || []) logLine(log, "   " + s, "dim");
      }
      await loadTtsModels();   // 重拉一次，让「服务当前加载」跟上
    } catch (e) {
      if (log) logLine(log, "[TTS] 切换失败：" + e, "err");
    }
    applyBtn.disabled = false;
    applyBtn.textContent = ok ? "⟳ 再切一次" : "⟳ 重试切换";
  };
}
