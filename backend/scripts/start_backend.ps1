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

function Test-LocalProxyPort {
    param([int]$ProxyPort)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.BeginConnect("127.0.0.1", $ProxyPort, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(200)) {
            return $false
        }
        $client.EndConnect($pending)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

$proxyNames = "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"
$proxyUrl = [Environment]::GetEnvironmentVariable("LIMITUPLAB_PROXY_URL", "Process")
foreach ($proxyName in $proxyNames) {
    [Environment]::SetEnvironmentVariable($proxyName, $null, "Process")
}
if (-not $proxyUrl) {
    foreach ($candidatePort in 17891, 7890, 10809, 1080) {
        if (Test-LocalProxyPort $candidatePort) {
            $proxyUrl = "http://127.0.0.1:$candidatePort"
            break
        }
    }
}
if ($proxyUrl) {
    foreach ($proxyName in $proxyNames) {
        [Environment]::SetEnvironmentVariable($proxyName, $proxyUrl, "Process")
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
