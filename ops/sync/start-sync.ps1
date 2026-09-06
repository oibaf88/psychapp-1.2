param([switch]$Initialize)
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..\..'))

$cloud = 'ops/sync/engines/cloud.properties'
$local = 'ops/sync/engines/local.properties'
if (-not (Test-Path $cloud) -or -not (Test-Path $local)) {
    throw 'Missing generated engine files. Run .\ops\sync\configure-sync.ps1 first.'
}

Write-Host 'Starting SymmetricDS (opt-in sync profile)...' -ForegroundColor Cyan
docker compose --env-file .env.local -f docker-compose.offline.yml --profile sync up -d symmetricds
if ($LASTEXITCODE -ne 0) { throw 'SymmetricDS failed to start.' }

Write-Host 'SymmetricDS is listening only on 127.0.0.1:31415.' -ForegroundColor Green
Write-Host 'When Internet is unavailable, local changes remain in the local PostgreSQL/SymmetricDS queues.'

if ($Initialize) {
    Write-Host ''
    Write-Host 'FIRST ROLLOUT - do these only after backup/staging validation:' -ForegroundColor Yellow
    Write-Host '1) Wait until the cloud engine has created psychdeep_sync.sym_* tables.'
    Write-Host '2) Apply ops/sync/config/psychdeep-sync.sql to the CLOUD database using DBeaver/psql.'
    Write-Host '3) Open registration inside the container:'
    Write-Host '   docker compose --env-file .env.local -f docker-compose.offline.yml exec symmetricds /opt/symmetric-ds/bin/symadmin --engine cloud open-registration local psychdeep-laptop'
    Write-Host '4) Restart SymmetricDS so the local engine registers:'
    Write-Host '   docker compose --env-file .env.local -f docker-compose.offline.yml restart symmetricds'
    Write-Host '5) Verify both directions with a NON-CLINICAL test row before enabling normal use.'
}
