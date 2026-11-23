"""Monitor de Salud Física de Discos (S.M.A.R.T., vida útil % y temperatura).

Consulta los contadores nativos de almacenamiento de Windows (`Get-PhysicalDisk`,
`Get-Disk`, `Get-Partition` y `Get-StorageReliabilityCounter`) sin abrir consola.
Si Windows restringe los sensores S.M.A.R.T. (`Wear`, `Temperature`, `PowerOnHours`)
en sesiones sin elevar, permite una lectura puntual elevada vía UAC que guarda los
sensores en `%LOCALAPPDATA%\\discoclaro\\smart_cache.json`.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import struct
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from discoclaro.platform import windows

HEALTH_CACHE_TTL_S = 60.0
_health_cache: tuple[float, tuple[PhysicalDiskHealth, ...]] | None = None
_IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400


def _read_nvme_smart_ioctl(device_id: str) -> dict[str, int | float] | None:
    """Lee la página NVMe SMART / Health Information Log (Log Page 0x02)
    directamente vía `IOCTL_STORAGE_QUERY_PROPERTY` con `dwDesiredAccess=0`
    (no requiere UAC ni permisos de administrador en Windows 10/11)."""
    if not device_id or not device_id.isdigit():
        return None
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = ctypes.c_void_p
        handle = k32.CreateFileW(
            rf"\\.\PhysicalDrive{device_id}",
            0,  # FILE_ANY_ACCESS: solo consulta de metadatos/SMART, sin UAC
            3,  # FILE_SHARE_READ | FILE_SHARE_WRITE
            None,
            3,  # OPEN_EXISTING
            0,
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if not handle or handle == invalid_handle:
            return None
        try:
            buf = bytearray(560)
            # StorageDeviceProtocolSpecificProperty (50), PropertyStandardQuery (0),
            # ProtocolTypeNvme (3), NVMeDataTypeLogPage (2), LogPageId=0x02 (SMART),
            # ProtocolDataOffset=40, ProtocolDataLength=512
            struct.pack_into("<IIIIIIIIII", buf, 0, 50, 0, 3, 2, 2, 0, 40, 512, 0, 0)
            raw_buf = (ctypes.c_char * 560).from_buffer(buf)
            returned = ctypes.c_ulong(0)
            ok = k32.DeviceIoControl(
                ctypes.c_void_p(handle),
                _IOCTL_STORAGE_QUERY_PROPERTY,
                raw_buf,
                560,
                raw_buf,
                560,
                ctypes.byref(returned),
                None,
            )
            if not ok or returned.value < 560:
                return None
            data = bytes(buf[48:560])
            temp_k = struct.unpack_from("<H", data, 1)[0]
            temp_c = temp_k - 273 if temp_k >= 273 else 0
            spare = int(data[3])
            wear = int(data[5])
            data_units_written = int.from_bytes(data[48:64], "little")
            power_on_hours = int.from_bytes(data[128:144], "little")
            media_errors = int.from_bytes(data[160:176], "little")
            written_tb = round((data_units_written * 512_000) / 1e12, 2)
            return {
                "Temperature": temp_c,
                "Wear": wear,
                "AvailableSpare": spare,
                "PowerOnHours": power_on_hours,
                "MediaErrors": media_errors,
                "WrittenTB": written_tb,
            }
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:
        return None


@dataclass(frozen=True, slots=True)
class PhysicalDiskHealth:
    """Estado físico S.M.A.R.T. y ficha técnica de un disco SSD / NVMe / HDD."""

    device_id: str
    friendly_name: str
    serial_number: str | None
    firmware_version: str | None
    media_type: str  # "SSD" | "HDD" | "Unspecified"
    bus_type: str  # "NVMe" | "SATA" | "USB" | ...
    partition_style: str | None  # "GPT" | "MBR"
    drive_letters: tuple[str, ...]
    health_status: str  # "Healthy" | "Warning" | "Unhealthy"
    operational_status: str
    size_bytes: int
    logical_sector_size: int | None
    physical_sector_size: int | None
    temperature_c: int | None
    temperature_max_c: int | None
    wear_percent: int | None
    life_remaining_percent: int | None
    power_on_hours: int | None
    read_errors: int | None
    write_errors: int | None
    available_spare_percent: int | None = None
    written_tb: float | None = None
    smart_available: bool = False


def _opt_int(val: object) -> int | None:
    if val is None or val == "":
        return None
    try:
        num = int(val)
        return num if num >= 0 else None
    except (TypeError, ValueError):
        return None


def _opt_str(val: object) -> str | None:
    if val is None:
        return None
    txt = str(val).strip()
    return txt or None


def _smart_cache_file() -> Path:
    return windows.default_data_dir() / "smart_cache.json"


def _load_elevated_smart_cache() -> dict[str, dict[str, object]]:
    """Lee sensores S.M.A.R.T. guardados previamente por una lectura UAC."""
    path = _smart_cache_file()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        rows = raw if isinstance(raw, list) else [raw]
        by_name: dict[str, dict[str, object]] = {}
        for r in rows:
            if isinstance(r, dict) and r.get("FriendlyName"):
                by_name[str(r["FriendlyName"]).strip().lower()] = r
        return by_name
    except Exception:
        return {}


def _parse_disks_json(
    raw_json: str,
    elevated_overlay: dict[str, dict[str, object]] | None = None,
) -> tuple[PhysicalDiskHealth, ...]:
    if not raw_json or not raw_json.strip():
        return ()
    try:
        parsed = json.loads(raw_json)
    except ValueError:
        return ()
    rows = parsed if isinstance(parsed, list) else [parsed]
    overlay = elevated_overlay or {}
    disks: list[PhysicalDiskHealth] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("FriendlyName") or "Disco Físico").strip()
        ov = overlay.get(name.lower(), {})
        dev_id = str(row.get("DeviceId") or "0").strip()
        nvme_smart = (
            _read_nvme_smart_ioctl(dev_id)
            if elevated_overlay is not None
            else None
        ) or {}
        serial = _opt_str(row.get("SerialNumber")) or _opt_str(ov.get("SerialNumber"))
        fw = _opt_str(row.get("FirmwareVersion")) or _opt_str(ov.get("FirmwareVersion"))
        media = str(row.get("MediaType") or "SSD").strip()
        bus = str(row.get("BusType") or "").strip()
        part_style = _opt_str(row.get("PartitionStyle"))
        raw_letters = row.get("DriveLetters") or []
        if isinstance(raw_letters, str):
            letters = tuple(
                f"{ch.upper()}:" for ch in raw_letters.split(",") if ch.strip()
            )
        elif isinstance(raw_letters, list):
            letters = tuple(
                f"{str(ch).strip().upper().rstrip(':')}:"
                for ch in raw_letters
                if str(ch).strip()
            )
        else:
            letters = ()

        health = str(row.get("HealthStatus") or "Healthy").strip()
        op_status = str(row.get("OperationalStatus") or "OK").strip()
        size_b = _opt_int(row.get("Size")) or 0
        l_sec = _opt_int(row.get("LogicalSectorSize"))
        p_sec = _opt_int(row.get("PhysicalSectorSize"))

        temp = _opt_int(row.get("Temperature"))
        if temp is None:
            temp = _opt_int(nvme_smart.get("Temperature"))
        if temp is None:
            temp = _opt_int(ov.get("Temperature"))
        temp_valid = temp if (temp is not None and 0 < temp < 120) else None

        temp_max = _opt_int(row.get("TemperatureMax"))
        if temp_max is None:
            temp_max = _opt_int(ov.get("TemperatureMax"))
        temp_max_valid = (
            temp_max if (temp_max is not None and 0 < temp_max < 130) else None
        )

        wear = _opt_int(row.get("Wear"))
        if wear is None:
            wear = _opt_int(nvme_smart.get("Wear"))
        if wear is None:
            wear = _opt_int(ov.get("Wear"))
        wear_valid = wear if (wear is not None and wear <= 100) else None
        life_remaining = (100 - wear_valid) if wear_valid is not None else None

        hours = _opt_int(row.get("PowerOnHours"))
        if hours is None:
            hours = _opt_int(nvme_smart.get("PowerOnHours"))
        if hours is None:
            hours = _opt_int(ov.get("PowerOnHours"))

        r_err = _opt_int(row.get("ReadErrorsTotal"))
        if r_err is None:
            r_err = _opt_int(nvme_smart.get("MediaErrors"))
        if r_err is None:
            r_err = _opt_int(ov.get("ReadErrorsTotal"))
        w_err = _opt_int(row.get("WriteErrorsTotal"))
        if w_err is None:
            w_err = _opt_int(ov.get("WriteErrorsTotal"))

        spare_pct = _opt_int(nvme_smart.get("AvailableSpare"))
        raw_tb = nvme_smart.get("WrittenTB")
        written_tb = float(raw_tb) if isinstance(raw_tb, (int, float)) else None

        smart_ok = any(
            v is not None for v in (temp_valid, wear_valid, hours, r_err, w_err)
        )
        disks.append(
            PhysicalDiskHealth(
                device_id=dev_id,
                friendly_name=name,
                serial_number=serial,
                firmware_version=fw,
                media_type=media,
                bus_type=bus,
                partition_style=part_style,
                drive_letters=letters,
                health_status=health,
                operational_status=op_status,
                size_bytes=size_b,
                logical_sector_size=l_sec,
                physical_sector_size=p_sec,
                temperature_c=temp_valid,
                temperature_max_c=temp_max_valid,
                wear_percent=wear_valid,
                life_remaining_percent=life_remaining,
                power_on_hours=hours,
                read_errors=r_err,
                write_errors=w_err,
                available_spare_percent=spare_pct,
                written_tb=written_tb,
                smart_available=smart_ok,
            )
        )
    return tuple(disks)


def _ps_query_script() -> str:
    return (
        "$disks = Get-PhysicalDisk -ErrorAction SilentlyContinue; "
        "if (-not $disks) { Write-Output '[]'; exit 0 }; "
        "$out = foreach ($d in $disks) { "
        "$rc = $null; $gd = $null; $letters = @(); "
        "try { $rc = $d | Get-StorageReliabilityCounter "
        "-ErrorAction SilentlyContinue } catch {}; "
        "try { $gd = Get-Disk -Number ([int]$d.DeviceId) "
        "-ErrorAction SilentlyContinue } catch {}; "
        "try { $letters = @(Get-Partition -DiskNumber ([int]$d.DeviceId) "
        "-ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter } | "
        "Select-Object -ExpandProperty DriveLetter) } catch {}; "
        "[PSCustomObject]@{ "
        "DeviceId = [string]$d.DeviceId; "
        "FriendlyName = $d.FriendlyName; "
        "SerialNumber = $d.SerialNumber; "
        "FirmwareVersion = $d.FirmwareVersion; "
        "MediaType = [string]$d.MediaType; "
        "BusType = [string]$d.BusType; "
        "PartitionStyle = if ($gd) { [string]$gd.PartitionStyle } else { $null }; "
        "DriveLetters = $letters; "
        "HealthStatus = [string]$d.HealthStatus; "
        "OperationalStatus = [string]$d.OperationalStatus; "
        "Size = [int64]$d.Size; "
        "LogicalSectorSize = [int64]$d.LogicalSectorSize; "
        "PhysicalSectorSize = [int64]$d.PhysicalSectorSize; "
        "Temperature = if ($rc) { $rc.Temperature } else { $null }; "
        "TemperatureMax = if ($rc) { $rc.TemperatureMax } else { $null }; "
        "Wear = if ($rc) { $rc.Wear } else { $null }; "
        "PowerOnHours = if ($rc) { $rc.PowerOnHours } else { $null }; "
        "ReadErrorsTotal = if ($rc) { $rc.ReadErrorsTotal } else { $null }; "
        "WriteErrorsTotal = if ($rc) { $rc.WriteErrorsTotal } else { $null } "
        "} }; "
        "$out | ConvertTo-Json -Compress"
    )


def read_physical_disks_health(
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    use_cache: bool = True,
) -> tuple[PhysicalDiskHealth, ...]:
    """Lee la salud física S.M.A.R.T. y ficha técnica de todos los discos."""
    global _health_cache
    now_mono = time.monotonic()
    if (
        use_cache
        and run is subprocess.run
        and _health_cache is not None
        and (now_mono - _health_cache[0]) < HEALTH_CACHE_TTL_S
    ):
        return _health_cache[1]

    windir = os.environ.get("WINDIR", r"C:\Windows")
    ps_exe = str(
        Path(windir)
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    kwargs: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "timeout": 8.0,
        "errors": "replace",
    }
    if run is subprocess.run:
        kwargs["stdin"] = subprocess.DEVNULL
        if windows.CREATE_NO_WINDOW:
            kwargs["creationflags"] = windows.CREATE_NO_WINDOW

    try:
        completed = run(
            [
                ps_exe,
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-Command",
                _ps_query_script(),
            ],
            **kwargs,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _health_cache[1] if _health_cache is not None else ()

    if completed.returncode != 0:
        return _health_cache[1] if _health_cache is not None else ()

    overlay = _load_elevated_smart_cache() if run is subprocess.run else {}
    parsed = _parse_disks_json(completed.stdout or "", elevated_overlay=overlay)
    if run is subprocess.run:
        _health_cache = (now_mono, parsed)
    return parsed


def read_smart_elevated() -> tuple[PhysicalDiskHealth, ...]:
    """Lanza una lectura puntual elevada (UAC) de `Get-StorageReliabilityCounter`
    para obtener `% de vida útil`, `Temperatura °C` y `Horas encendido` reales
    de los SSD NVMe, guarda el resultado en `smart_cache.json` y refresca."""
    global _health_cache
    out_file = _smart_cache_file()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_script = out_file.parent / "_read_smart.ps1"
    ps_body = (
        "$disks = Get-PhysicalDisk -ErrorAction SilentlyContinue\n"
        "$out = @()\n"
        "foreach ($d in $disks) {\n"
        "  $rc = $null; $gd = $null; $letters = @()\n"
        "  try { $rc = $d | Get-StorageReliabilityCounter "
        "-ErrorAction SilentlyContinue } catch {}\n"
        "  try { $gd = Get-Disk -Number ([int]$d.DeviceId) "
        "-ErrorAction SilentlyContinue } catch {}\n"
        "  try { $letters = @(Get-Partition -DiskNumber ([int]$d.DeviceId) "
        "-ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter } | "
        "Select-Object -ExpandProperty DriveLetter) } catch {}\n"
        "  $out += [PSCustomObject]@{\n"
        "    DeviceId = [string]$d.DeviceId\n"
        "    FriendlyName = $d.FriendlyName\n"
        "    SerialNumber = $d.SerialNumber\n"
        "    FirmwareVersion = $d.FirmwareVersion\n"
        "    MediaType = [string]$d.MediaType\n"
        "    BusType = [string]$d.BusType\n"
        "    PartitionStyle = if ($gd) { [string]$gd.PartitionStyle } else { $null }\n"
        "    DriveLetters = $letters\n"
        "    HealthStatus = [string]$d.HealthStatus\n"
        "    OperationalStatus = [string]$d.OperationalStatus\n"
        "    Size = [int64]$d.Size\n"
        "    LogicalSectorSize = [int64]$d.LogicalSectorSize\n"
        "    PhysicalSectorSize = [int64]$d.PhysicalSectorSize\n"
        "    Temperature = if ($rc) { $rc.Temperature } else { $null }\n"
        "    TemperatureMax = if ($rc) { $rc.TemperatureMax } else { $null }\n"
        "    Wear = if ($rc) { $rc.Wear } else { $null }\n"
        "    PowerOnHours = if ($rc) { $rc.PowerOnHours } else { $null }\n"
        "    ReadErrorsTotal = if ($rc) { $rc.ReadErrorsTotal } else { $null }\n"
        "    WriteErrorsTotal = if ($rc) { $rc.WriteErrorsTotal } else { $null }\n"
        "  }\n"
        "}\n"
        "$json = ConvertTo-Json -InputObject @($out) -Compress\n"
        "$utf8NoBom = New-Object System.Text.UTF8Encoding($false)\n"
        f"[System.IO.File]::WriteAllText('{str(out_file)}', $json, $utf8NoBom)\n"
    )
    tmp_script.write_text(ps_body, encoding="utf-8")

    windir = os.environ.get("WINDIR", r"C:\Windows")
    ps_exe = str(
        Path(windir)
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    try:
        windows.run_elevated(
            ps_exe,
            [
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(tmp_script),
            ],
            str(out_file.parent),
            45.0,
        )
    except (windows.ElevationDeclined, TimeoutError, OSError):
        pass
    finally:
        with contextlib.suppress(OSError):
            tmp_script.unlink()

    _health_cache = None
    return read_physical_disks_health(use_cache=False)
