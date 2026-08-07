# Start PsychApp for phone use on the same Wi‑Fi (no tunnel, no extra password).
# Right-click → Run with PowerShell. If prompted by UAC, Accept to open firewall.

Set-Location $PSScriptRoot
$ErrorActionPreference = "Continue"

Write-Host "=== 1) Docker ==="
$v = docker version --format '{{.Server.Version}}' 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Starting Docker Desktop..."
  Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
  $deadline = (Get-Date).AddMinutes(4)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep 5
    $v = docker version --format '{{.Server.Version}}' 2>$null
    if ($LASTEXITCODE -eq 0 -and $v) { break }
  }
}
if ($LASTEXITCODE -ne 0) {
  Write-Host "ERROR: Docker no esta listo. Abre Docker Desktop y vuelve a ejecutar este script."
  pause
  exit 1
}
Write-Host "Docker OK: $v"

Write-Host "=== 2) Red Wi-Fi a Privada + Firewall (UAC) ==="
# Public Wi-Fi profile blocks phone access by default on Windows.
$fw = @'
$ErrorActionPreference = "Continue"
Get-NetConnectionProfile | Where-Object { $_.InterfaceAlias -match "Wi-?Fi|WLAN" } | ForEach-Object {
  try {
    Set-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -NetworkCategory Private
    Write-Host "Wi-Fi profile set to Private: $($_.Name)"
  } catch { Write-Host "Could not set Private: $_" }
}
foreach ($port in 5173,8001) {
  $n = "PsychApp phone $port"
  Get-NetFirewallRule -DisplayName $n -EA SilentlyContinue | Remove-NetFirewallRule -EA SilentlyContinue
  New-NetFirewallRule -DisplayName $n -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -Profile Any | Out-Null
  Write-Host "Firewall allow TCP $port (all profiles)"
}
'@
$tmp = Join-Path $env:TEMP "psychapp-fw-open.ps1"
Set-Content $tmp $fw -Encoding UTF8
try {
  Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$tmp`"" -Wait
} catch {
  Write-Host "AVISO: acepta el UAC o ejecuta como admin:"
  Write-Host "  Set-NetConnectionProfile -Name (Get-NetConnectionProfile).Name -NetworkCategory Private"
  Write-Host "  New-NetFirewallRule -DisplayName 'PsychApp 5173' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5173 -Profile Any"
}

Write-Host "=== 3) Start stack ==="
docker compose up -d --remove-orphans
Start-Sleep 4

$ip = (
  Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object { $_.IPAddress -like '192.168.*' } |
  Select-Object -First 1 -ExpandProperty IPAddress
)
if (-not $ip) { $ip = "192.168.1.213" }

Write-Host ""
Write-Host "========================================"
Write-Host "  EN EL MOVIL (misma Wi-Fi que el PC):"
Write-Host "  http://${ip}:5173"
Write-Host "========================================"
Write-Host "  Usuario demo: patient@demo.psychapp.example.com"
Write-Host "  Password:     DemoPass123!"
Write-Host ""
Write-Host "  Instalar app: Chrome → menu → Instalar aplicacion"
Write-Host "  iPhone: Safari → Compartir → Anadir a pantalla de inicio"
Write-Host "========================================"

try {
  $h = Invoke-WebRequest "http://${ip}:5173/api/v1/health" -UseBasicParsing -TimeoutSec 10
  Write-Host "Comprobacion LAN: OK $($h.Content)"
} catch {
  Write-Host "Comprobacion LAN: FALLO — revisa firewall / Wi-Fi"
}

# Write URL for user
New-Item -ItemType Directory -Force -Path mobile-dist | Out-Null
Set-Content "mobile-dist\PC-URL.txt" "http://${ip}:5173" -Encoding utf8

Start-Process "http://${ip}:5173"
pause
