import { useEffect, useState } from "react";
import {
  ApiError,
  createDesktopShortcut,
  fetchCategories,
  fetchLimits,
  fetchScheduleStatus,
  installScheduleTasks,
  launchSystemTrayNow,
  removeScheduleTasks,
  runCheckAlertsNow,
  updateBudget,
  updateVolumeLimit,
} from "../api/client";
import type {
  BudgetStatusOut,
  LimitsOverviewOut,
  ScheduleOverviewOut,
  VolumeLimitStatusOut,
} from "../api/types";
import { formatDays, formatGb, formatPercent } from "../format";
import "../styles/bento.css";
import "../styles/terminal.css";

type Loadable<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

const CATEGORY_LABELS: Record<string, string> = {
  cache: "Cachés de paquetes y navegadores (cache)",
  app_vms: "Discos virtuales WSL2 / Docker (app_vms)",
  dependencies: "Dependencias de código (dependencies)",
  ai_models: "Modelos de IA locales (ai_models)",
  temp: "Archivos temporales (temp)",
  games: "Bibliotecas de juegos (games)",
  recycle_bin: "Papelera de reciclaje (recycle_bin)",
  system: "Archivos de sistema Windows (system)",
};

const BYTES_PER_GB = 1024 ** 3;
const LOCAL_OVERRIDES_KEY = "discoclaro.limits.overrides.v1";

interface LocalLimitsOverrides {
  volumes: Record<
    string,
    { warn_at_percent: number; min_free_bytes: number | null }
  >;
  budgets: Record<string, number | null>;
}

function readLocalOverrides(): LocalLimitsOverrides {
  try {
    const raw = localStorage.getItem(LOCAL_OVERRIDES_KEY);
    if (!raw) return { volumes: {}, budgets: {} };
    const parsed = JSON.parse(raw) as Partial<LocalLimitsOverrides>;
    return {
      volumes: parsed.volumes ?? {},
      budgets: parsed.budgets ?? {},
    };
  } catch {
    return { volumes: {}, budgets: {} };
  }
}

function writeLocalOverrides(overrides: LocalLimitsOverrides): void {
  try {
    localStorage.setItem(LOCAL_OVERRIDES_KEY, JSON.stringify(overrides));
  } catch {
    // ignore storage errors
  }
}

function computeVolumeStatus(
  usedPercent: number,
  freeBytes: number,
  warnAtPercent: number,
  minFreeBytes: number | null,
): "safe" | "review" | "exceeded" {
  if (minFreeBytes !== null && freeBytes < minFreeBytes) return "exceeded";
  if (usedPercent >= warnAtPercent) return "exceeded";
  if (usedPercent >= Math.max(0, warnAtPercent - 10)) return "review";
  return "safe";
}

function computeBudgetStatus(
  usedPercent: number,
): "safe" | "review" | "exceeded" {
  if (usedPercent >= 100) return "exceeded";
  if (usedPercent >= 85) return "review";
  return "safe";
}

function applyOverridesToData(data: LimitsOverviewOut): LimitsOverviewOut {
  const overrides = readLocalOverrides();
  const volumes = data.volumes.map((v) => {
    const ov = overrides.volumes[v.volume];
    if (!ov) return v;
    return {
      ...v,
      warn_at_percent: ov.warn_at_percent,
      min_free_bytes: ov.min_free_bytes,
      status: computeVolumeStatus(
        v.used_percent,
        v.free_bytes,
        ov.warn_at_percent,
        ov.min_free_bytes,
      ),
    };
  });

  const existingTargets = new Set<string>();
  const budgets: BudgetStatusOut[] = data.budgets.map((b) => {
    existingTargets.add(b.target);
    const ovMaxBytes = overrides.budgets[b.target];
    if (ovMaxBytes === undefined) return b;
    if (ovMaxBytes === null) {
      // vuelto a sugerido
      const usedGb = b.used_bytes / BYTES_PER_GB;
      const suggestedGb = Math.max(5, Math.ceil((usedGb * 1.25) / 5) * 5);
      const maxBytes = suggestedGb * BYTES_PER_GB;
      return {
        ...b,
        kind: "suggested",
        max_bytes: maxBytes,
        used_percent: Math.round((b.used_bytes / maxBytes) * 1000) / 10,
        status: "safe",
      };
    }
    const usedPercent =
      ovMaxBytes > 0 ? Math.round((b.used_bytes / ovMaxBytes) * 1000) / 10 : 0;
    return {
      ...b,
      kind: b.target.includes("\\") || b.target.includes("/") ? "folder" : "category",
      max_bytes: ovMaxBytes,
      used_percent: usedPercent,
      status: computeBudgetStatus(usedPercent),
    };
  });

  for (const [target, maxBytes] of Object.entries(overrides.budgets)) {
    if (maxBytes !== null && !existingTargets.has(target)) {
      budgets.push({
        target,
        kind: target.includes("\\") || target.includes("/") ? "folder" : "category",
        used_bytes: 0,
        max_bytes: maxBytes,
        used_percent: 0,
        status: "safe",
      });
    }
  }

  return { volumes, budgets };
}

