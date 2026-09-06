param([switch]$Initialize)
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..\..'))

$cloud = 'ops/sync/engines/cloud.properties'
$local = 'ops/sync/engines/local.properties'
if (-not (Test-Path $cloud) -or -not (Test-Path $local)) {
    throw 'Missing generated engine files. Run .\ops\sync\configure-sync.ps1 first.'
}

if ($Initialize) {
    Write-Host 'FIRST ROLLOUT - prerequisite before starting SymmetricDS:' -ForegroundColor Yellow
    Write-Host 'Apply ops/sync/config/bootstrap-cloud.sql to Supabase using DBeaver/SQL Editor.'
    Write-Host 'It only creates the private psychdeep_sync schema; it does NOT enable replication.'
    Write-Host 'Continue only after backup/staging validation.'
    Write-Host ''
}

Write-Host 'Starting SymmetricDS (opt-in sync profile)...' -ForegroundColor Cyan
docker compose --env-file .env.local -f docker-compose.offline.yml --profile sync up -d symmetricds
if ($LASTEXITCODE -ne 0) { throw 'SymmetricDS failed to start.' }

Write-Host 'SymmetricDS is listening only on 127.0.0.1:31415.' -ForegroundColor Green
Write-Host 'When Internet is unavailable, local changes remain in the local PostgreSQL/SymmetricDS queues.'

if ($Initialize) {
    Write-Host ''
    Write-Host 'After the cloud engine creates psychdeep_sync.sym_*:' -ForegroundColor Cyan
    Write-Host '1) Apply ops/sync/config/psychdeep-sync.sql to the CLOUD database using DBeaver/psql.'
    Write-Host '2) Open registration inside the container:'
    Write-Host '   docker compose --env-file .env.local -f docker-compose.offline.yml exec symmetricds /opt/symmetric-ds/bin/symadmin --engine cloud open-registration local psychdeep-laptop'
    Write-Host '3) Restart SymmetricDS so the local engine registers:'
    Write-Host '   docker compose --env-file .env.local -f docker-compose.offline.yml restart symmetricds'
    Write-Host '4) Verify both directions with a NON-CLINICAL test row before enabling normal use.'
}
