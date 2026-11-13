<#
  面向机器学习课程的资料驱动学情诊断小测平台 · Windows 一键启动
  说明：控制台输出使用 ASCII，避免 Windows PowerShell 5.1 中文乱码；
        本文件含中文注释，已保存为 UTF-8 with BOM 以便 PS 5.1 正确解析。
  已在 Windows PowerShell 5.1 环境实际验证。

  用法（在项目根目录）：
    .\start.ps1                    安装/构建（如需）→ 启动后端 + 后台 worker → 打开浏览器
    .\start.ps1 -NoBrowser         同上但不打开浏览器（CI / 验证用）
    .\start.ps1 -RebuildFrontend   强制重新构建前端
    .\start.ps1 -Check             只检查服务状态与最近日志
    .\start.ps1 -Stop              停止本脚本启动的后端与 worker（不删除数据）

  浏览器地址： http://127.0.0.1:8000/
  等价手工命令：
    .venv\Scripts\python.exe -X utf8 -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
    .venv\Scripts\python.exe -X utf8 -m backend.app.workers.worker --interval 2
#>
param(
    [switch]$NoBrowser,
    [switch]$RebuildFrontend,
    [switch]$Check,
    [switch]$Stop
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $Root '.venv\Scripts\python.exe'
$DataDb = Join-Path $Root 'data\db'
$BackendOut = Join-Path $DataDb 'uvicorn.log'
$BackendErr = Join-Path $DataDb 'uvicorn.err.log'
$WorkerOut = Join-Path $DataDb 'worker.log'
$WorkerErr = Join-Path $DataDb 'worker.err.log'
$BackendPid = Join-Path $DataDb 'backend.pid'
$WorkerPid = Join-Path $DataDb 'worker.pid'
$BaseUrl = 'http://127.0.0.1:8000'

if (-not (Test-Path $DataDb)) {
    New-Item -ItemType Directory -Path $DataDb -Force | Out-Null
}

function Say($m) { Write-Host "[start] $m" -ForegroundColor Cyan }

function Test-Backend {
    try {
        $r = Invoke-WebRequest -Uri "$BaseUrl/api/health" -UseBasicParsing -TimeoutSec 3
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Reset-DuplicateCaseEnv {
    # Windows PowerShell 5.1 的 Start-Process 在启用输出重定向时，若环境块中
    # 同时存在 HTTP_PROXY / http_proxy 这类仅大小写不同的键会抛
    # "已添加项 / An item with the same key has already been added"。
    # 合并重复键、保留大写取值；仅在启动进程前调用，pip/npm 不受影响。
    foreach ($k in @('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')) {
        $upper = $k.ToUpperInvariant()
        $val = [Environment]::GetEnvironmentVariable($upper)
        [Environment]::SetEnvironmentVariable($k, $null, 'Process')
        if ($val -and -not [Environment]::GetEnvironmentVariable($upper)) {
            [Environment]::SetEnvironmentVariable($upper, $val, 'Process')
        }
    }
}

function Stop-ByPidFile($file, $label) {
    if (Test-Path $file) {
        $raw = (Get-Content $file -Raw).Trim()
        if ($raw) {
            $proc = Get-Process -Id ([int]$raw) -ErrorAction SilentlyContinue
            if ($proc) {
                Stop-Process -Id ([int]$raw) -Force
                Say "Stopped $label PID $raw"
            }
        }
        Remove-Item $file -Force -ErrorAction SilentlyContinue
    }
}

# ------------------------------------------------------------------ stop / check
if ($Stop) {
    Stop-ByPidFile $BackendPid 'backend'
    Stop-ByPidFile $WorkerPid 'worker'
    # venv 的 python.exe 可能是启动器，会派生子进程；PID 文件不一定等于真实监听进程。
    # 因此再按端口与命令行清理，确保 -Stop 真正停止服务。
    $conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        $conn | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
            $p = Get-Process -Id $_ -ErrorAction SilentlyContinue
            if ($p) { Stop-Process -Id $_ -Force; Say "Stopped listener PID $_" }
        }
    }
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -like '*backend.app.workers.worker*' } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force
            Say "Stopped worker process PID $($_.ProcessId)"
        }
    Say 'Stopped. Database and parsed files are untouched.'
    return
}

if ($Check) {
    if (Test-Backend) {
        Say "Backend OK: $BaseUrl/api/health"
        try {
            $g = Invoke-RestMethod -Uri "$BaseUrl/api/graphs" -TimeoutSec 5
            Say "LangGraph loaded: $($g.graphs.PSObject.Properties.Name -join ', ')"
        } catch { Say 'LangGraph status query failed' }
    } else {
        Say 'Backend is not running. Run .\start.ps1 first.'
    }
    if (Test-Path $BackendErr) { Get-Content $BackendErr -Tail 8 }
    return
}

