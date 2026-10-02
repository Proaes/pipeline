<#
wt.ps1 —— 多 agent 并行工作树控制台（Worktree Control Plane）

用法：pwsh -NoProfile -ExecutionPolicy Bypass -File wt.ps1 <命令> [参数]
或双击同目录的 wt.cmd。

设计要点
  1. 每棵工作树 = 一个 agent 的独占目录 + 独占分支，写文件互不干扰；
  2. 所有工作树都放在 <仓库根>/.worktrees/<名字>/ 下 —— 因此仍在本工作区沙箱内，不需要额外权限；
  3. <仓库根>/.worktrees/_shared/ 是**唯一**的共享协调层，它随 base 分支走，所以：
     写它的人在 base 上写，所有工作树都能看见它；并行分支不要动它，否则合并必冲突；
  4. 合并永远发生在 base 工作树（<仓库根>/.worktrees/base）里，按登记顺序（.order）逐个 merge；
  5. 任何删除（清理工作树 / 删分支）都先检查未提交改动，默认拒绝，必须显式 -Force。
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [string]$Command = 'help',
  [Parameter(ValueFromRemainingArguments = $true)]
  [object[]]$Rest
)

# 为什么不是 'Stop'：git 的进度/错误行都走 stderr，而 Windows PowerShell 5.1 会把
# stderr 上含冒号的文本包装成 NativeCommandError —— 用 Stop 会把**已经成功**的
# `git worktree add` 当失败中断。本脚本对每个 git 调用都显式检查 ExitCode，不需要它兜底。
$ErrorActionPreference = 'Continue'

# ============================ 常量与配置 ============================

# 一个已登记任务：名字 + 所属分支 + 工作树路径
$script:Tasks = [System.Collections.Generic.List[object]]::new()

# 共享协调层里「只由 base 维护」的文件：并行分支一旦改动就报警（合并会冲突）
$script:BaseOnlyShared = @('TASKS.md', 'INTERFACES.md', 'ALIGN.md', '_templates', 'wt.ps1', 'wt.cmd')

# base 工作树停在独立的集成分支上 —— git 不允许同一分支被两棵工作树同时检出，
# 所以 base 不能直接检出 master；合并都落在 wt/integration，最后由 fold 折回 master。
$script:IntegrationBranch = 'wt/integration'

# clean 撤销登记时会往这里记「需要提交协调层的仓库」，最后统一提交一次
$script:SharedDirtyRoots = @()

# 本地身份，只用于本仓库提交，不碰全局 ~/.gitconfig
$script:GitUserName = 'worktree-agent'
$script:GitUserEmail = 'worktree-agent@localhost'

# ============================ 输出与校验工具 ============================

function Write-Step { param([string]$Message)
  Write-Host ''
  Write-Host ('== ' + $Message) -ForegroundColor Cyan
}

function Write-Info { param([string]$Message)
  Write-Host ('   ' + $Message)
}

function Write-Ok { param([string]$Message)
  Write-Host ('[OK]   ' + $Message) -ForegroundColor Green
}

function Write-Warn2 { param([string]$Message)
  Write-Host ('[WARN] ' + $Message) -ForegroundColor Yellow
}

function Write-Fail { param([string]$Message)
  Write-Host ('[FAIL] ' + $Message) -ForegroundColor Red
}

function Assert-TaskName { param([string]$Name)
  if ([string]::IsNullOrWhiteSpace($Name)) { throw '任务名不能为空' }
  if ($Name -match '[\\/:*?"<>|\s]') { throw ("任务名含非法字符（空格 / 路径分隔符等）：{0}" -f $Name) }
  if ($Name -match '^[.\-_]') { throw ("任务名不能以 . - _ 开头：{0}" -f $Name) }
  if ($Name.Length -gt 40) { throw '任务名不能超过 40 个字符' }
}

function Assert-RepoBranch { param([string]$Repo, [string]$Branch)
  $check = git -C $Repo check-ref-format --branch $Branch 2>&1
  if ($LASTEXITCODE -ne 0) { throw ("非法分支名：{0}" -f $Branch) }
}

function Invoke-Git { param([string]$Repo, [string[]]$Arguments)
  # 只收 stdout，stderr 让它直接落到控制台 —— 这样 Output 不会把 git 的错误提示当成功输出回显
  $output = git -C $Repo @Arguments 2>$null
  $code = $LASTEXITCODE
  return [pscustomobject]@{
    ExitCode = $code
    Output   = (($output | Out-String) -replace "`r", '').Trim()
  }
}

# git 的进度行（"Preparing worktree …"）走 stderr，而 stderr 上出现含冒号的文本时，
# Windows PowerShell 5.1 会把它包装成语法错误并触发 $ErrorActionPreference='Stop' ——
# 结果是把**已经成功**的 worktree add 当成失败中断。所以调用 git 的段落临时放宽到 Continue。
function Load-Eap { param([string]$Level)
  $script:EapSaved = $ErrorActionPreference
  $ErrorActionPreference = $Level
}

# 历史备注：曾经把 $ErrorActionPreference 设成 'Stop'，结果 git 的 stderr 进度行
# （"Preparing worktree …"）被 PS 5.1 当成语法错误，成功的 worktree add 被中断。
function Unload-Eap {
  if ($null -ne $script:EapSaved) { $ErrorActionPreference = $script:EapSaved; $script:EapSaved = $null }
}

function Get-GitCommonDir { param([string]$Repo)
  $common = git -C $Repo rev-parse --git-common-dir
  if (-not [System.IO.Path]::IsPathRooted($common)) {
    $common = Join-Path $Repo $common
  }
  return (Resolve-Path -LiteralPath $common).Path
}

function Set-LocalGitIdentity { param([string]$Repo)
  $null = git -C $Repo config user.name $script:GitUserName
  $null = git -C $Repo config user.email $script:GitUserEmail
}

