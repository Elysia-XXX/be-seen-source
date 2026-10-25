$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$backendPath = Join-Path $projectRoot 'backend'
$frontendPath = Join-Path $projectRoot 'frontend'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$nodePath = (Get-Command node -ErrorAction Stop).Source
$viteEntry = Join-Path $frontendPath 'node_modules\vite\bin\vite.js'
if (!(Test-Path -LiteralPath $pythonPath) -or !(Test-Path -LiteralPath $viteEntry)) {
    throw 'Install backend and frontend dependencies first; see README.md.'
}
foreach ($listenPort in @(8000, 5173)) {
    if (Get-NetTCPConnection -LocalPort $listenPort -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $listenPort is already in use. Reuse the existing preview or stop it first."
    }
}
$runtimeDir = Join-Path $backendPath 'data'
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$jobs = @()
function Stop-OwnedTree([int]$processNumber) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$processNumber" -ErrorAction SilentlyContinue
    foreach ($child in $children) { Stop-OwnedTree $child.ProcessId }
    Stop-Process -Id $processNumber -ErrorAction SilentlyContinue
}
try {
    $apiProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000') -WorkingDirectory $backendPath -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDir 'api.log') -RedirectStandardError (Join-Path $runtimeDir 'api-error.log')
    $jobs += $apiProcess
    $workerProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m','app.worker') -WorkingDirectory $backendPath -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDir 'worker.log') -RedirectStandardError (Join-Path $runtimeDir 'worker-error.log')
    $jobs += $workerProcess
    $webProcess = Start-Process -FilePath $nodePath -ArgumentList @('node_modules/vite/bin/vite.js','--host','127.0.0.1','--port','5173','--strictPort') -WorkingDirectory $frontendPath -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDir 'web.log') -RedirectStandardError (Join-Path $runtimeDir 'web-error.log')
    $jobs += $webProcess
    $ready = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ($jobs | Where-Object HasExited) { throw 'A service exited; inspect backend/data/*-error.log.' }
        try {
            $null = Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/health'
            $null = Invoke-WebRequest 'http://127.0.0.1:5173/' -UseBasicParsing
            $ready = $true
            break
        } catch { }
    }
    if (!$ready) { throw 'Services did not become ready; inspect backend/data logs.' }
    $jobs | ForEach-Object { @{Id=$_.Id; StartTicks=$_.StartTime.ToUniversalTime().Ticks.ToString()} } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimeDir 'local-processes.json') -Encoding utf8
    Write-Host 'Ready: http://127.0.0.1:5173/  (stop with scripts/Stop-Local.ps1)'
} catch {
    $jobs | Where-Object { !$_.HasExited } | ForEach-Object { Stop-OwnedTree $_.Id }
    throw
}
