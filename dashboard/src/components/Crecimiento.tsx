import { useEffect, useMemo, useState } from "react";
import { defineChart, lineY, areaY } from "@tanstack/charts";
import { scaleLinear } from "@tanstack/charts/scales/linear";
import { zoomX, type ZoomXChange, type ZoomXWindow } from "@tanstack/charts/interaction/zoom";
import { controlledSignal } from "@tanstack/charts/interaction/signal";
import { tooltip } from "@tanstack/charts/tooltip";
import { Chart } from "@tanstack/charts/react";
import { ApiError, fetchGrowth, fetchSnapshots, fetchSummary } from "../api/client";
import type { FolderGrowthOut, SnapshotOut } from "../api/types";
import { formatGb } from "../format";
import "../styles/bento.css";

type Loadable<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

interface Point {
  day: number;
  date: string;
  usedGb: number;
}

function toPoints(snapshots: SnapshotOut[]): Point[] {
  const sorted = [...snapshots].sort((a, b) => a.taken_at.localeCompare(b.taken_at));
  if (sorted.length === 0) return [];
  const first = new Date(sorted[0].taken_at).getTime();
  return sorted.map((snapshot) => ({
    day: (new Date(snapshot.taken_at).getTime() - first) / 86_400_000,
    date: snapshot.taken_at,
    usedGb: snapshot.used_bytes / 1024 ** 3,
  }));
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("es", { day: "2-digit", month: "short" });
}

export default function Crecimiento() {
  // "undefined" = todavía no se sabe qué disco mostrar; "null" = ninguno
  // tiene foto; una cadena = el nombre real del disco.
  const [volume, setVolume] = useState<string | null | undefined>(undefined);
  const [points, setPoints] = useState<Loadable<Point[]>>({ status: "loading" });
  const [topGrowth, setTopGrowth] = useState<Loadable<FolderGrowthOut[]>>({ status: "loading" });
  const [zoomWindow, setZoomWindow] = useState<[number, number] | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchSummary()
      .then((summary) => {
        if (cancelled) return;
        const scanned = summary.volumes.find((row) => row.snapshot_id !== null);
        setVolume(scanned ? scanned.volume.name : null);
      })
      .catch(() => {
        if (!cancelled) setVolume(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (volume === undefined) return;
    let cancelled = false;
    setPoints({ status: "loading" });
    setTopGrowth({ status: "loading" });
    setZoomWindow(null);
    fetchSnapshots(volume)
      .then((snapshots) => {
        if (!cancelled) setPoints({ status: "ready", data: toPoints(snapshots) });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message =
          error instanceof ApiError
            ? `No se pudo cargar (${error.status}): ${error.detail}`
            : "No se pudo conectar con la API local.";
        setPoints({ status: "error", message });
      });
    fetchGrowth(volume)
      .then((growth) => {
        if (!cancelled) {
          setTopGrowth({
            status: "ready",
            data: growth.folders.filter((folder) => folder.grown_bytes > 0).slice(0, 5),
          });
        }
      })
      .catch(() => {
        // Sin suficiente historia para comparar dos fotos: no es un error
        // del todo, el crecimiento se deja vacío.
        if (!cancelled) setTopGrowth({ status: "ready", data: [] });
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

  const definition = useMemo(() => {
    if (data.length === 0) return null;
    return defineChart({
      marks: [
        areaY(data, { x: "day", y: "usedGb", fillOpacity: 0.16 }),
        lineY(data, { x: "day", y: "usedGb", points: true }),
      ],
      scales: {
        x: {
          scale: scaleLinear,
          domain: activeWindow,
          axis: { label: "Día" },
        },
        y: { scale: scaleLinear, nice: true, grid: true, axis: { label: "GB usados" } },
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
  }, [data, activeWindow, extent]);

  if (volume === undefined || points.status === "loading") {
    return (
      <div className="bento__state" role="status">
        Cargando el crecimiento del disco…
      </div>
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
      <div className="bento__state">
        Hace falta más de una foto de {volume} para dibujar el crecimiento.
      </div>
    );
  }

  return (
    <main style={{ padding: "var(--space-5)" }} aria-label="Crecimiento">
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
          <div className="bento__label">Crecimiento de {volume}</div>
          {zoomWindow !== null && (
            <button
              type="button"
              onClick={() => setZoomWindow(null)}
              style={{
                minHeight: 32,
                padding: "0 var(--space-3)",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--color-border)",
                background: "transparent",
                color: "var(--color-text-primary)",
                fontSize: 13,
                cursor: "pointer",
              }}
            >
              Ver todo
            </button>
          )}
        </div>
        {definition && (
          <Chart definition={definition} height={320} ariaLabel="GB usados a lo largo del tiempo" />
        )}
        <p className="bento__detail">
          Primera foto: {formatDate(data[0].date)} · última: {formatDate(data.at(-1)!.date)}. Rueda
          del mouse o pellizcar para acercar un rango de fechas.
        </p>
      </section>

      <section
        className="bento__card"
        aria-label="Qué más creció"
        style={{ marginTop: "var(--space-5)" }}
      >
        <div className="bento__label">Qué más creció</div>
        {topGrowth.status !== "ready" || topGrowth.data.length === 0 ? (
          <p className="bento__detail">Sin crecimiento que destacar en este rango.</p>
        ) : (
          <ul style={{ listStyle: "none", margin: "var(--space-2) 0 0", padding: 0 }}>
            {topGrowth.data.map((folder) => (
              <li
                key={folder.path}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  gap: "var(--space-3)",
                  padding: "var(--space-2) 0",
                  borderBottom: "1px solid var(--color-border)",
                  fontSize: 14,
                }}
              >
                <span style={{ minWidth: 0, overflowWrap: "break-word" }}>{folder.path}</span>
                <span style={{ flexShrink: 0, fontWeight: 600, color: "var(--color-risk-review)" }}>
                  +{formatGb(folder.grown_bytes)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
