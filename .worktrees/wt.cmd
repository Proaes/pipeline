@echo off
rem wt.cmd —— 双击/命令行都可用的包装：绕过 RemoteSigned 对未签名 .ps1 的拦截
rem 用法同 wt.ps1，例如：wt spawn 任务A   /   wt list   /   wt merge
setlocal
set "PS=pwsh"
where pwsh >nul 2>nul || set "PS=powershell"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0wt.ps1" %*
exit /b %ERRORLEVEL%
