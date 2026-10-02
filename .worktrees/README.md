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
│   │   ├── TASKBOOK.md      任务书：本轮要做什么、写范围、验收判据（先审再开工）
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

## 4. 机制细节：它到底怎么隔离的

> 这一节专门回答「并行要改同一个文件怎么办」「各自建分支是不是要改文件名」「难道整个项目复制一遍」。

### 4.1 事实底座：**共享对象库，只隔离工作区**

`git worktree` **不是**复制仓库。一个仓库只有**一份** `.git`（对象库 + refs + 配置），
每棵工作树拿到的只是一个指向它的 `.git` **文件**（内容是 `gitdir: <主仓>/.git/worktrees/<名字>`）：

```
pipeline/.git/                       ← 全项目唯一一份：所有提交、所有分支、所有历史
├── objects/                         ← 只读对象库（同一份 blob/tree/commit 被所有工作树共享）
├── refs/heads/{master, wt/integration, wt/任务A}
└── worktrees/<名字>/{HEAD, index}    ← 每棵树自己的「HEAD + 暂存区」管理文件

pipeline/.worktrees/base/.git         ← 一个文本文件，写着 gitdir: ../../.git/worktrees/base
pipeline/.worktrees/任务A/.git        ← 同上
```

**因此每棵工作树各自独立的是**：工作区文件、暂存区（index）、HEAD（当前分支指针）。
**共享的是**：全部历史与分支（这就是为什么分支之间能互相 `merge`，而不是靠人复制文件合并）。

### 4.2 并行改同一个文件，会不会把前一个人的作业覆盖掉？

**不会「静默覆盖」。** 三种情形分开说：

**(a) 两人改不同文件** —— 最常见，也最省事：自动合并，零冲突。
在「先冻结接口、按文件划写范围」的前提下，这是默认情形。

**(b) 两人改同一个文件的不同位置** —— Git 的三方合并（three-way merge）会**同时保留两处改动**。
原理：Git 拿三份东西比对 —— `base`（两人的共同祖先）、`A`、`B`；
对**不重叠的改动区段**自动合成一个结果。所以只要两人改的不是同一段，合并是自动且无损的。

**(c) 两人改同一个文件的同一段** —— 这才是真冲突。
Git 不会替你猜，它把两边的文本都嵌进文件并打上 `<<<<<<< ======= >>>>>>>` 标记，
然后 `wt.ps1 merge` **当场停下、`merge --abort` 回滚**，并列出冲突文件。
**这就是「不会覆盖前者」的硬保证**：宁可停下报给你，也不会让后来的悄悄盖掉先来的。

那怎么**预先避免**走到 (c)？按代价从低到高：

| 手段 | 做法 | 代价 |
| --- | --- | --- |
| ① 按文件划写范围（首选） | 任务卡里写 `-Scope`：每个任务只许动指定文件；`wt list` 会把越界标出来 | 需要事先拆得开 |
| ② 按行段划（同文件不得不碰时） | 先冻结函数/区块边界，两个任务各改各的函数 | 依赖接口冻结做得好 |
| ③ 串行 + 同步 | 后一个任务先 `wt.ps1 sync <任务名>` 把前一个已合并的成果并进来，再动手 | 失去部分并行度 |
| ④ 冲突了人工解 | merge 停下 → 在工作树里改掉冲突标记 → 再 merge/fold | 最慢，但不会丢东西 |

### 4.3 「各自建分支，文件名是不是就得不一样？」——**不用，也不需要**

这是最容易误会的一点。**分支不改变文件路径，也不会给文件加后缀。**

一个提交（commit）的内容是**整棵目录树的快照**；两个分支上的同一个路径 `X.XX`
就是同一个文件的两份不同内容。切到哪个分支，磁盘上的 `X.XX` 就换成那份内容 ——
**路径始终是 `X.XX`**，不会变成 `X_分支名.XX`。

所以：

- 每棵工作树里都有完整的 `pipeline/tools/.../` 目录结构，**路径与主仓逐字节一致**；
- `import tools.common.text.to_audio` 这种导入在每棵树里都成立 —— 因为包路径没变；
- 谁都不用改文件名，也不用改 import。

**真正需要改文件名的情况只有一种**：你把「并行」做成了 `X_A.xx` / `X_B.xx` 这种手工副本 ——
那才会引发你担心的连锁：文件名变了，import 路径、配置里的路径、打包清单全都要跟着改。
工作树正是为了**避免**这种改法而存在的。

### 4.4 「难道是整个项目复制一遍？」——**不是，三种方案对比**

| 方案 | 历史 | 工作区文件 | 导入路径 | 合并方式 | 适用 |
| --- | --- | --- | --- | --- | --- |
| **git worktree（本方案）** | **共享一份** | 每棵树各一份（只含被跟踪文件） | 完全一致 | `git merge`，三方合并 | 并行改**同一项目**的不同部分 |
| 复制整个目录 | 各一份（互相不知道） | 各一份（含未跟踪的大文件） | 取决于是否改了目录名 | 只能靠人比对/拷文件 | 需求本就是**两条独立产品线** |
| 分支 + 反复 stash/checkout | 共享一份 | **同一份**（切来切去） | 一致 | merge | 单人串行，切分支会互相打断 |

