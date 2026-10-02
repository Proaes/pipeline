# 多 agent 并行工作树（worktree）

> 一句话：**一棵工作树 = 一个 agent**。每个 agent 有独立目录 + 独立分支，互不踩脚；
> 跨分支的约定冻结在 `_shared/INTERFACES.md`，消息走 `_shared/INBOX.md`，
> 合并由 `wt.ps1 merge` 按登记顺序做，最后 `wt.ps1 fold` 把结果折回主分支。

本文件是这套机制的入口说明；操作命令用同目录的 `wt.ps1`（或双击 `wt.cmd`）。

## 1. 为什么这么摆

| 需求 | 机制 |
| --- | --- |
| 几个 agent 并行改代码 | 每人在 `.worktrees/<任务名>/` 里干活，同一文件不会被两个人同时改 |
| 改完能合回去 | 每棵树一条分支 `wt/<任务名>`，`merge` 按登记顺序逐个合并，冲突当场停下并回滚 |
| 各自能对齐 | 合并工作区 `.worktrees/base/` 停在 `wt/integration`；`_shared/` 是全体共享的真源 |
| 能共享信息 | `_shared/INTERFACES.md`（接口冻结）· `INBOX.md`（留言）· `TASKS.md`（谁在干什么）· `ALIGN.md`（每次合并后的对齐快照） |
| 不动你的在制品 | `fold` 用 `git update-ref` 只移动分支指针，**不 checkout**，你未提交的改动一个字都不碰 |

**关键取舍**：所有工作树都在本仓库**内部**（`<repo>/.worktrees/`）——
① 落在工作区沙箱内，不需要申请工作区外权限；
② 共享协调层就近可读，不需要同步；
③ 代价是工作树目录会被 git 看到，所以 `.gitignore` 必须屏蔽（`wt.ps1 init` 会自动追加）。

## 2. 目录长什么样

```
<repo>/
├── .worktrees/
│   ├── README.md            本说明
│   ├── wt.ps1 / wt.cmd      控制台（init / spawn / list / sync / merge / fold / clean）
│   ├── _shared/             共享协调层（入库，靠提交留历史）
│   │   ├── INTERFACES.md    接口冻结页：跨分支约定钉死在这里
│   │   ├── TASKS.md         任务登记表（只有 base 能改）
│   │   ├── INBOX.md         共享收件箱（谁都能追加消息块）
│   │   ├── ALIGN.md         对齐快照（merge / fold 自动写）
│   │   └── tasks/<任务名>.md 每个 agent 的任务卡
│   ├── base/                合并工作区（停在 wt/integration，别在这里写业务代码）
│   ├── <任务名>/            各 agent 的工作树（分支 wt/<任务名>）
│   └── .order              合并顺序表（一行一个任务名，git 忽略）
```

## 3. 一轮完整流程

```powershell
cd <repo>\.worktrees
.\wt.cmd init                                   # 建协调层 + 忽略规则 + base 工作树（幂等）
# ↑ 只用 init -Base <分支> -NoBaseWorktree 的话，可以先只装协调层、不建任何工作树/分支
.\wt.cmd spawn 任务A -Scope "src/a/**"          # 给 A 建树（-Owner 记会话归属）
.\wt.cmd spawn 任务B -Scope "src/b/**"
.\wt.cmd list                                   # 看谁领先/落后几个提交、有没有动共享层
#   ↑ 此时先把 _shared/INTERFACES.md 里的跨分支约定写全，并在 base 里提交一次（先冻结、再并行）
#   然后各 agent 分别在 .worktrees/任务A/ 与 .worktrees/任务B/ 里干活、各自提交
.\wt.cmd sync 任务B                             # 若 A 已合并，B 可先取最新集成分支，减少合并冲突
.\wt.cmd merge 任务A                            # 合并单个（自动跑 tests/）
.\wt.cmd merge                                  # 或按登记顺序合一波
.\wt.cmd fold                                   # 把 wt/integration 折回主分支，完成最终合并
.\wt.cmd clean 任务A -Force                     # 收工：移除工作树（分支保留）
```

## 4. 三条「必须」纪律

1. **先冻结接口、再并行**：跨分支的签名 / 路径 / 数据格式先写进 `_shared/INTERFACES.md` 并在 base 提交；
   并行开始后要改约定，先追加一行改判，再动代码 —— 否则合并必冲突。
