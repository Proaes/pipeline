# 📄 Pipeline — 多格式文档转换工具集

> 一套轻量级、模块化的 Python 工具集，用于将 PPTX 演示文稿、SQL 建表语句、图片、音视频等批量转换为 JSON、Markdown、LaTeX、Word、draw.io 等多种格式。

---

## 🎯 需求 → 工具速查表

> 按"我有什么输入、想得到什么"查找。脚本统一命名 `to_输出格式.py`，位于 `tools/` 下。

| 我的需求 | 工具（相对 `tools/`） | 说明 |
| :--- | :--- | :--- |
| 旧版 .ppt → .pptx | `common/ppt/to_pptx.py` | OLE2 老格式转 OOXML；后端 PowerPoint COM → WPS COM → LibreOffice |
| PPT → JSON | `common/pptx/to_json.py` | 提取幻灯片结构化内容 |
| PPT → Markdown | `common/pptx/to_json.py` → `common/json/to_md.py` | 两步串联 |
| PPT → LaTeX | `common/pptx/to_json.py` → `common/json/to_tex.py` | article / beamer 模式 |
| PPT → Word | `common/pptx/to_json.py` → `common/json/to_docx.py` | 含封面、表格 |
| PPT 一键全格式 | `pipeline.py input.pptx --to all` | 编排器串联 |
| 专有报告 → Word | `specialized/specialized_tool/json/to_report.py` → `.../to_docx.py` | 某医院模板 |
| 硬编码生成专有报告 | `specialized/specialized_tool/generate_report.py` | 无需 PPT 源 |
| SQL → ER 图 | `common/sql/to_drawio.py` | draw.io 可打开 |
| 表格 → 折线图 / 柱状图 / 散点图 | `common/csv/to_chart.py` | CSV/TSV → PNG/SVG/PDF；选 x 与多列 y，自动跳过说明行与汇总行；表头重名用 `#N` 列号，`--list-columns` 先看列 |
| Markdown → JSON | `common/md/to_json.py` | 笔记结构化中间态（`--lint` 检查写法） |
| Markdown → LaTeX | `common/md/to_json.py` → `common/json/to_tex.py` | 两步串联（blocks 结构） |
| Markdown / LaTeX → PDF | `common/md/to_pdf.py` | latexmk 编译，含 synctex 定位；不给 `-o` 时按槽位规则落同级 `(N+1)_pdf/` |
| Markdown 笔记实时预览 | `common/md/watch.py` | 监听 md，保存即自动转 PDF |
| 文件转换图形界面 | `gui/server.py`（或双击 `run_gui.bat`，pipeline 根目录 / `launchers/` 各一份） | 本地 Web 面板：浏览 jobs/ 选文件、PDF/TeX/JSON/MD/DOCX/lint/图片OCR/文本转语音 转换（输入 md/tex/ppt/pptx/txt，`.ppt` 自动先转 `.pptx`）、一键启动 Ollama/拉取模型、实时日志、watch 监听 |
| 图片 → Markdown | `common/images/to_md.py` | 批量 OCR（Ollama） |
| 视频 → 转写文本 | `common/video/to_md.py` | 提取音频 + ASR |
| 音频 → 转写文本 | `common/audio/to_md.py` | 直接 ASR |
| 文本 → 语音 | `common/text/to_audio.py` | GENIE / GPT-SoVITS ONNX，**CPU 推理、零显存**；音色库 + 预设角色；长文自动切句，句间加静音、句尾淡出 |
| 视频链接 → 视频/音频 | `common/url/to_mp4.py` | B站/YouTube 下载 |
| m3u8 → mp4 | `common/url/fix_m3u8.py` | 分片合并 |
| 浏览器 → cookies | `common/url/export_cookies.py` | 下载鉴权用 |
| 批量多文件转换 | `main.py`（读 `config.json`） | 编排器 |

---

## 📁 目录结构

