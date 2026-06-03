$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$PythonExe = "C:\tmp\cfvenv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    throw "Local Python environment was not found at: $PythonExe. Create it with: py -m venv C:\tmp\cfvenv; C:\tmp\cfvenv\Scripts\python.exe -m pip install -r requirements.txt"
}

$env:PYTHONIOENCODING = "utf-8"

& $PythonExe run_local_server.py
