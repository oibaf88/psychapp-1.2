$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..\..'))

$engineDir = 'ops/sync/engines'
New-Item -ItemType Directory -Force -Path $engineDir | Out-Null

if (-not (Test-Path '.env.local')) {
    throw 'Missing .env.local. Run ops/local/start-local.ps1 once and complete the generated file first.'
}

# Read LOCAL_DB_PASSWORD without importing arbitrary .env content into the shell.
$localPasswordLine = Get-Content '.env.local' | Where-Object { $_ -match '^LOCAL_DB_PASSWORD=' } | Select-Object -First 1
$localPassword = if ($localPasswordLine) { $localPasswordLine.Substring('LOCAL_DB_PASSWORD='.Length).Trim() } else { '' }
if ([string]::IsNullOrWhiteSpace($localPassword) -or $localPassword -like 'CHANGE_ME*') {
    throw 'Set a real LOCAL_DB_PASSWORD in .env.local before enabling synchronization.'
}

Write-Host 'PsychDeep offline synchronization setup' -ForegroundColor Cyan
Write-Host 'This creates LOCAL secret files only; it does not modify Supabase yet.'
Write-Host ''
$hostName = Read-Host 'Supabase PostgreSQL pooler host (without port)'
$dbUser = Read-Host 'Supabase database user'
$dbPasswordSecure = Read-Host 'Supabase database password' -AsSecureString
$dbPassword = [System.Net.NetworkCredential]::new('', $dbPasswordSecure).Password
$laptopId = Read-Host 'Laptop node ID [psychdeep-laptop]'
if ([string]::IsNullOrWhiteSpace($laptopId)) { $laptopId = 'psychdeep-laptop' }

if ([string]::IsNullOrWhiteSpace($hostName) -or [string]::IsNullOrWhiteSpace($dbUser) -or [string]::IsNullOrWhiteSpace($dbPassword)) {
    throw 'Host, database user and password are required.'
}

$cloud = @"
engine.name=cloud
group.id=cloud
external.id=psychdeep-cloud
sync.url=http://localhost:31415/sync/cloud
registration.url=

db.driver=org.postgresql.Driver
db.url=jdbc:postgresql://${hostName}:5432/postgres?sslmode=require&currentSchema=psychdeep_sync
db.user=${dbUser}
db.password=${dbPassword}

auto.registration=false
auto.reload=true
"@

$local = @"
engine.name=local
group.id=local
external.id=${laptopId}
sync.url=http://localhost:31415/sync/local
registration.url=http://localhost:31415/sync/cloud

db.driver=org.postgresql.Driver
db.url=jdbc:postgresql://db:5432/psychapp?currentSchema=psychdeep_sync
db.user=psychapp
db.password=${localPassword}

auto.registration=false
auto.reload=true
"@

Set-Content (Join-Path $engineDir 'cloud.properties') $cloud -NoNewline
Set-Content (Join-Path $engineDir 'local.properties') $local -NoNewline

Write-Host ''
Write-Host 'Engine files created. They are ignored by Git.' -ForegroundColor Green
Write-Host ''
Write-Host 'Next safe sequence:' -ForegroundColor Cyan
Write-Host '  1. Make a Supabase backup (and preferably test on staging first).'
Write-Host '  2. Run:  .\ops\sync\start-sync.ps1 -Initialize'
Write-Host '  3. The script will start SymmetricDS and show the two commands needed to'
Write-Host '     install the allowlist and open registration for this laptop.'
Write-Host ''
Write-Host 'Do not use the local and hosted PsychDeep UI concurrently for the same patient during the first rollout.' -ForegroundColor Yellow
