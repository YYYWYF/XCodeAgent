param(
    [int]$Port = 8100,
    [string]$BindAddress = "127.0.0.1",
    [string]$PythonPath = ""
)

# 以单工作进程启动，避免重复执行启动时的历史中断对账。
$ErrorActionPreference = "Stop"
$ServiceRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $ServiceRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "未找到 Python。请创建 .venv 并安装 requirements.txt，或使用 -PythonPath 指定解释器。"
}
if (-not (Test-Path -LiteralPath (Join-Path $ServiceRoot ".env"))) {
    throw "请先将 .env.example 复制为 .env，并填写当前上游密钥及服务访问凭证。"
}
Push-Location -LiteralPath $ServiceRoot
try {
    & $PythonPath -B -m uvicorn app.main:create_app --factory --host $BindAddress --port $Port --workers 1
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
