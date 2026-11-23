<#
.SYNOPSIS
    Empaqueta DiscoClaro como aplicación standalone de Windows (`dist\DiscoClaro.exe`)
    con icono oficial, interfaz web compilada y modo 100% sin ventana de consola.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "1/4 Compilando frontend React (dashboard/dist)..." -ForegroundColor Cyan
npm --prefix dashboard run build

Write-Host "2/4 Generando icono oficial discoclaro.ico..." -ForegroundColor Cyan
.\.venv\Scripts\python.exe -c "from pathlib import Path; from discoclaro.platform.icon import ensure_discoclaro_ico; ensure_discoclaro_ico(Path('src/discoclaro/assets/discoclaro.ico'))"

Write-Host "3/4 Verificando PyInstaller en .venv..." -ForegroundColor Cyan
$hasPyInstaller = .\.venv\Scripts\python.exe -c "import importlib.util; print('ok' if importlib.util.find_spec('PyInstaller') else 'missing')"
if ($hasPyInstaller -ne "ok") {
    Write-Host "Instalando PyInstaller en el entorno virtual..." -ForegroundColor Yellow
    .\.venv\Scripts\python.exe -m pip install pyinstaller
}

Write-Host "4/4 Construyendo ejecutable standalone dist\DiscoClaro.exe..." -ForegroundColor Cyan
.\.venv\Scripts\python.exe -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "DiscoClaro" `
    --icon "src/discoclaro/assets/discoclaro.ico" `
    --add-data "dashboard/dist;dashboard/dist" `
    --add-data "src/discoclaro/locales;discoclaro/locales" `
    --add-data "src/discoclaro/rules;discoclaro/rules" `
    --add-data "src/discoclaro/assets/discoclaro.ico;discoclaro/assets" `
    --hidden-import "uvicorn.logging" `
    --hidden-import "uvicorn.loops.auto" `
    --hidden-import "uvicorn.protocols.http.auto" `
    --hidden-import "uvicorn.lifespan.on" `
    scripts/launcher.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller falló con código de salida $LASTEXITCODE"
}

Write-Host "`nListo! Ejecutable generado en: $Root\dist\DiscoClaro.exe" -ForegroundColor Green
if (Get-Command iscc -ErrorAction SilentlyContinue) {
    Write-Host "Compilando instalador Inno Setup..." -ForegroundColor Cyan
    iscc scripts/installer.iss
} else {
    Write-Host "Tip: Instala Inno Setup (iscc) y ejecuta 'iscc scripts/installer.iss' si deseas generar Setup_DiscoClaro.exe." -ForegroundColor DarkGray
}