```
pipeline/
├── tools/                      # 工具层（脚本全部在此）
│   ├── _lib/                   # 共享脚手架（common.py：路径/JSON 辅助）
│   ├── main.py                 # 批量转换入口（读取 config.json）
│   ├── pipeline.py             # 单文件串联入口（pptx --to json md tex docx）
│   ├── common/                 # 通用工具：按输入类型分类
│   │   ├── ppt/    to_pptx.py  #   旧版 .ppt → .pptx（PowerPoint/WPS COM，回退 LibreOffice）
│   │   ├── pptx/  to_json.py   #   PPT → JSON
│   │   ├── json/   to_md.py / to_tex.py / to_docx.py   # to_tex.py 自动识别 slides/blocks 两种结构
│   │   ├── md/     to_json.py / to_pdf.py / watch.py   # 笔记: md→json / md→pdf / 监听实时预览
│   │   ├── sql/    to_drawio.py
│   │   ├── csv/    to_chart.py #   表格 → 折线图 / 柱状图 / 散点图（PNG/SVG/PDF）
│   │   ├── images/ to_md.py    #   OCR
│   │   ├── video/  to_md.py    #   语音转文本
│   │   ├── audio/  to_md.py
│   │   ├── text/   to_audio.py #   文本 → 语音（GENIE / GPT-SoVITS，含音色库）
│   │   └── url/    to_mp4.py / fix_m3u8.py / export_cookies.py
│   └── specialized/            # 特化工具：按任务分类
│       └── specialized_tool/     #   专有报告（某医院模板）
│           ├── generate_report.py          # 硬编码生成报告 JSON
│           └── json/  to_report.py / to_docx.py
├── gui/                        # 本地 Web 转换面板（纯标准库）
│   ├── server.py               #   http.server 后端：静态页 + /api/convert /api/browse /api/watch
│   └── static/index.html       #   单页前端：浏览选文件、批量转换、实时日志、watch 监听
├── launchers/                  # 双击入口（run_*.bat，自动 cd 到项目根）
├── voices/                     # 音色库（参考音频 + 逐字文本 + meta.json）
├── jobs/                       # 任务归档（不入库，见 jobs_template.md）
├── config.json                 # 批量任务配置
├── jobs_template.md            # 任务目录规范
├── .gitignore                  # 数据层/敏感文件忽略
└── README.md
```

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- `pip install python-pptx python-docx`
- 转换旧版 `.ppt`：`pip install pywin32` + 本机装了 PowerPoint 或 WPS（都没有则装 LibreOffice 走 `--backend libreoffice`）
- 按需：Ollama（OCR）、FunASR/Whisper + ffmpeg（音视频转写）、yt-dlp（下载）
- 文本转语音（`common/text/to_audio.py`）：需要 **ffmpeg**；用 `genie` 引擎还要一个独立环境
  （本机为 `D:\mamba\envs\tts`，`pip install genie-tts`，**无 torch/CUDA**）+ 约 750 MB 模型资源
  （本机在 `D:\tools\genie`，可用 `--genie-root` 或环境变量 `GENIE_ROOT` 指定）

### 单文件转换

