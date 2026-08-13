param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8001,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"

$BackendRoot = Split-Path -Parent $PSScriptRoot
Set-Location $BackendRoot

function Set-EnvIfMissing {
    param(
        [string]$Name,
        [string]$Value
    )
    if (-not [Environment]::GetEnvironmentVariable($Name, "Process") -and $Value) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

$apiKey = [Environment]::GetEnvironmentVariable("DEEPSEEK_API_KEY", "Process")
if (-not $apiKey) {
    $apiKey = [Environment]::GetEnvironmentVariable("DEEPSEEK_API_KEY", "User")
}
if (-not $apiKey) {
    $apiKey = [Environment]::GetEnvironmentVariable("DEEPSEEK_API_KEY", "Machine")
}
Set-EnvIfMissing "DEEPSEEK_API_KEY" $apiKey

if ($apiKey) {
    Set-EnvIfMissing "LIMITUPLAB_LLM_ENABLED" "true"
    Set-EnvIfMissing "LIMITUPLAB_LLM_BASE_URL" "https://api.deepseek.com"
    Set-EnvIfMissing "LIMITUPLAB_LLM_MODEL" "deepseek-v4-flash"
}

foreach ($proxyName in "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY") {
    $proxyValue = [Environment]::GetEnvironmentVariable($proxyName, "Process")
    if ($proxyValue -eq "http://127.0.0.1:9") {
        [Environment]::SetEnvironmentVariable($proxyName, $null, "Process")
    }
}

$python = Join-Path $BackendRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Virtualenv not found: $python"
}

$args = @("-m", "uvicorn", "app.main:app", "--host", $HostAddress, "--port", "$Port")
if ($Reload) {
    $args += "--reload"
}

& $python @args
