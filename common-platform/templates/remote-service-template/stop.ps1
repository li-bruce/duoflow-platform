param([string]$DataDirectory = $env:REMOTE_SERVICE_DATA_DIR, [string]$Python, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath($PSScriptRoot)
if (-not $Python) { $Python = Join-Path (Split-Path -Parent (Split-Path -Parent $projectRoot)) '.venv\Scripts\python.exe' }
$pythonPath = (Get-Command -Name $Python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
if (-not $DataDirectory) { $DataDirectory = Join-Path $projectRoot 'data' }
if (-not [IO.Path]::IsPathRooted($DataDirectory)) { $DataDirectory = Join-Path $projectRoot $DataDirectory }
$dataPath = [IO.Path]::GetFullPath($DataDirectory)
$statePath = Join-Path $dataPath 'server.json'
if (-not (Test-Path -LiteralPath $statePath)) {
    if ($CheckOnly) { [pscustomobject]@{ running = $false; port = $null } } else { Write-Output 'No recorded server.' }
    return
}
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'The project Python environment is required to identify its processes safely.' }

# Each psutil Process retains its creation time. Verify every target before any
# signal; a reused PID or an unrelated command is never stopped.
$processScript = @'
import datetime, json, os, pathlib, sys
import psutil

project, state_name, mode = sys.argv[1:]
norm = lambda value: os.path.normcase(os.path.realpath(value))
state_path = pathlib.Path(state_name)
state = json.loads(state_path.read_text(encoding="utf-8-sig"))
expected_python = norm(sys.executable)
if norm(state["project"]) != norm(project) or norm(state["python"]) != expected_python:
    raise SystemExit("Recorded project/Python does not match. No process was stopped.")
port = int(state["port"])
if not 1 <= port <= 65535:
    raise SystemExit("Invalid recorded port. No process was stopped.")
allowed_exes = {expected_python, norm(sys._base_executable)}
expected_args = ["-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port), "--workers", "1"]
targets = {}

def verify(process, created=None):
    actual_created = process.create_time()
    if created is not None and abs(actual_created - float(created)) > 0.00001:
        raise SystemExit("PID has been reused. No process was stopped.")
    args = process.cmdline()
    if (norm(process.exe()) not in allowed_exes or not args
            or norm(args[0]) not in allowed_exes or args[1:] != expected_args
            or norm(process.cwd()) != norm(project)):
        raise SystemExit("Process identity does not match this project. No process was stopped.")
    if not process.is_running():
        raise psutil.NoSuchProcess(process.pid)
    targets[process.pid] = process

records = state.get("processes")
if not records:
    # The earlier launcher stored an ISO time with sub-second precision.
    created = datetime.datetime.fromisoformat(state["started_utc"].replace("Z", "+00:00")).timestamp()
    records = [{"pid": state["pid"], "create_time": created}]
for record in records:
    try:
        verify(psutil.Process(int(record["pid"])), record["create_time"])
    except psutil.NoSuchProcess:
        pass

# A Windows venv launcher starts a base-Python child. Discover it only through
# verified ancestors; never select processes by name or by an occupied port.
console_exe = norm(os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "conhost.exe"))
for parent in list(targets.values()):
    try:
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        continue
    for child in children:
        try:
            if norm(child.exe()) == console_exe:
                continue  # Windows closes the console when its clients exit.
            verify(child)
        except psutil.NoSuchProcess:
            pass

if mode == "check":
    print(json.dumps({"running": bool(targets), "port": port, "pids": list(targets)}))
    raise SystemExit(0)

# Verify the entire set again, then stop children before their launchers.
for process in list(targets.values()):
    try:
        verify(process, process.create_time())
    except psutil.NoSuchProcess:
        pass
ordered = sorted(targets.values(), key=lambda process: process.create_time(), reverse=True)
for process in ordered:
    try:
        if process.is_running():
            process.terminate()  # psutil checks PID + cached creation time.
    except psutil.NoSuchProcess:
        pass
_, alive = psutil.wait_procs(ordered, timeout=5)
if alive:
    raise SystemExit("Some verified processes did not exit. State retained; retry stop.")
state_path.unlink()
print(json.dumps({"running": False, "port": port, "stopped": list(targets)}))
'@
$mode = if ($CheckOnly) { 'check' } else { 'stop' }
$result = $processScript | & $pythonPath - $projectRoot $statePath $mode
if ($LASTEXITCODE -ne 0) { throw 'Process identity check or stop failed; no unverified process was targeted.' }
$status = $result | ConvertFrom-Json
if ($CheckOnly) { $status } else { Write-Output 'Stopped this independent service. Configuration and request cache were retained.' }
