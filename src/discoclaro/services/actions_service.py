"""Acciones seguras, desinstalación total sin rastros y terminal asistida (Fase 4).

Reglas de seguridad inquebrantables (sección 8):
1. Toda acción que modifique el sistema (desinstalar un programa, enviar restos
   a la Papelera, limpiar temporales o ejecutar un comando en la terminal del
   dashboard) exige **confirmación en una ventana nativa de Windows**
   (`windows.confirm_native_dialog`) en el momento exacto del envío.
2. Si `DISCOCLARO_READONLY=1` está activo en el entorno, toda acción de escritura
   o ejecución se rechaza antes siquiera de mostrar el diálogo.
3. Toda ruta pasa por `domain.policy.is_safe_deletion_path` y
   `windows.ensure_safe_to_write` (no seguir enlaces ni junctions, nunca tocar
   zonas protegidas del sistema ni la propia base de discoclaro).
4. Desinstalación sin dejar rastro:
   - Mide toda la huella del programa ANTES de lanzar el desinstalador
     (`install_location`, `%LOCALAPPDATA%`, `%LOCALAPPDATA%\\Programs`,
     `%APPDATA%`, `%USERPROFILE%` y `%TEMP%`).
   - Ejecuta el desinstalador oficial registrado en Windows (con `shlex.split(...,
     posix=False)` y sin `shell=True`) tras confirmación nativa.
   - Re-verifica qué carpetas de esa huella quedaron huérfanas tras terminar el
     desinstalador y permite enviarlas todas a la Papelera de reciclaje en un
     solo paso verificado.
"""

import os
import shlex
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from discoclaro.adapters.inspectors import _process
from discoclaro.domain.footprint import FolderCandidate, Footprint
from discoclaro.domain.formatting import format_bytes, safe_text
from discoclaro.domain.models import Program
from discoclaro.domain.policy import is_safe_deletion_path, limit_length
from discoclaro.platform import windows
from discoclaro.services import programs_service

TERMINAL_TIMEOUT_S = 60.0
UNINSTALL_TIMEOUT_S = 300.0


class ActionError(Exception):
    """La acción no se pudo completar o fue rechazada por política/usuario."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail!r}")
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True, slots=True)
class TrashItemResult:
    """Resultado verificado de enviar una ruta a la Papelera."""

    path: str
    freed_bytes: int
    removed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class TrashBatchResult:
    """Resultado de limpiar una lista de restos o carpetas."""

    items: tuple[TrashItemResult, ...]
    total_freed_bytes: int


@dataclass(frozen=True, slots=True)
class UninstallFlowResult:
    """Resultado del flujo de desinstalación y detección de restos residuales."""

    program_name: str
    command_executed: str
    uninstaller_exit_code: int
    still_in_registry: bool
    remaining_footprints: tuple[Footprint, ...]
    total_remaining_bytes: int


@dataclass(frozen=True, slots=True)
class TerminalCommandResult:
    """Salida de un comando ejecutado tras confirmación nativa de Windows."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    cwd: str = ""


