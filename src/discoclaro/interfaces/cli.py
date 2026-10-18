"""Línea de comandos de discoclaro.

Capa de entrada delgada: lee los argumentos, llama a un servicio e imprime el
resultado con los textos del catálogo. Aquí no va lógica de negocio.
"""

import argparse
import json
import sys
import time
from collections.abc import Sequence

from discoclaro import __version__
from discoclaro.domain.alerts import Alert
from discoclaro.domain.formatting import (
    format_bytes,
    format_decimal,
    format_duration,
    format_int,
    format_table,
    safe_text,
)
from discoclaro.domain.growth import CategoryGrowth, FolderGrowth
from discoclaro.domain.models import LargeFile, Node, ScanProgress
from discoclaro.domain.unexplained import Unexplained
from discoclaro.i18n import t
from discoclaro.platform import windows
from discoclaro.services import (
    check_service,
    elevation_service,
    growth_service,
    report_service,
    scan_service,
    schedule_service,
)
from discoclaro.services.check_service import CheckError
from discoclaro.services.growth_service import Growth, GrowthError
from discoclaro.services.report_service import Report, ReportError
from discoclaro.services.scan_service import ScanError, ScanSummary
from discoclaro.services.schedule_service import ScheduleError, TaskStatus

COMMANDS = ("scan", "report", "growth", "check", "schedule")
EXIT_CANCELLED = 130
PROGRESS_INTERVAL_S = 0.15


class ScanPaused(Exception):
    """El usuario pidió pausar (T1.10); ya queda a salvo en el punto de control."""


