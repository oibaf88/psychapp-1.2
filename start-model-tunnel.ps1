# Publishes the local OpenAI-compatible server (LM Studio :1234 by default)
# through a free Cloudflare Quick Tunnel so Render can reach it.
#
# Usage:
#   .\start-model-tunnel.ps1
#   .\start-model-tunnel.ps1 -Port 11434          # Ollama
#   .\start-model-tunnel.ps1 -Port 8080           # llama.cpp
#
# Leave this window open. Copy the https://….trycloudflare.com URL, add /v1,
# and paste it in PsychDeep → Ajustes → Modelo propio.

param(
    [int]$Port = 1234,
    [string]$BindHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$target = "http://${BindHost}:${Port}"

function Find-Cloudflared {
    $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $guesses = @(
        "$env:ProgramFiles\cloudflared\cloudflared.exe",
        "$env:LOCALAPPDATA\cloudflared\cloudflared.exe"
    )
    foreach ($path in $guesses) {
        if (Test-Path $path) { return $path }
    }
    return $null
}

$cloudflared = Find-Cloudflared
if (-not $cloudflared) {
    Write-Host "cloudflared no está instalado."
    Write-Host "Instálalo gratis con:  winget install Cloudflare.cloudflared"
    Write-Host "Docs: docs/LOCAL_MODEL_TUNNEL.md"
    exit 1
}

try {
    $probe = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "$target/models" -ErrorAction Stop
    Write-Host "Servidor local OK en $target (HTTP $($probe.StatusCode))"
} catch {
    Write-Host "No hay nadie en $target."
    Write-Host "Arranca LM Studio → Developer → Local Server (puerto $Port) y vuelve a lanzar este script."
    exit 1
}

Write-Host ""
Write-Host "Túnel gratuito hacia $target"
Write-Host "Cuando aparezca https://….trycloudflare.com, pégala en Ajustes con sufijo /v1"
Write-Host "Ejemplo: https://random-words.trycloudflare.com/v1"
Write-Host "Ctrl+C cierra el túnel y Render dejará de ver el modelo."
Write-Host ""

& $cloudflared @("tunnel", "--url", $target)
