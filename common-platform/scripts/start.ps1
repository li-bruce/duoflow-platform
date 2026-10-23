param([ValidateRange(1, 65535)][int]$Port = 8030, [string]$DataDirectory = $env:PLATFORM_DATA_DIR)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not $DataDirectory) { $DataDirectory = Join-Path $projectRoot 'data' }
if (-not [IO.Path]::IsPathRooted($DataDirectory)) { $DataDirectory = Join-Path $projectRoot $DataDirectory }
$dataPath = [IO.Path]::GetFullPath($DataDirectory)
# Each data directory has independent state and logs for isolated instances.
$logPath = Join-Path $dataPath 'logs'
$statePath = Join-Path $dataPath 'server.json'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run scripts/setup.ps1 first.' }
& $pythonPath (Join-Path $PSScriptRoot 'build_module_developer_prompt.py') --check
if ($LASTEXITCODE -ne 0) { throw 'Developer prompt is stale. Review prompts/README.md and rebuild it before starting.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'web\dist\index.html'))) { throw 'Build the web app first: npm run build in web/.' }
New-Item -ItemType Directory -Path $dataPath,$logPath -Force | Out-Null
if (Test-Path -LiteralPath $statePath) {
    $status = & (Join-Path $PSScriptRoot 'stop.ps1') -DataDirectory $dataPath -CheckOnly
    if ($status.running) {
        if ($status.port -ne $Port) { throw "This data directory already has a server on port $($status.port). Use a separate -DataDirectory for another instance." }
        try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 3 } catch { throw 'The recorded server is still running but not healthy. Inspect its logs before restarting.' }
        if ($health.service -ne 'common-platform') { throw 'The recorded server did not return the expected health response.' }
        Write-Output "Already running: http://127.0.0.1:$Port"
        return
    }
}
$portProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback,$Port)
try { $portProbe.Start() } catch { throw "Port $Port is occupied. Use -Port with a free port. No existing process was stopped." } finally { $portProbe.Stop() }
$outLog = Join-Path $logPath 'server.stdout.log'
$errLog = Join-Path $logPath 'server.stderr.log'
$previousDataDirectory = $env:PLATFORM_DATA_DIR
try {
    $env:PLATFORM_DATA_DIR = $dataPath
    $serverProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port',"$Port",'--workers','1') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
} finally { $env:PLATFORM_DATA_DIR = $previousDataDirectory }
@{pid=$serverProcess.Id; started_utc=$serverProcess.StartTime.ToUniversalTime().ToString('o'); project=$projectRoot; python=$pythonPath; port=$Port} | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8

# Persist the launcher and real Python server with precise creation times.
# stop.ps1 also discovers late children while the launcher still exists.
$captureScript = @'
import datetime, json, os, pathlib, sys
import psutil
state_path = pathlib.Path(sys.argv[1])
state = json.loads(state_path.read_text(encoding="utf-8-sig"))
norm = lambda value: os.path.normcase(os.path.realpath(value))
allowed = {norm(state["python"]), norm(sys._base_executable)}
args = ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(state["port"]), "--workers", "1"]
root = psutil.Process(state["pid"])
expected_created = datetime.datetime.fromisoformat(state["started_utc"].replace("Z", "+00:00")).timestamp()
if abs(root.create_time() - expected_created) > 0.00001:
    raise SystemExit("The launcher PID was reused. Process state was not changed.")
records = []
for process in [root] + root.children(recursive=True):
    try:
        command = process.cmdline()
        if (norm(process.exe()) in allowed and command and norm(command[0]) in allowed
                and command[1:] == args and norm(process.cwd()) == norm(state["project"])):
            records.append({"pid": process.pid, "create_time": process.create_time()})
    except psutil.NoSuchProcess:
        pass
if not records or not any(record["pid"] == root.pid for record in records):
    raise SystemExit("Could not verify the launched process.")
state["processes"] = records
temporary = state_path.with_suffix(".tmp")
temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
os.replace(temporary, state_path)
'@
for ($attempt=0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 300
    $serverProcess.Refresh()
    if ($serverProcess.HasExited) { throw "The server exited. Check $errLog" }
    try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 2 } catch { continue }
    if ($health.service -eq 'common-platform') {
        $captureScript | & $pythonPath - $statePath
        if ($LASTEXITCODE -ne 0) { throw 'The service started, but its process state could not be verified. State was retained for inspection.' }
        Write-Output "Started: http://127.0.0.1:$Port"
        Write-Output "Stop with scripts/stop.ps1 -DataDirectory `"$dataPath`""
        return
    }
}
throw "Service did not become ready. Check $errLog. State was retained so scripts/stop.ps1 can safely stop this instance."
