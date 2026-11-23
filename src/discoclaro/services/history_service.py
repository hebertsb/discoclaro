"""Historial persistente de espacio recuperado y auditoría de limpiezas.

Guarda cada acción ejecutada por el usuario (envío a Papelera, limpieza en lote
de cachés, desinstalación sin rastros, limpieza de proyectos antiguos o
compactación de discos virtuales `.vhdx`) en
`%LOCALAPPDATA%\\discoclaro\\cleanup_history.json` para mostrar el total
acumulado de GB recuperados y permitir exportar reportes.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from discoclaro.platform import windows

HISTORY_FILENAME = "cleanup_history.json"
MAX_HISTORY_ENTRIES = 500


@dataclass(frozen=True, slots=True)
class CleanupHistoryEntry:
    """Un registro individual de espacio liberado."""

    timestamp: str
    action_type: str  # trash | batch_safe | dev_project | vhdx_compact | uninstall
    target: str
    freed_bytes: int
    detail: str


@dataclass(frozen=True, slots=True)
class CleanupHistoryOverview:
    """Resumen acumulado y lista ordenada del historial de limpiezas."""

    total_freed_bytes: int
    total_actions: int
    entries: tuple[CleanupHistoryEntry, ...]


def _history_path(data_dir: Path | None = None) -> Path:
    directory = windows.default_data_dir() if data_dir is None else data_dir
    return Path(directory) / HISTORY_FILENAME


def load_history(data_dir: Path | None = None) -> CleanupHistoryOverview:
    """Lee el historial persistido en disco; devuelve vacío si aún no existe."""
    path = _history_path(data_dir)
    if not path.is_file():
        return CleanupHistoryOverview(
            total_freed_bytes=0, total_actions=0, entries=()
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw_entries = raw.get("entries", []) if isinstance(raw, dict) else []
        entries: list[CleanupHistoryEntry] = []
        for row in raw_entries:
            if not isinstance(row, dict):
                continue
            entries.append(
                CleanupHistoryEntry(
                    timestamp=str(row.get("timestamp", "")),
                    action_type=str(row.get("action_type", "trash")),
                    target=str(row.get("target", "")),
                    freed_bytes=max(0, int(row.get("freed_bytes", 0))),
                    detail=str(row.get("detail", "")),
                )
            )
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        total = sum(e.freed_bytes for e in entries)
        return CleanupHistoryOverview(
            total_freed_bytes=total,
            total_actions=len(entries),
            entries=tuple(entries),
        )
    except Exception:
        return CleanupHistoryOverview(
            total_freed_bytes=0, total_actions=0, entries=()
        )


def record_cleanup(
    action_type: str,
    target: str,
    freed_bytes: int,
    detail: str = "",
    *,
    data_dir: Path | None = None,
) -> CleanupHistoryEntry:
    """Agrega un evento al historial en disco sin fallar si no hay permisos."""
    entry = CleanupHistoryEntry(
        timestamp=datetime.now(UTC).isoformat(),
        action_type=action_type,
        target=target,
        freed_bytes=max(0, int(freed_bytes)),
        detail=detail,
    )
    with contextlib.suppress(Exception):
        current = load_history(data_dir)
        combined = [entry, *current.entries][:MAX_HISTORY_ENTRIES]
        path = _history_path(data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "entries": [
                {
                    "timestamp": e.timestamp,
                    "action_type": e.action_type,
                    "target": e.target,
                    "freed_bytes": e.freed_bytes,
                    "detail": e.detail,
                }
                for e in combined
            ]
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return entry
