import { useEffect, useState } from "react";
import {
  ApiError,
  cleanSafeBatch,
  fetchDiskHealth,
  fetchRecommendations,
  fetchSummary,
  openInExplorer,
  readSmartElevatedNow,
  runTerminalCommand,
  scanVolumeNow,
  trashPaths,
} from "../api/client";
import type {
  PhysicalDiskHealthOut,
  PlanLevel,
  RecommendationOut,
  SummaryOut,
  VolumeSummaryOut,
} from "../api/types";
import { formatDays, formatGb, formatPercent } from "../format";
import "../styles/bento.css";
import "../styles/terminal.css";

type Loadable<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

function freeRatioColor(usedPercent: number): string {
  if (usedPercent >= 90) return "var(--color-risk-no-touch)";
  if (usedPercent >= 75) return "var(--color-risk-review)";
  return "var(--color-risk-safe)";
}

function CircularGauge({ percent, color }: { percent: number; color: string }) {
  const radius = 34;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.min(Math.max(percent, 0), 100);
  const offset = circumference - (clamped / 100) * circumference;

  return (
    <svg width="86" height="86" viewBox="0 0 86 86" aria-hidden="true" style={{ flexShrink: 0 }}>
      <circle
        cx="43"
        cy="43"
        r={radius}
        fill="none"
        stroke="rgba(255,255,255,0.16)"
        strokeWidth="8"
      />
      <circle
        cx="43"
        cy="43"
        r={radius}
        fill="none"
        stroke={color}
        strokeWidth="8"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={offset}
        transform="rotate(-90 43 43)"
      />
      <text
        x="43"
        y="47"
        textAnchor="middle"
        fill="currentColor"
        fontSize="13"
        fontWeight="700"
        fontFamily="var(--font-mono)"
      >
        {Math.round(clamped)}%
      </text>
    </svg>
  );
}

function DiskCard({
  row,
  hero,
  scanningVolume,
  onScanVolume,
  onSelectVolume,
}: {
  row: VolumeSummaryOut;
  hero: boolean;
  scanningVolume: string | null;
  onScanVolume: (volumeName: string) => void;
  onSelectVolume: (volumeName: string) => void;
}) {
  const { volume } = row;
  const usedPercent = (volume.used_bytes / volume.total_bytes) * 100;
  const freePercent = 100 - usedPercent;
  const color = hero ? "#ffffff" : freeRatioColor(usedPercent);
  const isScanning = scanningVolume === volume.name;

  return (
    <section
      className={`bento__card${hero ? " bento__card--hero bento__card--accent" : " bento__card--wide"}`}
      aria-label={`Disco ${volume.name}`}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "var(--space-3)" }}>
        <div>
          <div className="bento__label">
            <span>●</span> Unidad {volume.name}
          </div>
          <p className={`bento__value${hero ? " bento__value--hero" : ""}`} style={{ marginTop: 6 }}>
            {formatGb(volume.free_bytes)} libres
          </p>
          <p className="bento__detail" style={{ marginTop: 4 }}>
            {formatGb(volume.used_bytes)} usados de {formatGb(volume.total_bytes)} ·{" "}
            {formatPercent(freePercent)} disponible
          </p>
        </div>
        <CircularGauge percent={usedPercent} color={color} />
      </div>

      <div>
        <div
          className="bento__bar"
          role="img"
          aria-label={`${formatPercent(usedPercent)} usado de ${volume.name}`}
        >
          <div
            className="bento__bar-fill"
            style={{ width: `${usedPercent}%`, background: color }}
          />
        </div>

        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: "var(--space-2)",
            marginTop: 10,
          }}
        >
          {row.snapshot_id === null ? (
            <span className="bento__detail" style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
              Sin foto guardada aún
            </span>
          ) : (
            <span className="bento__detail" style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
              Foto #{row.snapshot_id} ·{" "}
              {row.taken_at ? new Date(row.taken_at).toLocaleString("es") : "registrada"}
            </span>
          )}

          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {!hero && (
              <button
                type="button"
                className="term-btn"
                onClick={() => onSelectVolume(volume.name)}
              >
                Inspeccionar {volume.name}
              </button>
            )}
            <button
              type="button"
              className="term-btn"
              onClick={() => openInExplorer(`${volume.name}\\`).catch(() => {})}
            >
              Explorador
            </button>
            <button
              type="button"
              className={`term-btn${hero ? "" : " term-btn--primary"}`}
              disabled={scanningVolume !== null}
              onClick={() => onScanVolume(volume.name)}
            >
              {isScanning ? "Analizando…" : `↻ Analizar ${volume.name}`}
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

