[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$FrontendRoot = Join-Path $RepoRoot "Frontend"
$Package = Get-Content -LiteralPath (Join-Path $FrontendRoot "package.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$PreviousNpmCache = $env:npm_config_cache
$env:npm_config_cache = Join-Path $RepoRoot ".npm-cache"
Push-Location $FrontendRoot
try {
  # 执行与原工程相同的 pnpm dev，使用声明版本避开本机损坏的全局 pnpm 入口。
  & npm.cmd exec --yes --package $Package.packageManager -- pnpm dev
  $FrontendExitCode = $LASTEXITCODE
}
finally {
  Pop-Location
  $env:npm_config_cache = $PreviousNpmCache
}
exit $FrontendExitCode