function Confirm-WorktreePath { param([string]$Path, [string]$Repo)
  $root = (Resolve-Path -LiteralPath $Repo).Path
  $inside = Join-Path $root '.worktrees'
  $full = [System.IO.Path]::GetFullPath($Path)
  if (-not $full.StartsWith($inside, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw ("拒绝操作工作树目录之外的位置：{0}" -f $full)
  }
  return $full
}

# 本机内层 shell 实测是 Windows PowerShell 5.1（PSEdition=Desktop、无 utf8NoBOM 枚举），
# 而外层 harness 是 PowerShell 7 —— 所以写文件一律走这两个 helper，两边都能跑且无 BOM。
function Get-Utf8NoBom { return (New-Object System.Text.UTF8Encoding($false)) }

function Write-Utf8NoBom { param([string]$Path, [string[]]$Lines)
  [System.IO.File]::WriteAllLines($Path, $Lines, (Get-Utf8NoBom))
}

function Add-Utf8NoBom { param([string]$Path, [string]$Text)
  $full = [System.IO.Path]::GetFullPath($Path)
  $dir = [System.IO.Path]::GetDirectoryName($full)
  if ($dir -and -not (Test-Path -LiteralPath $dir)) { $null = New-Item -ItemType Directory -Path $dir -Force }
  [System.IO.File]::AppendAllText($full, $Text, (Get-Utf8NoBom))
}

function New-Placeholder { param([string]$Path, [string]$Content)
  if (Test-Path -LiteralPath $Path) { return $false }
  $parent = Split-Path -Parent $Path
  if (-not (Test-Path -LiteralPath $parent)) { $null = New-Item -ItemType Directory -Path $parent -Force }
  Write-Utf8NoBom -Path $Path -Lines @($Content)
  return $true
}

# ============================ 仓库根定位 ============================

function Get-RepoRoot { param([string]$StartPath)
  $start = $PSScriptRoot
  if ($StartPath) { $start = $StartPath }
  $top = git -C $start rev-parse --show-toplevel 2>&1
  if ($LASTEXITCODE -ne 0) { throw ("这里不是 git 仓库：{0}" -f $start) }
  return ($top | Select-Object -First 1).ToString().Trim()
}

function Get-BaseBranch { param([string]$Repo, [string]$Explicit)
  if ($Explicit) { return $Explicit }
  $b = git -C $Repo symbolic-ref --short HEAD 2>&1
  if ($LASTEXITCODE -eq 0) { return $b.Trim() }
  return 'master'
}

# ============================ 登记表（.order） ============================

function Get-OrderFilePath { param([string]$Repo)
  return (Join-Path (Join-Path $Repo '.worktrees') '.order')
}

function Get-OrderList { param([string]$Repo)
  $path = Get-OrderFilePath $Repo
  $names = @()
  if (Test-Path -LiteralPath $path) {
    foreach ($line in (Get-Content -LiteralPath $path)) {
      $t = $line.Trim()
      if ($t -and -not $t.StartsWith('#')) { $names += $t }
    }
  }
  # 磁盘上有工作树但没登记的，补在后面，保证「看到的都能合」
  # 注意：git 输出的路径是正斜杠、且可能是长路径形式，比较前必须归一化
  $porcelain = @(git -C $Repo worktree list --porcelain 2>$null)
  $onDisk = @()
  $wtRoot = ((Join-Path $Repo '.worktrees') -replace '\\', '/').TrimEnd('/')
  foreach ($line in $porcelain) {
    if ($line -like 'worktree *') {
      $p = ($line.Substring(9).Trim() -replace '\\', '/').TrimEnd('/')
      $parent = $p.Substring(0, $p.LastIndexOf('/'))
      if ($parent -ieq $wtRoot) {
        $leaf = $p.Substring($p.LastIndexOf('/') + 1)
        if ($leaf) { $onDisk += $leaf }
      }
    }
  }
  foreach ($n in $onDisk) {
    if ($names -notcontains $n -and $n -ne 'base') { $names += $n }
  }
  $result = @($names | Where-Object { $_ -ne 'base' })
  return $result
}

function Add-OrderEntry { param([string]$Repo, [string]$Name)
  $path = Get-OrderFilePath $Repo
  $existing = @(Get-OrderList $Repo)
  if ($existing -contains $Name) { return }
  if (-not (Test-Path -LiteralPath $path)) { Write-Utf8NoBom -Path $path -Lines @('# 合并顺序表：一行一个任务名，先建的在上（wt.ps1 自动维护）') }
  Add-Utf8NoBom -Path $path -Text ($Name + "`r`n")
}

function Get-BranchForTask { param([string]$Repo, [string]$Base, [string]$Name)
  $list = git -C $Repo worktree list --porcelain
  $currentPath = $null
  foreach ($line in $list) {
    if ($line -like 'worktree *') { $currentPath = $line.Substring(9).Trim(); continue }
    if ($line -like 'branch refs/heads/*' -and $currentPath) {
      $branch = $line.Substring(18).Trim()
      $leaf = Split-Path -Leaf $currentPath
      if ($leaf -eq $Name) { return $branch }
    }
  }
  return ("wt/{0}" -f $Name)
}

function Get-Tasks { param([string]$Repo, [string]$Base)
  $script:Tasks.Clear()
  foreach ($name in (Get-OrderList $Repo)) {
    $path = Join-Path (Join-Path $Repo '.worktrees') $name
    if (-not (Test-Path -LiteralPath $path)) { continue }
    $branch = Get-BranchForTask -Repo $Repo -Base $Base -Name $name
    $script:Tasks.Add([pscustomobject]@{ Name = $name; Path = $path; Branch = $branch })
  }
  return $script:Tasks
}

# ============================ 创建 ============================

function Create-BaseWorktree { param([string]$Repo, [string]$Base)  $basePath = Join-Path (Join-Path $Repo '.worktrees') 'base'
  if (Test-Path -LiteralPath $basePath) {
    Write-Info ("base 工作树已存在：{0}" -f $basePath)
    return $basePath
  }
  if (-not (Test-Path -LiteralPath ($repo + '/.worktrees'))) { throw '先跑 wt.ps1 init' }
  # 起点一律用 HEAD 的提交号，不用分支名 —— git 不允许「已被人检出」的分支做 worktree add 的起点
  $start = (git -C $Repo rev-parse 'HEAD^{commit}').Trim()
  $branchExists = git -C $Repo show-ref --verify --quiet ("refs/heads/" + $script:IntegrationBranch)
  if ($LASTEXITCODE -eq 0) {
    Write-Step ("创建 base 工作树（合并工作区，检出已有集成分支 {0}）" -f $script:IntegrationBranch)
    $r = Invoke-Git -Repo $Repo -Arguments @('worktree', 'add', $basePath, $script:IntegrationBranch)
  } else {
    Write-Step ("创建 base 工作树（合并工作区，从 {0} @ {1} 新建集成分支 {2}）" -f $Base, $start.Substring(0, 7), $script:IntegrationBranch)
    Load-Eap 'Continue'
    try { $r = Invoke-Git -Repo $Repo -Arguments @('worktree', 'add', '-b', $script:IntegrationBranch, $basePath, $start) }
    finally { Unload-Eap }
  }
  if ($r.ExitCode -ne 0) { throw ("git worktree add 失败：{0}" -f $r.Output) }
  Write-Ok $r.Output
  if ($script:IntegrationBranch -ne $Base) {
    Write-Info ("base 工作树停在 {0}；合并都落在这里，最后 wt.ps1 fold 折回 {1}（ff-only）" -f $script:IntegrationBranch, $Base)
  }
  return $basePath
}

function Ensure-SharedScaffold { param([string]$Repo)
  $shared = Join-Path (Join-Path $Repo '.worktrees') '_shared'
  $tpl = Join-Path $shared '_templates'
  $null = New-Item -ItemType Directory -Path $tpl -Force

  $created = @()
  if (New-Placeholder -Path (Join-Path $tpl 'TASK.md') -Content @'
# 任务卡：<任务名>

- 分支：`wt/<任务名>`
- 工作树：`<仓库根>/.worktrees/<任务名>/`
- 基线提交：<填创建时的 base 提交号>
- 负责的 agent / 会话：<会话显示名 · sid8>
- 写范围（只许动这些）：<路径通配符，一行一个>
- 依赖的任务与接口：见 `_shared/INTERFACES.md`

## 验收判据（可核验，别写「看起来好了」）

- [ ] <判据一：跑什么命令、期望什么输出>
- [ ] <判据二>

## 进度日志（只追加）

- [yymmdd hh:mm] 开工…
'@) { $created += 'TASK.md' }

  if (New-Placeholder -Path (Join-Path $shared 'TASKS.md') -Content @'
# 任务登记表（base 维护 · 并行分支不要改本文件）

> 合并顺序 = `_shared/.order` 里的顺序（wt.ps1 spawn 时追加）。改判状态就改本表对应行。
> 状态取值：`待办` / `进行中` / `待合并` / `已合并` / `已废弃`。

| 顺序 | 任务 | 分支 | 写范围 | 依赖 | 状态 | 会话 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | <任务名> | `wt/<任务名>` | `<路径>` | 无 | 待办 | <会话名 · sid8> |
'@) { $created += 'TASKS.md' }

  if (New-Placeholder -Path (Join-Path $shared 'INTERFACES.md') -Content @'
# 接口冻结页（先冻结、再并行 · base 维护）

> 并行开工**之前**，由 base 把所有跨分支的约定钉死在这里：函数签名、文件路径、数据格式、命名。
> 并行期间任何一方要改约定，**先在本文件追加一行改判**，再在任务卡里写明，最后才动代码。
> 改判格式：`→ 状态 yymmdd 改判 <新约定>`，**不回改原文**。

## 1. 模块边界

| 模块 | 归属任务 | 唯一对外入口 | 签名 |
| --- | --- | --- | --- |
| <模块> | <任务名> | `<文件>` | `<函数(参数) -> 返回>` |

## 2. 共享数据格式

- <字段名 · 类型 · 单位 · 缺省值>

## 3. 命名与路径

- 分支：`wt/<任务名>`；工作树：`.worktrees/<任务名>/`
- 共享层：`.worktrees/_shared/`（只读；只有 base 能改 `TASKS.md` / `INTERFACES.md` / `ALIGN.md`）

## 4. 改判记录

- （暂无）
'@) { $created += 'INTERFACES.md' }

  if (New-Placeholder -Path (Join-Path $shared 'INBOX.md') -Content @'
# 共享收件箱（所有工作树可写 · 消息块追加，不改别人的块）

> 一个 agent 卡住、要改接口、要别人知道的事，都往这里追加一段。
> **格式**：标题行 `## [yymmdd hh:mm] <来自哪个任务> → <要谁看>`，下面是正文，末尾空一行。
> 只追加，不删别人的块；问题解决了就再追加一段 `→ 已解决 yymmdd`。

## [yymmdd hh:mm] <任务> → all

- 正文…
'@) { $created += 'INBOX.md' }

  if (New-Placeholder -Path (Join-Path $shared 'ALIGN.md') -Content @'
# 对齐页（每次合并后由 wt.ps1 merge 自动追加 · 不要手改）

> 记录：合并了哪个分支、base 停在哪个提交、还有哪些分支没合、是否落后。
> 由 `wt.ps1 merge` 生成，任何并行分支都不要改本文件（改了合并必冲突）。
'@) { $created += 'ALIGN.md' }

  return [pscustomobject]@{ Path = $shared; Created = $created }
}

function New-GitignoreContent {
  return @'
# ============ 多 agent 工作树（本目录只放 README 与共享协调层）============
# 工作树本身（*/.git 是指向仓库 .git 的文件）绝不入库；只放行协调层与操作脚本。
# 复用 260922 的教训：某目录被忽略后，`!` 放行不了它「内部子目录」里的东西，
# 所以四个协调文件必须直接挂在 _shared/ 这一层放行，不能只写 !/_shared/。
/.worktrees/*
!/.worktrees/README.md
!/.worktrees/wt.ps1
!/.worktrees/wt.cmd
!/.worktrees/_shared/
/.worktrees/_shared/*
!/.worktrees/_shared/TASKS.md
!/.worktrees/_shared/INTERFACES.md
!/.worktrees/_shared/INBOX.md
!/.worktrees/_shared/ALIGN.md
!/.worktrees/_shared/tasks/
/.worktrees/_shared/tasks/*
!/.worktrees/_shared/tasks/*.md
'@
}

function Ensure-Gitignore { param([string]$Repo)
  $path = Join-Path $Repo '.gitignore'
  $existing = ''
  if (Test-Path -LiteralPath $path) { $existing = Get-Content -LiteralPath $path -Raw }
  if ($existing -match '(?m)^/\.worktrees/\*') {
    Write-Info '.gitignore 已含 .worktrees 规则，未改动'
    return $false
  }
  $append = "`r`n" + (New-GitignoreContent) + "`r`n"
  Add-Utf8NoBom -Path $path -Text $append
  Write-Ok '.gitignore 已追加 .worktrees 规则（工作树不入库，协调层入库）'
  return $true
}

function Ensure-RepoArtifactIgnores { param([string]$Repo)
  # 各分支各自跑测试会生成 __pycache__/*.pyc，这些二进制产物一旦被提交，
  # 合并时必然 add/add 冲突（实测过一次）。所以 init 顺手补齐「产物类」忽略规则。
  $path = Join-Path $Repo '.gitignore'
  $existing = ''
  if (Test-Path -LiteralPath $path) { $existing = Get-Content -LiteralPath $path -Raw }
  if ($existing -match '(?m)^__pycache__/?\s*$') {
    Write-Info '.gitignore 已忽略 __pycache__，未改动'
    return $false
  }
  $block = @'

# ============ 构建/测试产物（261002 由 wt.ps1 init 追加）============
# 并行分支各自跑测试会生成这些文件；被提交后合并必冲突（二进制 add/add）
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
'@
  Add-Utf8NoBom -Path $path -Text $block
  Write-Ok '.gitignore 已追加产物忽略规则（__pycache__ / *.pyc / 各类缓存目录）'
  return $true
}

# base 工作树停在集成分支上；主分支后来改过 .gitignore（放行协调层）等文件时，
# 集成分支必须跟上 —— 否则 base 里那份旧 .gitignore 会把 TASKS.md 继续忽略掉，
# 导致「spawn 写进去的行在 base 里 add 不上、commit 报 nothing to commit」（实测踩到）。
function Sync-BaseWorktree { param([string]$Repo, [string]$Base)
  $basePath = Join-Path (Join-Path $Repo '.worktrees') 'base'
  if (-not (Test-Path -LiteralPath $basePath)) { return }
  $dirty = @(git -C $basePath status --porcelain 2>$null).Count
  if ($dirty -gt 0) {
    Write-Warn2 ("base 工作树有 {0} 处未提交改动，跳过自动同步（先提交或清理）" -f $dirty)
    return
  }
  $on = [string](git -C $basePath symbolic-ref --short HEAD 2>$null)
  $on = $on.Trim()
  if ($on -ne $script:IntegrationBranch) { return }
  $behind = (git -C $Repo rev-list --count ("{0}..{1}" -f $script:IntegrationBranch, $Base)).Trim()
  if ($behind -eq '0') { return }
  $r = Invoke-Git -Repo $basePath -Arguments @('merge', '--no-edit', $Base)
  if ($r.ExitCode -ne 0) {
    Write-Warn2 ("集成分支未能自动跟上 {0}（{1}）—— 手动处理：在 base 工作树里 git merge {0}" -f $Base, $r.Output)
    return
  }
  Write-Ok ("集成分支已跟上 {0}（{1} 个提交）" -f $Base, $behind)
}

function Invoke-Init { param([string]$Repo, [string]$Base, [switch]$NoBaseWorktree)
  Write-Step ('初始化工作树控制台：{0}' -f $Repo)
  Set-LocalGitIdentity $Repo
  $null = New-Item -ItemType Directory -Path (Join-Path $Repo '.worktrees') -Force
  $scaffold = Ensure-SharedScaffold $Repo
  foreach ($f in $scaffold.Created) { Write-Info ('新建 ' + (Join-Path '_shared' $f)) }
  $changed = Ensure-Gitignore $Repo
  $null = Ensure-RepoArtifactIgnores -Repo $Repo
  $null = Create-BaseWorktree -Repo $Repo -Base $Base
  Sync-BaseWorktree -Repo $Repo -Base $Base

  # 协调层的「真源」在 base 工作树里。主仓那份 scaffold 只是模具：
  # 两者同时存在时以 base 为准，主仓那份要撤掉，否则 spawn 写一份、clean 撤另一份。
  $mainShared = Join-Path (Join-Path $Repo '.worktrees') '_shared'
  $baseShared = Join-Path (Join-Path $Repo '.worktrees\base') '.worktrees/_shared'
  if ((Test-Path -LiteralPath $mainShared) -and (Test-Path -LiteralPath $baseShared)) {
    Remove-Item -LiteralPath $mainShared -Recurse -Force
    Write-Info '已撤掉主仓里的重复协调层（真源 = base 工作树那份）'
  }

  if ($NoBaseWorktree) {
    Write-Info '按 -NoBaseWorktree：本轮不建 base 工作树、不建任何分支（协调层先就位，等你真要并行时再建）'
  }
  Write-Step '完成'
  Write-Info ('共享协调层（真源）：{0}' -f (Get-SharedRoot $Repo))
  Write-Info '下一步：spawn 之前先把跨分支约定写进 _shared/INTERFACES.md 并在 base 里提交'
  return [pscustomobject]@{ GitignoreChanged = $changed; Shared = $scaffold.Path }
}

function Write-SharedFile { param([string]$Repo, [string]$Relative, [string]$Content)
  $path = Join-Path (Join-Path $Repo '.worktrees') $Relative
  $dir = Split-Path -Parent $path
  if (-not (Test-Path -LiteralPath $dir)) { $null = New-Item -ItemType Directory -Path $dir -Force }
  Write-Utf8NoBom -Path $path -Lines @($Content)
}

function Invoke-Spawn { param([string]$Repo, [string]$Base, [string]$Name, [string]$Branch, [string]$Owner, [string]$Scope)
  Assert-TaskName $Name
  if ($Name -eq 'base') { throw '任务名不能叫 base —— base 是合并工作区专用名' }
  if (-not $Branch) { $Branch = "wt/{0}" -f $Name }
  Assert-RepoBranch -Repo $Repo -Branch $Branch

  $basePath = Create-BaseWorktree -Repo $Repo -Base $Base
  $path = Join-Path (Join-Path $Repo '.worktrees') $Name
  if (Test-Path -LiteralPath $path) { throw ("工作树已存在：{0}" -f $path) }

  $exists = git -C $Repo show-ref --verify --quiet ("refs/heads/" + $Branch)
  if ($LASTEXITCODE -eq 0) { throw ("分支已存在：{0}（先清理或换个任务名）" -f $Branch) }

  Write-Step ("创建任务工作树 {0}（分支 {1}，基线 {2}）" -f $Name, $Branch, $Base)
  $start = (git -C $Repo rev-parse 'HEAD^{commit}').Trim()
  Load-Eap 'Continue'
  try { $r = Invoke-Git -Repo $Repo -Arguments @('worktree', 'add', '-b', $Branch, $path, $start) }
  finally { Unload-Eap }
  if ($r.ExitCode -ne 0) { throw ("git worktree add 失败：{0}" -f $r.Output) }
  Write-Ok ("工作树就绪：{0}" -f $path)

  $headCommit = (git -C $path rev-parse --short HEAD).Trim()
  $today = Get-Date -Format 'yyMMdd HH:mm'
  if ([string]::IsNullOrWhiteSpace($Scope)) { $Scope = '<待填：只许动哪些路径>' }
  if ([string]::IsNullOrWhiteSpace($Owner)) { $Owner = '<待填：会话显示名 · sid8>' }

  $card = @"
# 任务卡：$Name

- 分支：``$Branch``
- 工作树：``$path``
- 基线提交：``$headCommit``（开始时的 $Base）
- 负责的 agent / 会话：$Owner
- 写范围（只许动这些）：$Scope
- 依赖的任务与接口：见 ``_shared/INTERFACES.md``

## 验收判据（可核验，别写「看起来好了」）

- [ ] <判据一：跑什么命令、期望什么输出>
- [ ] <判据二>

## 进度日志（只追加）

- [$today] 开工：工作树与分支建好，基线 $headCommit。
"@
  # 任务卡与登记表必须落在同一处（Get-SharedRoot）—— 否则 spawn 写一处、clean 撤另一处
  $cardPath = Join-Path (Get-SharedRoot $Repo) ('tasks/' + $Name + '.md')
  $cardDir = Split-Path -Parent $cardPath
  if (-not (Test-Path -LiteralPath $cardDir)) { $null = New-Item -ItemType Directory -Path $cardDir -Force }
  Write-Utf8NoBom -Path $cardPath -Lines @($card)

  # 登记任务清单（写协调层真源那份；见 Get-SharedRoot）
  Add-TaskRow -Repo $Repo -Name $Name -Branch $Branch -Scope $Scope -Owner $Owner

  Add-OrderEntry -Repo $Repo -Name $Name
  Write-Info ("任务卡：{0}" -f $cardPath)
  return [pscustomobject]@{ Name = $Name; Path = $path; Branch = $Branch; Base = $basePath; Commit = $headCommit }
}

# ============================ 查看 ============================

function Get-TaskStatus { param([string]$Repo, [string]$Base)
  $tasks = Get-Tasks -Repo $Repo -Base $Base
  $out = @()
  foreach ($t in $tasks) {
    $dirty = @(git -C $t.Path status --porcelain 2>$null).Count
    $ahead = [int](git -C $t.Path rev-list --count ("{0}..{1}" -f $Base, $t.Branch) 2>$null)
    $behind = [int](git -C $t.Path rev-list --count ("{0}..{1}" -f $t.Branch, $Base) 2>$null)
    $head = (git -C $t.Path rev-parse --short HEAD).Trim()
    $violations = @()
    $changed = @(git -C $t.Path diff --name-only ("{0}...{1}" -f $Base, $t.Branch) 2>$null)
    foreach ($file in $changed) {
      $key = ($file -replace '\\', '/')
      foreach ($bo in $script:BaseOnlyShared) {
        if ($key -like ('.worktrees/_shared/' + $bo + '*')) { $violations += $key }
      }
    }
    $out += [pscustomobject]@{
      Name = $t.Name; Branch = $t.Branch; Head = $head
      Dirty = $dirty; Ahead = $ahead; Behind = $behind
      SharedViolation = ($violations -join ', ')
    }
  }
  return $out
}

function Invoke-Status { param([string]$Repo, [string]$Base)
  $basePath = Join-Path (Join-Path $Repo '.worktrees') 'base'
  $baseHead = (git -C $Repo rev-parse --short $Base 2>$null).Trim()
  Write-Step ("工作树总览（{0} = {1} @ {2}）" -f $Base, $Base, $baseHead)
  if (Test-Path -LiteralPath $basePath) {
    $dirty = @(git -C $basePath status --porcelain 2>$null).Count
    $tag = 'clean'
    if ($dirty -gt 0) { $tag = ("dirty: {0} 处未提交" -f $dirty) }
    $intBranch = (git -C $basePath rev-parse --abbrev-ref HEAD 2>$null).Trim()
    $behind = [int](git -C $basePath rev-list --count ("{0}..{1}" -f $script:IntegrationBranch, $Base) 2>$null)
    Write-Info ("base 工作树：{0}（分支 {1}，{2}，领先 {3} 个提交未折回）" -f $basePath, $intBranch, $tag, $behind)
    if ($behind -gt 0) { Write-Info ("折回命令：wt.ps1 fold（把 {0} ff 到 {1}）" -f $Base, $script:IntegrationBranch) }
  } else {
    Write-Warn2 'base 工作树还没建（先跑 wt.ps1 init）'
  }
  $tasks = @(Get-TaskStatus -Repo $Repo -Base $Base)
  if ($tasks.Count -eq 0) { Write-Info '还没有并行任务工作树'; return }
  for ($k = 0; $k -lt $tasks.Count; $k++) {
    $t = $tasks[$k]
    $flag = ''
    if ($t.Behind -gt 0) { $flag += ' [落后，需 sync]' }
    if ($t.SharedViolation) { $flag += ' [动了共享层!]' }
    Write-Host ('   {0,-12} {1,-18} HEAD {2,-9} 领先 {3} · 落后 {4} · 未提交 {5}{6}' -f `
      $t.Name, $t.Branch, $t.Head, $t.Ahead, $t.Behind, $t.Dirty, $flag)
  }
  $stale = @($tasks | Where-Object { $_.Behind -gt 0 })
  if ($stale.Count -gt 0) {
    Write-Warn2 ("有 {0} 个任务的基线已经落后 base —— 合并前先 wt.ps1 sync <任务名>，或按依赖顺序合并" -f $stale.Count)
  }
  $bad = @($tasks | Where-Object { $_.SharedViolation })
  if ($bad.Count -gt 0) {
    Write-Warn2 '有任务改动了 base 专属的共享文件（TASKS.md / INTERFACES.md / ALIGN.md）—— 合并时会冲突'
  }
  return $tasks
}

function Invoke-Sync { param([string]$Repo, [string]$Base, [string]$Name)
  if (-not $Name) { throw 'sync 需要一个任务名：wt.ps1 sync <任务名>' }
  $path = Join-Path (Join-Path $Repo '.worktrees') $Name
  if (-not (Test-Path -LiteralPath $path)) { throw ("没有这个工作树：{0}" -f $path) }
  Write-Step ("把 base（{0}）的最新提交同步进 {1}" -f $Base, $Name)
  $dirty = @(git -C $path status --porcelain).Count
  if ($dirty -gt 0) { throw ("{0} 里有 {1} 处未提交改动 —— 先提交或收起来，再同步" -f $Name, $dirty) }
  $r = Invoke-Git -Repo $path -Arguments @('merge', '--no-edit', $Base)
  if ($r.ExitCode -ne 0) {
    $conflicts = @(git -C $path diff --name-only --diff-filter=U)
    Write-Fail ("同步产生冲突，已中止：`n" + $r.Output)
    $null = git -C $path merge --abort
    if ($conflicts.Count -gt 0) { Write-Info ('冲突文件：' + ($conflicts -join ', ')) }
    throw 'sync 未完成（已回滚到同步前状态）'
  }
  Write-Ok ("已把 {0} 合进 {1}：{2}" -f $Base, $Name, $r.Output)
}

# ============================ 合并 ============================

function Get-TestRunner { param([string]$Repo)
  $tests = Join-Path $Repo 'tests'
  if (-not (Test-Path -LiteralPath $tests)) { return $null }
  $python = 'D:\mamba\envs\proaes\python.exe'
  if (-not (Test-Path -LiteralPath $python)) { return $null }
  return [pscustomobject]@{ Kind = 'pytest'; Python = $python; Path = $tests }
}

function Invoke-Tests { param([string]$Repo)
  $runner = Get-TestRunner $Repo
  if (-not $runner) { return [pscustomobject]@{ Ran = $false; Ok = $true; Detail = '未找到 tests 目录或 python，跳过' } }
  Write-Info ("跑测试：{0} -m pytest {1}" -f $runner.Python, $runner.Path)
  # 三条环境约束（都实测踩过）：
  #   TMP/TEMP 指向仓库内 → 否则 pytest 会在系统临时目录建 mkdtemp 子目录，本沙箱删不掉、越积越多
  #   -p no:cacheprovider → 沙箱里建 .pytest_cache 会被拒（刷 WARN 且无意义）
  $tmp = Join-Path $Repo '.tmp'
  if (-not (Test-Path -LiteralPath $tmp)) { $null = New-Item -ItemType Directory -Path $tmp -Force }
  $savedTmp = $env:TMP; $savedTemp = $env:TEMP
  $env:TMP = $tmp; $env:TEMP = $tmp
  try { $out = & $runner.Python -m pytest $runner.Path -q -p no:cacheprovider 2>&1 }
  finally { $env:TMP = $savedTmp; $env:TEMP = $savedTemp }
  $code = $LASTEXITCODE
  $tail = (($out | Select-Object -Last 6) -join ' | ')
  if ($code -eq 0) { Write-Ok $tail } else { Write-Fail $tail }
  return [pscustomobject]@{ Ran = $true; Ok = ($code -eq 0); Detail = $tail }
}

function Write-AlignRow { param([string]$Repo, [string]$Base, [string]$Text)
  $path = Join-Path (Join-Path $Repo '.worktrees') '_shared/ALIGN.md'
  if (-not (Test-Path -LiteralPath $path)) { return }
  Add-Utf8NoBom -Path $path -Text $Text
}

# 对齐簿记（ALIGN.md）本身是入库文件：写完必须立刻提交，否则 base 工作树变脏，
# 下一次 merge 会被「base 有未提交改动」挡下。
function Save-AlignBookkeeping { param([string]$Repo)
  $basePath = Join-Path (Join-Path $Repo '.worktrees') 'base'
  Set-LocalGitIdentity $basePath
  $null = git -C $basePath add -A 2>$null
  $staged = @(git -C $basePath diff --cached --name-only 2>$null)
  if ($staged.Count -eq 0) { return }
  $null = git -C $basePath commit -q -m 'chore(align): 记录本次合并与对齐快照' 2>$null
}

# 协调层的「真源」= base 工作树里那份（它随集成分支提交，是入库的）；
# 没建 base 工作树时（init -NoBaseWorktree）才回落到主仓那份。
# 两份同时存在时**只认 base 那份**：否则 spawn 写一份、clean 撤另一份，登记表必然对不上。
function Get-SharedRoot { param([string]$Repo)
  $baseShared = Join-Path (Join-Path $Repo '.worktrees\base') '.worktrees/_shared'
  if (Test-Path -LiteralPath $baseShared) { return $baseShared }
  return (Join-Path (Join-Path $Repo '.worktrees') '_shared')
}

function Get-TaskListPath { param([string]$Repo)
  return (Join-Path (Get-SharedRoot $Repo) 'TASKS.md')
}

function Add-TaskRow { param([string]$Repo, [string]$Name, [string]$Branch, [string]$Scope, [string]$Owner)
  $path = Get-TaskListPath $Repo
  if (-not (Test-Path -LiteralPath $path)) { return }
  $existing = @(Get-OrderList $Repo)
  if ($existing -notcontains $Name) { $existing += $Name }
  $row = "| {0} | {1} | ``{2}`` | ``{3}`` | <依赖> | 待办 | {4} |" -f $existing.Count, $Name, $Branch, $Scope, $Owner
  Add-Utf8NoBom -Path $path -Text ($row + "`r`n")
}

# spawn 会往 TASKS.md 加一行，clean 必须把这一行撤掉 —— 否则登记表会攒下已经不存在的任务，
# 而它是（入库的）协调层文件，假登记会被后续会话当成真任务。
# 提交时**只 add 这一个文件**：主仓里可能有你正在写的别的东西，绝不能用 `git add -A`。
function Remove-TaskRow { param([string]$Repo, [string]$Name)
  $path = Get-TaskListPath $Repo
  if (-not (Test-Path -LiteralPath $path)) { return }
  $lines = @(Get-Content -LiteralPath $path)
  $kept = @($lines | Where-Object { -not ($_ -match ('^\|\s*\d+\s*\|\s*' + [regex]::Escape($Name) + '\s*\|')) })
  if ($kept.Count -eq $lines.Count) { return }
  Write-Utf8NoBom -Path $path -Lines $kept

  # 任务卡也要一起收掉，否则 _shared/tasks/ 会攒一堆已完成任务的卡
  $card = Join-Path (Split-Path -Parent $path) ('tasks/' + $Name + '.md')
  if (Test-Path -LiteralPath $card) { Remove-Item -LiteralPath $card -Force }
  # 提交不在这里做：clean 可能一次清多个任务，逐个提交会产生一串噪声提交。
  # 登记到「待提交根目录」，由 Commit-SharedChanges 统一提交一次。
  $sharedRoot = Split-Path -Parent $path
  $top = [string](git -C $sharedRoot rev-parse --show-toplevel 2>$null)
  if ($top) { $script:SharedDirtyRoots += $top.Trim() }
  Write-Info ("已撤销登记行与任务卡：{0}" -f $Name)
}

# 在指定仓库里只提交这两个协调层路径（绝不用 git add -A：主仓可能有你正在写的东西），
# 并且**不吞失败**：暂存为空或 commit 失败都要如实报出来。
function Commit-SharedChanges { param([string]$Repo, [string]$Message)
  $top = [string](git -C $Repo rev-parse --show-toplevel 2>$null)
  if (-not $top) { return }
  $top = $top.Trim()
  $rel = '.worktrees/_shared'
  $st = [string](git -C $top status --porcelain -- $rel 2>$null)
  if (-not $st.Trim()) { return }
  Set-LocalGitIdentity $top
  $null = git -C $top add -A -- $rel 2>$null
  $staged = @(git -C $top diff --cached --name-only 2>$null)
  if ($staged.Count -eq 0) {
    Write-Warn2 ("{0} 下有改动但暂存不上 —— 多半是被 .gitignore 挡住了，检查 .worktrees 的忽略规则" -f $rel)
    return
  }
  $out = git -C $top commit -q -m $Message 2>&1
  if ($LASTEXITCODE -ne 0) { Write-Warn2 ("提交协调层失败：{0}" -f (($out | Out-String).Trim())) }
}

function Invoke-Merge { param([string]$Repo, [string]$Base, [string]$Name)
  $basePath = Join-Path (Join-Path $Repo '.worktrees') 'base'
  if (-not (Test-Path -LiteralPath $basePath)) { throw 'base 工作树不存在 —— 先跑 wt.ps1 init' }

  $all = @(Get-Tasks -Repo $Repo -Base $Base)
  if ($all.Count -eq 0) { Write-Info '没有可合并的任务'; return }

  if ($Name) {
    $picked = @($all | Where-Object { $_.Name -eq $Name })
    if ($picked.Count -eq 0) { throw ("没有这个任务：{0}" -f $Name) }
  } else {
    $picked = @($all | Where-Object {
        $ahead = [int](git -C $_.Path rev-list --count ("{0}..{1}" -f $Base, $_.Branch))
        $ahead -gt 0
      })
    if ($picked.Count -eq 0) { Write-Info '所有任务分支都没有新提交，无需合并'; return }
  }

  $dirty = @(git -C $basePath status --porcelain).Count
  if ($dirty -gt 0) { throw ("base 工作树有 {0} 处未提交改动 —— 先提交（协调层改动）再合并" -f $dirty) }
  Set-LocalGitIdentity $basePath

  # 合并一律落在集成分支上（base 工作树本来就停在它上面）；
  # git 不允许同一分支被两棵工作树同时检出，所以这里绝不能去 checkout $Base。
  $cur = (git -C $basePath symbolic-ref --short HEAD 2>$null).Trim()
  if ($cur -ne $script:IntegrationBranch) {
    Write-Info ("base 工作树当前在 {0}，切到集成分支 {1}" -f $cur, $script:IntegrationBranch)
    $r = Invoke-Git -Repo $basePath -Arguments @('checkout', $script:IntegrationBranch)
    if ($r.ExitCode -ne 0) { throw ("切到集成分支失败：{0}" -f $r.Output) }
  }

  $results = @()
  foreach ($t in $picked) {
    Write-Step ("合并 {0}（{1}）→ {2}" -f $t.Name, $t.Branch, $Base)
    $ahead = (git -C $Repo rev-list --count ("{0}..{1}" -f $Base, $t.Branch)).Trim()
    if ($ahead -eq '0') { Write-Info '无新提交，跳过'; continue }

    $msg = ("merge: {0}（wt.ps1 合并分支 {1}）" -f $t.Name, $t.Branch)
    $r = Invoke-Git -Repo $basePath -Arguments @('merge', '--no-ff', '-m', $msg, $t.Branch)
    if ($r.ExitCode -ne 0) {
      $conflicts = @(git -C $basePath diff --name-only --diff-filter=U)
      Write-Fail ("合并冲突，已回滚本次合并：`n" + $r.Output)
      $null = git -C $basePath merge --abort
      Write-AlignRow -Repo $Repo -Base $Base -Text ("`n- [{0}] 合并 {1} **失败（冲突）**：{2}" -f (Get-Date -Format 'yyMMdd HH:mm'), $t.Name, ($conflicts -join ' / '))
      Save-AlignBookkeeping $Repo
      $results += [pscustomobject]@{ Task = $t.Name; Ok = $false; Detail = ('冲突：' + ($conflicts -join ', ')); Commit = '' }
      continue
    }
    $commit = (git -C $basePath rev-parse --short HEAD).Trim()
    Write-Ok ("合并成功 → {0} @ {1}" -f $script:IntegrationBranch, $commit)
    $test = Invoke-Tests $basePath
    if ($test.Ran -and -not $test.Ok) {
      Write-Warn2 '测试没过 —— 集成分支停在这次合并上，等你决定回滚还是继续修'
    }
    Write-AlignRow -Repo $Repo -Base $Base -Text ("`n- [{0}] 合并 {1}（{2}）→ {3} @ {4}；测试：{5}" -f (Get-Date -Format 'yyMMdd HH:mm'), $t.Name, $t.Branch, $script:IntegrationBranch, $commit, $test.Detail)
    Save-AlignBookkeeping $Repo
    $results += [pscustomobject]@{ Task = $t.Name; Ok = $true; Detail = $test.Detail; Commit = $commit }
  }

  # 每次合并后刷新对齐页：还有谁没合、谁落后了
  $remaining = @(Get-Tasks -Repo $Repo -Base $Base | Where-Object {
      $a = [int](git -C $_.Path rev-list --count ("{0}..{1}" -f $Base, $_.Branch)); $a -gt 0
    })
  $aheadOfBase = [int](git -C $basePath rev-list --count ("{0}..HEAD" -f $Base) 2>$null)
  $lines = @("`n### 对齐快照 {0}" -f (Get-Date -Format 'yyMMdd HH:mm'))
  $lines += ("- 集成分支 {0} 提交：{1}（领先 {2} {3} 个提交）" -f $script:IntegrationBranch, (git -C $basePath rev-parse --short HEAD).Trim(), $Base, $aheadOfBase)
  if ($aheadOfBase -gt 0) { $lines += ("- 折回：`wt.ps1 fold`（把 {0} 快进到 {1}）" -f $Base, $script:IntegrationBranch) }
  if ($remaining.Count -eq 0) { $lines += '- 待合并：无（全部已并入 base）' }
  else {
    $lines += ('- 待合并：' + (($remaining | ForEach-Object { $_.Name }) -join ', '))
    foreach ($t in $remaining) {
      $b = [int](git -C $t.Path rev-list --count ("{0}..{1}" -f $t.Branch, $Base))
      if ($b -gt 0) { $lines += ("  - {0} 已落后 base {1} 个提交：合并前先 wt.ps1 sync {0}" -f $t.Name, $b) }
    }
    $lines += '- 各工作树取最新 base：`wt.ps1 sync <任务名>`'
  }
  Write-AlignRow -Repo $Repo -Base $Base -Text ($lines -join "`r`n")
  Save-AlignBookkeeping $Repo
  Write-Step '合并结果'
  $results | Format-Table -AutoSize
  if ($aheadOfBase -gt 0) {
    Write-Info ("下一步：wt.ps1 fold —— 把 {0} 快进折回 {1}，完成最终合并" -f $script:IntegrationBranch, $Base)
  }
  return $results
}

# ============================ 折回 ============================

function Invoke-Fold { param([string]$Repo, [string]$Base)
  $basePath = Join-Path (Join-Path $Repo '.worktrees') 'base'
  if (-not (Test-Path -LiteralPath $basePath)) { throw 'base 工作树不存在 —— 先跑 wt.ps1 init' }
  $intExists = git -C $Repo show-ref --verify --quiet ("refs/heads/" + $script:IntegrationBranch)
  if ($LASTEXITCODE -ne 0) { throw ("没有集成分支 {0} —— 还没有合并过任何任务" -f $script:IntegrationBranch) }

  $intCommit = (git -C $Repo rev-parse ("refs/heads/{0}" -f $script:IntegrationBranch)).Trim()
  git -C $Repo merge-base --is-ancestor ("refs/heads/{0}" -f $Base) $intCommit 2>$null
  if ($LASTEXITCODE -ne 0) {
    throw ("{0} 与 {1} 已分叉 —— 无法快进。先把 {0} 合进 {1}（在 base 工作树里 git merge {0}），再 fold" -f $Base, $script:IntegrationBranch)
  }
  $ahead = (git -C $Repo rev-list --count ("refs/heads/{0}..{1}" -f $Base, $intCommit)).Trim()
  if ($ahead -eq '0') { Write-Info ("{0} 没有新提交，无需折回" -f $script:IntegrationBranch); return }

  Write-Step ("把 {0} 折回 {1}（{2} 个提交，快进）" -f $script:IntegrationBranch, $Base, $ahead)
  Set-LocalGitIdentity $Repo

  # 关键：不用 checkout。
  # 启动仓库里可能有你正在改的东西（高频开发时必然如此），checkout 会把它挡下来甚至搅乱它；
  # 而 update-ref 是**只动分支指针**的原子操作 —— 工作区、暂存区、你的未提交改动全都不碰。
  # 打 CAS 补丁（旧值 = 刚读到的 master commit）→ 期间若有人提交过就会失败，不会覆盖别人的工作。
  # （PowerShell 坑：单元素数组上 -eq 返回布尔而非字符串，所以取字段一律用 [string]。）
  $baseCommit = [string](git -C $Repo rev-parse ("refs/heads/{0}" -f $Base)).Trim()
  $r = Invoke-Git -Repo $Repo -Arguments @('update-ref', ("refs/heads/{0}" -f $Base), $intCommit, $baseCommit)
  if ($r.ExitCode -ne 0) {
    Write-Fail $r.Output
    throw ("折回失败（{0} 没被改动）：{1}" -f $Base, $r.Output)
  }
  $commit = (git -C $Repo rev-parse --short HEAD).Trim()
  Write-Ok ("{0} 已到 {1}（工作区一个字都没动：{2} 处未提交改动原样保留）" -f $Base, $commit, (@(git -C $Repo status --porcelain 2>$null).Count))

  # 注意：**不要**在这里把 base 工作树切到 $Base。
  # 若 $Base 正被启动仓库检出，切过去会让启动仓库的 index 与 HEAD 脱节，
  # 用户的 git status 会凭空多出一堆假的 D（实测踩到）。base 工作树留在集成分支即可。

  $remaining = @(Get-Tasks -Repo $Repo -Base $Base | Where-Object {
      $a = [int](git -C $_.Path rev-list --count ("{0}..{1}" -f $Base, $_.Branch)); $a -gt 0
    })
  if ($remaining.Count -gt 0) {
    Write-Warn2 ('还有任务没合：' + (($remaining | ForEach-Object { $_.Name }) -join ', '))
  }
  Write-AlignRow -Repo $Repo -Base $Base -Text ("`n- [{0}] fold：{1} 快进到 {2} @ {3}（未触碰启动仓库工作区）" -f (Get-Date -Format 'yyMMdd HH:mm'), $Base, $script:IntegrationBranch, $commit)
  return $commit
}

# ============================ 清理 ============================

function Invoke-Clean { param([string]$Repo, [string]$Name, [switch]$Force)
  $wtRoot = Join-Path $Repo '.worktrees'
  if ($Name) {
    $path = Join-Path $wtRoot $Name
    if ($Name -eq 'base') { throw 'base 工作树不能清理（它是合并工作区）' }
    $targets = @([pscustomobject]@{ Name = $Name; Path = $path })
  } else {
    $targets = @(Get-Tasks -Repo $Repo -Base (Get-BaseBranch -Repo $Repo))
  }

  Write-Step '拟清理的工作树（分支保留，随时可重新挂出来）'
  $rows = @()
  foreach ($t in $targets) {
    $exists = Test-Path -LiteralPath $t.Path
    $dirty = 0
    $branch = ''
    if ($exists) {
      $dirty = @(git -C $t.Path status --porcelain 2>$null).Count
      $branch = (git -C $t.Path rev-parse --abbrev-ref HEAD 2>$null).Trim()
    }
    $rows += [pscustomobject]@{
      任务 = $t.Name; 路径 = $t.Path; 存在 = $exists
      未提交 = $dirty; 分支 = $branch
      处置 = $(if (-not $exists) { '跳过' } elseif ($dirty -gt 0 -and -not $Force) { '拒绝（有未提交改动）' } else { 'git worktree remove' })
    }
  }
  $rows | Format-Table -AutoSize

  if (-not $Force) {
    $blocked = @($rows | Where-Object { $_.处置 -like '拒绝*' })
    if ($blocked.Count -gt 0) {
      Write-Warn2 ("有 {0} 个工作树含未提交改动 —— 这是安全闸门。确认要丢弃就加 -Force 重跑" -f $blocked.Count)
    }
    Write-Info '（默认不动任何东西。确认后加 -Force 执行）'
    return $rows
  }

  $done = @()
  foreach ($t in $targets) {
    if ($t.Name -eq 'base') { continue }
    if (-not (Test-Path -LiteralPath $t.Path)) { continue }
    $path = Confirm-WorktreePath -Path $t.Path -Repo $Repo
    $r = Invoke-Git -Repo $Repo -Arguments @('worktree', 'remove', '--force', $path)
    if ($r.ExitCode -ne 0) { Write-Fail ("移除失败：{0} —— {1}" -f $t.Name, $r.Output); continue }
    Write-Ok ("已移除工作树 {0}" -f $t.Name)
    $done += $t.Name
    Remove-TaskRow -Repo $Repo -Name $t.Name
  }
  $orderPath = Get-OrderFilePath $Repo
  if (Test-Path -LiteralPath $orderPath) {
    $keep = @(Get-Content -LiteralPath $orderPath | Where-Object { $_.Trim() -and ($done -notcontains $_.Trim()) })
    Write-Utf8NoBom -Path $orderPath -Lines $keep
  }
  foreach ($root in ($script:SharedDirtyRoots | Select-Object -Unique)) {
    Commit-SharedChanges -Repo $root -Message ("chore(align): 清理已回收任务的登记行与任务卡" )
  }
  $porcelain = (git -C $Repo worktree list --porcelain) -join "`n"
  foreach ($n in $done) {
    $branch = "wt/{0}" -f $n
    if ($porcelain -notmatch [regex]::Escape($branch)) {
      Write-Info ("分支 {0} 仍在（要删：git -C {1} branch -D {0}）" -f $branch, $Repo)
    }
  }
  return $done
}

# ============================ 命令分发 ============================

function Show-Help {
  Write-Host @'
wt.ps1 —— 多 agent 并行工作树控制台

  init [-Base <基线分支>] [-NoBaseWorktree]
                                        初始化：建 .worktrees、共享协调层、base 工作树、.gitignore 规则
                                        -NoBaseWorktree = 只装协调层，不建任何工作树/分支（在改的项目用这个）
  spawn <任务名> [-Scope <路径>] [-Owner <会话>] [-Branch <分支>] [-Base <基线分支>]
                                        为一个 agent 建工作树 + 分支 + 任务卡
  list / status                         看所有工作树：分支、领先/落后几个提交、未提交、是否动了共享层
  sync <任务名>                         把 base 的最新提交合进该工作树（对齐用）
  merge [任务名] [-Base <基线分支>]      按登记顺序合并到集成分支（不带任务名 = 合一波）
  fold                                  把集成分支快进折回基线分支（master），完成最终合并
  clean [任务名] [-Force]               移除工作树（默认只预演；分支保留）
  help                                  这份说明

路径约定
  仓库根              <repo>
  合并工作区          <repo>/.worktrees/base（停在集成分支 wt/integration）
  每个 agent 的工作树  <repo>/.worktrees/<任务名>（各自分支 wt/<任务名>）
  共享协调层          <repo>/.worktrees/_shared/（TASKS.md / INTERFACES.md / INBOX.md / ALIGN.md / tasks/）

为什么 base 不直接检出 master
  git 不允许同一分支被两棵工作树同时检出。所以合并落在一个独立的集成分支上，
  最后由 fold 用 --ff-only 折回 master —— 快进不产生额外提交，历史上看不出中间层。
'@
}

$repo = Get-RepoRoot
$baseArg = ''

# 解析 -Base / -Scope / -Owner / -Branch / -Force（同时支持 `-Scope=值` 与 `-Scope 值` 两种写法）
$positional = @()
$knownSwitches = @('-Base', '-Scope', '-Owner', '-Branch')
$pending = $null
foreach ($item in $Rest) {
  if ($item -is [string] -and $item.StartsWith('-')) {
    if ($item -ieq '-Force') { $Force = $true; continue }
    if ($item -ieq '-NoBaseWorktree') { $NoBaseWorktree = $true; continue }
    if ($item -imatch '^-(Base|Scope|Owner|Branch)=(.+)$') {
      $key = $Matches[1].ToLower()
      $val = $Matches[2]
      switch ($key) {
        'base' { $baseArg = $val }
        'scope' { $Scope = $val }
        'owner' { $Owner = $val }
        'branch' { $Branch = $val }
      }
      continue
    }
    if ($knownSwitches -contains $item) { $pending = $item.Substring(1).ToLower(); continue }
    throw ("不认识的参数：{0}（用 wt.ps1 help 看说明）" -f $item)
  }
  if ($pending) {
    switch ($pending) {
      'base' { $baseArg = [string]$item }
      'scope' { $Scope = [string]$item }
      'owner' { $Owner = [string]$item }
      'branch' { $Branch = [string]$item }
    }
    $pending = $null
    continue
  }
  if ($null -ne $item) {
    $s = [string]$item
    # 以 - 开头的多字符项一定是（写错的）开关，不能当成任务名收下去
    if ($s.Length -gt 1 -and $s.StartsWith('-') -and $s -notmatch '^-\d') {
      throw ("不认识的参数：{0}（用 wt.ps1 help 看说明）" -f $s)
    }
    $positional += $s
  }
}
if ($pending) { throw ("参数 -{0} 后面缺值" -f $pending) }

$base = Get-BaseBranch -Repo $repo -Explicit $baseArg

try {
  switch ($Command.ToLower()) {
    'help' { Show-Help }
    'init' { $null = Invoke-Init -Repo $repo -Base $base -NoBaseWorktree:$NoBaseWorktree }
    'spawn' {
      if ($positional.Count -lt 1) { throw 'spawn 需要一个任务名：wt.ps1 spawn <任务名>' }
      $null = Invoke-Spawn -Repo $repo -Base $base -Name $positional[0] -Branch $Branch -Owner $Owner -Scope $Scope
    }
    'list' { $null = Invoke-Status -Repo $repo -Base $base }
    'status' { $null = Invoke-Status -Repo $repo -Base $base }
    'sync' {
      if ($positional.Count -lt 1) { throw 'sync 需要一个任务名：wt.ps1 sync <任务名>' }
      Invoke-Sync -Repo $repo -Base $base -Name $positional[0]
    }
    'merge' {
      $mergeName = ''
      if ($positional.Count -ge 1) { $mergeName = $positional[0] }
      $null = Invoke-Merge -Repo $repo -Base $base -Name $mergeName
    }
    'fold' { $null = Invoke-Fold -Repo $repo -Base $base }
    'clean' {
      $cleanName = ''
      if ($positional.Count -ge 1) { $cleanName = $positional[0] }
      $null = Invoke-Clean -Repo $repo -Name $cleanName -Force:$Force
    }
    'diag' {
      Write-Step '诊断：任务发现'
      $p = Get-OrderFilePath $repo
      Write-Info ('.order        = {0}（存在：{1}）' -f $p, (Test-Path -LiteralPath $p))
      if (Test-Path -LiteralPath $p) { Write-Info ('内容：' + ((Get-Content -LiteralPath $p) -join ' / ')) }
      $wtRoot = ((Join-Path $repo '.worktrees') -replace '\\', '/').TrimEnd('/')
      Write-Info ('.worktrees 归一化 = {0}' -f $wtRoot)
      foreach ($line in @(git -C $repo worktree list --porcelain 2>$null)) {
        if ($line -like 'worktree *') {
          $wp = ($line.Substring(9).Trim() -replace '\\', '/').TrimEnd('/')
          $parent = $wp.Substring(0, $wp.LastIndexOf('/'))
          Write-Info ('  路径 {0}`n        parent 匹配 = {1}' -f $wp, ($parent -ieq $wtRoot))
        }
      }
      $list = @(Get-OrderList $repo)
      Write-Info ('Get-OrderList 返回 {0} 项' -f $list.Count)
      for ($k = 0; $k -lt $list.Count; $k++) {
        Write-Info ('   [{0}] type={1} value={2}' -f $k, $list[$k].GetType().Name, $list[$k])
      }
      $ts = @(Get-Tasks -Repo $repo -Base $base)
      Write-Info ('Get-Tasks   返回 {0} 项' -f $ts.Count)
      for ($k = 0; $k -lt $ts.Count; $k++) { Write-Info ('   [{0}] {1}' -f $k, $ts[$k].Name) }
      $st = @(Get-TaskStatus -Repo $repo -Base $base)
      Write-Info ('Get-TaskStatus 返回 {0} 项' -f $st.Count)
      $b = Join-Path $repo '.worktrees\base'
      Write-Info ('base 工作树存在 = {0}' -f (Test-Path -LiteralPath $b))
      Write-Info ('tasks 集合类型 = {0}' -f $script:Tasks.GetType().Name)
    }
    default { throw ("不认识的命令：{0}（用 wt.ps1 help 看说明）" -f $Command) }
  }
  exit 0
} catch {
  Write-Host ''
  Write-Fail $_.Exception.Message
  exit 1
}
