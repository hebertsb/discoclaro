import { useEffect, useState } from "react";
import { ApiError, fetchCategories } from "../api/client";
import type { CategoryTotalOut } from "../api/types";
import { categoryColor } from "../colors";
import { formatGb } from "../format";
import "../styles/bento.css";
import "../styles/categorias.css";

type Loadable<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

export default function Categorias() {
  const [state, setState] = useState<Loadable<CategoryTotalOut[]>>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    fetchCategories()
      .then((data) => {
        if (!cancelled) setState({ status: "ready", data });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message =
          error instanceof ApiError
            ? error.status === 404
              ? "Todavía no hay ninguna foto del disco (hace falta escanear antes)."
              : `No se pudo cargar (${error.status}): ${error.detail}`
            : "No se pudo conectar con la API local.";
        setState({ status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === "loading") {
    return (
      <div className="bento__state" role="status">
        Cargando categorías…
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
  if (state.data.length === 0) {
    return (
      <div className="bento__state">
        Ninguna carpeta de la última foto coincidió con el catálogo de reglas.
      </div>
    );
  }

  const biggest = Math.max(...state.data.map((row) => row.size_bytes));

  return (
    <main style={{ padding: "var(--space-5)" }} aria-label="Categorías">
      <section
        className="bento__card"
        aria-label="Categorías por tamaño"
        style={{ maxWidth: 900, margin: "0 auto" }}
      >
        <div className="bento__label">Qué tipo de cosas ocupan más</div>
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {state.data.map((row, index) => {
            const color = categoryColor(index);
            const widthPercent = biggest > 0 ? (row.size_bytes / biggest) * 100 : 0;
            return (
              <li key={row.category} className="cat-row">
                <span aria-hidden="true" className="cat-dot" style={{ background: color }} />
                <span className="cat-name">{row.category}</span>
                <span className="cat-size">{formatGb(row.size_bytes)}</span>
                <span className="cat-count">
                  {row.folders} carpeta{row.folders === 1 ? "" : "s"}
                </span>
                <div className="cat-bar-track">
                  <div
                    className="cat-bar-fill"
                    style={{ width: `${widthPercent}%`, background: color }}
                  />
                </div>
              </li>
            );
          })}
        </ul>
      </section>
    </main>
  );
}
