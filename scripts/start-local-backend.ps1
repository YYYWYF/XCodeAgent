[CmdletBinding()]
param([int]$Port = 8000, [switch]$Check)
# 固定使用本工程 Python 和配置，不受其他已激活虚拟环境影响。
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
$env:DEVAGENTSTUDIO_BACKEND_ENV_FILE = Join-Path $BackendRoot ".env"
if (-not (Test-Path -LiteralPath $env:DEVAGENTSTUDIO_BACKEND_ENV_FILE)) { throw "Missing Backend/.env." }
if (-not $env:DEVAGENTSTUDIO_WORKING_DIR) { $env:DEVAGENTSTUDIO_WORKING_DIR = ".devagentstudio_dev" }
if ($Check) { & $Python --version; & $Pnpm --version; exit $LASTEXITCODE }
Push-Location -LiteralPath $BackendRoot
try { & $Python -B -m uvicorn app.main:app --host 127.0.0.1 --port $Port }
finally { Pop-Location }