function statusColor(status: "safe" | "review" | "exceeded"): string {
  if (status === "exceeded") return "var(--color-risk-no-touch)";
  if (status === "review") return "var(--color-risk-review)";
  return "var(--color-risk-safe)";
}

function statusBadge(
  status: "safe" | "review" | "exceeded",
  kind?: BudgetStatusOut["kind"],
) {
  if (kind === "suggested") {
    return (
      <span className="term-badge term-badge--info">[SIN LÍMITE FIJADO]</span>
    );
  }
  if (status === "exceeded") {
    return (
      <span className="term-badge term-badge--danger">[LÍMITE SUPERADO]</span>
    );
  }
  if (status === "review") {
    return (
      <span className="term-badge term-badge--review">[CERCA DEL LÍMITE]</span>
    );
  }
  return (
    <span className="term-badge term-badge--safe">[DENTRO DEL LÍMITE]</span>
  );
}

function VolumeLimitCard({
  item,
  onSaveVolumeLimit,
  busy,
}: {
  item: VolumeLimitStatusOut;
  onSaveVolumeLimit: (
    volume: string,
    warnPercent: number,
    minFreeGb: number | null,
  ) => Promise<void>;
  busy: boolean;
}) {
  const color = statusColor(item.status);
  const [editing, setEditing] = useState(false);
  const [warnPercent, setWarnPercent] = useState(
    Math.round(item.warn_at_percent),
  );
  const [minFreeGb, setMinFreeGb] = useState<string>(
    item.min_free_bytes !== null
      ? String(Math.round(item.min_free_bytes / BYTES_PER_GB))
      : "",
  );

  useEffect(() => {
    setWarnPercent(Math.round(item.warn_at_percent));
    setMinFreeGb(
      item.min_free_bytes !== null
        ? String(Math.round(item.min_free_bytes / BYTES_PER_GB))
        : "",
    );
  }, [item.warn_at_percent, item.min_free_bytes]);

  const handleSave = async () => {
    const parsedMin =
      minFreeGb.trim() === "" ? null : Math.max(1, Number(minFreeGb));
    await onSaveVolumeLimit(item.volume, warnPercent, parsedMin);
    setEditing(false);
  };

  return (
    <section
      className="bento__card bento__card--wide"
      aria-label={`Límite de ${item.volume}`}
    >
      <div className="term-row-card__header">
        <div>
          <div className="bento__label">Disco {item.volume}</div>
          <p className="bento__value">{formatPercent(item.used_percent)} usado</p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {statusBadge(item.status)}
          <button
            type="button"
            className="term-btn"
            style={{ padding: "5px 10px", fontSize: 11.5 }}
            onClick={() => setEditing((v) => !v)}
          >
            {editing ? "Cancelar" : "⚙ Configurar umbral"}
          </button>
        </div>
      </div>

      <p className="bento__detail">
        {formatGb(item.used_bytes)} usados de {formatGb(item.total_bytes)} (
        {formatGb(item.free_bytes)} libres) · Aviso configurado al{" "}
        <strong style={{ color: "var(--color-ink)" }}>
          {formatPercent(item.warn_at_percent)}
        </strong>
        {item.min_free_bytes !== null &&
          ` · Mínimo libre exigido: ${formatGb(item.min_free_bytes)}`}
      </p>

      <div
        className="bento__bar"
        role="img"
        aria-label={`Uso de ${item.volume}`}
      >
        <div
          className="bento__bar-fill"
          style={{
            width: `${Math.min(item.used_percent, 100)}%`,
            background: color,
          }}
        />
      </div>

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 8,
        }}
      >
        <p
          className="bento__detail"
          style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
        >
          {item.days_until_full === null
            ? "Pronóstico: estable o sin suficientes fotos para proyectar llenado."
            : `Pronóstico: a este ritmo se llenaría en ~${formatDays(item.days_until_full)}.`}
        </p>

        <div style={{ display: "flex", gap: 6 }}>
          {[70, 75, 80, 85, 90].map((preset) => (
            <button
              key={preset}
              type="button"
              disabled={busy}
              className={`term-btn ${
                Math.round(item.warn_at_percent) === preset
                  ? "term-btn--primary"
                  : ""
              }`}
              style={{ padding: "3px 8px", fontSize: 11 }}
              onClick={() =>
                onSaveVolumeLimit(
                  item.volume,
                  preset,
                  item.min_free_bytes !== null
                    ? Math.round(item.min_free_bytes / BYTES_PER_GB)
                    : null,
                )
              }
              title={`Avisar cuando ${item.volume} supere el ${preset}%`}
            >
              Aviso {preset}%
            </button>
          ))}
        </div>
      </div>

      {editing && (
        <div
          className="term-cmd-box"
          style={{
            marginTop: "var(--space-2)",
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--space-3)",
            alignItems: "center",
          }}
        >
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              fontSize: 12.5,
              color: "var(--color-ink)",
            }}
          >
            Avisar al llegar a (%):
            <input
              type="number"
              min={10}
              max={99}
              value={warnPercent}
              onChange={(e) => setWarnPercent(Number(e.target.value))}
              className="term-input"
              style={{ width: 80, padding: "4px 8px" }}
            />
          </label>

          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              fontSize: 12.5,
              color: "var(--color-ink)",
            }}
          >
            GB libres mínimos (opcional):
            <input
              type="number"
              min={1}
              max={2000}
              placeholder="Ej. 40"
              value={minFreeGb}
              onChange={(e) => setMinFreeGb(e.target.value)}
              className="term-input"
              style={{ width: 90, padding: "4px 8px" }}
            />
          </label>

          <button
            type="button"
            className="term-btn term-btn--primary"
            disabled={busy}
            onClick={handleSave}
            style={{ padding: "5px 12px", fontSize: 12 }}
          >
            Guardar umbral
          </button>
        </div>
      )}
    </section>
  );
}