function UnexplainedCard({ row }: { row: VolumeSummaryOut }) {
  if (row.unexplained === null) {
    return (
      <section className="bento__card bento__card--wide" aria-label="Espacio no explicado">
        <div className="bento__label">Espacio no explicado ({row.volume.name})</div>
        <p className="bento__value" style={{ fontSize: 22 }}>
          Sin medir
        </p>
        <p className="bento__detail">
          Pulsa «Analizar {row.volume.name}» para escanear la unidad y detectar carpetas ocultas o protegidas.
        </p>
      </section>
    );
  }
  const { unexplained } = row;
  return (
    <section className="bento__card bento__card--wide" aria-label="Espacio no explicado">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div className="bento__label">Espacio no explicado ({row.volume.name})</div>
        {unexplained.exceeds_threshold ? (
          <span className="term-badge term-badge--review">[REVISAR CON --ADMIN]</span>
        ) : (
          <span className="term-badge term-badge--safe">[NORMAL]</span>
        )}
      </div>
      <p className="bento__value">{formatGb(unexplained.size_bytes)}</p>
      <p className="bento__detail">
        {formatPercent(unexplained.percent_of_used)} de lo usado en {row.volume.name} fuera de carpetas visibles
      </p>
    </section>
  );
}

function ForecastCard({ row }: { row: VolumeSummaryOut }) {
  return (
    <section className="bento__card bento__card--wide" aria-label="Días hasta llenarse">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div className="bento__label">Pronóstico de llenado ({row.volume.name})</div>
        <span className="term-badge term-badge--safe">[TENDENCIA]</span>
      </div>
      {row.days_until_full === null ? (
        <>
          <p className="bento__value" style={{ fontSize: 24 }}>
            Estable
          </p>
          <p className="bento__detail">
            El uso no crece o aún faltan fotos periódicas para proyectar una fecha de llenado.
          </p>
        </>
      ) : (
        <>
          <p className="bento__value">~{formatDays(row.days_until_full)}</p>
          <p className="bento__detail">al ritmo actual de crecimiento entre fotos</p>
        </>
      )}
    </section>
  );
}

function GrowthCard({ row }: { row: VolumeSummaryOut }) {
  return (
    <section className="bento__card bento__card--wide" aria-label="Qué más creció">
      <div className="bento__label">Mayor crecimiento reciente ({row.volume.name})</div>
      {row.top_growth === null ? (
        <p className="bento__detail">
          Toma una segunda foto después de unos días para detectar qué carpeta crece en silencio.
        </p>
      ) : (
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
          <div style={{ minWidth: 0 }}>
            <p className="bento__value" style={{ color: "var(--color-accent)", fontSize: 26 }}>
              +{formatGb(row.top_growth.grown_bytes)}
            </p>
            <p className="term-path" style={{ marginTop: 6 }}>
              {row.top_growth.path}
            </p>
          </div>
          <button
            type="button"
            className="term-btn"
            onClick={() => openInExplorer(row.top_growth!.path).catch(() => {})}
          >
            Ir a carpeta →
          </button>
        </div>
      )}
    </section>
  );
}

