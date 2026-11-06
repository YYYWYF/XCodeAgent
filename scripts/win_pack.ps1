# 在 Windows x64 主机上依次打包后端和 Electron 安装包。
[CmdletBinding()]
param(
  [string]$Env = "",
  [string]$Python = "",
  [switch]$Slim
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackendScript = Join-Path $PSScriptRoot "build-backend-win.ps1"
$FrontendRoot = Join-Path $RepoRoot "Frontend"
. (Join-Path $PSScriptRoot "BackendEnvironment.ps1")
Set-BackendEnvironment -AppEnvironment $Env -BackendRoot (Join-Path $RepoRoot "Backend") -Required | Out-Null

Get-Command pnpm -ErrorAction Stop | Out-Null

$PreviousGrammarProfile = [Environment]::GetEnvironmentVariable("DEVAGENTSTUDIO_BACKEND_GRAMMARS", "Process")
try {
  if ($Slim) {
    $env:DEVAGENTSTUDIO_BACKEND_GRAMMARS = "builtin"
  }
  else {
    $env:DEVAGENTSTUDIO_BACKEND_GRAMMARS = "full"
  }

  & $BackendScript -Env $Env -Python $Python
  if ($LASTEXITCODE -ne 0) {
    throw "Windows backend packaging failed."
  }
}
finally {
  if ($null -eq $PreviousGrammarProfile) {
    Remove-Item Env:DEVAGENTSTUDIO_BACKEND_GRAMMARS -ErrorAction SilentlyContinue
  }
  else {
    $env:DEVAGENTSTUDIO_BACKEND_GRAMMARS = $PreviousGrammarProfile
  }
}

Push-Location $FrontendRoot
try {
  & pnpm "build:win:$Env"
  if ($LASTEXITCODE -ne 0) {
    throw "Windows Electron packaging failed."
  }
}
finally {
  Pop-Location
}

Write-Host "Windows x64 package created in $FrontendRoot\dist"