2. **并行分支只写自己那份**：`TASKS.md` / `INTERFACES.md` / `ALIGN.md` / 别人的模块都不要碰
   （`wt.ps1 list` 会把「动了共享层」标出来）。
3. **只追加、不回改**：`INBOX.md` 与任务卡的进度日志只追加；改判写成 `→ 状态 yymmdd 改判 X`。

## 5. `wt.ps1` 命令一览

| 命令 | 作用 |
| --- | --- |
| `init` | 建 `.worktrees/`、协调层、base 工作树，并把忽略规则追加进 `.gitignore`（幂等） |
| `spawn <任务名>` | 建工作树 + 分支 + 任务卡；`-Scope <路径>` 写范围、`-Owner <会话名>` 记归属 |
| `list` / `status` | 总览：分支、HEAD、领先/落后提交数、未提交数、是否动了共享层 |
| `sync <任务名>` | 把 base（`wt/integration`）合进该工作树，让它的基线跟上 |
| `merge [任务名]` | 按登记顺序合并到 `wt/integration`；有冲突就 `merge --abort` 回滚并报冲突文件 |
| `fold` | 把 `wt/integration` 折回基线分支（默认 master）；**只动分支指针，不碰工作区** |
| `clean [任务名] [-Force]` | 移除工作树；默认只预演，有未提交改动时拒绝执行 |
| `diag` | 自检：登记表、任务发现、工作树识别 |

## 6. 已知边界（都是实测踩出来的）

- **git 不允许同一分支被两棵工作树同时检出** ⇒ base 不能直接检出 `master`，
  所以它停在 `wt/integration`，最后 `fold` 折回（快进，不留额外提交）。
- **`fold` 用 `update-ref` 而不是 `checkout`** ⇒ 启动仓库里有未提交的在制品也能折回，且不会被搅动。
  代价：主仓工作区**不会自动出现**合并后的文件（分支指针动了、工作区没动），要看合并结果就读 `.worktrees/base/`，
  或在方便时自己 `git checkout master` / `git reset --hard`。
- **本机 harness 内层 shell 实测是 Windows PowerShell 5.1**（`PSEdition=Desktop`、无 `utf8NoBOM` 枚举、无 `pwsh`），
  所以 `wt.ps1` 里写文件走 `[System.IO.File]` + `UTF8Encoding($false)`，两代 PowerShell 都能跑且无 BOM。
- **不要把 `-ErrorActionPreference` 设成 `Stop`**：git 的进度行（`Preparing worktree …`）走 stderr，
  PS 5.1 会把它包装成 NativeCommandError —— 会把**已经成功**的 `git worktree add` 当失败中断。本脚本对每个 git 调用都显式查 `ExitCode`。
- **`worktree add` 的起点用 HEAD 的提交号，不要用分支名**：`git worktree add -b <新分支> <路径> master` 会报
  `'master' is already used by worktree`。脚本已改用 `HEAD^{commit}`。
- **产物类文件必须被忽略**：各分支跑测试会产出 `__pycache__/*.pyc`，一旦被提交，合并时必然二进制 add/add 冲突。
  `init` 会顺手补齐 `__pycache__/` / `*.py[cod]` / `.pytest_cache/` 等规则。
- **pytest 的临时目录要指回工作区内**：沙箱里 `mkdtemp` 出来的目录删不掉（连 .NET `Directory.Delete` 都被拒），
  所以 `merge` 跑测试时把 `TMP`/`TEMP` 指向 `<工作树>/.tmp`，并加 `-p no:cacheprovider`。
- **`.worktrees/` 必须在 `.gitignore` 里屏蔽**：本项目若用「默认不忽略」型 `.gitignore` 而漏了这条，
  工作树里的 `.git` 文件会被当成子模块记进去（`wt.ps1 init` 已自动处理）。
  复用 260922 的教训：**某目录被忽略后，`!` 放行不了它内部子目录里的东西** —— 协调层四个文件是直接
  挂在 `_shared/` 这一层放行的。
- **`git check-ignore -v` 会把 `!` 否定规则也列出来**（退出码同样是 0），
  判据要看输出的**规则是否以 `!` 开头**，不能只看退出码。
- `clean` 不带 `-Force` 只做预演；带 `-Force` 会丢弃该工作树里未提交的改动（分支保留，可重新挂出来）。
- `sync` 用的是 merge 而不是 rebase：`rebase` 会在本沙箱里因 `mkdtemp` 权限失败，且会重写已有提交。