代价与省下的东西，用本项目**实测**的数字看（261002 用一棵临时工作树量过）：

| 量 | 数值 |
| --- | --- |
| 主仓被跟踪源码 | **0.40 MB** |
| 主仓 `.git` 总量 | **0.57 MB** |
| 一棵工作树新增的磁盘占用 | **≈0.44 MB**（只有工作区文件；`.git` 那个"文件"本身是 0 字节的文本） |
| 若改成复制一份仓库 | ≈0.97 MB 起（工作区 + 再一份 `.git`），而且**每多一个 agent 就再多一份** |
| 实测 import | `from tools.common.text import to_audio`、`import gui.server` 在**主仓与工作树里都成立** —— 路径没变 |

- **每棵工作树看不见那些「没入库的大件」**：本项目 `output/`（模型权重、训练产物）、`voices/`、
  `jobs/`、`download/`、`wav/`、`test/audio/` 都在 `.gitignore` 范围内 —— 它们**不会**出现在工作树里。
  这是优点（每棵树轻、不会被无关大文件干扰），也是**必须知道的边界**：
  工作树里那些目录是**空的**。要跑依赖它们的验证，用**主仓的绝对路径**指向它们，
  例如 `E:\files\Main\pipeline\output\tts\...`、`E:\files\Main\pipeline\test\audio\...`。
  本项目的规矩：并行任务只改文本代码；真要跑重产物校验，回主仓跑。
- 一个容易看错的细节：工作树目录里的 `.git` 是**文件不是目录**（内容就一行 `gitdir: …`），
  所以按「文件总量」统计时它是 0 字节 —— 别以为仓库没了。
- **真正的成本**：每棵树的 `index` 与 refs 会让 `git status/gc` 稍慢；以及必须记住
  「合并只在 `.worktrees/base/` 那棵树里做」。

**什么时候才该「整个复制」**：两个方向的成果**永远不需要合并**（比如同一份代码要分叉成
两个不再往来的产品），那就复制 / `git clone`，别用工作树。**只要还要合并，就用工作树。**

### 4.5 本项目的落地形状（所以它长这样）

```
pipeline/
├── .worktrees/
│   ├── base/          ← 合并工作区：停在 wt/integration，所有 merge 在这里发生
│   ├── <任务名>/       ← 每个 agent 的独立工作区：完整的 tools/ gui/ test/ 目录树，分支 wt/<任务名>
│   └── _shared/       ← 协调层：接口冻结 / 任务登记 / 收件箱 / 对齐快照
├── tools/ gui/ test/  ← 主工作区：你日常开发的地方（折叠后 master 指针前进，这里文件不动）
└── output/ voices/ …  ← 大件与产物：不在任何工作树里，靠绝对路径引用
```

## 5. 三条「必须」纪律

1. **先冻结接口、再并行**：跨分支的签名 / 路径 / 数据格式先写进 `_shared/INTERFACES.md` 并在 base 提交；
   并行开始后要改约定，先追加一行改判，再动代码 —— 否则合并必冲突。
2. **并行分支只写自己那份**：`TASKS.md` / `INTERFACES.md` / `ALIGN.md` / 别人的模块都不要碰
   （`wt.ps1 list` 会把「动了共享层」标出来）。
3. **只追加、不回改**：`INBOX.md` 与任务卡的进度日志只追加；改判写成 `→ 状态 yymmdd 改判 X`。

## 6. `wt.ps1` 命令一览

| 命令 | 作用 |
| --- | --- |
| `init` | 建 `.worktrees/`、协调层、base 工作树，并把忽略规则追加进 `.gitignore`（幂等） |
| `spawn <任务名>` | 建工作树 + 分支 + 任务卡；`-Scope <路径>` 写范围、`-Owner <会话名>` 记归属 |
| `list` / `status` | 总览：分支、HEAD、领先/落后提交数、未提交数、是否动了共享层 |
| `sync <任务名>` | 把 base（`wt/integration`）合进该工作树，让它的基线跟上 |
| `merge [任务名]` | 按登记顺序合并到 `wt/integration`；有冲突就 `merge --abort` 回滚并报冲突文件 |
| `fold` | 把 `wt/integration` 折回基线分支（默认 master）；**只动分支指针，不碰工作区** |
| `clean [任务名] [-Force]` | 移除工作树并**撤销其任务登记行**；默认只预演，有未提交改动时拒绝执行 |
| `diag` | 自检：登记表、任务发现、工作树识别 |

参数写法两种都行：`-Scope "gui/**"` 或 `-Scope="gui/**"`。

## 7. 已知边界（都是实测踩出来的）

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
