import { useEffect, useMemo, useState } from "react";
import { defineChart } from "@tanstack/charts";
import { treemap } from "@tanstack/charts/hierarchy/treemap";
import { tooltip } from "@tanstack/charts/tooltip";
import { Chart } from "@tanstack/charts/react";
import type { ChartPoint } from "@tanstack/charts";
import {
  ApiError,
  fetchDuplicates,
  fetchSearch,
  fetchSummary,
  fetchTree,
  openInExplorer,
  scanVolumeNow,
  trashPaths,
} from "../api/client";
import type {
  DuplicateGroupOut,
  DuplicatesOverviewOut,
  LargeFileOut,
  NodeOut,
  SearchResultsOut,
  TreeOut,
  VolumeSummaryOut,
} from "../api/types";
import { categoryColor } from "../colors";
import { formatGb, formatPercent } from "../format";
import "../styles/bento.css";
import "../styles/terminal.css";

const ROOT_ID = "__root__";

type SortMode = "size-desc" | "size-asc" | "alpha-asc" | "alpha-desc" | "files-desc";
type ExplorerTab = "folders" | "large-files" | "recent-files" | "duplicates";

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

function sortFolders(items: NodeOut[], mode: SortMode): NodeOut[] {
  const copy = [...items];
  switch (mode) {
    case "size-desc":
      return copy.sort((a, b) => b.size_bytes - a.size_bytes);
    case "size-asc":
      return copy.sort((a, b) => a.size_bytes - b.size_bytes);
    case "alpha-asc":
      return copy.sort((a, b) => shortName(a.path).localeCompare(shortName(b.path)));
    case "alpha-desc":
      return copy.sort((a, b) => shortName(b.path).localeCompare(shortName(a.path)));
    case "files-desc":
      return copy.sort((a, b) => b.n_files - a.n_files);
  }
}

function sortFiles(items: LargeFileOut[], mode: SortMode): LargeFileOut[] {
  const copy = [...items];
  switch (mode) {
    case "size-asc":
      return copy.sort((a, b) => a.size_bytes - b.size_bytes);
    case "alpha-asc":
      return copy.sort((a, b) => shortName(a.path).localeCompare(shortName(b.path)));
    case "alpha-desc":
      return copy.sort((a, b) => shortName(b.path).localeCompare(shortName(a.path)));
    default:
      return copy.sort((a, b) => b.size_bytes - a.size_bytes);
  }
}

