[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$FrontendRoot = Join-Path $RepoRoot "Frontend"
$ElectronViteEntry = Join-Path $FrontendRoot "node_modules\electron-vite\bin\electron-vite.js"
$NodeCommand = Get-Command node -ErrorAction SilentlyContinue

if (-not $NodeCommand) {
  throw "Node.js was not found. Install Node.js before starting the frontend."
}
if (-not (Test-Path -LiteralPath $ElectronViteEntry -PathType Leaf)) {
  throw "Frontend dependencies are missing. Run npm exec --yes pnpm@9.15.9 -- install --frozen-lockfile in Frontend."
}

# 直接使用项目内 Electron Vite，避免全局 pnpm/Corepack 版本探测影响日常启动。
$env:APP_ENV = "dev"
Push-Location $FrontendRoot
try {
  & $NodeCommand.Source $ElectronViteEntry dev
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}
finally {
  Pop-Location
}
