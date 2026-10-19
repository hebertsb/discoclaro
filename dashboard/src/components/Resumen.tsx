import { useEffect, useState } from "react";
import { ApiError, fetchPlan, fetchSummary } from "../api/client";
import type { PlanOut, SummaryOut, VolumeSummaryOut } from "../api/types";
import { formatDays, formatGb, formatPercent } from "../format";
import "../styles/bento.css";

type Loadable<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

function useLoadable<T>(load: () => Promise<T>): Loadable<T> {
  const [state, setState] = useState<Loadable<T>>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    load()
      .then((data) => {
        if (!cancelled) setState({ status: "ready", data });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message =
          error instanceof ApiError
            ? `No se pudo cargar (${error.status}): ${error.detail}`
            : "No se pudo conectar con la API local. ¿Está corriendo?";
        setState({ status: "error", message });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return state;
}

function freeRatioColor(usedPercent: number): string {
  if (usedPercent >= 90) return "var(--color-risk-no-touch)";
  if (usedPercent >= 75) return "var(--color-risk-review)";
  return "var(--color-risk-safe)";
}

function DiskCard({ row, hero }: { row: VolumeSummaryOut; hero: boolean }) {
  const { volume } = row;
  const usedPercent = (volume.used_bytes / volume.total_bytes) * 100;
  const freePercent = 100 - usedPercent;
  const color = freeRatioColor(usedPercent);

  return (
    <section
      className={`bento__card${hero ? " bento__card--hero" : ""}`}
      aria-label={`Disco ${volume.name}`}
    >
      <div className="bento__label">Disco {volume.name}</div>
      <p className={`bento__value${hero ? " bento__value--hero" : ""}`}>
        {formatGb(volume.free_bytes)} libres
      </p>
      <p className="bento__detail">
        de {formatGb(volume.total_bytes)} — {formatPercent(freePercent)} libre
      </p>
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
      {row.snapshot_id === null && (
        <p className="bento__detail">Todavía no se escaneó este disco.</p>
      )}
    </section>
  );
}

function UnexplainedCard({ row }: { row: VolumeSummaryOut }) {
  if (row.unexplained === null) {
    return (
      <section className="bento__card" aria-label="Espacio no explicado">
        <div className="bento__label">Espacio no explicado</div>
        <p className="bento__detail">Sin datos todavía (hace falta una foto completa del disco).</p>
      </section>
    );
  }
  const { unexplained } = row;
  return (
    <section className="bento__card" aria-label="Espacio no explicado">
      <div className="bento__label">Espacio no explicado</div>
      <p className="bento__value">{formatGb(unexplained.size_bytes)}</p>
      <p className="bento__detail">{formatPercent(unexplained.percent_of_used)} de lo usado</p>
      {unexplained.exceeds_threshold && (
        <span
          className="bento__badge"
          style={{
            color: "var(--color-risk-review)",
            border: "1px solid var(--color-risk-review)",
          }}
        >
          Revisar
        </span>
      )}
    </section>
  );
}

function ForecastCard({ row }: { row: VolumeSummaryOut }) {
  return (
    <section className="bento__card" aria-label="Días hasta llenarse">
      <div className="bento__label">Días hasta llenarse</div>
      {row.days_until_full === null ? (
        <p className="bento__detail">Todavía no hay suficiente historia para estimarlo.</p>
      ) : (
        <>
          <p className="bento__value">~{formatDays(row.days_until_full)}</p>
          <p className="bento__detail">a este ritmo de crecimiento</p>
        </>
      )}
    </section>
  );
}

function GrowthCard({ row }: { row: VolumeSummaryOut }) {
  return (
    <section className="bento__card bento__card--wide" aria-label="Qué más creció">
      <div className="bento__label">Qué más creció</div>
      {row.top_growth === null ? (
        <p className="bento__detail">Sin datos de crecimiento todavía.</p>
      ) : (
        <p className="bento__detail">
          <strong style={{ color: "var(--color-text-primary)" }}>{row.top_growth.path}</strong>
          {" — "}
          <span style={{ fontWeight: 600 }}>+{formatGb(row.top_growth.grown_bytes)}</span>
        </p>
      )}
    </section>
  );
}

function PlanCard({ plan }: { plan: Loadable<PlanOut> }) {
  if (plan.status === "loading") {
    return (
      <section className="bento__card bento__card--full" aria-label="Plan recomendado">
        <div className="bento__label">Plan recomendado</div>
        <p className="bento__detail">Cargando…</p>
      </section>
    );
  }
  if (plan.status === "error") {
    return (
      <section className="bento__card bento__card--full" aria-label="Plan recomendado">
        <div className="bento__label">Plan recomendado</div>
        <p className="bento__detail">{plan.message}</p>
      </section>
    );
  }
  const { data } = plan;
  return (
    <section className="bento__card bento__card--full" aria-label="Plan recomendado">
      <div className="bento__label">Plan recomendado</div>
      <div
        style={{
          display: "flex",
          gap: "var(--space-6)",
          flexWrap: "wrap",
          marginTop: "var(--space-2)",
        }}
      >
        <p className="bento__detail">
          <span
            className="bento__badge"
            style={{
              color: "var(--color-risk-safe)",
              border: "1px solid var(--color-risk-safe)",
            }}
          >
            Seguro
          </span>{" "}
          {formatGb(data.safe_bytes)}
        </p>
        <p className="bento__detail">
          <span
            className="bento__badge"
            style={{
              color: "var(--color-risk-review)",
              border: "1px solid var(--color-risk-review)",
            }}
          >
            Revisar
          </span>{" "}
          {formatGb(data.review_bytes)}
        </p>
        <p className="bento__detail">
          <span
            className="bento__badge"
            style={{
              color: "var(--color-accent)",
              border: "1px solid var(--color-accent)",
            }}
          >
            Mover
          </span>{" "}
          {formatGb(data.move_bytes)}
        </p>
      </div>
    </section>
  );
}

export default function Resumen() {
  const summary = useLoadable<SummaryOut>(fetchSummary);
  const plan = useLoadable<PlanOut>(fetchPlan);

  if (summary.status === "loading") {
    return (
      <div className="bento__state" role="status">
        Cargando el resumen del disco…
      </div>
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

  const [first, ...rest] = summary.data.volumes;

  return (
    <main className="bento" aria-label="Resumen del disco">
      <DiskCard row={first} hero />
      <UnexplainedCard row={first} />
      <ForecastCard row={first} />
      <GrowthCard row={first} />
      {rest.map((row) => (
        <DiskCard key={row.volume.name} row={row} hero={false} />
      ))}
      <PlanCard plan={plan} />
    </main>
  );
}
