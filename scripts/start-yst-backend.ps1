[CmdletBinding()]
param()
# 统一使用原工程的后端启动脚本，不增加云效专属启动流程。
& (Join-Path $PSScriptRoot "start-backend.ps1")
exit $LASTEXITCODE
