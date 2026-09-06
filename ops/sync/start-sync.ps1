param(
    [switch]$Initialize,
    [int]$CloudInitTimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..\..'))

$compose = @('compose','--env-file','.env.local','-f','docker-compose.offline.yml')
$cloud = 'ops/sync/engines/cloud.properties'
$local = 'ops/sync/engines/local.properties'
$syncEnv = 'ops/local/secrets/supabase-sync.env'
$configDir = (Resolve-Path 'ops/sync/config').Path

if (-not (Test-Path '.env.local')) { throw 'Missing .env.local. Start/configure the local stack first.' }
if (-not (Test-Path $cloud) -or -not (Test-Path $local)) {
    throw 'Missing generated engine files. Run .\ops\sync\configure-sync.ps1 first.'
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker is not available.' }

function Invoke-CloudPsql {
    param([string[]]$PsqlArgs)
    if (-not (Test-Path $syncEnv)) {
        throw 'Missing ops/local/secrets/supabase-sync.env. Run configure-sync.ps1 first.'
    }
    & docker run --rm --env-file $syncEnv -v "${configDir}:/config:ro" postgres:17-alpine psql @PsqlArgs
    if ($LASTEXITCODE -ne 0) { throw 'Cloud PostgreSQL command failed.' }
}

if ($Initialize) {
    Write-Host 'Validating least-privilege Supabase sync prerequisites...' -ForegroundColor Cyan
    Invoke-CloudPsql @('-v','ON_ERROR_STOP=1','-f','/config/bootstrap-cloud.sql')
}

Write-Host 'Starting SymmetricDS (sync profile)...' -ForegroundColor Cyan
& docker @compose --profile sync up -d symmetricds
if ($LASTEXITCODE -ne 0) { throw 'SymmetricDS failed to start.' }

Write-Host 'SymmetricDS management endpoint is bound to 127.0.0.1:31415.' -ForegroundColor Green
Write-Host 'Offline writes remain queued locally and resume when connectivity returns.'

if (-not $Initialize) { exit 0 }

Write-Host 'Waiting for the cloud engine to create its psychdeep_sync runtime tables...' -ForegroundColor Cyan
$deadline = (Get-Date).AddSeconds($CloudInitTimeoutSeconds)
$ready = $false
while ((Get-Date) -lt $deadline) {
    $result = & docker run --rm --env-file $syncEnv postgres:17-alpine psql -Atqc "select case when to_regclass('psychdeep_sync.sym_node') is null then '0' else '1' end" 2>$null
    if ($LASTEXITCODE -eq 0 -and ($result | Select-Object -Last 1).Trim() -eq '1') {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 3
}
if (-not $ready) {
    throw "Cloud SymmetricDS runtime tables did not appear within $CloudInitTimeoutSeconds seconds. Check container logs and cloud DB connectivity."
}

Write-Host 'Installing the fixed 19-table PsychDeep replication allowlist...' -ForegroundColor Cyan
Invoke-CloudPsql @('-v','ON_ERROR_STOP=1','-f','/config/psychdeep-sync.sql')

$laptopLine = Get-Content $local | Where-Object { $_ -match '^external\.id=' } | Select-Object -First 1
$laptopId = if ($laptopLine) { $laptopLine.Substring('external.id='.Length).Trim() } else { 'psychdeep-laptop' }
if ([string]::IsNullOrWhiteSpace($laptopId)) { $laptopId = 'psychdeep-laptop' }

Write-Host "Opening one registration window for local node '$laptopId'..." -ForegroundColor Cyan
& docker @compose exec -T symmetricds /opt/symmetric-ds/bin/symadmin --engine cloud open-registration local $laptopId
if ($LASTEXITCODE -ne 0) { throw 'Could not open SymmetricDS registration.' }

Write-Host 'Restarting SymmetricDS so the local engine registers and begins initial sync...' -ForegroundColor Cyan
& docker @compose restart symmetricds
if ($LASTEXITCODE -ne 0) { throw 'SymmetricDS restart failed.' }

Start-Sleep -Seconds 8
Write-Host 'Checking cloud registration metadata...' -ForegroundColor Cyan
Invoke-CloudPsql @('-Atqc',"select external_id || ':' || node_group_id from psychdeep_sync.sym_node where external_id in ('psychdeep-cloud', '$laptopId') order by external_id;")

Write-Host ''
Write-Host 'Initial synchronization plumbing is configured.' -ForegroundColor Green
Write-Host 'Do not use a clinical record as a test. Verify with a disposable non-clinical account/row before enabling simultaneous local/cloud editing.' -ForegroundColor Yellow
