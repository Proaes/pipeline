# _lib — 工具共享脚手架

供 `tools/` 下各 `to_*.py` 复用的公共函数，非直接入口。

## 用法

各工具脚本保持独立可运行；需要复用公共逻辑时：

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 指向 tools/
from _lib import common
```

## 内容

| 函数 | 说明 |
| :--- | :--- |
| `PROJECT_ROOT` / `JOBS_DIR` | 项目根 / 任务归档目录常量 |
| `ensure_parent(path)` | 确保输出父目录存在 |
| `write_json / read_json` | UTF-8 JSON 读写（保留中文） |
| `resolve_output(...)` | 统一解析输出路径（`-o` > `--outdir` > 输入同目录） |
| `print_summary(results)` | 打印结果摘要 |

## 约定

- 新增工具优先复用本模块，避免重复实现路径/JSON 逻辑。
- `tools/` 下新增目录/脚本后，在 `../README.md` 速查表登记。