def build_parser() -> argparse.ArgumentParser:
    """Arma el analizador de argumentos: `--version` y los subcomandos."""
    parser = argparse.ArgumentParser(
        prog="discoclaro", description=t("cli.description")
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        subparser = subparsers.add_parser(name, help=t(f"cli.commands.{name}"))
        if name == "scan":
            subparser.add_argument(
                "target",
                metavar=t("cli.scan.target_name"),
                help=t("cli.scan.target_help"),
            )
            subparser.add_argument(
                "--admin", action="store_true", help=t("cli.scan.admin_help")
            )
            subparser.add_argument(
                "--restart", action="store_true", help=t("cli.scan.restart_help")
            )
        elif name == "report":
            subparser.add_argument(
                "target",
                nargs="?",
                metavar=t("cli.report.target_name"),
                help=t("cli.report.target_help"),
            )
            subparser.add_argument(
                "--top",
                type=int,
                default=report_service.DEFAULT_TOP_N,
                metavar="N",
                help=t("cli.report.top_help"),
            )
            subparser.add_argument(
                "--json", action="store_true", help=t("cli.report.json_help")
            )
            subparser.add_argument(
                "--by-category",
                action="store_true",
                help=t("cli.report.by_category_help"),
            )
        elif name == "growth":
            subparser.add_argument(
                "target",
                nargs="?",
                metavar=t("cli.growth.target_name"),
                help=t("cli.growth.target_help"),
            )
            subparser.add_argument(
                "--since",
                default=growth_service.DEFAULT_SINCE,
                metavar=t("cli.growth.since_name"),
                help=t("cli.growth.since_help"),
            )
            subparser.add_argument(
                "--top",
                type=int,
                default=growth_service.DEFAULT_TOP_N,
                metavar="N",
                help=t("cli.growth.top_help"),
            )
            subparser.add_argument(
                "--json", action="store_true", help=t("cli.growth.json_help")
            )
            subparser.add_argument(
                "--by-category",
                action="store_true",
                help=t("cli.growth.by_category_help"),
            )
        elif name == "check":
            subparser.add_argument(
                "--json", action="store_true", help=t("cli.check.json_help")
            )
        elif name == "schedule":
            schedule_subparsers = subparser.add_subparsers(
                dest="schedule_action", required=True
            )
            install_parser = schedule_subparsers.add_parser(
                "install", help=t("cli.schedule.install_help")
            )
            install_parser.add_argument(
                "target",
                metavar=t("cli.schedule.target_name"),
                help=t("cli.schedule.target_help"),
            )
            schedule_subparsers.add_parser("remove", help=t("cli.schedule.remove_help"))
            schedule_subparsers.add_parser("status", help=t("cli.schedule.status_help"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada del comando `discoclaro`; devuelve el código de salida."""
    args = build_parser().parse_args(argv)
    _make_output_robust()
    if args.command == "scan":
        return _scan(args.target, args.admin, args.restart)
    if args.command == "report":
        return _report(args.target, args.top, args.json, args.by_category)
    if args.command == "growth":
        return _growth(args.target, args.since, args.top, args.json, args.by_category)
    if args.command == "check":
        return _check(args.json)
    if args.command == "schedule":
        return _schedule(args.schedule_action, getattr(args, "target", None))
    return 0


def _make_output_robust() -> None:
    """Evita que un carácter que la consola no sabe escribir tumbe el programa."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="replace")


def _announce_elevation(volume: str, command: str) -> None:
    """Dice qué se va a pedir y la orden exacta, antes de la ventana de Windows."""
    print(t("admin.announce", target=safe_text(volume)))
    print(t("admin.command", command=safe_text(command)))


def _announce_writable_install(directory: str) -> None:
    """Avisa, antes de pedir el permiso, si la carpeta del paquete se puede escribir."""
    print(t("admin.writable_warning", directory=safe_text(directory)))


class _ProgressReporter:
    """Progreso en una sola línea (solo en una terminal interactiva) y pausa a tecla.

    `on_progress` se pasa al escaneo normal; `on_checkpoint` se llama cada vez que una
    carpeta de primer nivel queda a salvo en el punto de control (T1.10), que es el
    único momento seguro para pausar sin perder nada: ahí se revisa si el usuario
    pulsó la tecla de pausar.
    """

    def __init__(self) -> None:
        self._shown = False
        self._last = 0.0

    def on_progress(self, progress: ScanProgress) -> None:
        if not sys.stdout.isatty():
            return
        now = time.monotonic()
        if self._shown and now - self._last < PROGRESS_INTERVAL_S:
            return
        self._shown = True
        self._last = now
        line = t(
            "scan.progress",
            folders=_number(progress.folders),
            files=_number(progress.files),
            size=_size(progress.bytes_scanned),
            path=safe_text(progress.current_path),
        )
        print(f"\r{line}", end="", flush=True)

    def on_checkpoint(self) -> None:
        if windows.pause_requested():
            raise ScanPaused

    def finish(self) -> None:
        """Pasa a una línea nueva si se llegó a mostrar progreso."""
        if self._shown:
            print()


def _scan(target: str, admin: bool = False, restart: bool = False) -> int:
    """Escanea `target`, guarda la foto y muestra el resumen."""
    reporter = _ProgressReporter()
    try:
        if admin:
            summary = elevation_service.run_admin_scan(
                target,
                on_elevate=_announce_elevation,
                on_integrity_warning=_announce_writable_install,
                restart=restart,
            )
        else:
            summary = scan_service.run_scan(
                target,
                on_progress=reporter.on_progress,
                on_checkpoint=reporter.on_checkpoint,
                restart=restart,
            )
    except ScanPaused:
        reporter.finish()
        print(t("scan.paused", target=safe_text(target)), file=sys.stderr)
        return EXIT_CANCELLED
    except KeyboardInterrupt:
        reporter.finish()
        print(t("errors.scan_cancelled"), file=sys.stderr)
        return EXIT_CANCELLED
    except ScanError as error:
        reporter.finish()
        message = t(f"errors.{error.kind}", target=safe_text(error.target))
        print(message, file=sys.stderr)
        return 1
    reporter.finish()
    for line in _summary_lines(summary):
        print(line)
    return 0


def _report(target: str | None, top_n: int, as_json: bool, by_category: bool) -> int:
    """Arma el informe de una foto ya guardada y lo muestra en tabla o en JSON."""
    try:
        report = report_service.run_report(target, top_n=top_n, by_category=by_category)
    except ReportError as error:
        message = t(f"errors.{error.kind}", target=safe_text(error.target))
        print(message, file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(_report_to_json(report), ensure_ascii=False, indent=2))
    else:
        for line in _report_lines(report):
            print(line)
    return 0


def _growth(
    target: str | None, since: str, top_n: int, as_json: bool, by_category: bool
) -> int:
    """Compara dos fotos ya guardadas y muestra lo que cambió, en tabla o en JSON."""
    try:
        growth = growth_service.run_growth(
            target, since, top_n=top_n, by_category=by_category
        )
    except GrowthError as error:
        message = t(f"errors.{error.kind}", target=safe_text(error.target))
        print(message, file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(_growth_to_json(growth), ensure_ascii=False, indent=2))
    else:
        for line in _growth_lines(growth):
            print(line)
    return 0


def _check(as_json: bool) -> int:
    """Evalúa y notifica los avisos configurados, y muestra cuáles se avisaron."""
    try:
        alerts = check_service.run_check()
    except CheckError as error:
        message = t(f"errors.{error.kind}", target=safe_text(error.target))
        print(message, file=sys.stderr)
        return 1
    if as_json:
        print(
            json.dumps(
                [_alert_to_json(a) for a in alerts], ensure_ascii=False, indent=2
            )
        )
    else:
        for line in _check_lines(alerts):
            print(line)
    return 0


def _schedule(action: str, target: str | None) -> int:
    """Instala, quita o consulta las tareas programadas, según `action`."""
    try:
        if action == "install":
            schedule_service.install(target)
            print(
                t(
                    "schedule.installed",
                    target=safe_text(target),
                    time=schedule_service.DEFAULT_START_TIME,
                )
            )
        elif action == "remove":
            removed = schedule_service.remove()
            print(t("schedule.removed" if removed else "schedule.not_installed"))
        else:
            result = schedule_service.status()
            print(t(f"schedule.status_scan_{_status_suffix(result.scan)}"))
            print(t(f"schedule.status_check_{_status_suffix(result.check)}"))
    except ScheduleError as error:
        message = t(f"errors.{error.kind}", target=safe_text(error.target))
        print(message, file=sys.stderr)
        return 1
    return 0


def _status_suffix(task: TaskStatus) -> str:
    if not task.installed:
        return "not_installed"
    return "altered" if task.altered else "installed"


def _check_lines(alerts: list[Alert]) -> list[str]:
    """Las líneas del chequeo: una tabla con lo que se avisó, o que no hubo nada."""
    if not alerts:
        return [t("check.none_found")]
    headers = [
        t("check.column_title"),
        t("check.column_target"),
        t("check.column_detail"),
    ]
    rows = []
    for alert in alerts:
        title, message = check_service.format_alert_text(alert)
        rows.append([safe_text(title), safe_text(alert.target), safe_text(message)])
    lines = format_table(headers, rows, align=("l", "l", "l"))
    lines.append(t("check.shown", shown=_number(len(alerts))))
    return lines


def _alert_to_json(alert: Alert) -> dict[str, object]:
    return {
        "kind": alert.kind,
        "target": alert.target,
        "measured_bytes": alert.measured_bytes,
        "limit_bytes": alert.limit_bytes,
        "measured_percent": alert.measured_percent,
        "limit_percent": alert.limit_percent,
    }


def _size(value: int) -> str:
    return format_bytes(value, t("format.decimal_separator"))


def _signed_size(value: int) -> str:
    """Como `_size`, pero con el signo adelante: `format_bytes` no acepta negativos."""
    sign = "+" if value >= 0 else "-"
    return f"{sign}{_size(abs(value))}"


def _number(value: int) -> str:
    return format_int(value, t("format.thousands_separator"))


def _summary_lines(summary: ScanSummary) -> list[str]:
    """Las líneas del resumen de un escaneo, en el idioma actual."""
    snapshot = summary.snapshot
    shown = snapshot.volume if summary.is_volume else summary.target
    space_key = "scan.space_volume" if summary.is_volume else "scan.space_folder"
    lines = [
        t(
            "scan.done",
            target=safe_text(shown),
            duration=format_duration(
                snapshot.duration_s, t("format.decimal_separator")
            ),
        )
    ]
    if summary.resumed_folders:
        lines.append(t("scan.resumed", count=_number(summary.resumed_folders)))
    lines.append(
        t(
            "scan.counts",
            folders=_number(summary.folders),
            files=_number(summary.files),
            links=_number(summary.skipped_links),
        )
    )
    lines.append(
        t(
            space_key,
            scanned=_size(snapshot.scanned_bytes),
            used=_size(snapshot.used_bytes),
            total=_size(snapshot.total_bytes),
            free=_size(snapshot.free_bytes),
        )
    )
    if snapshot.duplicate_bytes > 0:
        lines.append(t("scan.duplicates", size=_size(snapshot.duplicate_bytes)))
    if snapshot.system_files_bytes > 0:
        lines.append(t("scan.system_files", size=_size(snapshot.system_files_bytes)))
    if snapshot.unreadable_count:
        lines.append(t("scan.unreadable", count=_number(snapshot.unreadable_count)))
    if summary.unexplained is not None:
        lines.extend(
            _unexplained_lines(summary.unexplained, snapshot.is_admin, snapshot.volume)
        )
    lines.append(t("scan.saved", id=snapshot.id, nodes=_number(summary.nodes_saved)))
    if summary.pruned_snapshots:
        lines.append(t("scan.pruned", count=_number(summary.pruned_snapshots)))
    return lines


def _report_lines(report: Report) -> list[str]:
    """Las líneas del informe: título, tabla de carpetas y espacio sin explicar."""
    snapshot = report.snapshot
    taken_at = snapshot.taken_at.astimezone().strftime("%Y-%m-%d %H:%M")
    lines = [
        t(
            "report.title",
            target=safe_text(snapshot.volume),
            taken_at=taken_at,
            id=snapshot.id,
        )
    ]
    if report.by_category is not None:
        headers = [
            t("report.column_category"),
            t("report.column_size"),
            t("report.column_folders"),
        ]
        rows = [
            [safe_text(total.category), _size(total.size_bytes), _number(total.folders)]
            for total in report.by_category
        ]
        lines.extend(format_table(headers, rows, align=("l", "r", "r")))
        lines.append(
            t("report.shown_categories", shown=_number(len(report.by_category)))
        )
    else:
        headers = [
            t("report.column_path"),
            t("report.column_size"),
            t("report.column_files"),
        ]
        rows = [
            [safe_text(node.path), _size(node.size_bytes), _number(node.n_files)]
            for node in report.top_folders
        ]
        lines.extend(format_table(headers, rows, align=("l", "r", "r")))
        lines.append(
            t(
                "report.shown",
                shown=_number(len(report.top_folders)),
                total=_number(report.total_folders),
            )
        )
    if report.unexplained is not None:
        lines.extend(
            _unexplained_lines(report.unexplained, snapshot.is_admin, snapshot.volume)
        )
    return lines


def _report_to_json(report: Report) -> dict[str, object]:
    """El informe como datos simples y sin traducir, para `--json`."""
    snapshot = report.snapshot
    unexplained = report.unexplained
    return {
        "target": snapshot.volume,
        "snapshot_id": snapshot.id,
        "taken_at": snapshot.taken_at.isoformat(),
        "is_admin": snapshot.is_admin,
        "total_bytes": snapshot.total_bytes,
        "used_bytes": snapshot.used_bytes,
        "free_bytes": snapshot.free_bytes,
        "scanned_bytes": snapshot.scanned_bytes,
        "duplicate_bytes": snapshot.duplicate_bytes,
        "system_files_bytes": snapshot.system_files_bytes,
        "unreadable_count": snapshot.unreadable_count,
        "total_folders": report.total_folders,
        "top_folders": [_node_to_json(node) for node in report.top_folders],
        "by_category": None
        if report.by_category is None
        else [
            {
                "category": total.category,
                "size_bytes": total.size_bytes,
                "folders": total.folders,
            }
            for total in report.by_category
        ],
        "unexplained": None
        if unexplained is None
        else {
            "size_bytes": unexplained.size_bytes,
            "percent_of_used": unexplained.percent_of_used,
            "exceeds_threshold": unexplained.exceeds_threshold,
        },
    }


def _node_to_json(node: Node) -> dict[str, object]:
    return {
        "path": node.path,
        "size_bytes": node.size_bytes,
        "n_files": node.n_files,
        "depth": node.depth,
    }


def _growth_lines(growth: Growth) -> list[str]:
    """Las líneas de la comparación: título, tabla de cambios y archivos nuevos."""
    before_at = growth.before.taken_at.astimezone().strftime("%Y-%m-%d %H:%M")
    after_at = growth.after.taken_at.astimezone().strftime("%Y-%m-%d %H:%M")
    lines = [
        t(
            "growth.title",
            target=safe_text(growth.after.volume),
            before=before_at,
            after=after_at,
        )
    ]
    if growth.categories is not None:
        lines.extend(_category_growth_table(growth.categories))
        lines.append(
            t("growth.shown_categories", shown=_number(len(growth.categories)))
        )
    else:
        lines.extend(_folder_growth_table(growth.folders))
        lines.append(t("growth.shown_folders", shown=_number(len(growth.folders))))
    if growth.new_large_files:
        lines.append(t("growth.new_files_title"))
        headers = [t("growth.column_file"), t("growth.column_size")]
        rows = [
            [safe_text(file.path), _size(file.size_bytes)]
            for file in growth.new_large_files
        ]
        lines.extend(format_table(headers, rows, align=("l", "r")))
    return lines


def _folder_growth_table(folders: tuple[FolderGrowth, ...]) -> list[str]:
    headers = [
        t("growth.column_path"),
        t("growth.column_before"),
        t("growth.column_after"),
        t("growth.column_grown"),
    ]
    rows = [
        [
            safe_text(folder.path),
            _size(folder.before_bytes),
            _size(folder.after_bytes),
            _signed_size(folder.grown_bytes),
        ]
        for folder in folders
    ]
    return format_table(headers, rows, align=("l", "r", "r", "r"))


def _category_growth_table(categories: tuple[CategoryGrowth, ...]) -> list[str]:
    headers = [
        t("growth.column_category"),
        t("growth.column_before"),
        t("growth.column_after"),
        t("growth.column_grown"),
    ]
    rows = [
        [
            safe_text(category.category),
            _size(category.before_bytes),
            _size(category.after_bytes),
            _signed_size(category.grown_bytes),
        ]
        for category in categories
    ]
    return format_table(headers, rows, align=("l", "r", "r", "r"))


def _growth_to_json(growth: Growth) -> dict[str, object]:
    """La comparación como datos simples y sin traducir, para `--json`."""
    return {
        "target": growth.after.volume,
        "before_snapshot_id": growth.before.id,
        "after_snapshot_id": growth.after.id,
        "before_taken_at": growth.before.taken_at.isoformat(),
        "after_taken_at": growth.after.taken_at.isoformat(),
        "folders": [_folder_growth_to_json(folder) for folder in growth.folders],
        "by_category": None
        if growth.categories is None
        else [_category_growth_to_json(category) for category in growth.categories],
        "new_large_files": [
            _large_file_to_json(file) for file in growth.new_large_files
        ],
    }


def _folder_growth_to_json(folder: FolderGrowth) -> dict[str, object]:
    return {
        "path": folder.path,
        "depth": folder.depth,
        "before_bytes": folder.before_bytes,
        "after_bytes": folder.after_bytes,
        "grown_bytes": folder.grown_bytes,
        "is_new": folder.is_new,
    }


def _category_growth_to_json(category: CategoryGrowth) -> dict[str, object]:
    return {
        "category": category.category,
        "before_bytes": category.before_bytes,
        "after_bytes": category.after_bytes,
        "grown_bytes": category.grown_bytes,
    }


def _large_file_to_json(file: LargeFile) -> dict[str, object]:
    return {"path": file.path, "size_bytes": file.size_bytes}


def _unexplained_lines(
    unexplained: Unexplained, is_admin: bool, volume: str
) -> list[str]:
    """El espacio sin explicar y, si pasa el umbral, el aviso que corresponde.

    No se muestra nada si no sobra espacio: un resultado negativo no es un problema.
    """
    if unexplained.size_bytes <= 0:
        return []
    size = _size(unexplained.size_bytes)
    percent = format_decimal(unexplained.percent_of_used, t("format.decimal_separator"))
    lines = [t("scan.unexplained", size=size, percent=percent)]
    if unexplained.exceeds_threshold:
        key = (
            "scan.unexplained_warning_admin" if is_admin else "scan.unexplained_warning"
        )
        lines.append(t(key, size=size, volume=safe_text(volume)))
    return lines
