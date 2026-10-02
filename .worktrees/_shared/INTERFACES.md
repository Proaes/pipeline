# 接口冻结页（先冻结、再并行 · base 维护）

> 并行开工**之前**，由 base 把所有跨分支的约定钉死在这里：函数签名、文件路径、数据格式、命名。
> 并行期间任何一方要改约定，**先在本文件追加一行改判**，再在任务卡里写明，最后才动代码。
> 改判格式：`→ 状态 yymmdd 改判 <新约定>`，**不回改原文**。
> **本页现在管两条接缝**：① 静态资源 URL 方案（S1/S2 已实现，§1）；② 前后端分工的 API 契约（待后端实现，§2）。
> 最后更新：261002。

## 1. 静态资源 URL 方案（✅ 已实现 · 分支 `wt/前端重构基础`）

- **约定**：静态资源一律走 **`/static/<相对 static 目录的路径>`**；`/` 与 `/index.html` 仍返回 `gui/static/index.html`。
- 允许的扩展名白名单：`.html .js .mjs .css .json .svg .png .jpg .jpeg .gif .ico .woff2 .map`
- **`Cache-Control: no-store` 对静态资源同样生效** —— 否则改了 JS，浏览器还用旧缓存，界面"看起来没变"（实测踩点）。
- **安全**：只允许 `gui/static/` 之内的文件（`resolve()` 后必须仍在 static 内）；后缀不在白名单一律 404。
- 实测（端口 8791）：`/static/app.css` 200 `text/css` · `/static/app.js` 200 `text/javascript` · `/static/nope.js` 404 · `/static/../server.py` 404（原始 TCP 直连也不泄源码）。

## 2. 前后端分工的 API 契约（⏳ 待后端实现 → 前端按此接入）

### 2.1 事实：`tts_serve_start` 已经是**非阻塞**的

现状（`gui/server.py`）用 `subprocess.Popen` 立刻返回，**不阻塞**：
`{"started": true, "alive": false, "pid": …, "log": …}`，文案已说明"模型加载要几十秒"。

⇒ 用户诉求「提前拉起模型、等待期间继续配选项」**不需要重写后端**，只要下面两个接口把"进行中 / 已就绪"说清楚。

### 2.2 后端要提供的接口（**待实现**）

| 方法 | 路径 | 请求 | 应答（字段冻结） | 说明 |
| --- | --- | --- | --- | --- |
| GET | `/api/tts/status` | — | `{"alive":bool,"ready":bool,"stage":"down"\|"starting"\|"ready"\|"error","message":str,"host":str,"port":int,"pid":int\|null,"managed_by_gui":bool}` | **廉价轮询**用。不再拿 `/api/tts/voices` 顶替（那个会顺带扫一遍音色列表） |
| POST | `/api/tts/serve/start` | 无（或 `{}`） | 同上 status 对象 | 已存在；**保证响应体也能当 status 用**（补 `ready` / `stage`） |
| POST | `/api/tts/serve/stop` | 无 | `{"stopped":bool,"message":str}` | 已存在，字段不变 |
| GET | `/api/tts/voices` | — | 保持现有（含 `service.{host,port,alive}`） | 保留兼容；其 `service.alive` 必须与 status 的 `alive` 一致 |

**语义定义（本次要冻结的核心）**

- `alive`：**端口能连上**（沿用现有 `tts_service_alive()` 判据，不要改）。
- `ready`：**能立刻接受合成请求**。实现建议 `alive == True`；若后端能探到模型未载入可再收紧 —— 但字段语义必须与这里一致。
- `stage`：`down` / `starting` / `ready` / `error` 四值。前端只按这四个分支，**不要再发明第五个**。
- `managed_by_gui`：该服务是否由本界面启动（对应现有 `_tts_serve_proc`），决定"停止"按钮是否可用。

### 2.3 前端要照做的（**待实现**，在 `gui/static/app.js`）

- 点「启动模型」后**不锁死其他选项**：立刻显示"正在加载…"，用 `GET /api/tts/status` 轮询（建议 1.5–3 s 一次，上限 120 s 后提示查日志）。
- 轮询期间允许继续改引擎 / 输出目录 / 格式 / 版本数等选项。
- `stage == "ready"` → 状态栏转绿；`"error"` → 显示 `message`；超时 → 提示日志路径（`message` 里已带）。
- 引擎文案：`gptsovits` 需服务；`qwen` 与 `genie` **不需要**服务（避免用户以为必须先启动）。

### 2.4 其它已存在端点（前端继续用，签名不变）

`/api/health` · `/api/watch` · `/api/browse` · `/api/ocr/check` · `/api/ocr/start` · `/api/ocr/pull` · `/api/convert`

## 3. 命名与路径

- 分支：`wt/<任务名>`；工作树：`.worktrees/<任务名>/`
- 共享层：`.worktrees/_shared/`（真源在 base 工作树的集成分支上）
- **文件所有权（转交规则）**：`gui/server.py` 在 S1/S2 完成后**转交后端分支独占**；
  前端分支此后**不再修改 `gui/server.py`**，需要新接口就追加到本页 + `INBOX.md`。

## 4. 改判记录

- 261002：首版。§1 = 已实现（S1/S2，分支 `wt/前端重构基础`）；§2 = 冻结待实现（后端按 §2.2 实现，前端按 §2.3 接入）。
