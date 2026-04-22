param(
    [int]$Port = 8501,
    [string]$Region = "ap",
    [string]$Subdomain = ""
)

$loclxCommand = Get-Command "loclx" -ErrorAction SilentlyContinue
if (-not $loclxCommand) {
    $projectRoot = Split-Path -Parent $PSScriptRoot
    $candidatePaths = @(
        (Join-Path $projectRoot "tools\loclx.exe"),
        (Join-Path $projectRoot "Scripts\loclx.exe"),
        "C:\ProgramData\chocolatey\bin\loclx.exe",
        "C:\ProgramData\chocolatey\lib\localxpose\tools\loclx.exe"
    )
    $foundLocalPath = $candidatePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($foundLocalPath) {
        $loclxCommand = $foundLocalPath
    }
}

if (-not $loclxCommand) {
    Write-Error "LocalXpose CLI (loclx) is not installed or not in PATH. Install it from localxpose.io/download, then re-run this script. You can also place loclx.exe at salesagent/tools/loclx.exe."
    exit 1
}

$loclxPath = ""
if ($loclxCommand -is [string]) {
    $loclxPath = $loclxCommand
} else {
    $loclxPath = $loclxCommand.Source
    if (-not $loclxPath) {
        $loclxPath = $loclxCommand.Path
    }
}

# Prefer the real LocalXpose executable over Chocolatey shim when available.
$realLoclx = "C:\ProgramData\chocolatey\lib\localxpose\tools\loclx.exe"
if (Test-Path $realLoclx) {
    $loclxPath = $realLoclx
}

Write-Host "Using LocalXpose binary: $loclxPath"

# Optional: read access token from env and configure CLI before opening tunnel.
$envToken = $env:LOCALXPOSE_ACCESS_TOKEN
if (-not $envToken) {
    $envToken = $env:LOCLX_ACCESS_TOKEN
}

if (-not $envToken) {
    $projectRoot = Split-Path -Parent $PSScriptRoot
    $envFile = Join-Path $projectRoot ".env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            $trimmed = $line.Trim()
            if (-not $trimmed -or $trimmed.StartsWith("#")) {
                continue
            }
            if ($trimmed -match '^LOCALXPOSE_ACCESS_TOKEN\s*=\s*(.+)$') {
                $envToken = $matches[1].Trim().Trim('"').Trim("'")
                break
            }
        }
    }
}

if ($envToken -and $envToken.Trim().Length -gt 0) {
    $statusOutput = & $loclxPath account status 2>&1
    $statusText = ($statusOutput | Out-String)
    if ($statusText -match "not logged in|unauthorized|login") {
        Write-Host "Logging in to LocalXpose using token from environment variable..."
        $envToken.Trim() | & $loclxPath account login | Out-Null
    }
}

function Test-AgentReady {
    $probe = & $loclxPath tunnel list 2>&1
    $probeText = ($probe | Out-String)
    return -not ($probeText -match "cannot connect to the localxpose agent")
}

function Ensure-AgentReady {
    if (Test-AgentReady) {
        return $true
    }

    Write-Host "Starting LocalXpose API agent..."
    Start-Process -FilePath $loclxPath -ArgumentList "api" -WindowStyle Hidden | Out-Null

    for ($i = 0; $i -lt 8; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-AgentReady) {
            return $true
        }
    }

    return $false
}

if (-not (Ensure-AgentReady)) {
    Write-Error "LocalXpose API agent could not be started. Please run: loclx api (in a separate terminal), then rerun this script."
    exit 1
}

$argsList = @("tunnel", "http", "--to", "http://127.0.0.1:$Port")

if ($Region -and $Region.Trim().Length -gt 0) {
    $argsList += @("--region", $Region)
}

if ($Subdomain -and $Subdomain.Trim().Length -gt 0) {
    $argsList += @("--subdomain", $Subdomain)
}

Write-Host "Starting LocalXpose tunnel for http://127.0.0.1:$Port ..."
Write-Host "Command: loclx $($argsList -join ' ')"

$tunnelOutput = & $loclxPath @argsList 2>&1
$tunnelText = ($tunnelOutput | Out-String)

if ($tunnelText -match "cannot connect to the localxpose agent") {
    Write-Host "Agent was unavailable during tunnel launch. Retrying once..."
    if (-not (Ensure-AgentReady)) {
        Write-Error "LocalXpose API agent unavailable. Start it manually with: loclx api"
        exit 1
    }
    & $loclxPath @argsList
} else {
    $tunnelOutput
}
