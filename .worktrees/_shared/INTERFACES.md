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

### 2.5 改判（261002 三次，Lead 拍板）

1. **`/api/tts/serve/start` 的应答是 `status` 的「超集」** —— 保留原有 `started` / `log` 等字段，只**补** `ready` / `stage`。
   （后端实现与契约文字原本留了歧义，此处明确：**不删既有字段**。）
2. **`message` 语义**：在 `start` 应答里，`message` **以 start 动作自己的结果为准** ——
   「已经在跑、不用重复启动」「找不到启动脚本」这类信息不能被 status 的通用文案覆盖；
   `GET /api/tts/status` 里才用 status 的通用文案。
   （理由：用户点了「启动」，看到的必须是"这次点击发生了什么"，否则会以为按钮没生效。）

### 2.6 改判（261002 次四 · `wt/后端-接口层2`：就绪判据升级为「模型可服务」）

> 起因：`stage:"ready"` 原来只等价于「端口能连」，而**端口在听 ≠ 能服务** —— 半启动的服务也在听端口，
> 界面于是会显示就绪、用户一点就报错/挂住。§2.2 里 `ready` 的「实现建议 `alive == True`」据此收紧。

1. **status 新增两个字段（既有 8 个字段只增不改）**：
   - `model_ready`（`bool | null`）：`true` = 健康探测确认服务能应答；`false` = 端口在听但健康探测没过；
     `null` = 探不到（服务没起，或这个服务没有可探的健康端点）。
   - `probe`：本次就绪判据的来源，**只许三个值** —— `"health"`（做过健康探测）／
     `"port"`（只有「端口可连」这条弱证据）／`"none"`（连端口都没连上）。
2. **`ready` 的新语义（优先级）**：`model_ready === true` → `true`；`=== false` → `false`；
   `=== null` → **回退 `alive`**（弱判据）。前端若要严格，只认 `probe == "health" && model_ready === true`；
   弱判据下不要显示成"确定就绪"。
3. **`stage` 映射补充**：`alive && ready` → `ready`；`alive && !ready` → **`starting`**
   （端口在听、健康探测没过，按「还在载模型」显示，不要报 ready）。其余映射不变。
4. **探测实现（后端自定，不属契约）**：`GET /openapi.json`，超时 1.0 s，结论短缓存 1.5 s；
   `2xx` → `model_ready=true`；`404/405/501` → `model_ready=null` 且 `probe="port"`（没有健康端点，
   退回弱判据）；其它状态码／连不上／超时／响应不是 HTTP → `model_ready=false`。
   **探测抛任何异常都不许让 `/api/tts/status` 变成 4xx/5xx。**
5. **`/api/tts/serve/start`**：仍是 status 超集（§2.5 第 1 条），新增的两个字段同样出现在它的应答里；
   `message` 仍以 start 动作结果为**准**（§2.5 第 2 条，不变）。
6. **实测口径（后端证据，供判断探测到底挡住了什么）**：本仓库这条链路是
   `api_v2.py:149 TTS(...)` →（`TTS.py:446 _init_models()` 载 t2s/vits/bert/cnhubert 权重）→ 之后才
   `uvicorn.run`（`api_v2.py:572`），所以**用 `p10_serve_si.ps1` 起时，「端口在听」≈权重已载**。
   探测真正挡住的是：①`uvicorn api_v2:APP` 那种「先绑端口、后导入模块」的起法；
   ②端口上根本不是这个服务/只是半启动的监听；③`/set_*_weights` 换权重那类运行中重载窗口。

### 2.7 模型选择（261002 六次 · Lead 单人实现，不再分前端/后端两支）

用户要求：**语音引擎下拉里要能选 qwen**、**要能分别选「微调模型」与「原模型」**、
**引擎/模型名字后面要保留简要描述**。冻结如下（实现：`gui/server.py` + `gui/tts_models.json`
+ `gui/static/modules/ttsmodels.js`，无头改动 `gui/static/index.html`）：

1. **真源 = `pipeline/gui/tts_models.json`**（进 git）。结构：
   `{engines:[{id,label,summary,voice,models:[{id,label,note,engine?,voice?,
   gpt_weights?,sovits_weights?,onnx_dir?,qwen_model?}]}]}`。路径可写相对 `pipeline/` 的相对路径
   （后端补成绝对路径）或绝对路径。**代号规则 `<音色>-<引擎>`，首个代号 = `祀-G`**。
   加模型只改这个文件，不动代码。
2. **`GET /api/tts/models`** → `{ok, engines[], file, current, message?}`；每个 model 带
   `{id,label,note,engine,voice,gpt_weights,sovits_weights,onnx_dir,qwen_model,exists}`。
   `exists=false` 表示权重文件不在，界面把它置灰并写「⚠ 权重文件缺失」。
   `current = {gpt,sovits,model,config} | null`：正在跑的服务**实际加载**的权重
   （读 `output/tts/tmp/tts_infer_si.yaml` 的 custom 段再与注册表按绝对路径比对）；
   服务没在跑 → `null`。**认不出就 `model:null`，不许猜**。
