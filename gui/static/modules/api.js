"use strict";

/* ── 请求封装：界面里所有 fetch 都从这里走 ── */

/** 请求并解 JSON。注意：HTTP 404 也照样解出 `{"error":"not found"}`，不抛异常。 */
export async function api(path, opts) { return (await fetch(path, opts)).json(); }

/** OCR 那几个 POST 端点都用空 JSON 体。 */
export function ocrPost(path) {
return api(path, { method:"POST", headers:{"Content-Type":"application/json"}, body:"{}" });
}
