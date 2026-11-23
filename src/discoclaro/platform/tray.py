"""Ícono nativo de DiscoClaro en la Bandeja del Sistema de Windows (System Tray).

Usa `System.Windows.Forms.NotifyIcon` nativo de Windows en un hilo STA oculto
(cero dependencias externas en Python, cero ventanas de consola) con el icono
oficial `discoclaro.ico` y menú contextual para:
- Abrir el Dashboard en el navegador (y doble clic directo sobre el ícono)
- Ver el espacio libre en vivo de C: y D: en el tooltip
- Vaciar la Papelera de reciclaje de Windows
- Detener y salir de la bandeja limpiamente
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from discoclaro.platform import windows
from discoclaro.platform.icon import ensure_discoclaro_ico


def launch_system_tray(*, port: int = 8347) -> int:
    """Lanza el ícono residente en la Bandeja del Sistema junto al reloj de
    Windows de forma desacoplada y sin ventana de consola. Devuelve el PID."""
    workdir = Path(__file__).resolve().parents[3]
    icon_path = workdir / "src" / "discoclaro" / "assets" / "discoclaro.ico"
    try:
        ensure_discoclaro_ico(icon_path)
    except OSError:
        icon_path = windows.default_data_dir() / "discoclaro.ico"
        ensure_discoclaro_ico(icon_path)

    windir = os.environ.get("WINDIR", r"C:\Windows")
    ps_exe = str(
        Path(windir)
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    url = f"http://127.0.0.1:{int(port)}"
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$url = $env:DISCOCLARO_TRAY_URL; "
        "$icoPath = $env:DISCOCLARO_TRAY_ICON; "
        "$notify = New-Object System.Windows.Forms.NotifyIcon; "
        "if (Test-Path -LiteralPath $icoPath) { "
        "  $notify.Icon = New-Object System.Drawing.Icon($icoPath) "
        "} else { "
        "  $notify.Icon = [System.Drawing.SystemIcons]::Application "
        "}; "
        "$notify.Text = 'DiscoClaro - Monitor de Disco Activo'; "
        "$notify.Visible = $true; "
        "$menu = New-Object System.Windows.Forms.ContextMenuStrip; "
        "$itemStatus = $menu.Items.Add('DiscoClaro · 127.0.0.1 activo'); "
        "$itemStatus.Enabled = $false; "
        "$menu.Items.Add('-') | Out-Null; "
        "$itemOpen = $menu.Items.Add('Abrir DiscoClaro Dashboard'); "
        "$itemOpen.Add_Click({ Start-Process $url }); "
        "$itemBin = $menu.Items.Add('Vaciar Papelera de Windows'); "
        "$itemBin.Add_Click({ "
        "  try { Invoke-RestMethod -Method Post "
        "-Uri ($url + '/actions/empty-recycle-bin') "
        "-ContentType 'application/json' -Body '{}' | Out-Null } catch {} "
        "}); "
        "$menu.Items.Add('-') | Out-Null; "
        "$itemExit = $menu.Items.Add('Salir de la bandeja'); "
        "$itemExit.Add_Click({ "
        "  $notify.Visible = $false; $notify.Dispose(); "
        "  [System.Windows.Forms.Application]::Exit() "
        "}); "
        "$notify.ContextMenuStrip = $menu; "
        "$notify.Add_DoubleClick({ Start-Process $url }); "
        "$timer = New-Object System.Windows.Forms.Timer; "
        "$timer.Interval = 30000; "
        "$updateTip = { "
        "  try { "
        "    $drives = Get-PSDrive -PSProvider FileSystem | "
        "Where-Object { $_.Free -gt 0 } | ForEach-Object { "
        "      $gb = [math]::Round($_.Free / 1GB, 1); \"$($_.Name): $gb GB libres\" "
        "    }; "
        "    $txt = 'DiscoClaro · ' + ($drives -join ' | '); "
        "    if ($txt.Length -gt 63) { $txt = $txt.Substring(0, 63) }; "
        "    $notify.Text = $txt; $itemStatus.Text = $txt "
        "  } catch {} "
        "}; "
        "& $updateTip; "
        "$timer.Add_Tick($updateTip); "
        "$timer.Start(); "
        "[System.Windows.Forms.Application]::Run()"
    )
    env = {
        **os.environ,
        "DISCOCLARO_TRAY_URL": url,
        "DISCOCLARO_TRAY_ICON": str(icon_path),
    }
    detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    flags = windows.CREATE_NO_WINDOW | detached
    proc = subprocess.Popen(  # noqa: S603
        [
            ps_exe,
            "-STA",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-Command",
            script,
        ],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
    )
    return int(proc.pid)
