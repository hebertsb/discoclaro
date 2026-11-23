import { useEffect, useMemo, useState } from "react";
import { defineChart, lineY, areaY } from "@tanstack/charts";
import { scaleLinear } from "@tanstack/charts/scales/linear";
import { zoomX, type ZoomXChange, type ZoomXWindow } from "@tanstack/charts/interaction/zoom";
import { controlledSignal } from "@tanstack/charts/interaction/signal";
import { tooltip } from "@tanstack/charts/tooltip";
import { Chart } from "@tanstack/charts/react";
import {
  ApiError,
  fetchGrowth,
  fetchSearch,
  fetchSnapshots,
  fetchSummary,
  openInExplorer,
  scanVolumeNow,
} from "../api/client";
import type { FolderGrowthOut, LargeFileOut, SnapshotOut } from "../api/types";
import { formatGb } from "../format";
import "../styles/bento.css";
import "../styles/terminal.css";

type Loadable<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

type MetricMode = "used" | "free";

interface Point {
  day: number;
  date: string;
  usedGb: number;
  freeGb: number;
}

function toPoints(snapshots: SnapshotOut[]): Point[] {
  const sorted = [...snapshots].sort((a, b) => a.taken_at.localeCompare(b.taken_at));
  if (sorted.length === 0) return [];
  const first = new Date(sorted[0].taken_at).getTime();
  return sorted.map((snapshot) => ({
    day: (new Date(snapshot.taken_at).getTime() - first) / 86_400_000,
    date: snapshot.taken_at,
    usedGb: snapshot.used_bytes / 1024 ** 3,
    freeGb: snapshot.free_bytes / 1024 ** 3,
  }));
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("es", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function Crecimiento() {
  const [volumesWithScan, setVolumesWithScan] = useState<string[]>([]);
  const [volume, setVolume] = useState<string | null | undefined>(undefined);
  const [metric, setMetric] = useState<MetricMode>("used");
  const [points, setPoints] = useState<Loadable<Point[]>>({ status: "loading" });
  const [topGrowth, setTopGrowth] = useState<Loadable<FolderGrowthOut[]>>({ status: "loading" });
  const [recentFiles, setRecentFiles] = useState<LargeFileOut[]>([]);
  const [zoomWindow, setZoomWindow] = useState<[number, number] | null>(null);
  const [scanningVol, setScanningVol] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchSummary()
      .then((summary) => {
        if (cancelled) return;
        const scannedRows = summary.volumes.filter((row) => row.snapshot_id !== null);
        // Prioriza el disco que ya tiene historial de crecimiento (>=2 fotos), ej. C:
        const ordered = [...scannedRows].sort((a, b) => {
          const aHasHistory = a.top_growth !== null || a.days_until_full !== null ? 1 : 0;
          const bHasHistory = b.top_growth !== null || b.days_until_full !== null ? 1 : 0;
          if (aHasHistory !== bHasHistory) return bHasHistory - aHasHistory;
          return a.volume.name.localeCompare(b.volume.name);
        });
        const names = ordered.map((row) => row.volume.name);
        setVolumesWithScan(names);
        setVolume(names[0] ?? null);
      })
      .catch(() => {
        if (!cancelled) setVolume(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (volume === undefined || volume === null) return;
    let cancelled = false;
    setPoints({ status: "loading" });
    setTopGrowth({ status: "loading" });
    setZoomWindow(null);

    Promise.all([
      fetchSnapshots(volume),
      fetchGrowth(volume).catch(() => null),
      fetchSearch("", volume).catch(() => null),
    ])
      .then(([snapshots, growth, searchRes]) => {
        if (cancelled) return;
        setPoints({ status: "ready", data: toPoints(snapshots) });
        if (growth) {
          setTopGrowth({
            status: "ready",
            data: growth.folders.filter((folder) => folder.grown_bytes > 0).slice(0, 10),
          });
        } else {
          setTopGrowth({ status: "ready", data: [] });
        }
        setRecentFiles(searchRes?.new_large_files ?? []);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message =
          error instanceof ApiError
            ? `No se pudo cargar (${error.status}): ${error.detail}`
            : "No se pudo conectar con la API local.";
        setPoints({ status: "error", message });
      });

    return () => {
      cancelled = true;
    };
  }, [volume]);

  const data = points.status === "ready" ? points.data : [];
  const extent: [number, number] = useMemo(() => {
    if (data.length === 0) return [0, 1];
    return [0, Math.max(...data.map((point) => point.day), 1)];
  }, [data]);
  const activeWindow = zoomWindow ?? extent;

  const yField = metric === "used" ? "usedGb" : "freeGb";
  const strokeColor = metric === "used" ? "#3b63f6" : "#10b981";
  const yLabel = metric === "used" ? "GB usados" : "GB libres";

  const definition = useMemo(() => {
    if (data.length === 0) return null;
    return defineChart({
      marks: [
        areaY(data, {
          x: "day",
          y: yField,
          fill: strokeColor,
          fillOpacity: 0.22,
        }),
        lineY(data, {
          x: "day",
          y: yField,
          stroke: strokeColor,
          strokeWidth: 3,
          points: true,
        }),
      ],
      scales: {
        x: {
          scale: scaleLinear,
          domain: activeWindow,
          axis: { label: "Días transcurridos" },
        },
        y: {
          scale: scaleLinear,
          nice: true,
          grid: true,
          axis: { label: yLabel },
        },
      },
      controls: [
        zoomX({
          window: controlledSignal<ZoomXWindow<number>, ZoomXChange<number>>(
            { start: activeWindow[0], end: activeWindow[1] },
            (next) => setZoomWindow([next.start, next.end]),
          ),
          extent,
          scaleExtent: [1, 20],
          ariaLabel: "Rango de fechas visible",
          format: (day: number) => `día ${Math.round(day)}`,
        }),
      ],
      tooltip,
      // eslint-disable-next-line react-hooks/exhaustive-deps
    });
  }, [data, activeWindow, extent, yField, strokeColor, yLabel]);

  if (volume === undefined || points.status === "loading") {
    return (
      <main style={{ padding: "var(--space-5)", maxWidth: 1600, margin: "0 auto" }} aria-label="Cargando crecimiento">
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
            gap: "var(--space-4)",
            marginBottom: "var(--space-4)",
          }}
        >
          <div className="bento__card">
            <div className="skeleton-block" style={{ height: 18, width: "60%" }} />
            <div className="skeleton-block" style={{ height: 36, width: "45%" }} />
          </div>
          <div className="bento__card">
            <div className="skeleton-block" style={{ height: 18, width: "50%" }} />
            <div className="skeleton-block" style={{ height: 36, width: "40%" }} />
          </div>
          <div className="bento__card">
            <div className="skeleton-block" style={{ height: 18, width: "55%" }} />
            <div className="skeleton-block" style={{ height: 36, width: "65%" }} />
          </div>
        </div>
        <div className="bento__card" style={{ marginBottom: "var(--space-4)" }}>
          <div className="skeleton-block" style={{ height: 24, width: 260 }} />
          <div className="skeleton-block" style={{ height: 280, width: "100%" }} />
        </div>
      </main>
    );
  }
  if (volume === null) {
    return (
      <div className="bento__state">Todavía no hay ninguna foto de ningún disco.</div>
    );
  }
  if (points.status === "error") {
    return (
      <div className="bento__state" role="alert">
        {points.message}
      </div>
    );
  }
  if (data.length < 2) {
    return (
      <main style={{ padding: "var(--space-5)", maxWidth: 1600, margin: "0 auto" }} aria-label="Crecimiento">
        <section className="bento__card">
          <div className="glass-toolbar">
            <div>
              <div className="bento__label">Crecimiento histórico ({volume})</div>
              <p className="bento__detail" style={{ marginTop: 6 }}>
                Actualmente hay solo <strong>{data.length} foto</strong> guardada de <strong>{volume}</strong>.
                Se necesitan al menos 2 fotos para dibujar la curva de comportamiento en el tiempo.
              </p>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              {volumesWithScan.length > 1 && (
                <div className="glass-tabs" role="tablist" aria-label="Seleccionar unidad">
                  {volumesWithScan.map((v) => (
                    <button
                      key={v}
                      type="button"
                      className={`glass-tab${volume === v ? " glass-tab--active" : ""}`}
                      onClick={() => setVolume(v)}
                    >
                      Disco {v}
                    </button>
                  ))}
                </div>
              )}

              <button
                type="button"
                className="term-btn term-btn--primary"
                disabled={scanningVol}
                onClick={async () => {
                  setScanningVol(true);
                  try {
                    await scanVolumeNow(volume);
                    const snaps = await fetchSnapshots(volume);
                    setPoints({ status: "ready", data: toPoints(snaps) });
                  } catch {
                    // ignore cancel
                  } finally {
                    setScanningVol(false);
                  }
                }}
              >
                {scanningVol ? `Analizando ${volume}…` : `↻ Tomar nueva foto de ${volume}`}
              </button>
            </div>
          </div>
        </section>
      </main>
    );
  }

  const firstPoint = data[0];
  const lastPoint = data.at(-1)!;
  const deltaUsedGb = lastPoint.usedGb - firstPoint.usedGb;
  const maxFolderGrowth =
    topGrowth.status === "ready" && topGrowth.data.length > 0
      ? Math.max(...topGrowth.data.map((f) => f.grown_bytes))
      : 0;

  return (
    <main style={{ padding: "var(--space-5)", maxWidth: 1600, margin: "0 auto" }} aria-label="Crecimiento">
      {/* KPIs superiores */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
          gap: "var(--space-4)",
          marginBottom: "var(--space-4)",
        }}
      >
        <section className="bento__card bento__card--accent">
          <div className="bento__label">Variación de uso ({volume})</div>
          <p className="bento__value">
            {deltaUsedGb >= 0 ? "+" : ""}
            {deltaUsedGb.toFixed(1)} GB
          </p>
          <p className="bento__detail">
            Usado: {firstPoint.usedGb.toFixed(1)} GB → {lastPoint.usedGb.toFixed(1)} GB
          </p>
        </section>

        <section className="bento__card">
          <div className="bento__label">Espacio libre actual ({volume})</div>
          <p className="bento__value" style={{ color: "var(--color-risk-safe)" }}>
            {lastPoint.freeGb.toFixed(1)} GB
          </p>
          <p className="bento__detail">
            Antes: {firstPoint.freeGb.toFixed(1)} GB libres · {data.length} fotos registradas
          </p>
        </section>

        <section className="bento__card">
          <div className="bento__label">Unidad y métrica en tiempo real</div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {volumesWithScan.map((v) => (
              <button
                key={v}
                type="button"
                className={`term-btn${v === volume ? " term-btn--primary" : ""}`}
                onClick={() => setVolume(v)}
              >
                Disco {v}
              </button>
            ))}
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <button
              type="button"
              className={`term-btn${metric === "used" ? " term-btn--primary" : ""}`}
              onClick={() => setMetric("used")}
            >
              📈 Cómo se llena (GB usados)
            </button>
            <button
              type="button"
              className={`term-btn${metric === "free" ? " term-btn--primary" : ""}`}
              onClick={() => setMetric("free")}
            >
              📉 Espacio libre (GB libres)
            </button>
          </div>
        </section>
      </div>

      {/* Gráfica interactiva */}
      <section className="bento__card" aria-label="Crecimiento del disco">
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: "var(--space-2)",
          }}
        >
          <div>
            <div className="bento__label">
              {metric === "used"
                ? `Evolución de espacio usado en ${volume}`
                : `Evolución de espacio libre disponible en ${volume}`}
            </div>
            <p className="bento__detail">
              Primera medición: {formatDate(firstPoint.date)} · Última: {formatDate(lastPoint.date)}. Rueda del mouse para zoom.
            </p>
          </div>
          {zoomWindow !== null && (
            <button
              type="button"
              className="term-btn"
              onClick={() => setZoomWindow(null)}
            >
              Restablecer zoom
            </button>
          )}
        </div>
        {definition && (
          <Chart definition={definition} height={320} ariaLabel="Comportamiento del disco en el tiempo" />
        )}
      </section>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))",
          gap: "var(--space-4)",
          marginTop: "var(--space-4)",
        }}
      >
        {/* Columna 1: Carpetas que más crecieron con botón para abrir ubicación */}
        <section className="bento__card" aria-label="Qué más creció">
          <div className="bento__label">Carpetas con mayor crecimiento detectado</div>
          {topGrowth.status !== "ready" || topGrowth.data.length === 0 ? (
            <p className="bento__detail">Sin crecimiento significativo que destacar en este rango.</p>
          ) : (
            <ul className="term-list" style={{ marginTop: "var(--space-2)" }}>
              {topGrowth.data.map((folder) => {
                const pct = maxFolderGrowth > 0 ? (folder.grown_bytes / maxFolderGrowth) * 100 : 0;
                return (
                  <li key={folder.path} className="term-row-card" style={{ padding: "var(--space-3)" }}>
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        gap: "var(--space-2)",
                        flexWrap: "wrap",
                      }}
                    >
                      <span className="term-path" style={{ fontWeight: 600, flex: 1 }}>
                        {folder.path}
                      </span>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span
                          style={{
                            fontFamily: "var(--font-mono)",
                            fontWeight: 700,
                            fontSize: 15,
                            color: "var(--color-accent)",
                          }}
                        >
                          +{formatGb(folder.grown_bytes)}
                        </span>
                        <button
                          type="button"
                          className="term-btn"
                          onClick={() => openInExplorer(folder.path).catch(() => {})}
                        >
                          Ir a carpeta →
                        </button>
                      </div>
                    </div>
                    <div className="bento__bar" style={{ height: 6 }}>
                      <div
                        className="bento__bar-fill"
                        style={{
                          width: `${pct}%`,
                          background: "linear-gradient(90deg, #ff622e 0%, #f59e0b 100%)",
                        }}
                      />
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {/* Columna 2: Archivos grandes recientes / nuevos */}
        <section className="bento__card" aria-label="Archivos grandes recientes">
          <div className="bento__label">Archivos grandes recientes / nuevos ({recentFiles.length})</div>
          {recentFiles.length === 0 ? (
            <p className="bento__detail">
              No aparecieron archivos individuales mayores a 50 MB desde la foto anterior.
            </p>
          ) : (
            <ul className="term-list" style={{ marginTop: "var(--space-2)" }}>
              {recentFiles.slice(0, 10).map((file) => (
                <li key={file.path} className="term-row-card" style={{ padding: "var(--space-3)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
                    <span className="term-path" style={{ flex: 1 }}>
                      {file.path}
                    </span>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <strong style={{ fontFamily: "var(--font-mono)", color: "var(--color-risk-review)" }}>
                        {formatGb(file.size_bytes)}
                      </strong>
                      <button
                        type="button"
                        className="term-btn"
                        onClick={() => openInExplorer(file.path).catch(() => {})}
                      >
                        Ir al archivo →
                      </button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </main>
  );
}