function rowsFrom(tree: TreeOut): Row[] {
  const rootName = tree.node ? shortName(tree.node.path) : tree.volume;
  const topChildren = tree.children.slice(0, 18);
  const rootSize = topChildren.reduce((sum, child) => sum + child.size_bytes, 0);
  return [
    { id: ROOT_ID, parentId: undefined, name: rootName, size: rootSize },
    ...topChildren.map((child) => ({
      id: child.path,
      parentId: ROOT_ID,
      name: `${shortName(child.path)} (${formatGb(child.size_bytes)})`,
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
  const [volumes, setVolumes] = useState<VolumeSummaryOut[]>([]);
  const [activeVolume, setActiveVolume] = useState<string | null>(null);
  const [trail, setTrail] = useState<{ label: string; path: string }[]>([]);
  const [state, setState] = useState<Loadable<TreeOut>>({ status: "loading" });

  // Buscador global, pestañas de archivos grandes / recientes y ordenamiento
  const [activeTab, setActiveTab] = useState<ExplorerTab>("folders");
  const [sortMode, setSortMode] = useState<SortMode>("size-desc");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchData, setSearchData] = useState<SearchResultsOut | null>(null);
  const [searching, setSearching] = useState(false);
  const [scanningNow, setScanningNow] = useState(false);
  const [copiedPath, setCopiedPath] = useState<string | null>(null);
  const [duplicatesData, setDuplicatesData] = useState<DuplicatesOverviewOut | null>(null);
  const [dupStatusMsg, setDupStatusMsg] = useState<string | null>(null);
  const [busyDupFp, setBusyDupFp] = useState<string | null>(null);

  const currentPath = trail.at(-1)?.path ?? null;

  useEffect(() => {
    fetchSummary()
      .then((s) => {
        setVolumes(s.volumes);
        const withScan = s.volumes.find((v) => v.snapshot_id !== null);
        if (withScan && !activeVolume) {
          setActiveVolume(withScan.volume.name);
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });

    async function loadLevel(targetPath: string | null, vol: string | null) {
      let data = await fetchTree(targetPath, vol);
      if (targetPath === null && data.children.length === 1) {
        const onlyRoot = data.children[0];
        const expanded = await fetchTree(onlyRoot.path, vol).catch(() => null);
        if (expanded && expanded.children.length > 0) {
          data = expanded;
        }
      }
      if (!cancelled) {
        setState({ status: "ready", data });
      }
    }

    loadLevel(currentPath, activeVolume).catch((error: unknown) => {
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
  }, [currentPath, activeVolume]);

  useEffect(() => {
    let cancelled = false;
    setSearching(true);
    fetchSearch(searchQuery, activeVolume)
      .then((res) => {
        if (!cancelled) setSearchData(res);
      })
      .catch(() => {
        if (!cancelled) setSearchData(null);
      })
      .finally(() => {
        if (!cancelled) setSearching(false);
      });
    return () => {
      cancelled = true;
    };
  }, [searchQuery, activeVolume]);

  useEffect(() => {
    let cancelled = false;
    fetchDuplicates(activeVolume)
      .then((res) => {
        if (!cancelled) setDuplicatesData(res);
      })
      .catch(() => {
        // Fallback client-side agrupando large_files por tamaño exacto si el servidor no se reinició aún
        if (!cancelled && searchData) {
          const bySize = new Map<number, string[]>();
          for (const lf of searchData.large_files) {
            const arr = bySize.get(lf.size_bytes) ?? [];
            arr.push(lf.path);
            bySize.set(lf.size_bytes, arr);
          }
          const groups: DuplicateGroupOut[] = [];
          for (const [sz, paths] of bySize.entries()) {
            if (paths.length >= 2) {
              groups.push({
                filename: shortName(paths[0]),
                size_bytes: sz,
                reclaimable_bytes: sz * (paths.length - 1),
                fingerprint: `size:${sz}`,
                paths,
              });
            }
          }
          groups.sort((a, b) => b.reclaimable_bytes - a.reclaimable_bytes);
          setDuplicatesData({
            volume: activeVolume ?? "C:",
            total_reclaimable_bytes: groups.reduce((s, g) => s + g.reclaimable_bytes, 0),
            groups,
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [activeVolume, searchData]);

  async function handleTrashDuplicateCopies(group: DuplicateGroupOut) {
    if (group.paths.length < 2) return;
    const copiesToTrash = group.paths.slice(1);
    setBusyDupFp(group.fingerprint);
    setDupStatusMsg(null);
    try {
      const res = await trashPaths(copiesToTrash);
      setDupStatusMsg(
        `✓ Se enviaron ${copiesToTrash.length} copia(s) repetidas de «${group.filename}» a la Papelera (${formatGb(res.total_freed_bytes)} liberados). Se conservó el original: ${group.paths[0]}`,
      );
      const updated = await fetchDuplicates(activeVolume).catch(() => null);
      if (updated) setDuplicatesData(updated);
    } catch (error: unknown) {
      setDupStatusMsg(
        error instanceof ApiError && error.detail === "action_declined"
          ? "Cancelaste el envío a la Papelera en la ventana nativa de Windows."
          : "No se pudieron enviar las copias a la Papelera.",
      );
    } finally {
      setBusyDupFp(null);
    }
  }

  const realRows = state.status === "ready" ? rowsFrom(state.data) : [];
  const rows: Row[] =
    realRows.length > 0
      ? realRows
      : [{ id: ROOT_ID, parentId: undefined, name: "", size: 0 }];

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
            labelFill: "#ffffff",
            inset: 3,
            round: true,
            stroke: "var(--color-surface)",
          }),
        ],
        scales: { x: null, y: null },
        guides: false,
        margin: 0,
        tooltip,
      }),
    [rows],
  );

  function enterFolder(folder: NodeOut) {
    setActiveTab("folders");
    setSearchQuery("");
    setTrail((prev) => [
      ...prev,
      { label: shortName(folder.path), path: folder.path },
    ]);
  }

  function drillDownFromChart(point: ChartPoint<NodeDatum> | null) {
    const target = pointPath(point);
    if (target === null || state.status !== "ready") return;
    const child = state.data.children.find((c) => c.path === target);
    if (child) enterFolder(child);
  }

  function goToBreadcrumb(index: number) {
    if (index < 0) {
      setTrail([]);
    } else {
      setTrail((prev) => prev.slice(0, index + 1));
    }
  }

  function handleSelectVolume(volName: string) {
    setTrail([]);
    setActiveVolume(volName);
  }

  async function handleScanActiveVolume() {
    const target = activeVolume ?? "C:";
    setScanningNow(true);
    try {
      await scanVolumeNow(target);
      setTrail([]);
      const s = await fetchSummary();
      setVolumes(s.volumes);
      setActiveVolume(target);
    } catch {
      // ignore decline
    } finally {
      setScanningNow(false);
    }
  }

  function copyPath(path: string) {
    navigator.clipboard?.writeText(path).catch(() => {});
    setCopiedPath(path);
    setTimeout(() => {
      setCopiedPath((c) => (c === path ? null : c));
    }, 1800);
  }

  const children = state.status === "ready" ? state.data.children : [];
  const totalBytesInLevel = children.reduce((s, c) => s + c.size_bytes, 0);
  const currentTitle =
    state.status === "ready"
      ? (state.data.node?.path ?? `${state.data.volume}\\`)
      : (activeVolume ?? "C:");

  const displayedFolders = sortFolders(
    searchQuery.trim() && searchData ? searchData.folders : children,
    sortMode,
  );
  const displayedLargeFiles = sortFiles(
    searchData?.large_files ?? [],
    sortMode,
  );
  const displayedRecentFiles = sortFiles(
    searchData?.new_large_files ?? [],
    sortMode,
  );

  return (
    <main style={{ padding: "var(--space-5)", maxWidth: 1600, margin: "0 auto" }} aria-label="Qué ocupa">
      {/* Barra de control superior elegante (Glassy V7): 1 sola barra aireada y estructurada */}
      <section className="bento__card" style={{ marginBottom: "var(--space-4)", padding: "var(--space-4)" }}>
        <div className="glass-toolbar">
          {/* Grupo 1: Píldoras segmentadas de Unidad + botón Analizar */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <div className="glass-tabs" role="tablist" aria-label="Seleccionar unidad">
              {volumes.map((v) => (
                <button
                  key={v.volume.name}
                  type="button"
                  className={`glass-tab${activeVolume === v.volume.name ? " glass-tab--active" : ""}`}
                  onClick={() => handleSelectVolume(v.volume.name)}
                >
                  <span>Unidad {v.volume.name}</span>
                  <span style={{ opacity: 0.8, fontFamily: "var(--font-mono)", fontSize: 11 }}>
                    {formatGb(v.volume.free_bytes)} libres
                  </span>
                </button>
              ))}
            </div>
            <button
              type="button"
              className="term-btn"
              disabled={scanningNow}
              onClick={handleScanActiveVolume}
            >
              {scanningNow ? "Analizando…" : `↻ Analizar ${activeVolume ?? ""}`}
            </button>
          </div>

          {/* Grupo 2: Píldoras segmentadas de Vista (Carpetas / Archivos Grandes / Recientes) */}
          <div className="glass-tabs" role="tablist" aria-label="Modo de exploración">
            <button
              type="button"
              className={`glass-tab${activeTab === "folders" ? " glass-tab--active" : ""}`}
              onClick={() => setActiveTab("folders")}
            >
              Carpetas ({displayedFolders.length})
            </button>
            <button
              type="button"
              className={`glass-tab${activeTab === "large-files" ? " glass-tab--active" : ""}`}
              onClick={() => setActiveTab("large-files")}
            >
              Archivos Grandes ({searchData?.large_files.length ?? 0})
            </button>
            <button
              type="button"
              className={`glass-tab${activeTab === "recent-files" ? " glass-tab--active" : ""}`}
              onClick={() => setActiveTab("recent-files")}
            >
              Recientes ({searchData?.new_large_files.length ?? 0})
            </button>
            <button
              type="button"
              className={`glass-tab${activeTab === "duplicates" ? " glass-tab--active" : ""}`}
              onClick={() => setActiveTab("duplicates")}
            >
              Duplicados ({duplicatesData?.groups.length ?? 0})
            </button>
          </div>
        </div>

        {/* Sub-barra limpia: Buscador + Menú desplegable de ordenamiento + Ruta actual */}
        <div className="glass-toolbar" style={{ marginTop: 4 }}>
          <input
            type="search"
            className="term-input"
            placeholder="🔎 Buscar carpeta o archivo en toda la unidad (ej. docker, weights, chrome, .vhdx)…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            aria-label="Buscar carpeta o archivo"
          />

          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <label style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--color-text-secondary)" }}>
              <span>Ordenar por:</span>
              <select
                className="glass-select"
                value={sortMode}
                onChange={(e) => setSortMode(e.target.value as SortMode)}
                aria-label="Ordenar elementos"
              >
                <option value="size-desc">Mayor tamaño (GB ↓)</option>
                <option value="size-asc">Menor tamaño (GB ↑)</option>
                <option value="alpha-asc">Nombre A → Z</option>
                <option value="alpha-desc">Nombre Z → A</option>
                {activeTab === "folders" && (
                  <option value="files-desc">Mayor cantidad de archivos</option>
                )}
              </select>
            </label>

            {activeTab === "folders" && (
              <button
                type="button"
                className="term-btn"
                onClick={() => openInExplorer(currentTitle).catch(() => {})}
              >
                Abrir en Explorador →
              </button>
            )}
          </div>
        </div>

        {/* Migas de pan compactas */}
        {activeTab === "folders" && trail.length > 0 && (
          <nav aria-label="Ruta actual" style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <button
              type="button"
              className="term-btn"
              onClick={() => goToBreadcrumb(-1)}
            >
              Raíz {activeVolume ?? ""}
            </button>
            {trail.map((segment, index) => (
              <span key={segment.path} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <span style={{ color: "var(--color-text-muted)" }}>/</span>
                <button
                  type="button"
                  className={`term-btn${index === trail.length - 1 ? " term-btn--primary" : ""}`}
                  onClick={() => goToBreadcrumb(index)}
                >
                  {segment.label}
                </button>
              </span>
            ))}
          </nav>
        )}
      </section>

      {state.status === "loading" ? (
        <div className="bento__card">
          <div className="skeleton-block" style={{ height: 360, width: "100%" }} />
        </div>
      ) : state.status === "error" ? (
        <div className="bento__card">
          <p className="bento__detail">{state.message}</p>
        </div>
      ) : activeTab === "folders" ? (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))",
            gap: "var(--space-4)",
            alignItems: "start",
          }}
        >
          {/* Columna 1: Mapa de bloques interactivo */}
          <section className="bento__card" aria-label="Treemap">
            <div className="bento__label">Mapa de bloques :: {currentTitle}</div>
            <p className="bento__detail">
              Haz clic en un bloque o en cualquier fila de la derecha para entrar a esa carpeta.
            </p>
            <Chart
              definition={treemapDefinition}
              height={420}
              ariaLabel="Tamaño de las carpetas, en rectángulos"
              onSelect={drillDownFromChart}
            />
          </section>

          {/* Columna 2: Lista interactiva de carpetas con redirección directa */}
          <section className="bento__card" aria-label="Desglose de carpetas">
            <div className="bento__label">
              {searchQuery.trim()
                ? `Resultados de búsqueda para «${searchQuery}» (${displayedFolders.length})`
                : `Carpetas en ${currentTitle} (${displayedFolders.length})`}
            </div>
            <ul className="term-list" style={{ maxHeight: 460, overflowY: "auto", paddingRight: 4 }}>
              {displayedFolders.slice(0, 60).map((child, idx) => {
                const ratio = totalBytesInLevel > 0 ? (child.size_bytes / totalBytesInLevel) * 100 : 0;
                const color = categoryColor(idx);
                return (
                  <li key={child.path} className="term-row-card" style={{ padding: "var(--space-3)" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
                      <div
                        style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0, cursor: "pointer", flex: 1 }}
                        onClick={() => enterFolder(child)}
                      >
                        <span
                          aria-hidden="true"
                          style={{
                            width: 10,
                            height: 10,
                            borderRadius: "50%",
                            background: color,
                            flexShrink: 0,
                          }}
                        />
                        <strong className="term-path" style={{ fontSize: 14 }}>
                          {shortName(child.path)}
                        </strong>
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0, flexWrap: "wrap" }}>
                        <span style={{ fontFamily: "var(--font-mono)", fontWeight: 700, fontSize: 14 }}>
                          {formatGb(child.size_bytes)}
                        </span>
                        {!searchQuery.trim() && (
                          <span className="term-badge term-badge--safe">
                            {formatPercent(ratio)}
                          </span>
                        )}
                        <button
                          type="button"
                          className="term-btn"
                          onClick={() => enterFolder(child)}
                        >
                          Entrar
                        </button>
                        <button
                          type="button"
                          className="term-btn"
                          onClick={() => openInExplorer(child.path).catch(() => {})}
                          title="Abrir directamente donde está esta carpeta en el Explorador de Windows"
                        >
                          Ir a ubicación →
                        </button>
                      </div>
                    </div>

                    {!searchQuery.trim() && (
                      <div className="bento__bar" style={{ height: 6 }}>
                        <div
                          className="bento__bar-fill"
                          style={{ width: `${Math.min(Math.max(ratio, 1.5), 100)}%`, background: color }}
                        />
                      </div>
                    )}

                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--color-text-muted)" }}>
                      <span className="term-path" style={{ color: "var(--color-text-muted)" }}>
                        {child.path}
                      </span>
                      <span>{child.n_files.toLocaleString("es")} archivos</span>
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        </div>
      ) : activeTab === "duplicates" ? (
        <section className="bento__card" aria-label="Detector de archivos duplicados">
          <div className="term-row-card__header">
            <div>
              <div className="bento__label">
                Detector de Archivos Duplicados (Mismo tamaño + huella SHA-256)
              </div>
              <p className="bento__detail">
                Recuperable eliminando solo las copias repetidas (conservando siempre 1 copia original):{" "}
                <strong style={{ color: "var(--color-accent)" }}>
                  {formatGb(duplicatesData?.total_reclaimable_bytes ?? 0)}
                </strong>
              </p>
            </div>
          </div>

          {dupStatusMsg && (
            <div className="term-banner" role="status" style={{ marginTop: 8 }}>
              {dupStatusMsg}
            </div>
          )}

          {!duplicatesData || duplicatesData.groups.length === 0 ? (
            <p className="bento__detail" style={{ marginTop: 12 }}>
              ✓ No se detectaron archivos grandes ni instaladores duplicados en {activeVolume ?? "C:"}.
            </p>
          ) : (
            <ul className="term-list" style={{ marginTop: "var(--space-3)" }}>
              {duplicatesData.groups.map((group) => (
                <li key={group.fingerprint} className="term-row-card">
                  <div className="term-row-card__header">
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                        <span className="term-badge term-badge--review">
                          [{group.paths.length} COPIAS IDÉNTICAS]
                        </span>
                        <strong className="term-path" style={{ fontSize: 15 }}>
                          {group.filename}
                        </strong>
                      </div>
                      <p className="bento__detail" style={{ marginTop: 4 }}>
                        Tamaño de cada copia: {formatGb(group.size_bytes)} · Espacio recuperable:{" "}
                        <strong style={{ color: "var(--color-accent)" }}>
                          {formatGb(group.reclaimable_bytes)}
                        </strong>
                      </p>
                    </div>
                    <button
                      type="button"
                      className="term-btn term-btn--danger"
                      disabled={busyDupFp === group.fingerprint}
                      onClick={() => handleTrashDuplicateCopies(group)}
                    >
                      {busyDupFp === group.fingerprint
                        ? "Enviando a Papelera…"
                        : `🗑 Enviar ${group.paths.length - 1} copia(s) repetida(s) a Papelera`}
                    </button>
                  </div>

                  <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
                    {group.paths.map((p, idx) => (
                      <div
                        key={p}
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                          gap: 8,
                          flexWrap: "wrap",
                          fontSize: 12,
                        }}
                      >
                        <span className="term-path">
                          {idx === 0 ? (
                            <span className="term-badge term-badge--safe" style={{ marginRight: 6 }}>
                              [CONSERVAR ORIGINAL]
                            </span>
                          ) : (
                            <span className="term-badge term-badge--danger" style={{ marginRight: 6 }}>
                              [COPIA REPETIDA #{idx}]
                            </span>
                          )}
                          {p}
                        </span>
                        <button
                          type="button"
                          className="term-btn"
                          style={{ padding: "3px 8px", fontSize: 11 }}
                          onClick={() => openInExplorer(p).catch(() => {})}
                        >
                          Ver en Explorador →
                        </button>
                      </div>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : (
        /* Pestaña de Archivos Más Grandes o Archivos Recientes / Nuevos */
        <section className="bento__card" aria-label="Explorador de archivos grandes y recientes">
          <div className="bento__label">
            {activeTab === "large-files"
              ? `Archivos más grandes en ${activeVolume ?? currentTitle} (${displayedLargeFiles.length})`
              : `Archivos grandes recientes / nuevos detectados (${displayedRecentFiles.length})`}
          </div>
          <p className="bento__detail">
            Pulsa «Ir al archivo en Explorador» para abrir la carpeta exacta con el archivo seleccionado en Windows.
          </p>

          {searching ? (
            <div className="skeleton-block" style={{ height: 180, width: "100%" }} />
          ) : (activeTab === "large-files" ? displayedLargeFiles : displayedRecentFiles).length === 0 ? (
            <p className="bento__detail">
              {activeTab === "recent-files"
                ? "No se detectaron archivos grandes nuevos entre las dos últimas fotos de esta unidad."
                : "No se encontraron archivos grandes que coincidan con el filtro."}
            </p>
          ) : (
            <ul className="term-list" style={{ marginTop: "var(--space-2)" }}>
              {(activeTab === "large-files" ? displayedLargeFiles : displayedRecentFiles).map((file) => (
                <li key={file.path} className="term-row-card" style={{ padding: "var(--space-3)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                        <strong className="term-path" style={{ fontSize: 14 }}>
                          {shortName(file.path)}
                        </strong>
                        {file.is_new && (
                          <span className="term-badge term-badge--review">[NUEVO / RECIENTE]</span>
                        )}
                      </div>
                      <div className="term-path" style={{ color: "var(--color-text-secondary)", marginTop: 4 }}>
                        {file.path}
                      </div>
                    </div>

                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <strong style={{ fontFamily: "var(--font-mono)", fontSize: 16, color: "var(--color-accent)" }}>
                        {formatGb(file.size_bytes)}
                      </strong>
                      <button
                        type="button"
                        className="term-btn"
                        onClick={() => copyPath(file.path)}
                      >
                        {copiedPath === file.path ? "✓ Copiada" : "Copiar ruta"}
                      </button>
                      <button
                        type="button"
                        className="term-btn term-btn--primary"
                        onClick={() => openInExplorer(file.path).catch(() => {})}
                      >
                        Ir al archivo en Explorador →
                      </button>
                    </div>
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
