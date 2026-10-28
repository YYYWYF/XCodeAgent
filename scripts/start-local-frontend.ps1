[CmdletBinding()]
param([string]$BackendUrl = "http://127.0.0.1:8000", [switch]$Check)
# 确保 pnpm 的嵌套脚本及 Electron 子进程使用项目指定的 9.15.9。
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackendRoot = Join-Path $RepoRoot "Backend"
$VenvScripts = Join-Path $BackendRoot ".venv\Scripts"
$Python = Join-Path $VenvScripts "python.exe"
$Pnpm = Join-Path $VenvScripts "pnpm.cmd"
if (-not (Test-Path -LiteralPath $Python)) { throw "Missing Backend/.venv; install dependencies first." }
if (-not (Test-Path -LiteralPath $Pnpm)) { throw "Missing project pnpm 9 launcher." }
$env:PATH = "$VenvScripts;$env:PATH"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:DEVAGENTSTUDIO_BACKEND_URL = $BackendUrl
$env:DEVAGENTSTUDIO_BASE_URL = $BackendUrl
if ($Check) { & node --version; & $Pnpm --version; exit $LASTEXITCODE }
Push-Location -LiteralPath (Join-Path $RepoRoot "Frontend")
try { & $Pnpm dev }
finally { Pop-Location }
