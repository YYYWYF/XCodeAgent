# 解析环境并校验唯一配置文件，不读取或输出配置内容。
function Set-BackendEnvironment {
  param(
    [string]$AppEnvironment,
    [string]$BackendRoot,
    [switch]$Required,
    [switch]$AllowExplicitFile
  )
  if (-not $AppEnvironment -and -not $Required) {
    $AppEnvironment = if ($null -ne $env:APP_ENV) { $env:APP_ENV } else { "dev" }
  }
  if ($AppEnvironment -cnotin @("dev", "st", "uat", "prd")) {
    throw "Provide -Env dev|st|uat|prd. Packaging requires an explicit environment."
  }
  $FileName = if ($AppEnvironment -eq "dev") { ".env" } else { ".env.$AppEnvironment" }
  $FilePath = Join-Path $BackendRoot $FileName
  if ($AllowExplicitFile -and $env:DEVAGENTSTUDIO_BACKEND_ENV_FILE) {
    $FilePath = $env:DEVAGENTSTUDIO_BACKEND_ENV_FILE
  }
  if (-not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
    throw "Missing backend configuration for ${AppEnvironment}: $FilePath"
  }
  $env:APP_ENV = $AppEnvironment
  $env:DEVAGENTSTUDIO_BACKEND_ENV_FILE = (Resolve-Path -LiteralPath $FilePath).Path
  return $env:DEVAGENTSTUDIO_BACKEND_ENV_FILE
}
