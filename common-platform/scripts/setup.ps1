param([string]$Python = 'python', [string]$Npm = 'npm.cmd', [string]$Node = 'node')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$nodePath = (Get-Command -Name $Node -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$nodeVersionText = & $nodePath -p 'process.versions.node'
if ($LASTEXITCODE -ne 0) { throw 'Could not read the selected Node version.' }
$nodeVersion = [version]$nodeVersionText
# Vite 7's engines.node is ^20.19.0 || >=22.12.0.
if (-not (($nodeVersion.Major -eq 20 -and $nodeVersion.Minor -ge 19) -or $nodeVersion -ge [version]'22.12.0')) {
    throw "Node $nodeVersion is too old for this frontend. Supply -Node with the full path to Node 22.12+ (or 20.19+)."
}
$nodeDirectory = Split-Path -Parent $nodePath
if (-not $PSBoundParameters.ContainsKey('Npm') -and (Test-Path -LiteralPath (Join-Path $nodeDirectory 'npm.cmd'))) {
    $Npm = Join-Path $nodeDirectory 'npm.cmd'
}
$npmPath = (Get-Command -Name $Npm -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$npmCli = if ([IO.Path]::GetFileName($npmPath) -eq 'npm-cli.js') { $npmPath } else { Join-Path (Split-Path -Parent $npmPath) 'node_modules\npm\bin\npm-cli.js' }
if (-not (Test-Path -LiteralPath $npmCli)) { throw 'Cannot find npm-cli.js beside the selected npm. Supply -Npm with the npm.cmd path from a standard Node installation.' }
# Invoke npm with the selected Node, and make lifecycle scripts use it too.
$previousPath = $env:PATH
$env:PATH = $nodeDirectory + [IO.Path]::PathSeparator + $previousPath
Push-Location $projectRoot
try {
    Write-Output "Using Node $nodeVersion at $nodePath"
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { & $Python -m venv .venv; if ($LASTEXITCODE) { throw 'venv failed' } }
    & '.\.venv\Scripts\python.exe' -m pip install -r requirements.lock.txt
    if ($LASTEXITCODE) { throw 'Python dependency installation failed.' }
    Push-Location web
    try {
        & $nodePath $npmCli ci
        if ($LASTEXITCODE) { throw 'Frontend dependency installation failed.' }
        & $nodePath $npmCli run build
        if ($LASTEXITCODE) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
} finally { Pop-Location; $env:PATH = $previousPath }