# ------------------------------------------------------------------ 1. Python 3.13 + venv
if (-not (Test-Path $Py)) {
    Say 'Creating isolated virtualenv .venv ...'
    $pythonCommand = $null
    $pythonArgs = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.13 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)" *> $null
        if ($LASTEXITCODE -eq 0) {
            $pythonCommand = 'py'
            $pythonArgs = @('-3.13')
        }
    }
    if (-not $pythonCommand -and (Get-Command python -ErrorAction SilentlyContinue)) {
        & python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)"
        if ($LASTEXITCODE -eq 0) { $pythonCommand = 'python' }
    }
    if (-not $pythonCommand) {
        throw 'Python 3.13 not found. Install Python 3.13 (including the py launcher) and retry.'
    }
    & $pythonCommand @pythonArgs -m venv (Join-Path $Root '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create virtualenv' }
}
$venvVersion = (& $Py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
if ($venvVersion -ne '3.13') {
    throw "The existing .venv uses Python $venvVersion; remove .venv and recreate it with Python 3.13."
}
Say 'Checking backend dependencies ...'
$requirements = Join-Path $Root 'backend\requirements.lock.txt'
if (-not (Test-Path $requirements)) {
    throw 'backend\requirements.lock.txt is missing'
}
& $Py -m pip install -q -r $requirements
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency install failed' }

# ------------------------------------------------------------------ 2. 首次运行数据
Say 'Preparing local demo data ...'
& $Py -X utf8 (Join-Path $Root 'backend\scripts\bootstrap_demo.py')
if ($LASTEXITCODE -ne 0) { throw 'Demo data bootstrap failed' }

# ------------------------------------------------------------------ 3. 前端构建
$node = Get-Command node -ErrorAction SilentlyContinue
$npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $node -or -not $npm) {
    throw 'Node.js 20+ with npm is required'
}
$nodeMajor = [int](& node -p "process.versions.node.split('.')[0]")
if ($nodeMajor -lt 20) {
    throw "Node.js 20+ is required; found $(& node --version)"
}
$dist = Join-Path $Root 'frontend\dist\index.html'
if ($RebuildFrontend -or -not (Test-Path $dist)) {
    Say 'Building frontend ...'
    Push-Location (Join-Path $Root 'frontend')
    try {
        if (-not (Test-Path 'node_modules')) {
            & npm ci --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
        }
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    } finally {
        Pop-Location
    }
}

# ------------------------------------------------------------------ 4. 后端
Reset-DuplicateCaseEnv

if (Test-Backend) {
    Say "Backend already running at $BaseUrl - not starting a duplicate"
} else {
    Say 'Starting FastAPI backend ...'
    $bargs = @('-X', 'utf8', '-m', 'uvicorn', 'backend.app.main:app',
               '--host', '127.0.0.1', '--port', '8000')
    $backend = Start-Process -FilePath $Py -ArgumentList $bargs `
        -WorkingDirectory $Root -RedirectStandardOutput $BackendOut `
        -RedirectStandardError $BackendErr -WindowStyle Hidden -PassThru
    Set-Content -Path $BackendPid -Value $backend.Id -Encoding ascii
    Say "Backend PID $($backend.Id)"
}

# ------------------------------------------------------------------ 5. worker
$workerRunning = $false
if (Test-Path $WorkerPid) {
    $raw = (Get-Content $WorkerPid -Raw).Trim()
    if ($raw) {
        $p = Get-Process -Id ([int]$raw) -ErrorAction SilentlyContinue
        if ($p) { $workerRunning = $true }
    }
    if (-not $workerRunning) { Remove-Item $WorkerPid -Force -ErrorAction SilentlyContinue }
}
if (-not $workerRunning) {
    Say 'Starting background job worker ...'
    $wargs = @('-X', 'utf8', '-m', 'backend.app.workers.worker', '--interval', '2')
    $worker = Start-Process -FilePath $Py -ArgumentList $wargs `
        -WorkingDirectory $Root -RedirectStandardOutput $WorkerOut `
        -RedirectStandardError $WorkerErr -WindowStyle Hidden -PassThru
    Set-Content -Path $WorkerPid -Value $worker.Id -Encoding ascii
    Say "Worker PID $($worker.Id)"
}

# ------------------------------------------------------------------ 6. 就绪检查
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
    if (Test-Backend) { $ready = $true; break }
    Start-Sleep -Milliseconds 500
}
if (-not $ready) {
    Write-Host '[start] Backend not ready within 20s. Error log:' -ForegroundColor Red
    if (Test-Path $BackendErr) { Get-Content $BackendErr -Tail 30 }
    throw 'Startup failed'
}

if (-not $NoBrowser) {
    Start-Process $BaseUrl | Out-Null
    Say 'Opened default browser'
}

Write-Host ''
Write-Host '================ Platform is up ================' -ForegroundColor Green
Write-Host "  URL            : $BaseUrl/" -ForegroundColor Green
Write-Host "  Health         : $BaseUrl/api/health" -ForegroundColor Green
Write-Host "  API docs       : $BaseUrl/docs" -ForegroundColor Green
Write-Host "  Backend log    : $BackendOut" -ForegroundColor Green
Write-Host "  Worker log     : $WorkerOut" -ForegroundColor Green
Write-Host "  Stop           : .\start.ps1 -Stop" -ForegroundColor Green
$modelNote = 'unknown (config/status unreachable)'
try {
    $cfgStatus = Invoke-RestMethod -Uri "$BaseUrl/api/config/status" -TimeoutSec 5
    if ($cfgStatus.model.configured) {
        $modelNote = "configured -> $($cfgStatus.model.chat_model)"
    } else {
        $modelNote = 'not configured -> AI features show "pending" (see .env.example)'
    }
} catch { }
Write-Host "  Model API      : $modelNote" -ForegroundColor Yellow
Write-Host '=================================================' -ForegroundColor Green