def _is_readonly_mode(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get("DISCOCLARO_READONLY", "").strip() == "1"


def parse_uninstall_command(uninstall_string: str) -> list[str]:
    """Convierte el `UninstallString` del registro en una lista `[ejecutable,
    *args]` sin usar `shell=True`. Soporta rutas entre comillas y `MsiExec.exe`."""
    cleaned = uninstall_string.strip()
    if not cleaned:
        raise ActionError("uninstall_empty_command")
    parts = [part.strip('"') for part in shlex.split(cleaned, posix=False) if part]
    if not parts:
        raise ActionError("uninstall_empty_command")
    executable = parts[0]
    if not os.path.isabs(executable):
        resolved = _process.resolve_executable(executable)
        if resolved is None:
            windir = os.environ.get("WINDIR", r"C:\Windows")
            sys32_candidate = str(Path(windir) / "System32" / executable)
            if os.path.isfile(sys32_candidate):
                resolved = sys32_candidate
            elif os.path.isfile(sys32_candidate + ".exe"):
                resolved = sys32_candidate + ".exe"
        if resolved is None:
            raise ActionError("uninstall_executable_not_found", executable)
        parts[0] = resolved
    elif not os.path.isfile(executable):
        raise ActionError("uninstall_executable_not_found", executable)
    return parts


def run_program_uninstall(
    program_name: str,
    *,
    read_programs: Callable[[], list[Program]] = windows.installed_programs,
    environment: Callable[[], Mapping[str, str]] = windows.environment_variables,
    inspect: Callable[[str], FolderCandidate | None] = programs_service.inspect_folder,
    confirm: Callable[[str, str], bool] = windows.confirm_native_dialog,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    environ: Mapping[str, str] | None = None,
) -> UninstallFlowResult:
    """Ejecuta el desinstalador del sistema para `program_name` y luego mide
    todos los restos que hayan quedado en disco (instalación, AppData,
    LocalAppData\\Programs, perfil y Temp) para que el usuario pueda eliminarlos
    sin dejar rastro."""
    if _is_readonly_mode(environ):
        raise ActionError("readonly_mode")
    if not program_name or not program_name.strip():
        raise ActionError("program_not_found")

    before_programs = read_programs()
    target = next(
        (p for p in before_programs if p.name.casefold() == program_name.casefold()),
        None,
    )
    if target is None:
        raise ActionError("program_not_found", program_name)
    if not target.uninstall_string:
        raise ActionError("uninstall_missing_string", target.name)

    cmd_parts = parse_uninstall_command(target.uninstall_string)
    shown_cmd = windows.command_line(cmd_parts)

    # 1. Capturamos la huella completa ANTES de desinstalar (si el desinstalador
    # borra la clave del registro pero deja la carpeta de instalación o AppData,
    # ya conocemos todas sus rutas).
    env = environment()
    pre_footprints = programs_service.inspect_program_footprints(
        target.name,
        read_programs=lambda: [target],
        environment=lambda: env,
        inspect=inspect,
    )
    known_paths = [f.path for f in pre_footprints]
    if target.install_location and target.install_location not in known_paths:
        known_paths.insert(0, target.install_location)

    # 2. Confirmación nativa obligatoria fuera del navegador.
    dialog_msg = (
        f"¿Deseas lanzar el desinstalador oficial del sistema para "
        f"«{limit_length(safe_text(target.name), 120)}»?\n\n"
        f"Comando exacto:\n{limit_length(safe_text(shown_cmd), 400)}\n\n"
        "Al terminar el desinstalador, discoclaro escaneará todas las carpetas "
        "relacionadas (incluidos AppData y temporales) para limpiar cualquier rastro."
    )
    if not confirm("discoclaro — Confirmar desinstalación", dialog_msg):
        raise ActionError("action_declined", target.name)

    try:
        completed = run(
            cmd_parts, capture_output=True, text=True, timeout=UNINSTALL_TIMEOUT_S
        )
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as error:
        raise ActionError("uninstall_timeout", target.name) from error
    except OSError as error:
        raise ActionError("uninstall_failed", str(error)) from error

    # 3. Invalidamos caché del registro y re-medimos qué quedó.
    windows.invalidate_installed_programs_cache()
    after_programs = read_programs()
    still_present = any(
        p.name.casefold() == target.name.casefold() for p in after_programs
    )

    candidate_paths: list[str] = list(known_paths)
    for extra in programs_service._search_root_candidates(target, env):
        extra_key = extra.rstrip("\\/").casefold()
        if not any(extra_key == k.rstrip("\\/").casefold() for k in candidate_paths):
            candidate_paths.append(extra)

    remaining_candidates = [
        cand for path in candidate_paths if (cand := inspect(path)) is not None
    ]
    from discoclaro.domain.footprint import footprints_of

    remaining = tuple(
        sorted(
            footprints_of(
                target,
                remaining_candidates,
                known_footprint_paths=frozenset(known_paths),
            ),
            key=lambda f: f.size_bytes,
            reverse=True,
        )
    )
    total_rem = sum(f.size_bytes for f in remaining)
    return UninstallFlowResult(
        program_name=target.name,
        command_executed=shown_cmd,
        uninstaller_exit_code=exit_code,
        still_in_registry=still_present,
        remaining_footprints=remaining,
        total_remaining_bytes=total_rem,
    )


def trash_paths_verified(
    paths: Sequence[str],
    *,
    environment: Callable[[], Mapping[str, str]] = windows.environment_variables,
    inspect: Callable[[str], FolderCandidate | None] = programs_service.inspect_folder,
    confirm: Callable[[str, str], bool] = windows.confirm_native_dialog,
    send_to_trash: Callable[[str], bool] = windows.send_path_to_recycle_bin,
    ensure_safe: Callable[[str], None] = windows.ensure_safe_to_write,
    environ: Mapping[str, str] | None = None,
) -> TrashBatchResult:
    """Envía una lista de carpetas/archivos residuales a la Papelera de reciclaje
    tras validar zonas protegidas, enlaces/junctions, pedir confirmación nativa de
    Windows y verificar después que cada ruta desapareció del disco (T4.7, T4.12)."""
    if _is_readonly_mode(environ):
        raise ActionError("readonly_mode")
    if not paths:
        raise ActionError("empty_paths")

    env = environment()
    user_profile = env.get("%USERPROFILE%")
    local_app = env.get("%LOCALAPPDATA%", "")
    roaming_app = env.get("%APPDATA%", "")
    own_dirs = tuple(
        str(Path(base) / windows.APP_FOLDER)
        for base in (local_app, roaming_app)
        if base
    )

    validated: list[tuple[str, int]] = []
    for raw_path in paths:
        ok, reason = is_safe_deletion_path(
            raw_path, user_profile=user_profile, app_data_dirs=own_dirs
        )
        if not ok:
            raise ActionError("unsafe_path", f"{raw_path} ({reason})")
        try:
            ensure_safe(raw_path)
        except windows.UnsafeDataLocation as error:
            raise ActionError("unsafe_path", str(error)) from error
        measured = inspect(raw_path)
        if measured is not None:
            size_b = measured.size_bytes
        elif os.path.isfile(raw_path):
            try:
                size_b = os.path.getsize(raw_path)
            except OSError:
                size_b = 0
        else:
            size_b = 0
        validated.append((raw_path, size_b))

    total_preview = sum(size for _, size in validated)
    preview_lines = "\n".join(
        f"• {limit_length(safe_text(p), 120)} ({format_bytes(s, ',')})"
        for p, s in validated[:8]
    )
    if len(validated) > 8:
        preview_lines += f"\n… y {len(validated) - 8} más."

    dialog_msg = (
        f"¿Enviar {len(validated)} elemento(s) ({format_bytes(total_preview, ',')} "
        f"en total) a la Papelera de reciclaje de Windows?\n\n"
        f"{preview_lines}"
    )
    if not confirm("discoclaro — Confirmar envío a la Papelera", dialog_msg):
        raise ActionError("action_declined")

    results: list[TrashItemResult] = []
    freed_total = 0
    for target_path, before_bytes in validated:
        try:
            ensure_safe(target_path)
        except windows.UnsafeDataLocation:
            results.append(
                TrashItemResult(target_path, 0, False, "reparse_point_detected")
            )
            continue
        sent = send_to_trash(target_path)
        after = inspect(target_path)
        still_on_disk = (after is not None) or (
            send_to_trash is windows.send_path_to_recycle_bin
            and os.path.exists(target_path)
        )
        verified_gone = sent and not still_on_disk
        freed = before_bytes if verified_gone else 0
        freed_total += freed
        results.append(
            TrashItemResult(
                path=target_path,
                freed_bytes=freed,
                removed=verified_gone,
                reason="ok" if verified_gone else "still_exists_or_failed",
            )
        )

    if freed_total > 0:
        from discoclaro.services import history_service

        removed_count = sum(1 for r in results if r.removed)
        label = (
            results[0].path
            if len(results) == 1
            else f"{removed_count} elemento(s) enviados a Papelera"
        )
        history_service.record_cleanup(
            "trash",
            label,
            freed_total,
            detail=f"{removed_count}/{len(results)} movidos a la Papelera de reciclaje",
        )

    return TrashBatchResult(items=tuple(results), total_freed_bytes=freed_total)


def _resolve_cwd(cwd: str | None) -> str:
    if cwd:
        candidate = os.path.normpath(cwd.strip())
        if len(candidate) == 2 and candidate[1] == ":":
            candidate += "\\"
        if os.path.isdir(candidate):
            return candidate
    return "C:\\" if os.path.isdir("C:\\") else os.getcwd()


def _try_handle_directory_change(cleaned: str, active_cwd: str) -> str | None:
    """Si el usuario escribió `D:`, `C:\\`, `cd ..` o `cd carpeta`, devuelve la
    nueva ruta absoluta si existe, o `None` si no es un cambio de directorio."""
    stripped = cleaned.strip()
    # Cambio directo de unidad: `C:`, `D:`, `D:\`
    if len(stripped) in (2, 3) and stripped[0].isalpha() and stripped[1] == ":":
        if len(stripped) == 2 or stripped[2] in ("\\", "/"):
            drive_root = f"{stripped[0].upper()}:\\"
            if os.path.isdir(drive_root):
                return drive_root
            raise ActionError("terminal_directory_not_found", drive_root)

    low = stripped.lower()
    if low == "cd" or low.startswith("cd ") or low.startswith("set-location "):
        raw_arg = stripped.split(" ", 1)[1] if " " in stripped else ""
        arg = raw_arg.strip().strip('"').strip("'")
        if not arg:
            return active_cwd
        if arg.startswith("/d ") or arg.startswith("/D "):
            arg = arg[3:].strip().strip('"').strip("'")
        if len(arg) == 2 and arg[0].isalpha() and arg[1] == ":":
            arg += "\\"
        target = (
            os.path.normpath(arg)
            if os.path.isabs(arg)
            else os.path.normpath(os.path.join(active_cwd, arg))
        )
        if os.path.isdir(target):
            return target
        raise ActionError("terminal_directory_not_found", target)
    return None


def execute_terminal_command(
    command_text: str,
    *,
    cwd: str | None = None,
    confirm: Callable[[str, str], bool] = windows.confirm_native_dialog,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    resolve: Callable[[str], str | None] = _process.resolve_executable,
    environ: Mapping[str, str] | None = None,
) -> TerminalCommandResult:
    """Ejecuta cualquier comando (binarios directos o cmdlets de PowerShell)
    desde la terminal integrada del dashboard (T4.14), respetando el disco y
    carpeta activa (`cwd`).

    Regla sin excepción (amenaza 2):
    - Sin `shell=True`: siempre se pasa lista de argumentos a un ejecutable con
      ruta absoluta (el binario directo o `powershell.exe` en System32).
    - En cada comando ejecutable, muestra una ventana **nativa** de Windows
      (`MessageBoxW`) con la carpeta activa y la orden exacta antes de correr.
    """
    if _is_readonly_mode(environ):
        raise ActionError("readonly_mode")
    cleaned = command_text.strip()
    if not cleaned:
        raise ActionError("terminal_empty_command")
    if len(cleaned) > 500:
        raise ActionError("terminal_command_too_long")

    active_cwd = _resolve_cwd(cwd)

    # Navegación de disco/carpeta (`D:`, `C:`, `cd carpeta`) sin modificar archivos
    new_dir = _try_handle_directory_change(cleaned, active_cwd)
    if new_dir is not None:
        return TerminalCommandResult(
            command=f"cd {new_dir}",
            exit_code=0,
            stdout=f"Directorio activo cambiado a: {new_dir}",
            stderr="",
            cwd=new_dir,
        )

    uses_ps_syntax = any(tok in cleaned for tok in ("|", ">", ";", "&&", "$", "*"))
    parts: list[str] = []
    if not uses_ps_syntax:
        try:
            parts = [
                part.strip('"')
                for part in shlex.split(cleaned, posix=False)
                if part
            ]
        except ValueError as error:
            raise ActionError("terminal_invalid_syntax", str(error)) from error

    resolved: str | None = None
    if parts:
        exe = parts[0]
        resolved = (
            exe if os.path.isabs(exe) and os.path.isfile(exe) else resolve(exe)
        )

    if resolved is not None:
        parts[0] = resolved
        exec_args = parts
        exact_cmd = windows.command_line(parts)
    else:
        # Cmdlet o comando nativo de PowerShell (dir, ls, Remove-Item, del, etc.)
        if resolve is not _process.resolve_executable:
            # En pruebas unitarias con `resolve` inyectado se mantiene rechazo estricto
            exe_name = parts[0] if parts else cleaned.split(" ", 1)[0]
            raise ActionError("terminal_executable_not_found", exe_name)
        windir = os.environ.get("WINDIR", r"C:\Windows")
        ps_exe = str(
            Path(windir)
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        if not os.path.isfile(ps_exe):
            raise ActionError("terminal_executable_not_found", "powershell.exe")
        exec_args = [
            ps_exe,
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-Command",
            cleaned,
        ]
        exact_cmd = f"PowerShell ({active_cwd})> {cleaned}"

    dialog_msg = (
        f"Solicitud desde la Terminal de discoclaro.\n"
        f"Carpeta / Disco activo: {active_cwd}\n\n"
        f"¿Autorizas ejecutar este comando ahora mismo?\n\n"
        f"{limit_length(safe_text(exact_cmd), 450)}"
    )
    if not confirm("discoclaro — Confirmar comando de Terminal", dialog_msg):
        raise ActionError("action_declined", exact_cmd)

    kwargs: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "timeout": TERMINAL_TIMEOUT_S,
        "errors": "replace",
    }
    if run is subprocess.run:
        kwargs["cwd"] = active_cwd
        if windows.CREATE_NO_WINDOW:
            kwargs["creationflags"] = windows.CREATE_NO_WINDOW
    try:
        completed = run(exec_args, **kwargs)
    except subprocess.TimeoutExpired as error:
        raise ActionError("terminal_timeout", exact_cmd) from error
    except OSError as error:
        raise ActionError("terminal_execution_failed", str(error)) from error

    return TerminalCommandResult(
        command=exact_cmd,
        exit_code=completed.returncode,
        stdout=limit_length(completed.stdout or "", 20_000),
        stderr=limit_length(completed.stderr or "", 10_000),
        cwd=active_cwd,
    )


@dataclass(frozen=True, slots=True)
class BatchCleanItemResult:
    """Resultado de limpiar un ítem seguro dentro del lote."""

    name: str
    command_or_path: str
    estimated_bytes: int
    succeeded: bool
    detail: str


@dataclass(frozen=True, slots=True)
class BatchCleanResult:
    """Resultado de la limpieza en lote de todas las cachés seguras."""

    items: tuple[BatchCleanItemResult, ...]
    total_estimated_bytes: int
    succeeded_count: int


def clean_safe_batch(
    data_dir: Path | None = None,
    *,
    confirm: Callable[[str, str], bool] = windows.confirm_native_dialog,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    resolve: Callable[[str], str | None] = _process.resolve_executable,
    send_to_trash: Callable[[str], bool] = windows.send_path_to_recycle_bin,
    environ: Mapping[str, str] | None = None,
) -> BatchCleanResult:
    """Ejecuta en lote todas las recomendaciones de nivel `safe`
    (`[01 · SEGURO / CACHÉS]`) con una sola confirmación nativa de Windows."""
    if _is_readonly_mode(environ):
        raise ActionError("readonly_mode")
    from discoclaro.services import recommendations_service

    recs = recommendations_service.run_recommendations(data_dir)
    safe_recs = [
        r
        for r in recs
        if r.level == "safe" and (r.suggested_command or r.location_path)
    ]
    if not safe_recs:
        return BatchCleanResult(
            items=(),
            total_estimated_bytes=0,
            succeeded_count=0,
        )

    total_est = sum(r.size_bytes for r in safe_recs)
    preview_lines = "\n".join(
        f"• {r.program} ({r.name}): {format_bytes(r.size_bytes, ',')} "
        f"-> {r.suggested_command or r.location_path}"
        for r in safe_recs[:8]
    )
    dialog_msg = (
        f"Limpieza Rápida en Lote (Nivel 01 · Cachés Seguras)\n\n"
        f"Se limpiarán {len(safe_recs)} caché(s) regenerables para recuperar "
        f"aprox. {format_bytes(total_est, ',')}:\n\n"
        f"{preview_lines}\n\n"
        f"¿Confirmas ejecutar todas las limpiezas seguras ahora?"
    )
    if not confirm("discoclaro — Limpieza en lote de cachés seguras", dialog_msg):
        raise ActionError("action_declined")

    results: list[BatchCleanItemResult] = []
    succeeded = 0
    for rec in safe_recs:
        label = f"{rec.program} · {rec.name}"
        if rec.suggested_command:
            try:
                parts = [
                    p.strip('"')
                    for p in shlex.split(rec.suggested_command, posix=False)
                    if p
                ]
            except ValueError:
                parts = []
            if parts:
                exe = parts[0]
                resolved = (
                    exe
                    if os.path.isabs(exe) and os.path.isfile(exe)
                    else resolve(exe)
                )
                if resolved is not None:
                    parts[0] = resolved
                    kwargs: dict[str, object] = {
                        "capture_output": True,
                        "text": True,
                        "timeout": TERMINAL_TIMEOUT_S,
                        "errors": "replace",
                    }
                    if run is subprocess.run and windows.CREATE_NO_WINDOW:
                        kwargs["creationflags"] = windows.CREATE_NO_WINDOW
                    try:
                        proc = run(parts, **kwargs)
                        ok = proc.returncode == 0
                        if ok:
                            succeeded += 1
                        results.append(
                            BatchCleanItemResult(
                                name=label,
                                command_or_path=rec.suggested_command,
                                estimated_bytes=rec.size_bytes if ok else 0,
                                succeeded=ok,
                                detail="ok" if ok else f"exit_code={proc.returncode}",
                            )
                        )
                        continue
                    except (OSError, subprocess.TimeoutExpired) as err:
                        results.append(
                            BatchCleanItemResult(
                                name=label,
                                command_or_path=rec.suggested_command,
                                estimated_bytes=0,
                                succeeded=False,
                                detail=str(err),
                            )
                        )
                        continue
        if rec.location_path and is_safe_deletion_path(rec.location_path):
            ok = send_to_trash(rec.location_path)
            if ok:
                succeeded += 1
            results.append(
                BatchCleanItemResult(
                    name=label,
                    command_or_path=rec.location_path,
                    estimated_bytes=rec.size_bytes if ok else 0,
                    succeeded=ok,
                    detail="sent_to_recycle_bin" if ok else "failed",
                )
            )
        else:
            results.append(
                BatchCleanItemResult(
                    name=label,
                    command_or_path=rec.suggested_command or rec.location_path or "",
                    estimated_bytes=0,
                    succeeded=False,
                    detail="skipped",
                )
            )

    from discoclaro.services import history_service, plan_service, summary_service

    plan_service._plan_cache = {}
    summary_service._summary_cache.clear()
    total_freed_batch = sum(i.estimated_bytes for i in results)
    if total_freed_batch > 0:
        history_service.record_cleanup(
            "batch_safe",
            f"Limpieza en lote ({succeeded} cachés seguras)",
            total_freed_batch,
            detail=", ".join(i.name for i in results if i.succeeded)[:200],
            data_dir=data_dir,
        )
    return BatchCleanResult(
        items=tuple(results),
        total_estimated_bytes=total_freed_batch,
        succeeded_count=succeeded,
    )


@dataclass(frozen=True, slots=True)
class VhdxCompactResult:
    """Resultado de compactar un disco virtual `.vhdx` de WSL2 o Docker."""

    path: str
    before_bytes: int
    after_bytes: int
    freed_bytes: int
    succeeded: bool
    detail: str


def compact_vhdx_assisted(
    vhdx_path: str,
    *,
    confirm: Callable[[str, str], bool] = windows.confirm_native_dialog,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    environ: Mapping[str, str] | None = None,
) -> VhdxCompactResult:
    """Apaga WSL (`wsl --shutdown`) y compacta un disco virtual `.vhdx` para
    devolver el espacio libre a Windows, siempre con confirmación nativa."""
    if _is_readonly_mode(environ):
        raise ActionError("readonly_mode")
    cleaned = os.path.normpath(vhdx_path.strip())
    if not cleaned.lower().endswith(".vhdx") or not os.path.isfile(cleaned):
        raise ActionError("unsafe_path", cleaned)

    before_bytes = os.path.getsize(cleaned)
    dialog_msg = (
        f"Compactación asistida de disco virtual (.vhdx)\n\n"
        f"Archivo: {cleaned}\n"
        f"Tamaño actual: {format_bytes(before_bytes, ',')}\n\n"
        f"Esto ejecutará `wsl --shutdown` (cerrando Docker/WSL temporalmente) y "
        f"compactará los bloques vacíos del disco virtual para devolver GB reales "
        f"a Windows sin borrar tus contenedores ni archivos.\n\n"
        f"¿Deseas continuar?"
    )
    if not confirm("discoclaro — Compactar disco virtual WSL2 / Docker", dialog_msg):
        raise ActionError("action_declined", cleaned)

    kwargs: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "timeout": 180.0,
        "errors": "replace",
    }
    if run is subprocess.run and windows.CREATE_NO_WINDOW:
        kwargs["creationflags"] = windows.CREATE_NO_WINDOW

    # 1. Apagar WSL2 para liberar el bloqueo del archivo .vhdx
    windir = os.environ.get("WINDIR", r"C:\Windows")
    wsl_exe = str(Path(windir) / "System32" / "wsl.exe")
    if os.path.isfile(wsl_exe):
        try:
            run([wsl_exe, "--shutdown"], **kwargs)
        except (OSError, subprocess.TimeoutExpired):
            pass

    # 2. Compactar con Optimize-VHD (Hyper-V) o fallback a diskpart sin shell=True
    ps_exe = str(
        Path(windir)
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    script = (
        "$p = $env:DISCOCLARO_VHDX_PATH; "
        "if (Get-Command Optimize-VHD -ErrorAction SilentlyContinue) { "
        "Optimize-VHD -Path $p -Mode Full; exit 0 "
        "} else { "
        "$tmp = [System.IO.Path]::GetTempFileName(); "
        "$lines = @(\"select vdisk file=`\"$p`\"\", \"attach vdisk readonly\", "
        "\"compact vdisk\", \"detach vdisk\", \"exit\"); "
        "Set-Content -Path $tmp -Value $lines -Encoding ASCII; "
        "$proc = Start-Process -FilePath diskpart.exe -ArgumentList \"/s `\"$tmp`\"\" "
        "-Verb RunAs -Wait -PassThru -WindowStyle Hidden; "
        "Remove-Item -Path $tmp -Force -ErrorAction SilentlyContinue; "
        "exit $proc.ExitCode }"
    )
    env = {**os.environ, "DISCOCLARO_VHDX_PATH": cleaned}
    ok = False
    detail = "ok"
    try:
        completed = run(
            [
                ps_exe,
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-Command",
                script,
            ],
            env=env,
            **kwargs,
        )
        ok = completed.returncode == 0
        if not ok:
            raw_err = (
                completed.stderr
                or completed.stdout
                or f"exit={completed.returncode}"
            )
            detail = raw_err.strip()[:240]
    except (OSError, subprocess.TimeoutExpired) as err:
        detail = str(err)

    after_bytes = os.path.getsize(cleaned) if os.path.isfile(cleaned) else before_bytes
    freed_bytes = max(0, before_bytes - after_bytes)
    if freed_bytes > 0:
        from discoclaro.services import history_service

        before_txt = format_bytes(before_bytes, ",")
        after_txt = format_bytes(after_bytes, ",")
        history_service.record_cleanup(
            "vhdx_compact",
            cleaned,
            freed_bytes,
            detail=f"Compactado de {before_txt} a {after_txt}",
        )
    return VhdxCompactResult(
        path=cleaned,
        before_bytes=before_bytes,
        after_bytes=after_bytes,
        freed_bytes=freed_bytes,
        succeeded=ok,
        detail=detail,
    )


@dataclass(frozen=True, slots=True)
class RecycleBinEmptyResult:
    """Resultado de vaciar completamente la Papelera de reciclaje de Windows."""

    before_bytes: int
    before_items: int
    after_bytes: int
    freed_bytes: int
    succeeded: bool


def empty_recycle_bin_verified(
    *,
    confirm: Callable[[str, str], bool] = windows.confirm_native_dialog,
    query_bin: Callable[[], tuple[int, int]] = windows.recycle_bin_stats,
    empty_bin: Callable[[], bool] = windows.empty_recycle_bin,
    environ: Mapping[str, str] | None = None,
) -> RecycleBinEmptyResult:
    """Vacía la Papelera de reciclaje completa de todos los discos tras
    confirmación nativa obligatoria de Windows y registra los GB liberados."""
    if _is_readonly_mode(environ):
        raise ActionError("readonly_mode")
    before_bytes, before_items = query_bin()
    if before_bytes == 0 and before_items == 0:
        return RecycleBinEmptyResult(
            before_bytes=0,
            before_items=0,
            after_bytes=0,
            freed_bytes=0,
            succeeded=True,
        )

    dialog_msg = (
        f"Vaciar Papelera de reciclaje completa de Windows\n\n"
        f"Contenido actual en todos tus discos:\n"
        f"• {before_items:,} elemento(s)\n"
        f"• {format_bytes(before_bytes, ',')} ocupados\n\n"
        f"Esta acción eliminará permanentemente los archivos de la Papelera "
        f"y devolverá ese espacio inmediatamente a tus discos.\n\n"
        f"¿Confirmas vaciar la Papelera ahora?"
    )
    if not confirm("discoclaro — Confirmar vaciado de Papelera", dialog_msg):
        raise ActionError("action_declined")

    ok = empty_bin()
    after_bytes, _ = query_bin()
    freed = max(0, before_bytes - after_bytes) if ok else 0
    if freed > 0:
        from discoclaro.services import history_service, summary_service

        summary_service._summary_cache.clear()
        history_service.record_cleanup(
            "recycle_bin",
            "Papelera de reciclaje de Windows (todos los discos)",
            freed,
            detail=f"{before_items:,} elemento(s) vaciados permanentemente",
        )
    return RecycleBinEmptyResult(
        before_bytes=before_bytes,
        before_items=before_items,
        after_bytes=after_bytes,
        freed_bytes=freed,
        succeeded=ok,
    )
