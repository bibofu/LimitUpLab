param(
    [ValidateSet("Install", "Uninstall", "Status")]
    [string]$Mode = "Install",
    [string]$RunAt = "16:10",
    [string]$TaskName = "LimitUpLab-DailyCloseLoop"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $ProjectRoot "backend"
$Python = Join-Path $BackendRoot ".venv\Scripts\python.exe"
$Runner = Join-Path $BackendRoot "scripts\run_daily_close_loop.py"

if ($Mode -eq "Uninstall") {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task: $TaskName"
    exit 0
}

if ($Mode -eq "Status") {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "Scheduled task is not installed: $TaskName"
        exit 1
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    [pscustomobject]@{
        TaskName = $TaskName
        State = $task.State
        LastRunTime = $info.LastRunTime
        LastTaskResult = $info.LastTaskResult
        NextRunTime = $info.NextRunTime
    } | Format-List
    exit 0
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Backend virtual environment not found: $Python"
}
if (-not (Test-Path -LiteralPath $Runner)) {
    throw "Daily close-loop runner not found: $Runner"
}

$at = [datetime]::ParseExact($RunAt, "HH:mm", $null)
$action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "`"$Runner`" --trigger scheduled" `
    -WorkingDirectory $BackendRoot
$trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -WeeksInterval 1 `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At $at
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "LimitUpLab after-close data, live Top10 prediction and D+1-D+5 outcome loop." `
    -Force | Out-Null

Write-Host "Installed scheduled task: $TaskName"
Write-Host "Schedule: weekdays at $RunAt (Asia/Shanghai local time)"
Write-Host "Latest report: $BackendRoot\data\daily_close_loop_latest.json"
Write-Host "Failure alert: $BackendRoot\data\daily_close_loop_alert.json"
