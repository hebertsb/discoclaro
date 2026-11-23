import { useEffect, useState } from "react";
import {
  ApiError,
  compactVhdxNow,
  fetchCleanupHistory,
  fetchDevProjects,
  fetchMigrationAssistant,
  fetchVhdxDisks,
  openInExplorer,
  trashPaths,
} from "../api/client";
import type {
  CleanupHistoryOut,
  DevProjectOut,
  DevProjectsOverviewOut,
  MigrationAssistantOut,
  VhdxDiskOut,
} from "../api/types";
import { formatGb, formatPercent } from "../format";
import "../styles/bento.css";
import "../styles/terminal.css";

type SubTab = "projects" | "vhdx" | "migration" | "history";

function downloadTextFile(filename: string, content: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function LimpiezaAvanzada() {
  const [tab, setTab] = useState<SubTab>("projects");
  const [staleDays, setStaleDays] = useState(30);
  const [onlyStale, setOnlyStale] = useState(false);

  const [projectsData, setProjectsData] =
    useState<DevProjectsOverviewOut | null>(null);
  const [projectsLoading, setProjectsLoading] = useState(true);

  const [vhdxList, setVhdxList] = useState<VhdxDiskOut[]>([]);
  const [vhdxLoading, setVhdxLoading] = useState(true);

  const [migrationData, setMigrationData] =
    useState<MigrationAssistantOut | null>(null);
  const [selectedMigIds, setSelectedMigIds] = useState<Set<string>>(new Set());

  const [historyData, setHistoryData] = useState<CleanupHistoryOut | null>(
    null,
  );

  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const loadProjects = (days = staleDays) => {
    setProjectsLoading(true);
    fetchDevProjects(days)
      .then((res) => setProjectsData(res))
      .catch(() => {
        setProjectsData({
          total_reclaimable_bytes: 0,
          stale_reclaimable_bytes: 0,
          projects: [],
        });
      })
      .finally(() => setProjectsLoading(false));
  };

  const loadVhdx = () => {
    setVhdxLoading(true);
    fetchVhdxDisks()
      .then((res) => setVhdxList(res))
      .catch(() => setVhdxList([]))
      .finally(() => setVhdxLoading(false));
  };

  const loadMigration = () => {
    fetchMigrationAssistant()
      .then((res) => {
        setMigrationData(res);
        setSelectedMigIds(new Set(res.candidates.map((c) => c.id)));
      })
      .catch(() => setMigrationData(null));
  };

  const loadHistory = () => {
    fetchCleanupHistory()
      .then((res) => setHistoryData(res))
      .catch(() =>
        setHistoryData({ total_freed_bytes: 0, total_actions: 0, entries: [] }),
      );
  };

  useEffect(() => {
    loadProjects(staleDays);
    loadVhdx();
    loadMigration();
    loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleCleanProjectArtifacts(project: DevProjectOut) {
    const paths = project.artifacts.map((a) => a.path);
    if (paths.length === 0) return;
    setBusyKey(project.root_path);
    setBanner(null);
    try {
      const res = await trashPaths(paths);
      setBanner(
        `✓ Compilados y dependencias de «${project.name}» enviados a la Papelera (${formatGb(res.total_freed_bytes)} recuperados). Tu código fuente quedó intacto.`,
      );
      loadProjects(staleDays);
      loadHistory();
    } catch (error: unknown) {
      setBanner(
        error instanceof ApiError && error.detail === "action_declined"
          ? "Cancelaste la confirmación en la ventana nativa de Windows."
          : "No se pudieron enviar los artefactos a la Papelera.",
      );
    } finally {
      setBusyKey(null);
    }
  }

  async function handleCleanAllStaleProjects() {
    if (!projectsData) return;
    const stalePaths = projectsData.projects
      .filter((p) => p.is_stale)
      .flatMap((p) => p.artifacts.map((a) => a.path));
    if (stalePaths.length === 0) return;
    setBusyKey("__all_stale__");
    setBanner(null);
    try {
      const res = await trashPaths(stalePaths);
      setBanner(
        `✓ Limpieza masiva completada: ${stalePaths.length} carpetas compiladas de proyectos antiguos enviadas a la Papelera (${formatGb(res.total_freed_bytes)} liberados).`,
      );
      loadProjects(staleDays);
      loadHistory();
    } catch (error: unknown) {
      setBanner(
        error instanceof ApiError && error.detail === "action_declined"
          ? "Cancelaste la confirmación en la ventana nativa de Windows."
          : "No se pudo completar la limpieza masiva.",
      );
    } finally {
      setBusyKey(null);
    }
  }

  async function handleCompactVhdx(disk: VhdxDiskOut) {
    setBusyKey(disk.path);
    setBanner(null);
    try {
      const res = await compactVhdxNow(disk.path);
      if (res.succeeded) {
        setBanner(
          `✓ Disco virtual compactado (${disk.name}): de ${formatGb(res.before_bytes)} a ${formatGb(res.after_bytes)} · Espacio real devuelto a Windows: ${formatGb(res.freed_bytes)}.`,
        );
      } else {
        setBanner(
          `⚠ Compactación finalizada con aviso (${res.detail}). Tamaño actual: ${formatGb(res.after_bytes)}.`,
        );
      }
      loadVhdx();
      loadHistory();
    } catch (error: unknown) {
      setBanner(
        error instanceof ApiError && error.detail === "action_declined"
          ? "Cancelaste la compactación en la ventana nativa de Windows."
          : "No se pudo compactar el archivo .vhdx.",
      );
    } finally {
      setBusyKey(null);
    }
  }

  function handleExportCsv() {
    if (!historyData) return;
    const header = "Fecha,Tipo,Objetivo,Bytes_Liberados,Detalle\n";
    const rows = historyData.entries
      .map(
        (e) =>
          `"${e.timestamp}","${e.action_type}","${e.target.replaceAll('"', '""')}",${e.freed_bytes},"${e.detail.replaceAll('"', '""')}"`,
      )
      .join("\n");
    downloadTextFile(
      "discoclaro-historial-limpieza.csv",
      header + rows,
      "text/csv;charset=utf-8",
    );
  }

  function handleExportHtml() {
    if (!historyData) return;
    const rowsHtml = historyData.entries
      .map(
        (e) =>
          `<tr><td>${new Date(e.timestamp).toLocaleString("es")}</td><td>${e.action_type}</td><td><code>${e.target}</code></td><td><strong>${formatGb(e.freed_bytes)}</strong></td><td>${e.detail}</td></tr>`,
      )
      .join("");
    const html = `<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Reporte DiscoClaro</title><style>body{font-family:system-ui,sans-serif;background:#0f131d;color:#f1f5f9;padding:32px}h1{color:#ff622e}table{width:100%;border-collapse:collapse;margin-top:20px}th,td{border:1px solid #334155;padding:10px;text-align:left}th{background:#1e293b}</style></head><body><h1>Reporte de Espacio Recuperado — discoclaro</h1><p>Total acumulado recuperado: <strong>${formatGb(historyData.total_freed_bytes)}</strong> en ${historyData.total_actions} acción(es).</p><table><thead><tr><th>Fecha</th><th>Acción</th><th>Ruta / Objetivo</th><th>Recuperado</th><th>Detalle</th></tr></thead><tbody>${rowsHtml}</tbody></table></body></html>`;
    downloadTextFile(
      "discoclaro-reporte.html",
      html,
      "text/html;charset=utf-8",
    );
  }

  const visibleProjects = (projectsData?.projects ?? []).filter((p) =>
    onlyStale ? p.is_stale : true,
  );
  const staleCount = (projectsData?.projects ?? []).filter(
    (p) => p.is_stale,
  ).length;

  const selectedMigBytes = (migrationData?.candidates ?? [])
    .filter((c) => selectedMigIds.has(c.id))
    .reduce((sum, c) => sum + c.size_bytes, 0);
  const simulatedSourcePct =
    migrationData && migrationData.source_total_bytes > 0
      ? Math.max(
          0,
          ((migrationData.source_used_bytes - selectedMigBytes) /
            migrationData.source_total_bytes) *
            100,
        )
      : 0;

  return (
    <main
      style={{ padding: "var(--space-5)", maxWidth: 1600, margin: "0 auto" }}
      aria-label="Limpieza avanzada, proyectos y compactación"
    >
      {/* Barra Glassy V7 con las 4 herramientas avanzadas */}
      <section
        className="bento__card"
        style={{ marginBottom: "var(--space-4)", padding: "var(--space-4)" }}
      >
        <div className="glass-toolbar">
          <div className="glass-tabs" role="tablist" aria-label="Herramientas avanzadas">
            <button
              type="button"
              className={`glass-tab${tab === "projects" ? " glass-tab--active" : ""}`}
              onClick={() => setTab("projects")}
            >
              🗂 Proyectos Antiguos ({projectsData?.projects.length ?? 0})
            </button>
            <button
              type="button"
              className={`glass-tab${tab === "vhdx" ? " glass-tab--active" : ""}`}
              onClick={() => setTab("vhdx")}
            >
              🗜 Compactar WSL2 / Docker ({vhdxList.length})
            </button>
            <button
              type="button"
              className={`glass-tab${tab === "migration" ? " glass-tab--active" : ""}`}
              onClick={() => setTab("migration")}
            >
              ⇄ Asistente C: → D: ({migrationData?.candidates.length ?? 0})
            </button>
            <button
              type="button"
              className={`glass-tab${tab === "history" ? " glass-tab--active" : ""}`}
              onClick={() => setTab("history")}
            >
              📊 Historial Recuperado ({formatGb(historyData?.total_freed_bytes ?? 0)})
            </button>
          </div>

          <span className="term-badge term-badge--safe">
            [CÓDIGO FUENTE PROTEGIDO · SOLO COMPILADOS Y CACHÉS]
          </span>
        </div>

        {banner && (
          <div className="term-banner" role="status" style={{ marginTop: 12 }}>
            {banner}
          </div>
        )}
      </section>

      {/* PESTAÑA 1: PROYECTOS ANTIGUOS (node_modules, .venv, dist, build, target) */}
      {tab === "projects" && (
        <section className="bento__card" aria-label="Proyectos de desarrollo antiguos">
          <div className="glass-toolbar">
            <div>
              <div className="bento__label">
                Escáner de Proyectos de Programación (C: y D:) · Solo carpetas compiladas y dependencias
              </div>
              <p className="bento__detail" style={{ marginTop: 4 }}>
                Total en artefactos regenerables (<code>node_modules</code>, <code>.venv</code>,{" "}
                <code>dist</code>, <code>build</code>, <code>target</code>, <code>.next</code>):{" "}
                <strong style={{ color: "var(--color-accent)" }}>
                  {formatGb(projectsData?.total_reclaimable_bytes ?? 0)}
                </strong>{" "}
                · En proyectos antiguos (+{staleDays} días sin tocar):{" "}
                <strong style={{ color: "var(--color-risk-safe)" }}>
                  {formatGb(projectsData?.stale_reclaimable_bytes ?? 0)}
                </strong>
              </p>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                <span>Antiguo si supera:</span>
                <select
                  className="glass-select"
                  value={staleDays}
                  onChange={(e) => {
                    const val = Number(e.target.value);
                    setStaleDays(val);
                    loadProjects(val);
                  }}
                >
                  <option value={7}>7 días sin tocar</option>
                  <option value={15}>15 días sin tocar</option>
                  <option value={30}>30 días sin tocar</option>
                  <option value={60}>60 días sin tocar</option>
                  <option value={90}>90 días sin tocar</option>
                </select>
              </label>

              <button
                type="button"
                className={`term-btn${onlyStale ? " term-btn--primary" : ""}`}
                onClick={() => setOnlyStale((v) => !v)}
              >
                {onlyStale
                  ? `Mostrando solo antiguos (${staleCount})`
                  : `Filtrar solo antiguos (${staleCount})`}
              </button>

              {staleCount > 0 && (
                <button
                  type="button"
                  className="term-btn term-btn--danger"
                  disabled={busyKey !== null}
                  onClick={handleCleanAllStaleProjects}
                >
                  {busyKey === "__all_stale__"
                    ? "Limpiando proyectos antiguos…"
                    : `⚡ Limpiar compilados de todos los antiguos (${formatGb(projectsData?.stale_reclaimable_bytes ?? 0)})`}
                </button>
              )}
            </div>
          </div>

          {projectsLoading ? (
            <div className="skeleton-block" style={{ height: 220, marginTop: 14 }} />
          ) : visibleProjects.length === 0 ? (
            <p className="bento__detail" style={{ marginTop: 14 }}>
              No se encontraron proyectos con carpetas compiladas pesadas en este filtro.
            </p>
          ) : (
            <ul className="term-list" style={{ marginTop: "var(--space-3)" }}>
              {visibleProjects.map((proj) => (
                <li key={proj.root_path} className="term-row-card">
                  <div className="term-row-card__header">
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                        {proj.is_stale ? (
                          <span className="term-badge term-badge--danger">
                            [PROYECTO ANTIGUO · {proj.days_inactive} DÍAS SIN TOCAR]
                          </span>
                        ) : (
                          <span className="term-badge term-badge--safe">
                            [ACTIVO · HACE {proj.days_inactive} DÍA{proj.days_inactive === 1 ? "" : "S"}]
                          </span>
                        )}
                        <span className="term-badge term-badge--info">
                          {proj.ecosystem}
                        </span>
                        <strong style={{ fontSize: 16 }}>{proj.name}</strong>
                      </div>
                      <div className="term-path" style={{ marginTop: 4, color: "var(--color-text-secondary)" }}>
                        {proj.root_path}
                      </div>
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
                        {proj.artifacts.map((art) => (
                          <span
                            key={art.path}
                            className="term-badge term-badge--review"
                            title={art.path}
                          >
                            {art.name}: {formatGb(art.size_bytes)}
                          </span>
                        ))}
                      </div>
                    </div>

                    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8 }}>
                      <strong
                        style={{
                          fontFamily: "var(--font-mono)",
                          fontSize: 20,
                          color: "var(--color-accent)",
                        }}
                      >
                        {formatGb(proj.reclaimable_bytes)}
                      </strong>
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                        <button
                          type="button"
                          className="term-btn"
                          onClick={() => openInExplorer(proj.root_path).catch(() => {})}
                        >
                          Abrir proyecto →
                        </button>
                        <button
                          type="button"
                          className="term-btn term-btn--danger"
                          disabled={busyKey !== null}
                          onClick={() => handleCleanProjectArtifacts(proj)}
                        >
                          {busyKey === proj.root_path
                            ? "Enviando a Papelera…"
                            : "🗑 Eliminar compilados/dependencias"}
                        </button>
                      </div>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {/* PESTAÑA 2: COMPACTADOR DE DISCOS VIRTUALES (.VHDX) */}
      {tab === "vhdx" && (
        <section className="bento__card" aria-label="Compactador de discos virtuales WSL2 y Docker">
          <div className="bento__label">
            Compactador Asistido de Discos Virtuales (.vhdx · Docker Desktop y WSL2)
          </div>
          <p className="bento__detail" style={{ marginTop: 4 }}>
            Cuando borras imágenes de Docker o archivos en Linux (WSL2), el archivo{" "}
            <code>.vhdx</code> en Windows <strong>no se encoge automáticamente</strong>. Este
            compactador apaga WSL de forma segura (<code>wsl --shutdown</code>) y compacta los
            bloques vacíos para devolver GB reales a tu disco sin perder tus datos.
          </p>

          {vhdxLoading ? (
            <div className="skeleton-block" style={{ height: 140, marginTop: 14 }} />
          ) : vhdxList.length === 0 ? (
            <p className="bento__detail" style={{ marginTop: 14 }}>
              No se encontraron archivos <code>.vhdx</code> de Docker Desktop o WSL2 en este usuario.
            </p>
          ) : (
            <ul className="term-list" style={{ marginTop: "var(--space-3)" }}>
              {vhdxList.map((disk) => (
                <li key={disk.path} className="term-row-card">
                  <div className="term-row-card__header">
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                        <span className="term-badge term-badge--review">
                          [{disk.engine.toUpperCase()}]
                        </span>
                        <strong style={{ fontSize: 15 }}>{disk.name}</strong>
                      </div>
                      <div className="term-path" style={{ marginTop: 4 }}>
                        {disk.path}
                      </div>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                      <strong
                        style={{
                          fontFamily: "var(--font-mono)",
                          fontSize: 19,
                          color: "var(--color-accent)",
                        }}
                      >
                        {formatGb(disk.size_bytes)}
                      </strong>
                      <button
                        type="button"
                        className="term-btn"
                        onClick={() => openInExplorer(disk.path).catch(() => {})}
                      >
                        Ver en Explorador →
                      </button>
                      <button
                        type="button"
                        className="term-btn term-btn--primary"
                        disabled={busyKey !== null}
                        onClick={() => handleCompactVhdx(disk)}
                      >
                        {busyKey === disk.path
                          ? "Compactando .vhdx…"
                          : "🗜 Compactar disco virtual ahora"}
                      </button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {/* PESTAÑA 3: ASISTENTE DE MIGRACIÓN C: -> D: */}
      {tab === "migration" && migrationData && (
        <section className="bento__card" aria-label="Asistente de migración entre unidades">
          <div className="bento__label">
            Simulador y Asistente de Migración ({migrationData.source_volume} →{" "}
            {migrationData.target_volume ?? "D:"})
          </div>
          <p className="bento__detail" style={{ marginTop: 4 }}>
            Marca los elementos pesados que desees trasladar de{" "}
            <strong>{migrationData.source_volume}</strong> a{" "}
            <strong>{migrationData.target_volume ?? "tu segundo disco"}</strong> para ver cuánto
            bajaría la ocupación de tu disco del sistema.
          </p>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
              gap: "var(--space-3)",
              marginTop: "var(--space-3)",
            }}
          >
            <div className="term-row-card">
              <div className="bento__label">
                Disco {migrationData.source_volume} (Actual → Simulado)
              </div>
              <p className="bento__value" style={{ fontSize: 24, marginTop: 4 }}>
                {formatPercent(migrationData.source_used_percent)} →{" "}
                <span style={{ color: "var(--color-risk-safe)" }}>
                  {formatPercent(simulatedSourcePct)}
                </span>
              </p>
              <p className="bento__detail">
                Liberarías{" "}
                <strong style={{ color: "var(--color-accent)" }}>
                  {formatGb(selectedMigBytes)}
                </strong>{" "}
                en {migrationData.source_volume}
              </p>
              <div className="bento__bar">
                <div
                  className="bento__bar-fill"
                  style={{
                    width: `${Math.min(simulatedSourcePct, 100)}%`,
                    background: "var(--color-risk-safe)",
                  }}
                />
              </div>
            </div>

            {migrationData.target_volume && (
              <div className="term-row-card">
                <div className="bento__label">
                  Disco destino {migrationData.target_volume}
                </div>
                <p className="bento__value" style={{ fontSize: 24, marginTop: 4 }}>
                  {formatGb(migrationData.target_free_bytes)} libres
                </p>
                <p className="bento__detail">
                  Espacio sobrante después de mover:{" "}
                  <strong>
                    {formatGb(
                      Math.max(0, migrationData.target_free_bytes - selectedMigBytes),
                    )}
                  </strong>
                </p>
                <button
                  type="button"
                  className="term-btn"
                  style={{ marginTop: 8, alignSelf: "flex-start" }}
                  onClick={() =>
                    openInExplorer(`${migrationData.target_volume}\\`).catch(() => {})
                  }
                >
                  Abrir {migrationData.target_volume}\ en Explorador →
                </button>
              </div>
            )}
          </div>

          <ul className="term-list" style={{ marginTop: "var(--space-3)" }}>
            {migrationData.candidates.map((cand) => {
              const checked = selectedMigIds.has(cand.id);
              return (
                <li key={cand.id} className="term-row-card">
                  <div className="term-row-card__header">
                    <label
                      style={{
                        display: "flex",
                        alignItems: "flex-start",
                        gap: 10,
                        cursor: "pointer",
                        flex: 1,
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => {
                          setSelectedMigIds((prev) => {
                            const next = new Set(prev);
                            if (next.has(cand.id)) next.delete(cand.id);
                            else next.add(cand.id);
                            return next;
                          });
                        }}
                        style={{ marginTop: 4 }}
                      />
                      <div>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                          <span className="term-badge term-badge--info">
                            {cand.category}
                          </span>
                          <strong style={{ fontSize: 15 }}>{cand.title}</strong>
                        </div>
                        <div className="term-path" style={{ marginTop: 4 }}>
                          {cand.source_path} → <strong>{cand.suggested_destination}</strong>
                        </div>
                        <p className="bento__detail" style={{ marginTop: 4 }}>
                          👉 {cand.how_to_move}
                        </p>
                      </div>
                    </label>

                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <strong
                        style={{
                          fontFamily: "var(--font-mono)",
                          fontSize: 18,
                          color: "var(--color-accent)",
                        }}
                      >
                        {formatGb(cand.size_bytes)}
                      </strong>
                      <button
                        type="button"
                        className="term-btn"
                        onClick={() => openInExplorer(cand.source_path).catch(() => {})}
                      >
                        Abrir origen →
                      </button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {/* PESTAÑA 4: HISTORIAL Y AUDITORÍA DE ESPACIO RECUPERADO */}
      {tab === "history" && (
        <section className="bento__card" aria-label="Historial de espacio recuperado">
          <div className="glass-toolbar">
            <div>
              <div className="bento__label">
                Auditoría de Espacio Recuperado con DiscoClaro
              </div>
              <p className="bento__value" style={{ color: "var(--color-risk-safe)" }}>
                {formatGb(historyData?.total_freed_bytes ?? 0)} recuperados en total
              </p>
              <p className="bento__detail">
                Registro histórico de todas tus limpiezas ({historyData?.total_actions ?? 0} acciones
                guardadas en <code>%LOCALAPPDATA%\discoclaro\cleanup_history.json</code>).
              </p>
            </div>

            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button
                type="button"
                className="term-btn"
                onClick={handleExportCsv}
              >
                ⬇ Exportar CSV
              </button>
              <button
                type="button"
                className="term-btn term-btn--primary"
                onClick={handleExportHtml}
              >
                ⬇ Exportar Reporte HTML
              </button>
            </div>
          </div>

          {!historyData || historyData.entries.length === 0 ? (
            <p className="bento__detail" style={{ marginTop: 14 }}>
              Todavía no hay limpiezas registradas. Cada vez que limpies cachés, compilados de
              proyectos antiguos, restos de programas o compactes un <code>.vhdx</code>, aparecerá
              aquí automáticamente.
            </p>
          ) : (
            <ul className="term-list" style={{ marginTop: "var(--space-3)" }}>
              {historyData.entries.map((entry, idx) => (
                <li key={`${entry.timestamp}-${idx}`} className="term-row-card">
                  <div className="term-row-card__header">
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                        <span className="term-badge term-badge--safe">
                          [{entry.action_type.toUpperCase()}]
                        </span>
                        <strong className="term-path" style={{ fontSize: 14 }}>
                          {entry.target}
                        </strong>
                      </div>
                      <p className="bento__detail" style={{ marginTop: 4 }}>
                        {new Date(entry.timestamp).toLocaleString("es")} · {entry.detail}
                      </p>
                    </div>
                    <strong
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 17,
                        color: "var(--color-risk-safe)",
                      }}
                    >
                      +{formatGb(entry.freed_bytes)}
                    </strong>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </main>
  );
}
