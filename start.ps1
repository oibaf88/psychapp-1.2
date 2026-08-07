# Convenience launcher for Windows PowerShell. See README.md for details.
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    Write-Host "No .env found. Copying .env.example -> .env"
    Write-Host "IMPORTANT: edit .env and set ANTHROPIC_API_KEY before using chat/diary features."
    Copy-Item ".env.example" ".env"
}

docker compose up --build
