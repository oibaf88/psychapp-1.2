param(
    [ValidateSet('auto','direct','session')]
    [string]$CloudMode = 'auto',
    [string]$SessionPoolerHost = 'aws-0-eu-north-1.pooler.supabase.com',
    [string]$LaptopId = 'psychdeep-laptop'
)

$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..\..'))

$projectRef = 'ifwexmoltnybvmrsuwtu'
$directHost = "db.$projectRef.supabase.co"
$syncRole = 'psychdeep_sync'
$secretDir = 'ops/local/secrets'
$syncPasswordFile = Join-Path $secretDir 'supabase-sync-password.txt'
$syncEnvFile = Join-Path $secretDir 'supabase-sync.env'
$engineDir = 'ops/sync/engines'

New-Item -ItemType Directory -Force -Path $secretDir, $engineDir | Out-Null

if (-not (Test-Path '.env.local')) {
    throw 'Missing .env.local. Run ops/local/start-local.ps1 once and complete the generated file first.'
}

$localPasswordLine = Get-Content '.env.local' | Where-Object { $_ -match '^LOCAL_DB_PASSWORD=' } | Select-Object -First 1
$localPassword = if ($localPasswordLine) { $localPasswordLine.Substring('LOCAL_DB_PASSWORD='.Length).Trim() } else { '' }
if ([string]::IsNullOrWhiteSpace($localPassword) -or $localPassword -like 'CHANGE_ME*') {
    throw 'Set a real LOCAL_DB_PASSWORD in .env.local before enabling synchronization.'
}

if (-not (Test-Path $syncPasswordFile)) {
    Write-Host 'Supabase sync password is not present on this PC.' -ForegroundColor Yellow
    $secure = Read-Host 'Paste the psychdeep_sync password from the secure operator handoff' -AsSecureString
    $plain = [System.Net.NetworkCredential]::new('', $secure).Password
    if ([string]::IsNullOrWhiteSpace($plain)) { throw 'No sync password supplied.' }
    Set-Content -Path $syncPasswordFile -Value $plain -NoNewline
}
$cloudPassword = (Get-Content $syncPasswordFile -Raw).Trim()
if ([string]::IsNullOrWhiteSpace($cloudPassword)) { throw 'Supabase sync password file is empty.' }

if ($CloudMode -eq 'auto') {
    Write-Host "Testing direct Supabase connection path $directHost`:5432..." -ForegroundColor Cyan
    $directReachable = Test-NetConnection -ComputerName $directHost -Port 5432 -InformationLevel Quiet -WarningAction SilentlyContinue
    $CloudMode = if ($directReachable) { 'direct' } else { 'session' }
}

if ($CloudMode -eq 'direct') {
    $cloudHost = $directHost
    $cloudUser = $syncRole
} else {
    $cloudHost = $SessionPoolerHost
    $cloudUser = "$syncRole.$projectRef"
}

if ([string]::IsNullOrWhiteSpace($cloudHost)) { throw 'Cloud database host is empty.' }
if ($cloudHost -match ':6543$') { throw 'Transaction pooler (6543) is not supported for SymmetricDS. Use direct or session mode on 5432.' }

$cloud = @"
engine.name=cloud
group.id=cloud
external.id=psychdeep-cloud
sync.url=http://localhost:31415/sync/cloud
registration.url=

db.driver=org.postgresql.Driver
db.url=jdbc:postgresql://${cloudHost}:5432/postgres?sslmode=require&currentSchema=psychdeep_sync
db.user=${cloudUser}
db.password=${cloudPassword}

auto.registration=false
auto.reload=true
"@

$local = @"
engine.name=local
group.id=local
external.id=${LaptopId}
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

# A transient postgres:17 container can consume this file to run preflight and
# install the SymmetricDS routing config without exposing the password on a
# command line. The whole secrets directory is git-ignored.
$pgEnv = @"
PGHOST=${cloudHost}
PGPORT=5432
PGDATABASE=postgres
PGUSER=${cloudUser}
PGPASSWORD=${cloudPassword}
PGSSLMODE=require
"@
Set-Content $syncEnvFile $pgEnv -NoNewline

Write-Host ''
Write-Host "SymmetricDS engine files created using Supabase $CloudMode mode." -ForegroundColor Green
Write-Host "Cloud role: $syncRole (no owner/admin credentials)."
Write-Host 'Next: .\ops\sync\start-sync.ps1 -Initialize'
