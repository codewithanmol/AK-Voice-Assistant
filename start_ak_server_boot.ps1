# AK Mobile Server - permanent auto-start (safe to run every boot/login).
# Idempotent: agar server pehle se chal raha hai to kuch nahi karega, double-start nahi hoga.

$ErrorActionPreference = "SilentlyContinue"
$port = 4097

if (Get-NetTCPConnection -LocalPort $port -State Listen) {
    exit 0
}

$proj = "C:\Users\YOUR_USERNAME\path\to\project"
$logdir = Join-Path $proj "logs"
if (-not (Test-Path -LiteralPath $logdir)) { New-Item -ItemType Directory -Path $logdir | Out-Null }

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = "python" }

Start-Process -FilePath $py `
    -ArgumentList "ak_server.py", "--port", "$port" `
    -WorkingDirectory $proj `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logdir "ak_server_run.log") `
    -RedirectStandardError  (Join-Path $logdir "ak_server_run_err.log")