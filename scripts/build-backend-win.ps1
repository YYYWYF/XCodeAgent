# Windows x64 后端打包步骤：
# 1. 打开powershell,在 64 位 Windows 机器的项目根目录执行本脚本：
#    powershell -ExecutionPolicy Bypass -File scripts/build-backend-win.ps1
# 2. 执行前请确保 Backend\.env 已存在。
# 3. 请使用 64 位 Python；脚本会在调用 PyInstaller 前检查 Python 架构。
# 4. 打包后的后端产物会被拷贝到 Frontend\resources\backend\win32。
# 5. 本脚本成功后，再构建 Electron Windows 安装包：
#    cd Frontend
#    pnpm build:win:dev

# 注意：后端需要有一个.env文件，存放密钥，这个后续要上传到git，也要参与打包

[CmdletBinding()]
param(
  [string]$Python = "python",
  [string]$BackendRoot = "",
  [string]$FrontendRoot = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if ([string]::IsNullOrWhiteSpace($BackendRoot)) {
  $BackendRoot = Join-Path $RepoRoot "Backend"
}
if ([string]::IsNullOrWhiteSpace($FrontendRoot)) {
  $FrontendRoot = Join-Path $RepoRoot "Frontend"
}

$BackendRoot = (Resolve-Path $BackendRoot).Path
$FrontendRoot = (Resolve-Path $FrontendRoot).Path
$EnvFile = Join-Path $BackendRoot ".env"
$SpecFile = Join-Path $BackendRoot "packaging\xcodeagent-backend.spec"
$DistDir = Join-Path $BackendRoot "dist\xcodeagent-backend"
$TargetDir = Join-Path $FrontendRoot "resources\backend\win32"
$TargetExe = Join-Path $TargetDir "xcodeagent-backend.exe"

if (-not [Environment]::Is64BitOperatingSystem) {
  throw "Windows 64-bit is required to build the packaged backend."
}

if (-not (Test-Path $EnvFile -PathType Leaf)) {
  throw "Missing Backend\.env. Create it before building the packaged backend."
}

Push-Location $BackendRoot
try {
  $PythonArchitecture = & $Python -c "import platform; print(platform.architecture()[0])"
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to inspect Python architecture."
  }
  if ($PythonArchitecture.Trim() -ne "64bit") {
    throw "Python must be 64-bit to build the Windows x64 backend. Current Python architecture: $PythonArchitecture"
  }

  & $Python -m pip install -r requirements-build.txt
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to install backend build requirements."
  }

  & $Python -m PyInstaller --noconfirm --clean $SpecFile
  if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed to build xcodeagent-backend.exe."
  }
}
finally {
  Pop-Location
}

if (-not (Test-Path $DistDir -PathType Container)) {
  throw "PyInstaller output not found: $DistDir"
}

& $Python (Join-Path $BackendRoot "packaging\verify_bundled_skills.py") $DistDir
if ($LASTEXITCODE -ne 0) {
  throw "Bundled built-in skill verification failed."
}

if (Test-Path $TargetDir) {
  Remove-Item $TargetDir -Recurse -Force
}

New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
Copy-Item (Join-Path $DistDir "*") $TargetDir -Recurse -Force
Copy-Item $EnvFile (Join-Path $TargetDir ".env") -Force

if (-not (Test-Path $TargetExe -PathType Leaf)) {
  throw "Staged backend executable not found: $TargetExe"
}

Write-Host "Backend staged for Electron at $TargetDir"
