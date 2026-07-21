# Convenience wrapper: activate venv, load secrets, run the agent.
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Dir
if (Test-Path .\.venv\Scripts\Activate.ps1) { . .\.venv\Scripts\Activate.ps1 }
if (Test-Path .\.env.ps1) { . .\.env.ps1 }
python main.py @args
