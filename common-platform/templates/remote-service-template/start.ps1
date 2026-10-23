param([ValidateRange(1,65535)][int]$Port = 8032, [string]$DataDirectory = $env:REMOTE_SERVICE_DATA_DIR, [string]$Python)
$ErrorActionPreference = 'Stop'
$serviceRoot = [IO.Path]::GetFullPath($PSScriptRoot)
if (-not $Python) { $Python = Join-Path (Split-Path -Parent (Split-Path -Parent $serviceRoot)) '.venv\Scripts\python.exe' }
$pythonPath = (Get-Command -Name $Python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
if (-not $DataDirectory) { $DataDirectory = Join-Path $serviceRoot 'data' }
if (-not [IO.Path]::IsPathRooted($DataDirectory)) { $DataDirectory = Join-Path $serviceRoot $DataDirectory }
$dataPath = [IO.Path]::GetFullPath($DataDirectory)
$statePath = Join-Path $dataPath 'server.json'
& $pythonPath (Join-Path $serviceRoot 'server.py') --init-data $dataPath
if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the service configuration.' }
$serviceToken = (Get-Content -LiteralPath (Join-Path $dataPath 'service-config.json') -Raw | ConvertFrom-Json).service_token
if (Test-Path -LiteralPath $statePath) {
    $status = & (Join-Path $serviceRoot 'stop.ps1') -DataDirectory $dataPath -Python $pythonPath -CheckOnly
    if ($status.running) {
        if ($status.port -ne $Port) { throw 'This data directory already belongs to another running port.' }
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/platform/v1/health" -Headers @{Authorization = ('Bearer ' + $serviceToken)} -TimeoutSec 3
        if ($health.status -ne 'ok') { throw 'The running service is not healthy.' }
        Write-Output "Already running: http://127.0.0.1:$Port"
        return
    }
}
$probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,$Port)
try { $probe.Start() } catch { throw "Port $Port is occupied; no process was stopped." } finally { $probe.Stop() }
$logPath = Join-Path $dataPath 'logs'
New-Item -ItemType Directory -Path $logPath -Force | Out-Null
$oldDataDirectory = $env:REMOTE_SERVICE_DATA_DIR
try {
    $env:REMOTE_SERVICE_DATA_DIR = $dataPath
    $serverProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m','uvicorn','server:app','--host','127.0.0.1','--port',"$Port",'--workers','1') -WorkingDirectory $serviceRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logPath 'stdout.log') -RedirectStandardError (Join-Path $logPath 'stderr.log') -PassThru
} finally { $env:REMOTE_SERVICE_DATA_DIR = $oldDataDirectory }
@{pid=$serverProcess.Id; started_utc=$serverProcess.StartTime.ToUniversalTime().ToString('o'); project=$serviceRoot; python=$pythonPath; port=$Port} | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
$captureScript = @'
import datetime, json, os, pathlib, sys, psutil
path = pathlib.Path(sys.argv[1])
state = json.loads(path.read_text(encoding="utf-8-sig"))
norm = lambda item: os.path.normcase(os.path.realpath(item))
allowed = {norm(state["python"]), norm(sys._base_executable)}
root = psutil.Process(state["pid"])
created = datetime.datetime.fromisoformat(state["started_utc"].replace("Z", "+00:00")).timestamp()
if abs(root.create_time() - created) > 0.00001:
    raise SystemExit("Launcher PID has been reused.")
expected = ["-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(state["port"]), "--workers", "1"]
records = []
for process in [root] + root.children(recursive=True):
    try:
        args = process.cmdline()
        if (norm(process.exe()) in allowed and args and norm(args[0]) in allowed
                and args[1:] == expected and norm(process.cwd()) == norm(state["project"])):
            records.append({"pid": process.pid, "create_time": process.create_time()})
    except psutil.NoSuchProcess:
        pass
if not any(item["pid"] == root.pid for item in records):
    raise SystemExit("Could not verify the service launcher.")
state["processes"] = records
temporary = path.with_suffix(".tmp")
temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
os.replace(temporary, path)
'@
for ($attempt=0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 300
    $serverProcess.Refresh()
    if ($serverProcess.HasExited) { throw "Service exited; inspect $logPath" }
    try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/platform/v1/health" -Headers @{Authorization = ('Bearer ' + $serviceToken)} -TimeoutSec 2 } catch { continue }
    if ($health.status -eq 'ok') {
        $captureScript | & $pythonPath - $statePath
        if ($LASTEXITCODE -ne 0) { throw 'Service started, but process state verification failed.' }
        Write-Output "Started independent service: http://127.0.0.1:$Port"
        Write-Output "Configuration: $(Join-Path $dataPath 'service-config.json') (token not printed)"
        return
    }
}
throw "Service was not ready. Inspect $logPath; its state was retained for stop.ps1."