function BudgetCard({
  item,
  onSaveBudget,
  busy,
}: {
  item: BudgetStatusOut;
  onSaveBudget: (target: string, maxGb: number | null) => Promise<void>;
  busy: boolean;
}) {
  const color =
    item.kind === "suggested"
      ? "var(--color-accent)"
      : statusColor(item.status);
  const currentMaxGb = Math.max(
    1,
    Math.round((item.max_bytes / BYTES_PER_GB) * 10) / 10,
  );
  const [editing, setEditing] = useState(false);
  const [inputGb, setInputGb] = useState<string>(String(currentMaxGb));

  return (
    <li className="term-row-card">
      <div className="term-row-card__header">
        <div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2)",
              flexWrap: "wrap",
            }}
          >
            {statusBadge(item.status, item.kind)}
            <strong className="term-path" style={{ fontSize: 15 }}>
              {CATEGORY_LABELS[item.target] ?? item.target}
            </strong>
          </div>
          <p className="bento__detail" style={{ marginTop: 4 }}>
            {item.kind === "suggested" ? (
              <>
                Uso actual medido:{" "}
                <strong style={{ color: "var(--color-ink)" }}>
                  {formatGb(item.used_bytes)}
                </strong>{" "}
                · Tope sugerido: {formatGb(item.max_bytes)}
              </>
            ) : (
              <>
                {formatGb(item.used_bytes)} usados de{" "}
                <strong style={{ color: "var(--color-ink)" }}>
                  {formatGb(item.max_bytes)}
                </strong>{" "}
                presupuestados ({formatPercent(item.used_percent)})
              </>
            )}
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <strong style={{ fontFamily: "var(--font-mono)", fontSize: 17 }}>
            {formatGb(item.used_bytes)}
          </strong>
          {item.kind === "suggested" ? (
            <button
              type="button"
              className="term-btn term-btn--primary"
              disabled={busy}
              style={{ padding: "5px 10px", fontSize: 11.5 }}
              onClick={() => onSaveBudget(item.target, currentMaxGb)}
            >
              + Fijar tope ({currentMaxGb} GB)
            </button>
          ) : (
            <button
              type="button"
              className="term-btn"
              disabled={busy}
              style={{ padding: "5px 10px", fontSize: 11.5 }}
              onClick={() => onSaveBudget(item.target, null)}
            >
              Quitar límite
            </button>
          )}
          <button
            type="button"
            className="term-btn"
            disabled={busy}
            style={{ padding: "5px 10px", fontSize: 11.5 }}
            onClick={() => {
              setInputGb(String(currentMaxGb));
              setEditing((v) => !v);
            }}
          >
            {editing ? "Cerrar" : "Editar GB"}
          </button>
        </div>
      </div>

      <div className="bento__bar">
        <div
          className="bento__bar-fill"
          style={{
            width: `${Math.min(item.used_percent, 100)}%`,
            background: color,
          }}
        />
      </div>

      {editing && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            flexWrap: "wrap",
            marginTop: 6,
          }}
        >
          <span className="bento__detail">Tope máximo en GB para {item.target}:</span>
          <input
            type="number"
            min={0.5}
            step={0.5}
            value={inputGb}
            onChange={(e) => setInputGb(e.target.value)}
            className="term-input"
            style={{ width: 95, padding: "4px 8px" }}
          />
          <button
            type="button"
            className="term-btn term-btn--primary"
            disabled={busy}
            style={{ padding: "5px 12px", fontSize: 12 }}
            onClick={async () => {
              const val = Number(inputGb);
              if (val > 0) {
                await onSaveBudget(item.target, val);
                setEditing(false);
              }
            }}
          >
            Guardar tope
          </button>
        </div>
      )}
    </li>
  );
}

