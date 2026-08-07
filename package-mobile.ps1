# Build installable phone package into .\mobile-dist\
# Recommended install path: PWA "Add to Home Screen" / Install app (no store, no tunnel).

Set-Location $PSScriptRoot
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path mobile-dist | Out-Null

Write-Host "==> Building frontend..."
Push-Location frontend
if (-not (Test-Path node_modules)) { npm install }
# Ensure public icons/manifest/sw are present for PWA
npm run build
Pop-Location

Write-Host "==> Packaging PsychApp-web.zip..."
$zip = Join-Path $PWD "mobile-dist\PsychApp-web.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $PWD "frontend\dist\*") -DestinationPath $zip -Force

# Prefer real home LAN (192.168.x), not WSL/Hyper-V (172.x)
$ip = (
  Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object { $_.IPAddress -like '192.168.*' } |
  Select-Object -First 1 -ExpandProperty IPAddress
)
if (-not $ip) {
  $ip = (
    Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object {
      $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and
      $_.InterfaceAlias -match 'Wi-?Fi|WLAN|Ethernet' -and $_.InterfaceAlias -notmatch 'WSL|vEthernet|Hyper-V|Docker'
    } |
    Select-Object -First 1 -ExpandProperty IPAddress
  )
}
if (-not $ip) { $ip = "192.168.1.213" }

$index = @"
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Instalar PsychApp</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 36rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; color: #1e2430; }
    a.btn { display: inline-block; background: #3a6ea5; color: #fff; padding: .85rem 1.1rem; border-radius: 10px; text-decoration: none; font-weight: 600; }
    code { background: #eef1f5; padding: .1rem .35rem; border-radius: 4px; word-break: break-all; }
    .card { border: 1px solid #e0e4ea; border-radius: 12px; padding: 1rem 1.1rem; margin: 1rem 0; background: #fff; }
    h1 { margin-bottom: .25rem; }
    .muted { color: #5a6472; }
  </style>
</head>
<body>
  <h1>PsychApp en el móvil</h1>
  <p class="muted">Sin Cloudflare, sin contraseña extra. Instalas la app en el teléfono; el servidor sigue en tu PC (Docker) en la misma Wi‑Fi.</p>

  <div class="card">
    <h2>1) Abrir e instalar (recomendado)</h2>
    <p>URL de tu PC ahora:</p>
    <p><code>http://$ip:5173</code></p>
    <p><a class="btn" href="http://$ip:5173/">Abrir PsychApp</a></p>
    <ol>
      <li>Móvil y PC en la <strong>misma Wi‑Fi</strong>.</li>
      <li>En el PC: Docker Desktop + <code>docker compose up -d</code>.</li>
      <li>Abre la URL de arriba en Chrome (Android) o Safari (iPhone).</li>
      <li><strong>Android:</strong> menú ⋮ → <em>Instalar aplicación</em> / Añadir a pantalla de inicio.</li>
      <li><strong>iPhone:</strong> Compartir → <em>Añadir a pantalla de inicio</em>.</li>
      <li>En la app: <strong>Ajustes</strong> → URL del servidor = <code>http://$ip:5173</code> → Guardar → Probar conexión.</li>
    </ol>
  </div>

  <div class="card">
    <h2>2) Paquete web (opcional)</h2>
    <p><a class="btn" href="./PsychApp-web.zip">Descargar PsychApp-web.zip</a></p>
    <p class="muted">Copia de la interfaz. La forma normal de usar el móvil es la instalación PWA del paso 1.</p>
  </div>
</body>
</html>
"@
Set-Content -Path (Join-Path $PWD "mobile-dist\index.html") -Value $index -Encoding utf8
Set-Content -Path (Join-Path $PWD "mobile-dist\PC-URL.txt") -Value "http://${ip}:5173" -Encoding utf8

Write-Host ""
Write-Host "OK mobile-dist ready"
Write-Host "  Install from phone: http://${ip}:5173  (or /download/ for this page)"
Write-Host "  Zip: $zip"