```bash
# 从项目根目录运行（脚本在 tools/ 下）
python tools/common/pptx/to_json.py input.pptx -o out/slides.json
python tools/common/json/to_md.py out/slides.json -o out/slides.md

# 一键串联（推荐）
python tools/pipeline.py input.pptx                          # JSON + MD
python tools/pipeline.py input.pptx --to json md tex docx    # 指定格式
python tools/pipeline.py input.pptx --to all --outdir ./out  # 全部格式
python tools/pipeline.py input.pptx --to tex --tex-mode beamer

# 旧版 .ppt（PowerPoint 97-2003）先转 .pptx，再走 PPT 链
python tools/common/ppt/to_pptx.py 老课件.ppt -o 0_pptx/老课件.pptx
python tools/pipeline.py 0_pptx/老课件.pptx --to json md docx

# SQL → ER 图
python tools/common/sql/to_drawio.py schema.sql -o ER图.drawio --lang zh

# 表格 → 图表（CSV / TSV → 折线图；柱状/散点用 --kind）
python tools/common/csv/to_chart.py data.csv --list-columns           # 先看识别到哪些列
python tools/common/csv/to_chart.py data.csv -o chart.png --x 抽数 --y 概率,xP(x)
python tools/common/csv/to_chart.py data.csv -o chart.png --x '#2' --y '#4,#9' --labels 池A,池B

# Markdown 笔记 → PDF（带页码目录、公式、表格）
python tools/common/md/to_pdf.py note.md --open
python tools/common/md/to_json.py note.md --lint      # 检查笔记写法（只报告，不改源）

# Markdown 笔记实时预览（保存即自动编译，配合 SumatraPDF 毫秒级刷新）
python tools/common/md/watch.py note.md --sumatra "D:\tools\SumatraPDF\SumatraPDF.exe"

# 文本 → 语音（GENIE，纯 CPU）
python tools/common/text/to_audio.py --check                  # 先看环境与可用音色
python tools/common/text/to_audio.py --list-voices            # 列出音色库 + GENIE 预设角色
python tools/common/text/to_audio.py note.md --voice feibi -o out/note.mp3
python tools/common/text/to_audio.py ./notes/ -r --voice feibi   # 批量
python tools/common/text/to_audio.py note.md --srt            # 同时出 .srt 时间轴
python tools/common/text/to_audio.py note.md --ref-audio 我的录音.wav --ref-text "录音的逐字文本"   # 用自己的声音，不必先建目录
python tools/common/text/to_audio.py --register 我的音色 --ref-audio 我的录音.wav --ref-text "逐字文本" --prompt-lang zh   # 注册成常用音色
python tools/common/text/to_audio.py note.md --voice 任命助理 --text-lang zh   # 日语参考音频 → 中文输出（跨语种）
python tools/common/text/to_audio.py note.md --dry-run        # 只看切句结果

# 图形化转换面板（浏览器操作：浏览选文件 → 选格式 → 转换 → 看日志）
python gui/server.py              # 默认端口 8765，自动打开浏览器
python gui/server.py --port 9000 --no-browser

# 专有报告（方式一：从 PPT）
python tools/common/pptx/to_json.py 示例.pptx -o 示例.json
python tools/specialized/specialized_tool/json/to_report.py 示例.json -o 报告.json
python tools/specialized/specialized_tool/json/to_docx.py 报告.json -o 专有报告.docx

# 专有报告（方式二：硬编码生成）
python tools/specialized/specialized_tool/generate_report.py
```

### 双击启动（Windows）

`launchers/` 下的 `run_*.bat` 直接双击（`pipeline/run_gui.bat` 为根目录快捷版）：
- `run_gui.bat` — 图形化转换面板（前台控制台显示日志，浏览器自动打开，关闭窗口即停止服务）
- `run_md_to_pdf.bat` / `run_md_watch.bat` — 笔记转 PDF / 监听实时预览
- `run_download.bat` — 交互式下载视频（自动带 cookie）
- `run_ocr.bat` — 批量图片 OCR
- `run_video_to_text.bat` / `run_audio_to_text.bat` / `run_qwen3asr.bat` — 音视频转写
- `run_tts.bat` — 文本转语音（自动环境检查 + 列出音色 + 打印用法）
- `run_m3u8_convert.bat` — m3u8 → mp4

### 批量转换（config.json）

```json
{
  "defaults": { "outdir": "output", "lang": "zh", "to": ["json", "md"] },
  "tasks": [
    { "input": "slides.pptx", "to": ["json", "md", "tex"] },
    { "input": "schema.sql",  "to": ["drawio"] },
    { "input": "another.pptx","to": ["json", "docx"], "outdir": "custom/" }
  ]
}
```

```bash
python tools/main.py                # 默认 config.json
python tools/main.py -c my_config.json
python tools/main.py --list         # 仅列出任务
```

---

## 🔄 新旧脚本名对照

