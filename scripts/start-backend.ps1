[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackendRoot = Join-Path $RepoRoot "Backend"
$env:XCODEAGENT_WORKING_DIR = if ($env:XCODEAGENT_WORKING_DIR) {
  $env:XCODEAGENT_WORKING_DIR
}
else {
  ".xcodeagent_dev"
}

$PythonCommand = $null
$PythonArguments = @()
$VirtualEnvironmentPython = if ($env:VIRTUAL_ENV) {
  Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
}
else {
  ""
}
$RepositoryPython = Join-Path $BackendRoot ".venv\Scripts\python.exe"

if ($VirtualEnvironmentPython -and (Test-Path -LiteralPath $VirtualEnvironmentPython -PathType Leaf)) {
  $PythonCommand = $VirtualEnvironmentPython
}
elseif (Test-Path -LiteralPath $RepositoryPython -PathType Leaf) {
  $PythonCommand = $RepositoryPython
}
elseif (Get-Command py -ErrorAction SilentlyContinue) {
  $PythonCommand = "py"
  $PythonArguments = @("-3.12")
}
elseif (Get-Command python -ErrorAction SilentlyContinue) {
  $PythonCommand = "python"
}
else {
  throw "Python 3.12 was not found. Create Backend\.venv or install Python 3.12."
}

Push-Location $BackendRoot
try {
  & $PythonCommand @PythonArguments -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
}
finally {
  Pop-Location
}
