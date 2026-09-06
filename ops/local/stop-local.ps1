$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..\..'))

Write-Host 'Stopping PsychDeep local services...' -ForegroundColor Cyan
docker compose --env-file .env.local -f docker-compose.offline.yml down
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose failed.' }

Write-Host 'Stopped. Local PostgreSQL data remains in its Docker volume.' -ForegroundColor Green
Write-Host 'Do NOT add -v unless you intentionally want to delete the local database.'
