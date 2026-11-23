@echo off

set filename=ZMD

for /f %%i in ('powershell -Command "Get-Date -Format 'yyyyMMddHHmmss'"') do set ts=%%i
echo making %ts%_%filename%
mkdir .\jobs\%ts%_%filename%