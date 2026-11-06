# Windows x64 后端打包步骤：
# 1. 打开powershell,在 64 位 Windows 机器的项目根目录执行本脚本：
#    powershell -ExecutionPolicy Bypass -File scripts/build-backend-win.ps1 -Env st
# 2. 执行前请确保 Backend\.env.st 等对应环境文件已存在。
# 3. 默认使用 64 位 Python 3.12；也可通过 -Python 指定 64 位 Python 3.14。
# 4. 打包后的后端产物会被拷贝到 Frontend\resources\backend\win32。
# 5. 本脚本成功后，再构建 Electron Windows 安装包：
#    cd Frontend
#    pnpm build:win:st
# 实际配置参与安装包构建，但不得提交到 Git。

[CmdletBinding()]
param(
  [string]$Env = "",
  [string]$Python = "",
  [string]$BackendRoot = "",
  [string]$FrontendRoot = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Python)) {
  $PythonCommand = "py"
  $PythonCommandArgs = @("-3.12")
}
else {
  $PythonCommand = $Python
  $PythonCommandArgs = @()
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if ([string]::IsNullOrWhiteSpace($BackendRoot)) {
  $BackendRoot = Join-Path $RepoRoot "Backend"
}
if ([string]::IsNullOrWhiteSpace($FrontendRoot)) {
  $FrontendRoot = Join-Path $RepoRoot "Frontend"
}

$BackendRoot = (Resolve-Path $BackendRoot).Path
$FrontendRoot = (Resolve-Path $FrontendRoot).Path
. (Join-Path $PSScriptRoot "BackendEnvironment.ps1")
$EnvFile = Set-BackendEnvironment -AppEnvironment $Env -BackendRoot $BackendRoot -Required
$SpecFile = Join-Path $BackendRoot "packaging\devagentstudio-backend.spec"
$DistDir = Join-Path $BackendRoot "dist\devagentstudio-backend"
$TargetDir = Join-Path $FrontendRoot "resources\backend\win32"
$TargetExe = Join-Path $TargetDir "devagentstudio-backend.exe"

if (-not [Environment]::Is64BitOperatingSystem) {
  throw "Windows 64-bit is required to build the packaged backend."
}

Push-Location $BackendRoot
try {
  $PythonVersion = & $PythonCommand @PythonCommandArgs -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to inspect Python version. Install 64-bit Python 3.12 or 3.14."
  }
  if ($PythonVersion.Trim() -notin @("3.12", "3.14")) {
    throw "Python 3.12 or 3.14 is required to build the Windows backend. Current Python version: $PythonVersion"
  }

  $PythonArchitecture = & $PythonCommand @PythonCommandArgs -c "import platform; print(platform.architecture()[0])"
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to inspect Python architecture."
  }
  if ($PythonArchitecture.Trim() -ne "64bit") {
    throw "Python must be 64-bit to build the Windows x64 backend. Current Python architecture: $PythonArchitecture"
  }

  & $PythonCommand @PythonCommandArgs -m pip install -r requirements-build.txt
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to install backend build requirements."
  }

  & $PythonCommand @PythonCommandArgs -m PyInstaller --noconfirm --clean $SpecFile
  if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed to build devagentstudio-backend.exe."
  }
}
finally {
  Pop-Location
}

if (-not (Test-Path $DistDir -PathType Container)) {
  throw "PyInstaller output not found: $DistDir"
}

& $PythonCommand @PythonCommandArgs (Join-Path $BackendRoot "packaging\verify_bundled_skills.py") $DistDir
if ($LASTEXITCODE -ne 0) {
  throw "Bundled backend resource verification failed."
}

if (Test-Path $TargetDir) {
  Remove-Item $TargetDir -Recurse -Force
}

New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
Copy-Item (Join-Path $DistDir "*") $TargetDir -Recurse -Force
Copy-Item $EnvFile (Join-Path $TargetDir (Split-Path $EnvFile -Leaf)) -Force

if (-not (Test-Path $TargetExe -PathType Leaf)) {
  throw "Staged backend executable not found: $TargetExe"
}

Write-Host "Backend staged for Electron at $TargetDir"
