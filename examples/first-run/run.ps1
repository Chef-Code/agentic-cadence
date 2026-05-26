$ErrorActionPreference = "Stop"

# Runtime output: examples/first-run/work
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$WorkDir = Join-Path $ScriptDir "work"
$TargetRepo = Join-Path $WorkDir "repo"
$RuntimeRoot = Join-Path $WorkDir "runtime"

function Invoke-Native {
    if ($args.Count -lt 1) {
        throw "native command is required"
    }

    $Command = $args[0]
    $Arguments = @()
    if ($args.Count -gt 1) {
        $Arguments = $args[1..($args.Count - 1)]
    }
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE"
    }
}

if (Test-Path $WorkDir) {
    Remove-Item -LiteralPath $WorkDir -Recurse -Force
}
New-Item -ItemType Directory -Path $WorkDir | Out-Null
Copy-Item -LiteralPath (Join-Path $ScriptDir "repo") -Destination $TargetRepo -Recurse

Invoke-Native git -C $TargetRepo init -b main | Out-Null
Invoke-Native git -C $TargetRepo config user.email "demo@example.com"
Invoke-Native git -C $TargetRepo config user.name "Cadence Demo"
Invoke-Native git -C $TargetRepo add README.md docs/cadence/business-memory.md
Invoke-Native git -C $TargetRepo commit -m "Initial example repo" | Out-Null

function Invoke-Cadence {
    if ($env:CODEX_CADENCE_PYTHON) {
        & $env:CODEX_CADENCE_PYTHON -m codex_cadence @args
    } else {
        & agentic-cadence @args
    }
    if ($LASTEXITCODE -ne 0) {
        throw "agentic-cadence command failed with exit code $LASTEXITCODE"
    }
}

Invoke-Cadence --root $RuntimeRoot status | Out-Null
Invoke-Cadence --root $RuntimeRoot create-handoff `
    --id read-the-repo `
    --title "Read the repo" `
    --repo local/example `
    --branch main `
    --task-type discovery `
    --message-file (Join-Path $ScriptDir "handoff.md") | Out-Null
Invoke-Cadence --root $RuntimeRoot next-handoff | Out-Null
Invoke-Cadence --root $RuntimeRoot claim-handoff read-the-repo --claimer demo | Out-Null
Invoke-Cadence --root $RuntimeRoot complete-handoff read-the-repo --summary "first run completed" | Out-Null
$DiscoveryJson = Invoke-Cadence --root $RuntimeRoot discover-candidates --cwd $TargetRepo --intent hybrid --discovery-mode local --elect
$Discovery = $DiscoveryJson | ConvertFrom-Json
if ($null -eq $Discovery.sources.business_memory -or [int] $Discovery.sources.business_memory -lt 1) {
    throw "sources.business_memory must be greater than zero"
}

Write-Output "Agentic Cadence first-run example completed."
