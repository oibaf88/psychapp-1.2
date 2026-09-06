$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..\..'))

$secretDir = 'ops/local/secrets'
$tokenFile = Join-Path $secretDir 'cloudflare-tunnel-token.txt'
New-Item -ItemType Directory -Force -Path $secretDir | Out-Null

if (-not (Test-Path $tokenFile)) {
    Write-Host 'Cloudflare Tunnel token not found.' -ForegroundColor Yellow
    $token = Read-Host 'Paste the tunnel token (the eyJ... value)'
    if ([string]::IsNullOrWhiteSpace($token)) { throw 'No token supplied.' }
    Set-Content -Path $tokenFile -Value $token.Trim() -NoNewline
}

Write-Host 'Starting outbound-only Cloudflare Tunnel...' -ForegroundColor Cyan
docker compose --env-file .env.local -f docker-compose.offline.yml --profile tunnel up -d cloudflared
if ($LASTEXITCODE -ne 0) { throw 'cloudflared failed to start.' }

Write-Host 'Tunnel container started.' -ForegroundColor Green
Write-Host 'In Cloudflare, publish the tunnel hostname to: http://host.docker.internal:1234'
Write-Host 'In LM Studio 0.4+, enable API-token authentication and use that token as the API key in PsychDeep.'
Write-Host 'Never route PostgreSQL (5432/5433) through this tunnel.' -ForegroundColor Yellow
