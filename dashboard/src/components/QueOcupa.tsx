import { useEffect, useMemo, useState } from "react";
import { defineChart } from "@tanstack/charts";
import { treemap } from "@tanstack/charts/hierarchy/treemap";
import { sunburst } from "@tanstack/charts/hierarchy/sunburst";
import { polar } from "@tanstack/charts/polar";
import { tooltip } from "@tanstack/charts/tooltip";
import { Chart } from "@tanstack/charts/react";
import type { ChartPoint } from "@tanstack/charts";
import { ApiError, fetchTree } from "../api/client";
import type { TreeOut } from "../api/types";
import "../styles/bento.css";

// Paleta del tema por defecto de momento: la paleta categórica validada de
// la skill `dataviz` (brief de T3.2) queda para cuando se resuelva cómo le
// pasa esta librería un rango de color propio a `color.scale`/`resolver`.
const ROOT_ID = "__root__";

interface Row {
  id: string;
  parentId: string | undefined;
  name: string;
  size: number;
}

function shortName(path: string): string {
  const trimmed = path.replace(/[\\/]+$/, "");
  const parts = trimmed.split(/[\\/]/);
  return parts.at(-1) || path;
}

function rowsFrom(tree: TreeOut): Row[] {
  const rootName = tree.node ? shortName(tree.node.path) : tree.volume;
  const rootSize = tree.children.reduce((sum, child) => sum + child.size_bytes, 0);
  return [
    { id: ROOT_ID, parentId: undefined, name: rootName, size: rootSize },
    ...tree.children.map((child) => ({
      id: child.path,
      parentId: ROOT_ID,
      name: shortName(child.path),
      size: child.size_bytes,
    })),
  ];
}

interface NodeDatum {
  id: string;
  data: Row | null;
}

function pointPath(point: ChartPoint<NodeDatum> | null): string | null {
  const id = point?.datum?.id;
  if (!id || id === ROOT_ID) return null;
  return id;
}

type Loadable<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

export default function QueOcupa() {
  const [path, setPath] = useState<string | null>(null);
  const [breadcrumb, setBreadcrumb] = useState<string[]>([]);
  const [state, setState] = useState<Loadable<TreeOut>>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    fetchTree(path)
      .then((data) => {
        if (!cancelled) setState({ status: "ready", data });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message =
          error instanceof ApiError
            ? `No se pudo cargar (${error.status}): ${error.detail}`
            : "No se pudo conectar con la API local.";
        setState({ status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  const realRows = state.status === "ready" ? rowsFrom(state.data) : [];
  // `treemap`/`sunburst` necesitan al menos un nodo raíz: con la carpeta
  // vacía (o mientras carga) se les da una raíz sin hijos en vez de un
  // arreglo vacío, que hace que la librería lance un error.
  const rows: Row[] =
    realRows.length > 0 ? realRows : [{ id: ROOT_ID, parentId: undefined, name: "", size: 0 }];

  const treemapDefinition = useMemo(
    () =>
      defineChart({
        marks: [
          treemap(rows, {
            nodeId: "id",
            parentId: "parentId",
            value: "size",
            color: (node) => node.id,
            label: (node) => node.name,
            // Contraste comprobado (skill tanstack-charts: "texto oscuro
            // sobre azul"): el color de texto del tema por defecto dio
            // ~4,49:1 contra el azul de una celda, por debajo de 4,5:1.
            // Blanco puro contra esa misma celda da ~5,17:1.
            labelFill: "#ffffff",
            inset: 2,
            round: true,
            stroke: "var(--color-surface)",
          }),
        ],
        scales: {
          x: null,
          y: null,
        },
        guides: false,
        margin: 0,
        tooltip,
      }),
    [rows],
  );

  const sunburstDefinition = useMemo(
    () =>
      defineChart({
        marks: [
          polar({
            marks: [
              sunburst(rows, {
                nodeId: "id",
                parentId: "parentId",
                value: "size",
                rootId: ROOT_ID,
                visibleDepth: 1,
                color: (node) => node.id,
                stroke: "var(--color-surface)",
              }),
            ],
            scales: { angle: null, radius: null },
          }),
        ],
        scales: {
          x: null,
          y: null,
        },
        tooltip,
      }),
    [rows],
  );

  function drillDown(point: ChartPoint<NodeDatum> | null) {
    const target = pointPath(point);
    if (target === null || state.status !== "ready") return;
    const child = state.data.children.find((c) => c.path === target);
    if (!child) return;
    setBreadcrumb((trail) => [...trail, shortName(child.path)]);
    setPath(child.path);
  }

  function goTo(index: number) {
    if (index < 0) {
      setBreadcrumb([]);
      setPath(null);
      return;
    }
    setBreadcrumb((trail) => trail.slice(0, index + 1));
    // La ruta real hay que reconstruirla desde las fotos: para un primer
    // nivel alcanza con volver a la raíz y bajar de nuevo navegando. Ver
    // nota de alcance en el plan.
    setPath(null);
  }

  if (state.status === "loading" && rows.length === 0) {
    return (
      <div className="bento__state" role="status">
        Cargando qué ocupa…
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
    <main style={{ padding: "var(--space-5)" }} aria-label="Qué ocupa">
      <nav aria-label="Ruta" style={{ marginBottom: "var(--space-4)", fontSize: 13 }}>
        <button
          type="button"
          onClick={() => goTo(-1)}
          style={{ background: "none", border: "none", color: "var(--color-accent)", cursor: "pointer", padding: 0 }}
        >
          Raíz
        </button>
        {breadcrumb.map((segment, index) => (
          <span key={`${segment}-${index}`}>
            {" / "}
            <button
              type="button"
              onClick={() => goTo(index)}
              style={{ background: "none", border: "none", color: "var(--color-accent)", cursor: "pointer", padding: 0 }}
            >
              {segment}
            </button>
          </span>
        ))}
      </nav>
      {realRows.length <= 1 ? (
        <p className="bento__detail">Esta carpeta no tiene subcarpetas que mostrar.</p>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr",
            gap: "var(--space-5)",
          }}
        >
          <section className="bento__card" aria-label="Treemap">
            <div className="bento__label">Treemap</div>
            <Chart
              definition={treemapDefinition}
              height={360}
              ariaLabel="Tamaño de las carpetas, en rectángulos"
              onSelect={drillDown}
            />
          </section>
          <section className="bento__card" aria-label="Sunburst">
            <div className="bento__label">Sunburst</div>
            <Chart
              definition={sunburstDefinition}
              height={360}
              ariaLabel="Tamaño de las carpetas, en anillos"
              onSelect={drillDown}
            />
          </section>
        </div>
      )}
    </main>
  );
}
