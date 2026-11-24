<#
.SYNOPSIS
  Starts the FinCall demo: API (in-memory data, Twilio browser calls), console behind ngrok.

.DESCRIPTION
  1. API: `sb run -- uv run building-call` in backend/, with BC_REPOSITORY=memory and
     BC_TELEPHONY=twilio. Secrets come from backend/.secretbroker through SecretBroker; this file
     holds none. Anything else (LLM, voice, owner e-mail, TwiML App SID) comes from your
     environment or backend/.env as usual.
  2. Console: Vite with BC_TUNEL=1 on 127.0.0.1:5173 (it proxies /api and the voice WebSockets).
  3. ngrok on the public URL, so Twilio and the browser reach the console and the API.

  The public URL is never written here. It comes from -PublicUrl, else $env:BC_PUBLIC_URL, else a
  `BC_PUBLIC_URL = <config credential>:url` line in backend/.secretbroker.
  Ctrl+C stops all three. Logs go to %TEMP%\fincall-demo.

.EXAMPLE
  $env:BC_PUBLIC_URL = "https://<your-domain>.ngrok-free.dev"; .\scripts\start-demo.ps1
#>
param(
    [string]$PublicUrl = $env:BC_PUBLIC_URL,
    [int]$ApiPort = 8000,
    [int]$ConsolePort = 5173,
    [int]$StartupTimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$logs = Join-Path $env:TEMP 'fincall-demo'
New-Item -ItemType Directory -Force $logs | Out-Null

function Stop-Tree([System.Diagnostics.Process]$process) {
    if ($process -and -not $process.HasExited) {
        & taskkill.exe /T /F /PID $process.Id 2>$null | Out-Null
    }
}

function Test-Url([string]$url, [hashtable]$headers = @{}) {
    try {
        $r = Invoke-WebRequest -Uri $url -Headers $headers -UseBasicParsing -TimeoutSec 5
        return $r.StatusCode -eq 200
    } catch { return $false }
}

# --- preconditions --------------------------------------------------------------------------
$sb = Get-Command sb -ErrorAction SilentlyContinue
if (-not $sb) { throw 'SecretBroker CLI (sb) not found: install SecretBroker and open a new terminal.' }
$ngrok = Get-Command ngrok -ErrorAction SilentlyContinue
if (-not $ngrok) { throw 'ngrok not found on PATH.' }

$secretbroker = Join-Path $backend '.secretbroker'
if (-not (Test-Path $secretbroker)) { throw "Missing $secretbroker (credential names for sb run)." }
$names = (Get-Content $secretbroker) -replace '\s*=.*$', '' | Where-Object { $_ -and -not $_.StartsWith('#') }
$needed = 'BC_TWILIO_ACCOUNT_SID', 'BC_TWILIO_AUTH_TOKEN', 'BC_TWILIO_API_KEY_SID', 'BC_TWILIO_API_KEY_SECRET'
$missing = $needed | Where-Object { $names -notcontains $_ }
if ($missing) {
    throw "backend/.secretbroker does not map: $($missing -join ', '). See 'Run the demo' in the README."
}

if (-not $PublicUrl -and ($names -contains 'BC_PUBLIC_URL')) {
    # A SecretBroker config entry (not secret): sb gives it without asking
    Push-Location $backend
    try { $PublicUrl = (& sb run -- powershell -NoProfile -Command '$env:BC_PUBLIC_URL' | Select-Object -Last 1) }
    finally { Pop-Location }
}
if (-not $PublicUrl) {
    throw 'No public URL: pass -PublicUrl, set $env:BC_PUBLIC_URL (your fixed ngrok domain, https://...), or map BC_PUBLIC_URL in backend/.secretbroker.'
}
$PublicUrl = $PublicUrl.Trim().TrimEnd('/')
if ($PublicUrl -notmatch '^https://[^/]+$') { throw "The public URL must be https://<domain>, got '$PublicUrl'." }

foreach ($port in $ApiPort, $ConsolePort) {
    $busy = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    if ($busy) {
        throw "Port $port is in use (PID $($busy[0].OwningProcess)). Stop it first: an orphan API worker keeps answering with old settings."
    }
}

# --- start ------------------------------------------------------------------------------------
$started = @()
$saved = @{}
$demoEnv = @{
    BC_REPOSITORY = 'memory'
    BC_TELEPHONY = 'twilio'
    BC_PUBLIC_URL = $PublicUrl
    BC_CONSOLE_URL = $PublicUrl
    BC_TUNEL = '1'
}
foreach ($k in $demoEnv.Keys) {
    $saved[$k] = [Environment]::GetEnvironmentVariable($k, 'Process')
    [Environment]::SetEnvironmentVariable($k, $demoEnv[$k], 'Process')
}

try {
    Write-Host "Logs: $logs"
    Write-Host 'Starting the API (in-memory data, Twilio browser calls)...'
    $started += Start-Process -FilePath $sb.Source -ArgumentList 'run', '--', 'uv', 'run', 'building-call' `
        -WorkingDirectory $backend -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logs 'api.out.log') -RedirectStandardError (Join-Path $logs 'api.log')

    Write-Host 'Starting the console (Vite, tunnel mode)...'
    $started += Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'corepack', 'pnpm', 'run', 'dev' `
        -WorkingDirectory $frontend -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logs 'console.log') -RedirectStandardError (Join-Path $logs 'console.err.log')

    Write-Host 'Starting ngrok...'
    $started += Start-Process -FilePath $ngrok.Source `
        -ArgumentList 'http', "127.0.0.1:$ConsolePort", '--url', $PublicUrl, '--log', 'stdout' `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logs 'ngrok.log') -RedirectStandardError (Join-Path $logs 'ngrok.err.log')

    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    $skip = @{ 'ngrok-skip-browser-warning' = '1' }
    while (-not (Test-Url "$PublicUrl/api/health" $skip)) {
        foreach ($p in $started) {
            if ($p.HasExited) { throw "A process stopped during startup (exit $($p.ExitCode)). See the logs in $logs." }
        }
        if ((Get-Date) -gt $deadline) { throw "Not healthy through the tunnel after $StartupTimeoutSeconds s. See the logs in $logs." }
        Start-Sleep -Seconds 2
    }

    $health = Invoke-RestMethod -Uri "$PublicUrl/api/health" -Headers $skip -TimeoutSec 10
    Write-Host ''
    Write-Host "  Open: $PublicUrl" -ForegroundColor Green
    Write-Host "  API: $($health.state) | data: $($health.repository) | model: $($health.model) | voice: $($health.server_tts)"
    if (-not $health.twilio_browser) {
        Write-Host '  Twilio browser calls are OFF: set BC_TWILIO_TWIML_APP_SID (env or backend/.env) and check the Twilio names in backend/.secretbroker.' -ForegroundColor Yellow
    }
    Write-Host '  Data is in memory: it is lost when you stop. Ctrl+C stops everything.'
    Write-Host ''

    while ($true) {
        foreach ($p in $started) {
            if ($p.HasExited) { throw "A process stopped (exit $($p.ExitCode)). See the logs in $logs." }
        }
        Start-Sleep -Seconds 2
    }
}
finally {
    Write-Host 'Stopping the demo...'
    foreach ($p in $started) { Stop-Tree $p }
    # uvicorn's reload worker can outlive its parent and keep the port
    Get-NetTCPConnection -State Listen -LocalPort $ApiPort, $ConsolePort -ErrorAction SilentlyContinue |
        ForEach-Object { & taskkill.exe /T /F /PID $_.OwningProcess 2>$null | Out-Null }
    foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k], 'Process') }
    Write-Host 'Stopped.'
}
