# 📄 Pipeline — 多格式文档转换工具集

> 一套轻量级、模块化的 Python 工具集，用于将 PPTX 演示文稿、SQL 建表语句、图片、音视频等批量转换为 JSON、Markdown、LaTeX、Word、draw.io 等多种格式。

---

## 🎯 需求 → 工具速查表

> 按"我有什么输入、想得到什么"查找。脚本统一命名 `to_输出格式.py`，位于 `tools/` 下。

| 我的需求 | 工具（相对 `tools/`） | 说明 |
| :--- | :--- | :--- |
| PPT → JSON | `common/pptx/to_json.py` | 提取幻灯片结构化内容 |
| PPT → Markdown | `common/pptx/to_json.py` → `common/json/to_md.py` | 两步串联 |
| PPT → LaTeX | `common/pptx/to_json.py` → `common/json/to_tex.py` | article / beamer 模式 |
| PPT → Word | `common/pptx/to_json.py` → `common/json/to_docx.py` | 含封面、表格 |
| PPT 一键全格式 | `pipeline.py input.pptx --to all` | 编排器串联 |
| 专有报告 → Word | `specialized/specialized_tool/json/to_report.py` → `.../to_docx.py` | 某医院模板 |
| 硬编码生成专有报告 | `specialized/specialized_tool/generate_report.py` | 无需 PPT 源 |
| SQL → ER 图 | `common/sql/to_drawio.py` | draw.io 可打开 |
| 图片 → Markdown | `common/images/to_md.py` | 批量 OCR（Ollama） |
| 视频 → 转写文本 | `common/video/to_md.py` | 提取音频 + ASR |
| 音频 → 转写文本 | `common/audio/to_md.py` | 直接 ASR |
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
│   │   ├── pptx/  to_json.py   #   PPT → JSON
│   │   ├── json/   to_md.py / to_tex.py / to_docx.py
│   │   ├── sql/    to_drawio.py
│   │   ├── images/ to_md.py    #   OCR
│   │   ├── video/  to_md.py    #   语音转文本
│   │   ├── audio/  to_md.py
│   │   └── url/    to_mp4.py / fix_m3u8.py / export_cookies.py
│   └── specialized/            # 特化工具：按任务分类
│       └── specialized_tool/     #   专有报告（某医院模板）
│           ├── generate_report.py          # 硬编码生成报告 JSON
│           └── json/  to_report.py / to_docx.py
├── launchers/                  # 双击入口（run_*.bat，自动 cd 到项目根）
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
- 按需：Ollama（OCR）、FunASR/Whisper + ffmpeg（音视频转写）、yt-dlp（下载）

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

# SQL → ER 图
python tools/common/sql/to_drawio.py schema.sql -o ER图.drawio --lang zh

# 专有报告（方式一：从 PPT）
python tools/common/pptx/to_json.py 示例.pptx -o 示例.json
python tools/specialized/specialized_tool/json/to_report.py 示例.json -o 报告.json
python tools/specialized/specialized_tool/json/to_docx.py 报告.json -o 专有报告.docx

# 专有报告（方式二：硬编码生成）
python tools/specialized/specialized_tool/generate_report.py
```

### 双击启动（Windows）

`launchers/` 下的 `run_*.bat` 直接双击：
- `run_download.bat` — 交互式下载视频（自动带 cookie）
- `run_ocr.bat` — 批量图片 OCR
- `run_video_to_text.bat` / `run_audio_to_text.bat` / `run_qwen3asr.bat` — 音视频转写
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
6. **任务归档规范**：见 `jobs_template.md`（任务命名 `YYMMDDHHMMSS_任务名`，槽位 `0_源/1_转/2_成品`）。

---

## 📄 许可证

个人工具集，仅供学习和内部使用。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📧 联系方式

如有问题，请通过 GitHub Issues 反馈。
