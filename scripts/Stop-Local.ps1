$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectRoot 'backend\data\local-processes.json'
if (!(Test-Path -LiteralPath $statePath)) { Write-Host 'No launcher process record found.'; exit }
$records = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
function Stop-OwnedTree([int]$processNumber) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$processNumber" -ErrorAction SilentlyContinue
    foreach ($child in $children) { Stop-OwnedTree $child.ProcessId }
    Stop-Process -Id $processNumber -ErrorAction SilentlyContinue
}
foreach ($record in $records) {
    $ownedProcess = Get-Process -Id $record.Id -ErrorAction SilentlyContinue
    # Never stop a reused PID. Only processes created by Start-Local are eligible.
    if ($ownedProcess -and $ownedProcess.StartTime.ToUniversalTime().Ticks.ToString() -eq $record.StartTicks) {
        Stop-OwnedTree $ownedProcess.Id
    }
}
Write-Host 'Local launcher services stopped. Reports are preserved.'