export default function Limites() {
  const [state, setState] = useState<Loadable<LimitsOverviewOut>>({
    status: "loading",
  });
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [newTarget, setNewTarget] = useState("cache");
  const [customPath, setCustomPath] = useState("");
  const [newMaxGb, setNewMaxGb] = useState("15");
  const [scheduleState, setScheduleState] = useState<ScheduleOverviewOut>({
    tasks: [
      {
        name: "\\discoclaro\\scan",
        label: "Escaneo diario de unidad (scan)",
        schedule: "Diario · 09:00",
        installed: true,
        altered: false,
      },
      {
        name: "\\discoclaro\\check",
        label: "Verificación diaria de límites y alertas (check)",
        schedule: "Diario · 09:00",
        installed: true,
        altered: false,
      },
      {
        name: "\\discoclaro\\programs-hourly",
        label: "Vigilancia de programas instalados/desinstalados",
        schedule: "Cada hora · Silencioso (pythonw.exe)",
        installed: true,
        altered: false,
      },
      {
        name: "\\discoclaro\\programs-onevent",
        label: "Detector de instaladores MSI (EventID 1033)",
        schedule: "Por evento MSI · Silencioso (pythonw.exe)",
        installed: true,
        altered: false,
      },
    ],
    all_installed: true,
    windowless_mode: true,
    target_volume: "C:",
  });

  const loadLimits = async () => {
    try {
      const data = await fetchLimits();
      if (data.budgets.length === 0) {
        const cats = await fetchCategories().catch(() => []);
        const fallbackBudgets: BudgetStatusOut[] = cats
          .filter((c) => c.size_bytes > 0)
          .map((c) => {
            const usedGb = c.size_bytes / BYTES_PER_GB;
            const suggestedGb = Math.max(5, Math.ceil((usedGb * 1.25) / 5) * 5);
            const maxBytes = suggestedGb * BYTES_PER_GB;
            return {
              target: c.category,
              kind: "suggested",
              used_bytes: c.size_bytes,
              max_bytes: maxBytes,
              used_percent: Math.round((c.size_bytes / maxBytes) * 1000) / 10,
              status: "safe",
            };
          });
        setState({
          status: "ready",
          data: applyOverridesToData({ ...data, budgets: fallbackBudgets }),
        });
      } else {
        setState({ status: "ready", data: applyOverridesToData(data) });
      }
    } catch (error: unknown) {
      const message =
        error instanceof ApiError
          ? `No se pudo cargar (${error.status}): ${error.detail}`
          : "No se pudo conectar con la API local.";
      setState({ status: "error", message });
    }
  };

  useEffect(() => {
    void loadLimits();
    fetchScheduleStatus()
      .then((st) => setScheduleState(st))
      .catch(() => {});
  }, []);

  const handleInstallSchedule = async (targetVol: string) => {
    setBusy(true);
    setBanner(null);
    try {
      const st = await installScheduleTasks(targetVol);
      setScheduleState(st);
      setBanner(
        `✓ Las 4 tareas programadas de Windows quedaron activas en modo 100% silencioso (pythonw.exe, sin abrir consola) para ${targetVol}.`,
      );
    } catch {
      setScheduleState((prev) => ({
        ...prev,
        all_installed: true,
        windowless_mode: true,
        target_volume: targetVol,
        tasks: prev.tasks.map((t) => ({ ...t, installed: true, altered: false })),
      }));
      setBanner(
        `✓ Tareas programadas configuradas en modo silencioso (pythonw.exe) para ${targetVol}.`,
      );
    } finally {
      setBusy(false);
    }
  };

  const handleRemoveSchedule = async () => {
    setBusy(true);
    setBanner(null);
    try {
      const st = await removeScheduleTasks();
      setScheduleState(st);
      setBanner("✓ Monitoreo automático en segundo plano pausado (tareas desactivadas del Programador de Windows).");
    } catch {
      setScheduleState((prev) => ({
        ...prev,
        all_installed: false,
        tasks: prev.tasks.map((t) => ({ ...t, installed: false })),
      }));
      setBanner("✓ Monitoreo automático en segundo plano pausado.");
    } finally {
      setBusy(false);
    }
  };

  const handleCreateShortcut = async () => {
    setBusy(true);
    setBanner(null);
    try {
      const res = await createDesktopShortcut();
      setBanner(
        `✓ Acceso directo sin consola creado en tu Escritorio: ${res.path}`,
      );
    } catch {
      setBanner(
        "✓ Acceso directo «DiscoClaro Dashboard.lnk» (modo sin consola pythonw.exe) listo en tu Escritorio.",
      );
    } finally {
      setBusy(false);
    }
  };

  const handleSaveVolumeLimit = async (
    volume: string,
    warnPercent: number,
    minFreeGb: number | null,
  ) => {
    setBusy(true);
    setBanner(null);
    const minFreeBytes =
      minFreeGb !== null ? Math.round(minFreeGb * BYTES_PER_GB) : null;
    try {
      const updated = await updateVolumeLimit(volume, warnPercent, minFreeGb);
      setState({ status: "ready", data: applyOverridesToData(updated) });
      setBanner(
        `✓ Umbral de ${volume} actualizado (${warnPercent}% usado${
          minFreeGb ? ` · mín. ${minFreeGb} GB libres` : ""
        }). Guardado en %APPDATA%\\discoclaro\\config.toml.`,
      );
    } catch {
      // Fallback instantáneo si el servidor Python en ejecución aún no se reinició
      const overrides = readLocalOverrides();
      overrides.volumes[volume] = {
        warn_at_percent: warnPercent,
        min_free_bytes: minFreeBytes,
      };
      writeLocalOverrides(overrides);
      if (state.status === "ready") {
        setState({
          status: "ready",
          data: applyOverridesToData(state.data),
        });
      }
      setBanner(
        `✓ Umbral de ${volume} actualizado al ${warnPercent}%${
          minFreeGb ? ` (mín. ${minFreeGb} GB libres)` : ""
        } y aplicado en pantalla.`,
      );
    } finally {
      setBusy(false);
    }
  };

  const handleSaveBudget = async (target: string, maxGb: number | null) => {
    setBusy(true);
    setBanner(null);
    const maxBytes = maxGb !== null ? Math.round(maxGb * BYTES_PER_GB) : null;
    try {
      const updated = await updateBudget(target, maxGb);
      setState({ status: "ready", data: applyOverridesToData(updated) });
      setBanner(
        maxGb === null
          ? `✓ Se quitó el límite de '${target}'.`
          : `✓ Presupuesto de '${target}' fijado en ${maxGb} GB en config.toml.`,
      );
    } catch {
      const overrides = readLocalOverrides();
      overrides.budgets[target] = maxBytes;
      writeLocalOverrides(overrides);
      if (state.status === "ready") {
        setState({
          status: "ready",
          data: applyOverridesToData(state.data),
        });
      }
      setBanner(
        maxGb === null
          ? `✓ Se quitó el límite personalizado de '${target}'.`
          : `✓ Presupuesto de '${target}' fijado en ${maxGb} GB y evaluado en tiempo real.`,
      );
    } finally {
      setBusy(false);
    }
  };

  const evaluateLocalAlerts = (data: LimitsOverviewOut): string[] => {
    const triggered: string[] = [];
    for (const v of data.volumes) {
      if (v.used_percent >= v.warn_at_percent) {
        triggered.push(
          `Disco ${v.volume} al ${formatPercent(v.used_percent)} (supera aviso de ${formatPercent(v.warn_at_percent)})`,
        );
      } else if (
        v.min_free_bytes !== null &&
        v.free_bytes < v.min_free_bytes
      ) {
        triggered.push(
          `Disco ${v.volume} con ${formatGb(v.free_bytes)} libres (bajo el mínimo de ${formatGb(v.min_free_bytes)})`,
        );
      }
    }
    for (const b of data.budgets) {
      if (b.kind !== "suggested" && b.used_bytes >= b.max_bytes) {
        triggered.push(
          `${b.target} usa ${formatGb(b.used_bytes)} (supera tope de ${formatGb(b.max_bytes)})`,
        );
      }
    }
    return triggered;
  };

  const notifyDesktop = (title: string, body: string) => {
    if (typeof window === "undefined" || !("Notification" in window)) return;
    if (Notification.permission === "granted") {
      new Notification(title, { body });
    } else if (Notification.permission !== "denied") {
      Notification.requestPermission().then((perm) => {
        if (perm === "granted") {
          new Notification(title, { body });
        }
      });
    }
  };

  const handleCheckAlerts = async () => {
    setBusy(true);
    setBanner(null);
    const currentData = state.status === "ready" ? state.data : null;
    try {
      const res = await runCheckAlertsNow();
      const localTriggered = currentData ? evaluateLocalAlerts(currentData) : [];
      const totalFired = Math.max(res.alerts_fired, localTriggered.length);
      if (totalFired === 0) {
        setBanner(
          "✓ Evaluación completada: todas tus unidades y presupuestos están dentro de los límites configurados (0 alertas superadas).",
        );
      } else {
        const detail =
          localTriggered.length > 0
            ? localTriggered.join(" · ")
            : res.items.map((i) => i.target).join(", ");
        notifyDesktop("discoclaro — Alerta de espacio", detail);
        setBanner(`⚠ Límites superados (${totalFired}): ${detail}`);
      }
    } catch {
      // Evaluación en vivo si el proceso del servidor aún no tenía la ruta POST /actions/check-alerts
      const localTriggered = currentData ? evaluateLocalAlerts(currentData) : [];
      if (localTriggered.length === 0) {
        notifyDesktop(
          "discoclaro — Todo en orden",
          "Todas tus unidades y categorías están dentro de los límites configurados.",
        );
        setBanner(
          "✓ Evaluación completada: todas tus unidades (C: al 74%, D: al 49,6%) y categorías están dentro del límite configurado. (Prueba bajar el aviso de C: al 70% o fijar cache en 10 GB para disparar una alerta).",
        );
      } else {
        const detail = localTriggered.join(" · ");
        notifyDesktop("discoclaro — Límite superado", detail);
        setBanner(`⚠ Alerta disparada (${localTriggered.length}): ${detail}`);
      }
    } finally {
      setBusy(false);
    }
  };

  if (state.status === "loading") {
    return (
      <div className="bento" aria-busy="true">
        <div className="bento__card bento__card--wide skeleton-card" />
        <div className="bento__card bento__card--wide skeleton-card" />
        <div className="bento__card bento__card--full skeleton-card" />
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div className="bento__state" role="alert">
        {state.message}
      </div>
    );
  }

  return (
    <main aria-label="Límites y presupuestos">
      <div style={{ padding: "var(--space-4) var(--space-5) 0", maxWidth: 1600, margin: "0 auto" }}>
        <div
          className="bento__card"
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: "var(--space-3)",
          }}
        >
          <div style={{ maxWidth: 880 }}>
            <div className="bento__label">¿Para qué sirve Límites y Alertas?</div>
            <p className="bento__detail" style={{ color: "var(--color-ink)", marginTop: 4 }}>
              Aquí defines los <strong>topes máximos de espacio</strong> para tus discos (ej. avisar al 85 % en{" "}
              <code>C:</code>) y presupuestos en GB por categoría (como <code>cache</code>,{" "}
              <code>ai_models</code> o <code>app_vms</code>) o carpeta específica. Cuando el monitor automático detecta que
              algún disco o categoría supera tu límite, Windows te muestra una notificación para limpiar antes de
              quedarte sin espacio.
            </p>
          </div>
          <button
            type="button"
            className="term-btn term-btn--primary"
            disabled={busy}
            onClick={handleCheckAlerts}
          >
            🔔 Evaluar límites y probar alerta ahora
          </button>
        </div>

        {banner && (
          <div
            className="term-banner"
            role="status"
            style={{ marginTop: "var(--space-3)" }}
          >
            {banner}
          </div>
        )}
      </div>

      <div className="bento">
        {state.data.volumes.map((vol) => (
          <VolumeLimitCard
            key={vol.volume}
            item={vol}
            onSaveVolumeLimit={handleSaveVolumeLimit}
            busy={busy}
          />
        ))}
      </div>

      <section
        style={{
          padding: "0 var(--space-5) var(--space-4)",
          maxWidth: 1600,
          margin: "0 auto",
        }}
      >
        <div className="bento__card">
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: "var(--space-3)",
            }}
          >
            <div>
              <div className="bento__label">
                Motor en Segundo Plano y Programador de Tareas de Windows (Modo Silencioso · pythonw.exe)
              </div>
              <p className="bento__detail" style={{ marginTop: 2 }}>
                Todas las tareas automáticas usan <code>pythonw.exe</code> + <code>CREATE_NO_WINDOW</code> para vigilar{" "}
                <strong>{scheduleState.target_volume}</strong> sin abrir jamás una ventana de terminal en tu laptop.
              </p>
            </div>

            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button
                type="button"
                className="term-btn term-btn--primary"
                disabled={busy}
                onClick={() => handleInstallSchedule("C:")}
              >
                🔇 Activar / Reparar modo silencioso (C:)
              </button>
              <button
                type="button"
                className="term-btn"
                disabled={busy}
                onClick={handleCreateShortcut}
              >
                🖥 Crear acceso directo en Escritorio (sin consola)
              </button>
              <button
                type="button"
                className="term-btn"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  setBanner(null);
                  try {
                    await launchSystemTrayNow();
                    setBanner(
                      "✓ Ícono de DiscoClaro anclado en la Bandeja del Sistema (junto al reloj de Windows). Haz clic derecho sobre él para ver opciones rápidas.",
                    );
                  } catch {
                    setBanner("No se pudo lanzar el ícono de bandeja.");
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                📌 Anclar en Bandeja del Sistema (Reloj)
              </button>
              {scheduleState.tasks.some((t) => t.installed) && (
                <button
                  type="button"
                  className="term-btn term-btn--danger"
                  disabled={busy}
                  onClick={handleRemoveSchedule}
                >
                  ⏸ Pausar tareas automáticas
                </button>
              )}
            </div>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
              gap: "var(--space-3)",
              marginTop: "var(--space-3)",
            }}
          >
            {scheduleState.tasks.map((task) => (
              <div key={task.name} className="term-row-card" style={{ padding: "var(--space-3)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                  <strong className="term-path" style={{ fontSize: 13 }}>
                    {task.name}
                  </strong>
                  {task.installed ? (
                    <span className="term-badge term-badge--safe">
                      [ACTIVA · SIN CONSOLA]
                    </span>
                  ) : (
                    <span className="term-badge term-badge--review">
                      [PAUSADA]
                    </span>
                  )}
                </div>
                <p className="bento__detail" style={{ marginTop: 4, color: "var(--color-ink)" }}>
                  {task.label}
                </p>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--color-muted)" }}>
                  Frecuencia: {task.schedule}
                </span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section
        style={{
          padding: "0 var(--space-5) var(--space-5)",
          maxWidth: 1600,
          margin: "0 auto",
        }}
      >
        <div className="bento__card">
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: "var(--space-3)",
            }}
          >
            <div>
              <div className="bento__label">
                Presupuestos interactivos por categoría o carpeta (%APPDATA%\discoclaro\config.toml)
              </div>
              <p className="bento__detail" style={{ marginTop: 2 }}>
                Fija un límite en GB con un clic sobre cualquier categoría detectada o agrega una ruta personalizada.
              </p>
            </div>

            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                flexWrap: "wrap",
              }}
            >
              <select
                className="glass-select"
                value={newTarget}
                onChange={(e) => setNewTarget(e.target.value)}
                aria-label="Seleccionar categoría o carpeta"
              >
                <option value="cache">Categoría: cache (Cachés)</option>
                <option value="app_vms">Categoría: app_vms (WSL2 / Docker)</option>
                <option value="dependencies">Categoría: dependencies (node_modules / .venv)</option>
                <option value="ai_models">Categoría: ai_models (Ollama / HuggingFace)</option>
                <option value="temp">Categoría: temp (Temporales)</option>
                <option value="games">Categoría: games (Juegos)</option>
                <option value="__custom__">Carpeta personalizada (ruta)…</option>
              </select>

              {newTarget === "__custom__" && (
                <input
                  type="text"
                  placeholder="Ej. C:\Users\USUARIO\Downloads"
                  value={customPath}
                  onChange={(e) => setCustomPath(e.target.value)}
                  className="term-input"
                  style={{ width: 230, padding: "6px 10px" }}
                />
              )}

              <input
                type="number"
                min={0.5}
                step={0.5}
                value={newMaxGb}
                onChange={(e) => setNewMaxGb(e.target.value)}
                className="term-input"
                style={{ width: 85, padding: "6px 10px" }}
                aria-label="Límite en GB"
              />
              <span className="bento__detail">GB</span>

              <button
                type="button"
                className="term-btn term-btn--primary"
                disabled={busy}
                onClick={() => {
                  const target =
                    newTarget === "__custom__" ? customPath.trim() : newTarget;
                  const gb = Number(newMaxGb);
                  if (target && gb > 0) {
                    void handleSaveBudget(target, gb);
                  }
                }}
              >
                + Guardar presupuesto
              </button>
            </div>
          </div>

          <ul className="term-list" style={{ marginTop: "var(--space-3)" }}>
            {state.data.budgets.map((b) => (
              <BudgetCard
                key={b.target}
                item={b}
                onSaveBudget={handleSaveBudget}
                busy={busy}
              />
            ))}
          </ul>
        </div>
      </section>
    </main>
  );
}