| 旧名（v1） | 新路径（v2） |
| :--- | :--- |
| `extract.py` | `tools/common/pptx/to_json.py` |
| `render_md.py` | `tools/common/json/to_md.py` |
| `render_tex.py` | `tools/common/json/to_tex.py` |
| `render_docx.py` | `tools/common/json/to_docx.py` |
| `sql2drawio.py` | `tools/common/sql/to_drawio.py` |
| `batch_ocr.py` | `tools/common/images/to_md.py` |
| `video_to_text.py` | `tools/common/video/to_md.py` |
| `audio_to_text.py` | `tools/common/audio/to_md.py` |
| `download_video.py` | `tools/common/url/to_mp4.py` |
| `fix_m3u8.py` | `tools/common/url/fix_m3u8.py` |
| `export_cookies.py` | `tools/common/url/export_cookies.py` |
| `main.py` / `pipeline.py` | `tools/main.py` / `tools/pipeline.py` |
| `build_report.py` | `tools/specialized/specialized_tool/json/to_report.py` |
| `gen_report.py` | `tools/specialized/specialized_tool/generate_report.py` |
| `render_specialized_tool.py` | `tools/specialized/specialized_tool/json/to_docx.py` |

---

## ⚠️ 注意事项

1. **中文支持**：LaTeX 输出需 TeX Live + `ctex` 宏包；Word 输出用"宋体"。
2. **SQL 解析限制**：仅标准 `CREATE TABLE`，不支持 VIEW/INDEX；MySQL 兼容性最佳。
3. **路径**：支持相对（相对项目根）和绝对路径；bat 已自动 `cd` 到项目根。
4. **编码**：全部 UTF-8；JSON 输出保留中文（`ensure_ascii=False`）。
5. **数据层不入库**：`jobs/`、`input/`、`output/` 等由 `.gitignore` 排除，仅脚本/配置/文档进 git。
6. **任务归档规范**：见 `jobs_template.md`（任务命名 `YYMMDDHHMMSS_任务名`，槽位 `0_源/1_转/2_成品`）。**默认输出槽位 = 源槽位号 + 1**：源在 `0_md/` 里转 PDF 就落同级 `1_pdf/`（TeX → `1_tex/`、JSON → `1_json/`，其余同理）；源不在 `N_xxx/` 槽位里则与源文件同目录。GUI 的「输出目录」留空、`md/to_pdf.py`（含 `watch.py`）不给 `-o` 时都按这条走；显式指定输出目录始终优先。OCR 是目录级输入，源目录不在槽位里时回落上级 `2_md/`。
7. **Markdown 笔记链**：`md/to_json.py`（解析）→ `json/to_tex.py`（渲染，自动识别 blocks/slides 结构）→ `md/to_pdf.py`（latexmk 编译，产物含 PDF + synctex.gz）。渲染层兜底：数学段中文自动包 `\text{}`、空 `\begin{array}{}` 自动补列数，均打印警告；中文文档一律用默认的 **xelatex**（2026-09-29 实测 pdflatex 在含中文小标题/行内中文的笔记上会 `! Undefined control sequence ... \CJK@plane` 直接中止，故 `to_pdf.py` 与 `watch.py` 都不再默认 pdflatex）。
8. **实时预览**：`launchers/run_md_to_pdf.bat`（拖 md 即编译）、`run_md_watch.bat`（监听实时重建）；脚本优先使用 conda 环境 `D:\mamba\envs\proaes\python.exe`（回退 `python`）。
9. **旧版 `.ppt`（PowerPoint 97-2003）**：是 OLE2 复合文档（magic `D0 CF 11 E0 A1 B1 1A E1`），`python-pptx` 只认 OOXML，读不了 → 必须先过 `tools/common/ppt/to_pptx.py`（后端顺序 PowerPoint COM → WPS COM → LibreOffice，`--backend` 可指定；COM 用 `DispatchEx` 起独立实例，不影响你桌面上已打开的 PowerPoint）。脚本按 **magic 而非扩展名**判真实格式：内容已是 OOXML 的"假 .ppt"直接复制、不启动 Office；既非 OLE2 也非 ZIP 的报 magic 并退出。输出已存在时**报错不覆盖**，需 `-f`（GUI 里对应「覆盖已存在产物」复选框）。GUI 选 `.ppt` 时会自动串这一步，中间产物 `.pptx` 与目标产物落在同一输出目录。
10. **PDF 链已知问题（2026-09-29 更新）**：
    - ① **已修**：`common/json/to_tex.py` 的转义缺陷 —— `_ESCAPE_MAP` / `_ESCAPE_RE` 原先漏了反斜杠（正文里的 Windows 路径 `D:\tools\x`、C 代码 `'\0'` 原样进 `.tex` → `! Undefined control sequence` → `to_pdf.py` 带 `-halt-on-error` 中止），且行内代码被 `_escape` 与 `_code_repl` **二次转义**（`snake_case` 渲染成 `snake\{}_case`：编译通过、内容坏掉）。现已把 `\` 纳入转义表，并把行内代码改成先按反引号切分、只转义一次。实测：`ppt/pptx → json → tex → PDF` 全链 exit 0 出 PDF；含反斜杠的 md 与含 `_ % #` 行内代码的 md 都能编译；旧产物里已有的 `\{}` 重编一次即消失。
    - ② **仍在**：`common/md/to_pdf.py` 的构建目录用 `tempfile.mkdtemp`（mode 0o700），DSH 沙箱内连写入都被拒（`PermissionError: ...\md_build\run_xxx\x.json`）→ **沙箱内跑请加 `--build-dir <工作区内已建目录>`**；沙箱外用普通账户跑（双击 `run_gui.bat`）不受影响。修法是把 `mkdtemp` 换成 `mkdir` 随机名目录（两行），待决定。

