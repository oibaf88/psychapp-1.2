$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..\..'))

if (-not (Test-Path '.env.local')) {
    Copy-Item '.env.local.example' '.env.local'
    Write-Host 'Created .env.local from template.' -ForegroundColor Yellow
    Write-Host 'Edit LOCAL_DB_PASSWORD and JWT_SECRET, then run this script again.' -ForegroundColor Yellow
    exit 1
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker was not found. Install Docker Desktop (or another Docker-compatible runtime) first.'
}

Write-Host 'Starting PsychDeep local/offline...' -ForegroundColor Cyan
docker compose --env-file .env.local -f docker-compose.offline.yml up -d --build db backend frontend
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose failed.' }

Write-Host ''
Write-Host 'PsychDeep local is ready at: http://127.0.0.1:5173' -ForegroundColor Green
Write-Host 'API health:               http://127.0.0.1:8001/api/v1/health'
Write-Host 'PostgreSQL:               127.0.0.1:5433 (local machine only)'
Write-Host ''
Write-Host 'For LM Studio, start its server on port 1234 and in PsychDeep Settings use:'
Write-Host '  http://host.docker.internal:1234/v1'