function InteractivePlanSection({
  recommendations,
  onReload,
}: {
  recommendations: Loadable<RecommendationOut[]>;
  onReload: () => void;
}) {
  const [selectedLevel, setSelectedLevel] = useState<PlanLevel>("safe");
  const [busyTarget, setBusyTarget] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  if (recommendations.status === "loading") {
    return (
      <section className="bento__card bento__card--full" aria-label="Plan recomendado">
        <div className="bento__label">Veredicto por niveles (Inspectores en paralelo)</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "var(--space-3)" }}>
          <div className="skeleton-block" style={{ height: 84 }} />
          <div className="skeleton-block" style={{ height: 84 }} />
          <div className="skeleton-block" style={{ height: 84 }} />
        </div>
      </section>
    );
  }
  if (recommendations.status === "error") {
    return (
      <section className="bento__card bento__card--full" aria-label="Plan recomendado">
        <div className="bento__label">Plan recomendado</div>
        <p className="bento__detail">{recommendations.message}</p>
      </section>
    );
  }

  const items = recommendations.data;
  const safeItems = items.filter((i) => i.level === "safe");
  const reviewItems = items.filter((i) => i.level === "review");
  const moveItems = items.filter((i) => i.level === "move");

  const safeBytes = safeItems.reduce((s, i) => s + i.size_bytes, 0);
  const reviewBytes = reviewItems.reduce((s, i) => s + i.size_bytes, 0);
  const moveBytes = moveItems.reduce((s, i) => s + i.size_bytes, 0);
  const totalRecoverable = safeBytes + reviewBytes + moveBytes;

  const activeItems = items.filter((i) => i.level === selectedLevel);

  async function handleExecuteCommand(item: RecommendationOut) {
    if (!item.suggested_command) return;
    setFeedback(null);
    setBusyTarget(item.target);
    try {
      const out = await runTerminalCommand(item.suggested_command);
      setFeedback(
        `✓ Limpieza ejecutada (${out.command}) · código ${out.exit_code}.`,
      );
      onReload();
    } catch (error: unknown) {
      const msg =
        error instanceof ApiError
          ? error.detail === "action_declined"
            ? "Cancelaste la confirmación en la ventana nativa de Windows."
            : `No se pudo ejecutar (${error.detail}).`
          : "Error al ejecutar comando.";
      setFeedback(msg);
    } finally {
      setBusyTarget(null);
    }
  }

  async function handleTrashLocation(item: RecommendationOut) {
    if (!item.location_path) return;
    setFeedback(null);
    setBusyTarget(item.target);
    try {
      const batch = await trashPaths([item.location_path]);
      setFeedback(
        `✓ Enviado a la Papelera · Espacio liberado: ${formatGb(batch.total_freed_bytes)}.`,
      );
      onReload();
    } catch (error: unknown) {
      const msg =
        error instanceof ApiError
          ? error.detail === "action_declined"
            ? "Cancelaste el envío a la Papelera en la ventana nativa de Windows."
            : `No se pudo enviar a la Papelera (${error.detail}).`
          : "Error al enviar a la Papelera.";
      setFeedback(msg);
    } finally {
      setBusyTarget(null);
    }
  }

  async function handleCleanAllSafe() {
    if (safeItems.length === 0) return;
    setFeedback(null);
    setBusyTarget("__batch_safe__");
    try {
      const res = await cleanSafeBatch();
      setFeedback(
        `✓ Limpieza en lote completada: ${res.succeeded_count} caché(s) limpiadas (~${formatGb(res.total_estimated_bytes)} recuperados).`,
      );
      onReload();
    } catch {
      // Fallback secuencial si el proceso de servidor aún no tenía /actions/clean-safe
      let cleaned = 0;
      for (const item of safeItems) {
        try {
          if (item.suggested_command) {
            await runTerminalCommand(item.suggested_command);
            cleaned += 1;
          } else if (item.location_path) {
            await trashPaths([item.location_path]);
            cleaned += 1;
          }
        } catch {
          break;
        }
      }
      if (cleaned > 0) {
        setFeedback(`✓ Se limpiaron ${cleaned} caché(s) seguras.`);
        onReload();
      } else {
        setFeedback("Limpieza en lote cancelada o sin cambios.");
      }
    } finally {
      setBusyTarget(null);
    }
  }

  return (
    <section className="bento__card bento__card--full" aria-label="Plan recomendado">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "var(--space-2)" }}>
        <div>
          <div className="bento__label">Veredicto de recuperación interactivo (`discoclaro plan`)</div>
          <p className="bento__detail">
            Potencial total detectado: <strong style={{ color: "var(--color-text-primary)" }}>{formatGb(totalRecoverable)}</strong>.{" "}
            <strong>Haz clic en cualquiera de los 3 niveles</strong> para ver cada elemento y limpiarlo, eliminarlo o abrir su ubicación para moverlo.
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          {safeItems.length > 0 && (
            <button
              type="button"
              className="term-btn term-btn--primary"
              disabled={busyTarget !== null}
              onClick={handleCleanAllSafe}
            >
              {busyTarget === "__batch_safe__"
                ? "Limpiando cachés seguras…"
                : `⚡ Limpiar todo lo seguro (${formatGb(safeBytes)})`}
            </button>
          )}
          {feedback && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--color-accent)" }}>
              {feedback}
            </span>
          )}
        </div>
      </div>

      {/* 3 tarjetas de nivel clicables */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))",
          gap: "var(--space-3)",
          marginTop: "var(--space-2)",
        }}
      >
        <div
          className="term-row-card"
          role="button"
          tabIndex={0}
          onClick={() => setSelectedLevel("safe")}
          onKeyDown={(e) => e.key === "Enter" && setSelectedLevel("safe")}
          style={{
            cursor: "pointer",
            borderColor: selectedLevel === "safe" ? "var(--color-risk-safe)" : undefined,
            boxShadow: selectedLevel === "safe" ? "0 0 0 1px var(--color-risk-safe)" : undefined,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span className="term-badge term-badge--safe">[01 · SEGURO / CACHÉS]</span>
            <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
              {safeItems.length} ítem{safeItems.length === 1 ? "" : "s"} →
            </span>
          </div>
          <p className="bento__value" style={{ fontSize: 26, color: "var(--color-risk-safe)" }}>
            {formatGb(safeBytes)}
          </p>
          <p className="bento__detail">
            Se regeneran solos sin perder datos. Pulsa para limpiar cachés ahora.
          </p>
        </div>

        <div
          className="term-row-card"
          role="button"
          tabIndex={0}
          onClick={() => setSelectedLevel("review")}
          onKeyDown={(e) => e.key === "Enter" && setSelectedLevel("review")}
          style={{
            cursor: "pointer",
            borderColor: selectedLevel === "review" ? "var(--color-risk-review)" : undefined,
            boxShadow: selectedLevel === "review" ? "0 0 0 1px var(--color-risk-review)" : undefined,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span className="term-badge term-badge--review">[02 · REVISAR / MODELOS Y JUEGOS]</span>
            <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
              {reviewItems.length} ítem{reviewItems.length === 1 ? "" : "s"} →
            </span>
          </div>
          <p className="bento__value" style={{ fontSize: 26, color: "var(--color-risk-review)" }}>
            {formatGb(reviewBytes)}
          </p>
          <p className="bento__detail">
            Archivos grandes que requieren volver a descargar si los borras. Pulsa para gestionarlos.
          </p>
        </div>

        <div
          className="term-row-card"
          role="button"
          tabIndex={0}
          onClick={() => setSelectedLevel("move")}
          onKeyDown={(e) => e.key === "Enter" && setSelectedLevel("move")}
          style={{
            cursor: "pointer",
            borderColor: selectedLevel === "move" ? "var(--color-accent)" : undefined,
            boxShadow: selectedLevel === "move" ? "0 0 0 1px var(--color-accent)" : undefined,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span className="term-badge term-badge--danger">[03 · MOVER A OTRO DISCO]</span>
            <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
              {moveItems.length} ítem{moveItems.length === 1 ? "" : "s"} →
            </span>
          </div>
          <p className="bento__value" style={{ fontSize: 26, color: "var(--color-accent)" }}>
            {formatGb(moveBytes)}
          </p>
          <p className="bento__detail">
            Instalados en C: teniendo biblioteca en D:. Pulsa para ver dónde cambiarlos de unidad.
          </p>
        </div>
      </div>

      {/* Panel desplegado del nivel seleccionado con botones reales de acción */}
      <div
        style={{
          marginTop: "var(--space-3)",
          paddingTop: "var(--space-3)",
          borderTop: "1px solid var(--color-border)",
        }}
      >
        <div className="bento__label" style={{ marginBottom: "var(--space-2)" }}>
          Elementos en el nivel seleccionado ({activeItems.length})
        </div>

        {activeItems.length === 0 ? (
          <p className="bento__detail">No hay elementos pendientes en este nivel.</p>
        ) : (
          <ul className="term-list">
            {activeItems.map((item) => (
              <li key={item.target} className="term-row-card">
                <div className="term-row-card__header">
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <strong className="term-path" style={{ fontSize: 15 }}>
                        {item.program} :: {item.name}
                      </strong>
                      {item.location_path && (
                        <span className="term-path" style={{ color: "var(--color-text-muted)" }}>
                          ({item.location_path})
                        </span>
                      )}
                    </div>
                    <p className="bento__detail" style={{ marginTop: 4 }}>
                      {item.explanation}
                    </p>
                    {item.suggested_note && (
                      <p className="bento__detail" style={{ marginTop: 4, color: "var(--color-text-primary)" }}>
                        👉 <strong>Cómo hacerlo:</strong> {item.suggested_note}
                      </p>
                    )}
                  </div>

                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <strong
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 18,
                        color:
                          item.level === "safe"
                            ? "var(--color-risk-safe)"
                            : item.level === "move"
                              ? "var(--color-accent)"
                              : "var(--color-risk-review)",
                        marginRight: 4,
                      }}
                    >
                      {formatGb(item.size_bytes)}
                    </strong>

                    {item.location_path && (
                      <button
                        type="button"
                        className="term-btn"
                        onClick={() => openInExplorer(item.location_path!).catch(() => {})}
                      >
                        {item.level === "move"
                          ? "Abrir carpeta para mover →"
                          : "Abrir ubicación en Explorador →"}
                      </button>
                    )}

                    {item.program === "steam" && (
                      <a
                        href="steam://open/settings/storage"
                        className="term-btn term-btn--primary"
                        style={{ textDecoration: "none" }}
                      >
                        Abrir Steam (Almacenamiento) ↗
                      </a>
                    )}

                    {item.suggested_command && (
                      <button
                        type="button"
                        className="term-btn term-btn--primary"
                        disabled={busyTarget === item.target}
                        onClick={() => handleExecuteCommand(item)}
                      >
                        {busyTarget === item.target
                          ? "Ejecutando…"
                          : `>_ Ejecutar limpieza (${item.suggested_command})`}
                      </button>
                    )}

                    {item.location_path && item.level === "safe" && (
                      <button
                        type="button"
                        className="term-btn term-btn--danger"
                        disabled={busyTarget === item.target}
                        onClick={() => handleTrashLocation(item)}
                      >
                        Enviar carpeta a Papelera
                      </button>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

export default function Resumen() {
  const [summary, setSummary] = useState<Loadable<SummaryOut>>({ status: "loading" });
  const [recommendations, setRecommendations] = useState<Loadable<RecommendationOut[]>>({
    status: "loading",
  });
  const [selectedVol, setSelectedVol] = useState<string | null>(null);
  const [scanningVolume, setScanningVolume] = useState<string | null>(null);
  const [scanMessage, setScanMessage] = useState<string | null>(null);
  const [diskHealth, setDiskHealth] = useState<PhysicalDiskHealthOut[]>([]);
  const [selectedDisk, setSelectedDisk] = useState<PhysicalDiskHealthOut | null>(null);
  const [readingSmart, setReadingSmart] = useState(false);

  async function handleReadSmartAdmin() {
    setReadingSmart(true);
    try {
      let updated = await fetchDiskHealth();
      if (!updated.some((d) => d.smart_available)) {
        updated = await readSmartElevatedNow();
      }
      setDiskHealth(updated);
      if (selectedDisk) {
        const refreshed = updated.find(
          (d) => d.friendly_name === selectedDisk.friendly_name,
        );
        if (refreshed) setSelectedDisk(refreshed);
      }
    } catch {
      // fallback si falla la lectura
    } finally {
      setReadingSmart(false);
    }
  }

  function loadAll() {
    fetchDiskHealth()
      .then((d) => setDiskHealth(d))
      .catch(() => {});
    fetchSummary()
      .then((data) => setSummary({ status: "ready", data }))
      .catch((error: unknown) => {
        const message =
          error instanceof ApiError
            ? `No se pudo cargar (${error.status}): ${error.detail}`
            : "No se pudo conectar con la API local.";
        setSummary({ status: "error", message });
      });
    fetchRecommendations()
      .then((data) => setRecommendations({ status: "ready", data }))
      .catch((error: unknown) => {
        const message =
          error instanceof ApiError
            ? `No se pudo cargar (${error.status}): ${error.detail}`
            : "No se pudo conectar con la API local.";
        setRecommendations({ status: "error", message });
      });
  }

  useEffect(() => {
    loadAll();
  }, []);

  async function handleScanVolume(volumeName: string) {
    setScanMessage(null);
    setScanningVolume(volumeName);
    try {
      const res = await scanVolumeNow(volumeName);
      setScanMessage(
        `✓ Análisis de ${res.volume} completado en ${res.duration_s.toFixed(1)} s (${res.folders.toLocaleString("es")} carpetas, ${res.files.toLocaleString("es")} archivos, foto #${res.snapshot_id}).`,
      );
      setSelectedVol(volumeName);
      loadAll();
    } catch (error: unknown) {
      const msg =
        error instanceof ApiError
          ? error.detail === "action_declined"
            ? "Cancelaste la confirmación nativa de Windows."
            : `No se pudo escanear (${error.detail}).`
          : "Error al iniciar el análisis.";
      setScanMessage(msg);
    } finally {
      setScanningVolume(null);
    }
  }

  if (summary.status === "loading") {
    return (
      <main className="bento" aria-label="Cargando resumen">
        <div className="bento__card bento__card--hero">
          <div className="skeleton-block" style={{ height: 24, width: "45%" }} />
          <div className="skeleton-block" style={{ height: 48, width: "70%" }} />
          <div className="skeleton-block" style={{ height: 14, width: "100%" }} />
        </div>
        <div className="bento__card bento__card--wide">
          <div className="skeleton-block" style={{ height: 80 }} />
        </div>
        <div className="bento__card bento__card--wide">
          <div className="skeleton-block" style={{ height: 80 }} />
        </div>
      </main>
    );
  }
  if (summary.status === "error") {
    return (
      <div className="bento__state" role="alert">
        {summary.message}
      </div>
    );
  }
  if (summary.data.volumes.length === 0) {
    return <div className="bento__state">No se encontró ningún disco.</div>;
  }

  const sortedVolumes = [...summary.data.volumes].sort((a, b) => {
    if (selectedVol) {
      if (a.volume.name === selectedVol) return -1;
      if (b.volume.name === selectedVol) return 1;
    }
    if ((a.snapshot_id !== null) !== (b.snapshot_id !== null)) {
      return a.snapshot_id !== null ? -1 : 1;
    }
    return a.volume.name.localeCompare(b.volume.name);
  });
  const [primary, ...others] = sortedVolumes;

  return (
    <main className="bento" aria-label="Resumen del disco">
      {/* Barra superior de selección por unidad */}
      <section className="bento__card bento__card--full" style={{ padding: "var(--space-3) var(--space-4)" }}>
        <div className="glass-toolbar">
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <span className="bento__label">Analizador por unidad:</span>
            <div className="glass-tabs">
              {summary.data.volumes.map((v) => (
                <button
                  key={v.volume.name}
                  type="button"
                  className={`glass-tab${primary.volume.name === v.volume.name ? " glass-tab--active" : ""}`}
                  onClick={() => setSelectedVol(v.volume.name)}
                >
                  <span>Disco {v.volume.name}</span>
                  <span style={{ opacity: 0.85, fontFamily: "var(--font-mono)", fontSize: 11 }}>
                    {formatGb(v.volume.free_bytes)} libres
                  </span>
                </button>
              ))}
            </div>
          </div>
          {scanMessage && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--color-accent)" }}>
              {scanMessage}
            </span>
          )}
        </div>
      </section>

      <DiskCard
        row={primary}
        hero
        scanningVolume={scanningVolume}
        onScanVolume={handleScanVolume}
        onSelectVolume={setSelectedVol}
      />
      <UnexplainedCard row={primary} />
      <ForecastCard row={primary} />
      <GrowthCard row={primary} />
      {others.map((row) => (
        <DiskCard
          key={row.volume.name}
          row={row}
          hero={false}
          scanningVolume={scanningVolume}
          onScanVolume={handleScanVolume}
          onSelectVolume={setSelectedVol}
        />
      ))}
      <InteractivePlanSection
        recommendations={recommendations}
        onReload={loadAll}
      />

      {diskHealth.length > 0 && (
        <section
          className="bento__card bento__card--full"
          aria-label="Salud física de discos S.M.A.R.T."
        >
          <div className="glass-toolbar">
            <div>
              <div className="bento__label">
                Salud Física de Discos (S.M.A.R.T. · SSD / NVMe / HDD)
              </div>
              <p className="bento__detail" style={{ marginTop: 2 }}>
                Haz clic en cualquiera de tus discos para abrir la ventana emergente con{" "}
                <strong>vida útil %, temperatura, horas de uso y ficha técnica completa</strong>.
              </p>
            </div>
            <button
              type="button"
              className="term-btn term-btn--primary"
              disabled={readingSmart}
              onClick={handleReadSmartAdmin}
              title="Actualiza en vivo los sensores S.M.A.R.T. (Vida útil %, Temperatura °C, Horas de uso y TB escritos)"
            >
              {readingSmart
                ? "Leyendo sensores S.M.A.R.T.…"
                : "↻ Actualizar sensores S.M.A.R.T."}
            </button>
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(290px, 1fr))",
              gap: "var(--space-3)",
              marginTop: "var(--space-2)",
            }}
          >
            {diskHealth.map((disk) => {
              const healthy = disk.health_status.toLowerCase() === "healthy";
              return (
                <div
                  key={`${disk.device_id}-${disk.friendly_name}`}
                  className="term-row-card"
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelectedDisk(disk)}
                  onKeyDown={(e) => e.key === "Enter" && setSelectedDisk(disk)}
                  style={{ padding: "var(--space-3)", cursor: "pointer" }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: 8,
                      flexWrap: "wrap",
                    }}
                  >
                    <strong style={{ fontSize: 14 }}>
                      {disk.friendly_name}
                    </strong>
                    <span
                      className={`term-badge ${
                        healthy ? "term-badge--safe" : "term-badge--danger"
                      }`}
                    >
                      {healthy
                        ? "[SALUDABLE]"
                        : `[${disk.health_status.toUpperCase()}]`}
                    </span>
                  </div>
                  <div
                    style={{
                      display: "flex",
                      gap: 8,
                      flexWrap: "wrap",
                      marginTop: 4,
                      alignItems: "center",
                    }}
                  >
                    {disk.drive_letters.length > 0 && (
                      <span className="term-badge term-badge--info">
                        Unidad {disk.drive_letters.join(", ")}
                      </span>
                    )}
                    <span className="term-badge term-badge--info">
                      {disk.media_type}
                      {disk.bus_type ? ` · ${disk.bus_type}` : ""}
                    </span>
                    <span className="term-badge term-badge--info">
                      {formatGb(disk.size_bytes)}
                    </span>
                    {disk.life_remaining_percent !== null && (
                      <span
                        className={`term-badge ${
                          disk.life_remaining_percent <= 20
                            ? "term-badge--danger"
                            : "term-badge--safe"
                        }`}
                      >
                        Vida útil: {disk.life_remaining_percent} %
                      </span>
                    )}
                    {disk.temperature_c !== null && (
                      <span
                        className={`term-badge ${
                          disk.temperature_c >= 65
                            ? "term-badge--danger"
                            : disk.temperature_c >= 52
                              ? "term-badge--review"
                              : "term-badge--safe"
                        }`}
                      >
                        🌡 {disk.temperature_c} °C
                      </span>
                    )}
                    <span
                      style={{
                        marginLeft: "auto",
                        fontSize: 12,
                        fontWeight: 600,
                        color: "var(--color-accent)",
                      }}
                    >
                      Ver detalles →
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Ventana emergente (Modal) con todos los datos S.M.A.R.T. y ficha técnica del disco */}
      {selectedDisk && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Detalles S.M.A.R.T. de ${selectedDisk.friendly_name}`}
          onClick={() => setSelectedDisk(null)}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 1000,
            background: "rgba(15, 23, 42, 0.58)",
            backdropFilter: "blur(8px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "var(--space-4)",
          }}
        >
          <div
            className="bento__card"
            onClick={(e) => e.stopPropagation()}
            style={{
              width: "100%",
              maxWidth: 680,
              maxHeight: "90vh",
              overflowY: "auto",
              padding: "var(--space-5)",
              gap: "var(--space-4)",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "flex-start",
                gap: 12,
              }}
            >
              <div>
                <div className="bento__label">
                  Ficha Técnica y Salud S.M.A.R.T. · Disco #{selectedDisk.device_id}
                </div>
                <h3 style={{ margin: "6px 0 4px", fontSize: 20 }}>
                  {selectedDisk.friendly_name}
                </h3>
                <p className="bento__detail">
                  {selectedDisk.media_type} · Bus {selectedDisk.bus_type || "NVMe"} ·{" "}
                  {formatGb(selectedDisk.size_bytes)}
                  {selectedDisk.drive_letters.length > 0 &&
                    ` · Letras asignadas: ${selectedDisk.drive_letters.join(", ")}`}
                </p>
              </div>
              <button
                type="button"
                className="term-btn"
                onClick={() => setSelectedDisk(null)}
              >
                ✕ Cerrar
              </button>
            </div>

            {/* Tarjetas de Vida Útil %, Temperatura y Tiempo de Vida / Uso */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(175px, 1fr))",
                gap: "var(--space-3)",
              }}
            >
              <div className="term-row-card" style={{ alignItems: "center", textAlign: "center" }}>
                <div className="bento__label">Vida útil restante</div>
                {selectedDisk.life_remaining_percent !== null ? (
                  <>
                    <CircularGauge
                      percent={selectedDisk.life_remaining_percent}
                      color={
                        selectedDisk.life_remaining_percent <= 20
                          ? "#ef4444"
                          : selectedDisk.life_remaining_percent <= 50
                            ? "#f59e0b"
                            : "#10b981"
                      }
                    />
                    <span className="bento__detail">
                      Desgaste medido: <strong>{selectedDisk.wear_percent} %</strong>
                    </span>
                  </>
                ) : (
                  <p className="bento__detail" style={{ marginTop: 8 }}>
                    Sensor protegido por Windows (pulsa el botón S.M.A.R.T. abajo).
                  </p>
                )}
              </div>

              <div className="term-row-card" style={{ alignItems: "center", textAlign: "center" }}>
                <div className="bento__label">Temperatura actual</div>
                <p
                  className="bento__value"
                  style={{
                    fontSize: 32,
                    color:
                      selectedDisk.temperature_c !== null && selectedDisk.temperature_c >= 65
                        ? "#ef4444"
                        : "var(--color-accent)",
                  }}
                >
                  {selectedDisk.temperature_c !== null
                    ? `${selectedDisk.temperature_c} °C`
                    : "—"}
                </p>
                <span className="bento__detail">
                  {selectedDisk.temperature_max_c !== null
                    ? `Máxima registrada: ${selectedDisk.temperature_max_c} °C`
                    : "Rango normal SSD: 30–55 °C"}
                </span>
              </div>

              <div className="term-row-card" style={{ alignItems: "center", textAlign: "center" }}>
                <div className="bento__label">Tiempo de vida / uso</div>
                <p className="bento__value" style={{ fontSize: 28 }}>
                  {selectedDisk.power_on_hours !== null
                    ? `${selectedDisk.power_on_hours.toLocaleString("es")} h`
                    : "—"}
                </p>
                <span className="bento__detail">
                  {selectedDisk.power_on_hours !== null
                    ? `≈ ${Math.round(selectedDisk.power_on_hours / 24).toLocaleString("es")} días encendido`
                    : "Horas de encendido S.M.A.R.T."}
                </span>
              </div>
            </div>

            {!selectedDisk.smart_available && (
              <div
                className="term-banner"
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 12,
                  flexWrap: "wrap",
                }}
              >
                <span>
                  Windows requiere permiso de Administrador (UAC) una sola vez para desbloquear los
                  sensores S.M.A.R.T. (Vida útil %, Temperatura °C y Horas de uso) de discos NVMe.
                </span>
                <button
                  type="button"
                  className="term-btn term-btn--primary"
                  disabled={readingSmart}
                  onClick={handleReadSmartAdmin}
                >
                  {readingSmart
                    ? "Leyendo sensores…"
                    : "🔓 Desbloquear sensores S.M.A.R.T. (UAC)"}
                </button>
              </div>
            )}

            {/* Tabla de ficha técnica completa */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))",
                gap: "var(--space-2)",
              }}
            >
              <div className="term-row-card" style={{ padding: "10px 14px", gap: 4 }}>
                <span className="bento__label">Estado de salud</span>
                <strong>{selectedDisk.health_status} ({selectedDisk.operational_status})</strong>
              </div>
              <div className="term-row-card" style={{ padding: "10px 14px", gap: 4 }}>
                <span className="bento__label">Número de serie</span>
                <span className="term-path">{selectedDisk.serial_number ?? "No reportado"}</span>
              </div>
              <div className="term-row-card" style={{ padding: "10px 14px", gap: 4 }}>
                <span className="bento__label">Versión de Firmware</span>
                <span className="term-path">{selectedDisk.firmware_version ?? "No reportado"}</span>
              </div>
              <div className="term-row-card" style={{ padding: "10px 14px", gap: 4 }}>
                <span className="bento__label">Estilo de partición</span>
                <strong>{selectedDisk.partition_style ?? "GPT"}</strong>
              </div>
              <div className="term-row-card" style={{ padding: "10px 14px", gap: 4 }}>
                <span className="bento__label">Tamaño de sector</span>
                <span className="term-path">
                  Lógico: {selectedDisk.logical_sector_size ?? 512} B · Físico:{" "}
                  {selectedDisk.physical_sector_size ?? 4096} B
                </span>
              </div>
              <div className="term-row-card" style={{ padding: "10px 14px", gap: 4 }}>
                <span className="bento__label">Errores de lectura / escritura</span>
                <strong>
                  {selectedDisk.read_errors ?? 0} lectura · {selectedDisk.write_errors ?? 0} escritura
                </strong>
              </div>
              {selectedDisk.written_tb !== null && selectedDisk.written_tb !== undefined && (
                <div className="term-row-card" style={{ padding: "10px 14px", gap: 4 }}>
                  <span className="bento__label">Total escrito (TBW)</span>
                  <strong>{selectedDisk.written_tb.toLocaleString("es")} TB escritos</strong>
                </div>
              )}
              {selectedDisk.available_spare_percent !== null &&
                selectedDisk.available_spare_percent !== undefined && (
                  <div className="term-row-card" style={{ padding: "10px 14px", gap: 4 }}>
                    <span className="bento__label">Bloques de reserva (Spare)</span>
                    <strong style={{ color: "var(--color-risk-safe)" }}>
                      {selectedDisk.available_spare_percent} % disponible
                    </strong>
                  </div>
                )}
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