11. **文本转语音（`common/text/to_audio.py`，2026-09-28 新增）**：默认引擎是 **GENIE**（GPT-SoVITS 的 ONNX 轻量引擎），**纯 CPU 推理、不占显存**，可与 Ollama / 游戏并存。它在 `Core/Inference.py` 里按 EOS token 边界截断后直接过声码器，**不做任何收尾**，所以「尾音生硬」是模型层面的已知现象；本工具因此默认给每段加 25 ms 淡入淡出 + 0.55 s 收尾静音，仍有杂音尾才考虑 `--trim-tail-ms`——**但 2026-09-28 实测不推荐**：裁 60 ms 会把真实语音一起削掉，听感反而更「截断」；默认值 0 是刻意的。GENIE 原始输出**开头还自带一段近乎无声的「起手」**，故 `--head-pad` 默认只有 0.12 s。音色解析顺序：`--ref-audio` > `voices/<名>/` > `<GENIE 根>/CharacterModels/v2ProPlus/<名>/`（预设角色，零配置可用）。模型有不确定性，同一句话两次生成的**时长可差约 1 秒、顿挫位置也会变**——重要内容不满意时重跑一次往往比调参数有效。**换音色不需要训练**：说话人身份不在权重里，而是运行时由「说话人验证模型」从参考音频算出来（见 `genie_tts/Audio/ReferenceAudio.py` 的 `update_global_emb`），所以录一段 3–10 秒的 wav 放进 `voices/<音色名>/ref.wav` 再配 `ref.txt` 即可；`--ref-audio` 还能免建目录直接试。实测声纹余弦相似度：用自己的参考音频克隆 → 0.800（对参考）vs 0.534（对 feibi），而 feibi 预设角色对自身参考是 0.928 —— **预设角色比随手一段 wav 更贴合**，但音色确实由参考音频主导。`--srt` 会按拼接后的真实时序输出同名时间轴（段间间隔 = `--gap`，末尾含 `--tail-pad`）。**参考音频语种与目标文本语种相互独立**：`load_character` 的 `language` 决定目标文本走哪套 G2P，`set_reference_audio` 的 `language`（本工具里 = `--prompt-lang` 或音色 `meta.ref_lang`）决定参考音频走哪套，所以「日语音色 + 中文文本」是原生支持的跨语种克隆。实测声纹相似度：同语种 0.800 vs 跨语种 0.806（**几乎无差**），但跨语种的「对自身参考 − 对 feibi 参考」差值从 +0.266 缩到 +0.099，**音色可能更泛化一些**；有无口音须自己听。注册音色用 `--register <名> --ref-audio x.wav --ref-text "逐字文本" --prompt-lang <参考语种>`（一条命令写出 `voices/<名>/{ref.wav,ref.txt,meta.json}`）。**参考音频建议 3–10 秒、一整句**；几十秒的素材先剪出一段连续无长停顿的再注册。

---

## 📄 许可证

个人工具集，仅供学习和内部使用。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📧 联系方式

如有问题，请通过 GitHub Issues 反馈。