3. **`POST /api/tts/model/apply`** body `{"id":"si-ft"}` → `{ok,id,label,engine,voice,
   gpt_weights,sovits_weights,switched,steps[],message|error}`。把该模型的两条权重转发给
   TTS 服务的 `/set_gpt_weights`、`/set_sovits_weights`（**绝对路径**，URL 编码），
   **不重启服务**。约定：
   - 服务没在跑 → `ok=true, switched=false` + 中性提示（**不是错误**：权重已在界面记下，
     合成时会由 `to_audio.py` 顺带切）；
   - 未知 id → `ok=false` + `error`；模型不属于 gptsovits → `ok=true, switched=false` + 说明；
   - 转发失败 → `ok=false` + `steps` 里已成功的那些 + `error`。超时 `TTS_APPLY_TIMEOUT`（默认 180 s）。
4. **合成时也要兑现选择**：`/api/convert` body 新增 `tts_model`（模型 id）。给了它就以它为准 ——
   引擎取该模型的 `engine`、音色取该模型的 `voice`，`gptsovits` 再加
   `--gpt-weights/--sovits-weights`（`to_audio.py` 在**合成前切一次**，`--dry-run` 不切、
   连不上服务就报错不静默）。**不给 `tts_model` 时行为与 261002 之前完全一致**（向后兼容）。
5. **`to_audio.py` 新增**：`--model <代号>`（只进日志，交接用）、`--gpt-weights`、`--sovits-weights`。
6. **界面**：引擎下拉 3 条（`gptsovits` / `genie` / `qwen`），**引擎名与模型名后面的括号描述一律保留**；
   模型下拉按当前引擎联动（引擎是主、模型是仆），名字下面那行显示模型 note + 引擎 summary +
   「服务当前加载：XXX」。缺文件的模型置灰。

## 3. 命名与路径

- 分支：`wt/<任务名>`；工作树：`.worktrees/<任务名>/`
- 共享层：`.worktrees/_shared/`（真源在 base 工作树的集成分支上）
- **文件所有权（转交规则）**：`gui/server.py` 在 S1/S2 完成后**转交后端分支独占**；
  前端分支此后**不再修改 `gui/server.py`**，需要新接口就追加到本页 + `INBOX.md`。

## 3.1 server.py 的「锚点边界」冻结（261002 加，防两个分支改同一区域）

两条分支都会动 `gui/server.py`，所以按**锚点**划开：

| 区域 | 归属 | 规则 |
| --- | --- | --- |
| 文件**末尾**逻辑块（`if __name__ == "__main__"` 之前） | **后端** | 新函数定义插在这里 |
| `do_GET` 里 `elif path == "/api/health"` **之后、`else: 404` 之前** | **后端** | 新增 `elif` 分支只插在这里 |
| `do_GET` 里 `/api/health` 之前 | **前端** | 只保留已有 `serve_static` 分支，**不得新增** |
| 任何 API 形状改动 | 双方 | 先改本页 §2 再动代码（走 §4 改判） |

> 判据：合并时 `git merge --no-commit` 无冲突，且 `python -m py_compile gui/server.py` 通过。

### 3.2 Lead 拍板：`start` 的前置判断**保持 `alive`**（261002 五次）

后端曾建议把 `tts_serve_start()` 的前置判断也从 `alive` 改成 `ready`（就绪探测）。**决定：不改。**

理由：端口在听但健康探测没过时，若允许"另起一个"，新进程会撞端口占用（BindException）而失败，
用户看到的是更难懂的错误；保持"提醒你已有服务在跑"更安全、也不改变既有行为。
前端按此设计：`stage == "starting"` 时「启动」按钮的语义是**等待/刷新状态**，不是"再起一个"。

## 4. 改判记录

- 261002：首版。§1 = 已实现（S1/S2，分支 `wt/前端重构基础`，已合入 master `5d926c6`）；§2 = 冻结待实现（后端按 §2.2 实现，前端按 §2.3 接入）。
- 261002 二次：加 §3.1 锚点边界；`gui/server.py` 已随 `wt/前端重构基础` 合入 master（静态路由段落地）。
- 261002 四次：加 §2.6 —— 就绪判据升级（新增 `model_ready` / `probe`；「端口在听但健康探测没过」改判 `stage="starting"`）；后端分支 `wt/后端-接口层2` 实现。
- 261002 五次：加 §3.2 —— `start` 前置判断保持 `alive`（不改成 `ready`）。**后端两条线至此全部落地并合入 master `6ac22cc`。**

