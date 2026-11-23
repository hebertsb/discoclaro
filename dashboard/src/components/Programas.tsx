import { useEffect, useState } from "react";
import {
  ApiError,
  fetchLeftovers,
  fetchProgramFootprints,
  fetchPrograms,
  openInExplorer,
  trashPaths,
  uninstallProgram,
} from "../api/client";
import type {
  FootprintOut,
  ProgramItemOut,
  ProgramsOverviewOut,
  TrashBatchOut,
  UninstallFlowOut,
} from "../api/types";
import { formatGb } from "../format";
import "../styles/bento.css";
import "../styles/terminal.css";

type Loadable<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

function confidenceBadge(confidence: FootprintOut["confidence"]) {
  if (confidence === "high") {
    return <span className="term-badge term-badge--safe">[CONFIANZA ALTA]</span>;
  }
  if (confidence === "medium") {
    return <span className="term-badge term-badge--review">[CONFIANZA MEDIA]</span>;
  }
  return <span className="term-badge term-badge--danger">[CONFIANZA BAJA]</span>;
}

function reasonLabel(reason: string): string {
  switch (reason) {
    case "install_location":
      return "Carpeta de instalación registrada";
    case "known_footprint":
      return "Huella medida al instalar";
    case "name_match":
      return "Nombre exacto del programa o editor";
    case "partial_name_match":
      return "Coincidencia parcial del nombre";
    case "near_install_date":
      return "Creada cerca de la fecha de instalación";
    default:
      return reason;
  }
}

export default function Programas() {
  const [state, setState] = useState<Loadable<ProgramsOverviewOut>>({
    status: "loading",
  });
  const [filterText, setFilterText] = useState("");
  const [expandedProgram, setExpandedProgram] = useState<string | null>(null);
  const [liveFootprints, setLiveFootprints] = useState<Record<string, FootprintOut[]>>({});
  const [loadingFootprintFor, setLoadingFootprintFor] = useState<string | null>(null);

  // Estado del flujo "Desinstalar y eliminar todo rastro"
  const [uninstallingName, setUninstallingName] = useState<string | null>(null);
  const [uninstallResult, setUninstallResult] = useState<UninstallFlowOut | null>(null);
  const [selectedTrashPaths, setSelectedTrashPaths] = useState<Record<string, boolean>>({});
  const [trashResult, setTrashResult] = useState<TrashBatchOut | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  // Buscador de restos de cualquier programa ya desinstalado antes
  const [searchLeftoverName, setSearchLeftoverName] = useState("");
  const [searchedLeftovers, setSearchedLeftovers] = useState<FootprintOut[] | null>(null);
  const [searchingLeftovers, setSearchingLeftovers] = useState(false);
  const [copiedPath, setCopiedPath] = useState<string | null>(null);

  function reloadOverview() {
    setState({ status: "loading" });
    fetchPrograms()
      .then((data) => setState({ status: "ready", data }))
      .catch((error: unknown) => {
        const message =
          error instanceof ApiError
            ? `No se pudo cargar (${error.status}): ${error.detail}`
            : "No se pudo conectar con la API local.";
        setState({ status: "error", message });
      });
  }

  useEffect(() => {
    reloadOverview();
  }, []);

  async function inspectProgram(prog: ProgramItemOut) {
    if (expandedProgram === prog.name) {
      setExpandedProgram(null);
      return;
    }
    setExpandedProgram(prog.name);
    if (liveFootprints[prog.name]) return;
    setLoadingFootprintFor(prog.name);
    try {
      const found = await fetchProgramFootprints(prog.name);
      setLiveFootprints((prev) => ({ ...prev, [prog.name]: found }));
    } catch {
      setLiveFootprints((prev) => ({ ...prev, [prog.name]: prog.footprints }));
    } finally {
      setLoadingFootprintFor(null);
    }
  }

  async function handleUninstallAndScan(prog: ProgramItemOut) {
    setActionMessage(null);
    setTrashResult(null);
    setUninstallResult(null);
    setUninstallingName(prog.name);
    try {
      const result = await uninstallProgram(prog.name);
      setUninstallResult(result);
      // Regla T4.7: solo los de confianza alta vienen marcados por defecto;
      // el usuario puede marcar también los de confianza media/baja para no dejar ni un rastro.
      const initialSelection: Record<string, boolean> = {};
      for (const fp of result.remaining_footprints) {
        initialSelection[fp.path] = fp.confidence === "high";
      }
      setSelectedTrashPaths(initialSelection);
      reloadOverview();
    } catch (error: unknown) {
      const detail =
        error instanceof ApiError
          ? error.detail === "action_declined"
            ? "Cancelaste la confirmación nativa de Windows."
            : `No se pudo desinstalar (${error.detail}).`
          : "Error de conexión con la API local.";
      setActionMessage(detail);
    } finally {
      setUninstallingName(null);
    }
  }

  async function handleCleanSelectedPaths(pathsToClean: string[]) {
    if (pathsToClean.length === 0) return;
    setActionMessage(null);
    try {
      const batch = await trashPaths(pathsToClean);
      setTrashResult(batch);
      if (uninstallResult) {
        const removedSet = new Set(
          batch.items.filter((i) => i.removed).map((i) => i.path),
        );
        const stillThere = uninstallResult.remaining_footprints.filter(
          (f) => !removedSet.has(f.path),
        );
        setUninstallResult({
          ...uninstallResult,
          remaining_footprints: stillThere,
          total_remaining_bytes: stillThere.reduce((s, f) => s + f.size_bytes, 0),
        });
      }
      if (searchedLeftovers) {
        const removedSet = new Set(
          batch.items.filter((i) => i.removed).map((i) => i.path),
        );
        setSearchedLeftovers(
          searchedLeftovers.filter((f) => !removedSet.has(f.path)),
        );
      }
      reloadOverview();
    } catch (error: unknown) {
      const detail =
        error instanceof ApiError
          ? error.detail === "action_declined"
            ? "Cancelaste el envío a la Papelera en la ventana de Windows."
            : `No se pudo limpiar (${error.detail}).`
          : "Error al enviar a la Papelera.";
      setActionMessage(detail);
    }
  }

  async function handleSearchLeftovers(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = searchLeftoverName.trim();
    if (!trimmed) return;
    setSearchingLeftovers(true);
    setActionMessage(null);
    try {
      const found = await fetchLeftovers(trimmed);
      setSearchedLeftovers(found);
      const sel: Record<string, boolean> = {};
      for (const fp of found) {
        sel[fp.path] = fp.confidence === "high";
      }
      setSelectedTrashPaths((prev) => ({ ...prev, ...sel }));
    } catch (error: unknown) {
      setActionMessage(
        error instanceof ApiError ? error.detail : "No se pudo buscar restos.",
      );
    } finally {
      setSearchingLeftovers(false);
    }
  }

  function handleCopyPath(path: string) {
    navigator.clipboard?.writeText(path).catch(() => {});
    setCopiedPath(path);
    setTimeout(() => {
      setCopiedPath((cur) => (cur === path ? null : cur));
    }, 1800);
  }

  async function handleOpenExplorer(path: string) {
    try {
      await openInExplorer(path);
    } catch {
      setActionMessage(`No se pudo abrir en el Explorador: ${path}`);
    }
  }

  if (state.status === "loading") {
    return (
      <main
        style={{ padding: "var(--space-5)", maxWidth: 1280, margin: "0 auto" }}
        aria-label="Cargando programas"
      >
        <div className="bento__card" style={{ marginBottom: "var(--space-4)" }}>
          <div className="skeleton-block" style={{ height: 22, width: 260 }} />
          <div className="skeleton-block" style={{ height: 42, width: "100%" }} />
        </div>
        <div className="bento__card">
          <div className="skeleton-block" style={{ height: 64, width: "100%" }} />
          <div className="skeleton-block" style={{ height: 64, width: "100%" }} />
          <div className="skeleton-block" style={{ height: 64, width: "100%" }} />
        </div>
      </main>
    );
  }
  if (state.status === "error") {
    return (
      <div className="bento__state" role="alert">
        {state.message}
      </div>
    );
  }

  const query = filterText.trim().toLowerCase();
  const filteredInstalled = state.data.installed.filter(
    (item) =>
      !query ||
      item.name.toLowerCase().includes(query) ||
      (item.publisher && item.publisher.toLowerCase().includes(query)) ||
      (item.install_location && item.install_location.toLowerCase().includes(query)),
  );

  return (
    <main
      style={{ padding: "var(--space-5)", maxWidth: 1280, margin: "0 auto" }}
      aria-label="Programas e instalaciones"
    >
      {/* Consola de estado o resultado de desinstalación y limpieza profunda */}
      {(actionMessage || uninstallResult || trashResult) && (
        <section className="term-window" style={{ marginBottom: "var(--space-5)" }} aria-label="Estado de desinstalación">
          <div className="term-window__bar">
            <span>discoclaro :: uninstaller &amp; deep-trace cleaner</span>
            <button
              type="button"
              className="term-btn"
              onClick={() => {
                setActionMessage(null);
                setUninstallResult(null);
                setTrashResult(null);
              }}
            >
              Cerrar panel
            </button>
          </div>
          <div className="term-window__body">
            {actionMessage && (
              <p style={{ color: "#fbbf24", margin: "0 0 var(--space-2)" }}>
                [AVISO] {actionMessage}
              </p>
            )}
            {trashResult && (
              <p style={{ color: "#22c55e", margin: "0 0 var(--space-3)", fontWeight: 700 }}>
                [VERIFICADO] Se enviaron {trashResult.items.filter((i) => i.removed).length} carpeta(s) a la
                Papelera de reciclaje · Espacio liberado: {formatGb(trashResult.total_freed_bytes)}
              </p>
            )}
            {uninstallResult && (
              <div>
                <p style={{ margin: "0 0 var(--space-2)" }}>
                  <span style={{ color: "#22c55e" }}>PS C:\&gt;</span>{" "}
                  <code>{uninstallResult.command_executed}</code> (código de salida:{" "}
                  {uninstallResult.uninstaller_exit_code})
                </p>
                {uninstallResult.remaining_footprints.length === 0 ? (
                  <p style={{ color: "#22c55e", margin: 0 }}>
                    ✓ Desinstalación limpia: no quedó ningún archivo, carpeta en AppData ni temporal
                    de «{uninstallResult.program_name}».
                  </p>
                ) : (
                  <div>
                    <p style={{ color: "#fbbf24", margin: "var(--space-2) 0" }}>
                      ! Se detectaron {uninstallResult.remaining_footprints.length} carpeta(s) residuales
                      de «{uninstallResult.program_name}» ({formatGb(uninstallResult.total_remaining_bytes)} en total),
                      incluyendo instalación, AppData y temporales:
                    </p>
                    <ul className="term-list" style={{ marginTop: "var(--space-3)" }}>
                      {uninstallResult.remaining_footprints.map((fp) => (
                        <li
                          key={fp.path}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                            gap: "var(--space-2)",
                            flexWrap: "wrap",
                            padding: "var(--space-2)",
                            border: "1px solid #1e293b",
                            borderRadius: 6,
                          }}
                        >
                          <label
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: "var(--space-2)",
                              cursor: "pointer",
                              minWidth: 0,
                              flex: 1,
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={Boolean(selectedTrashPaths[fp.path])}
                              onChange={(e) =>
                                setSelectedTrashPaths((prev) => ({
                                  ...prev,
                                  [fp.path]: e.target.checked,
                                }))
                              }
                            />
                            {confidenceBadge(fp.confidence)}
                            <span style={{ overflowWrap: "anywhere" }}>{fp.path}</span>
                          </label>
                          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                            <strong>{formatGb(fp.size_bytes)}</strong>
                            <button
                              type="button"
                              className="term-btn"
                              onClick={() => handleOpenExplorer(fp.path)}
                            >
                              Abrir
                            </button>
                          </div>
                        </li>
                      ))}
                    </ul>
                    <div style={{ display: "flex", gap: "var(--space-2)", marginTop: "var(--space-3)", flexWrap: "wrap" }}>
                      <button
                        type="button"
                        className="term-btn"
                        onClick={() => {
                          const all: Record<string, boolean> = {};
                          for (const fp of uninstallResult.remaining_footprints) {
                            all[fp.path] = true;
                          }
                          setSelectedTrashPaths((prev) => ({ ...prev, ...all }));
                        }}
                      >
                        Seleccionar todo (eliminar sin dejar rastro)
                      </button>
                      <button
                        type="button"
                        className="term-btn term-btn--danger"
                        onClick={() => {
                          const chosen = uninstallResult.remaining_footprints
                            .map((f) => f.path)
                            .filter((p) => selectedTrashPaths[p]);
                          handleCleanSelectedPaths(chosen);
                        }}
                      >
                        Eliminar restos seleccionados a la Papelera
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </section>
      )}

      {/* Buscador de restos y temporales de programas ya desinstalados */}
      <section className="bento__card" style={{ marginBottom: "var(--space-5)" }} aria-label="Cazador de restos">
        <div className="bento__label">Cazador de restos y archivos temporales (`discoclaro leftovers`)</div>
        <p className="bento__detail">
          Busca carpetas huérfanas en <code>%LOCALAPPDATA%</code>, <code>%LOCALAPPDATA%\Programs</code>,{" "}
          <code>%APPDATA%</code>, <code>%USERPROFILE%</code> y <code>%TEMP%</code> de cualquier programa
          y elimínalas sin dejar rastro.
        </p>
        <form
          onSubmit={handleSearchLeftovers}
          style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", marginTop: "var(--space-2)" }}
        >
          <input
            type="text"
            className="term-input"
            placeholder="Nombre del programa (ej. Ollama, 7-Zip, Docker, Buzz)…"
            value={searchLeftoverName}
            onChange={(e) => setSearchLeftoverName(e.target.value)}
            aria-label="Nombre del programa a buscar"
          />
          <button type="submit" className="term-btn term-btn--primary" disabled={searchingLeftovers}>
            {searchingLeftovers ? "Escaneando…" : ">_ Buscar restos y temporales"}
          </button>
        </form>

        {searchedLeftovers !== null && (
          <div style={{ marginTop: "var(--space-3)" }}>
            {searchedLeftovers.length === 0 ? (
              <p className="bento__detail">
                ✓ No se encontró ningún resto ni carpeta temporal para «{searchLeftoverName}».
              </p>
            ) : (
              <div>
                <ul className="term-list">
                  {searchedLeftovers.map((fp) => (
                    <li key={fp.path} className="term-row-card">
                      <div className="term-row-card__header">
                        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap", minWidth: 0 }}>
                          {confidenceBadge(fp.confidence)}
                          <span className="term-path">{fp.path}</span>
                          <span className="bento__detail">({reasonLabel(fp.reason)})</span>
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
                          <strong style={{ fontFamily: "var(--font-mono)" }}>
                            {formatGb(fp.size_bytes)}
                          </strong>
                          <button
                            type="button"
                            className="term-btn"
                            onClick={() => handleCopyPath(fp.path)}
                          >
                            {copiedPath === fp.path ? "✓ Copiada" : "Copiar ruta"}
                          </button>
                          <button
                            type="button"
                            className="term-btn"
                            onClick={() => handleOpenExplorer(fp.path)}
                          >
                            Abrir en Explorador
                          </button>
                          <button
                            type="button"
                            className="term-btn term-btn--danger"
                            onClick={() => handleCleanSelectedPaths([fp.path])}
                          >
                            Eliminar rastro
                          </button>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
                {searchedLeftovers.length > 1 && (
                  <div style={{ marginTop: "var(--space-3)" }}>
                    <button
                      type="button"
                      className="term-btn term-btn--danger"
                      onClick={() =>
                        handleCleanSelectedPaths(searchedLeftovers.map((f) => f.path))
                      }
                    >
                      Eliminar todos los {searchedLeftovers.length} restos encontrados (
                      {formatGb(searchedLeftovers.reduce((s, f) => s + f.size_bytes, 0))})
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </section>

      {/* Programas desinstalados con restos detectados en la base */}
      {state.data.uninstalled_with_leftovers.length > 0 && (
        <section className="bento__card" style={{ marginBottom: "var(--space-5)" }} aria-label="Desinstalados con restos">
          <div className="bento__label">Programas desinstalados con restos pendientes</div>
          <ul className="term-list" style={{ marginTop: "var(--space-2)" }}>
            {state.data.uninstalled_with_leftovers.map((rem) => (
              <li key={rem.name} className="term-row-card">
                <div className="term-row-card__header">
                  <div>
                    <strong style={{ fontSize: 15 }}>{rem.name}</strong>
                    {rem.publisher && <span className="bento__detail"> · {rem.publisher}</span>}
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                    <strong style={{ fontFamily: "var(--font-mono)" }}>
                      {formatGb(rem.total_leftover_bytes)}
                    </strong>
                    <button
                      type="button"
                      className="term-btn term-btn--danger"
                      onClick={() =>
                        handleCleanSelectedPaths(rem.footprints.map((f) => f.path))
                      }
                    >
                      Limpiar todos sus restos
                    </button>
                  </div>
                </div>
                {rem.footprints.map((fp) => (
                  <div
                    key={fp.path}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: "var(--space-2)",
                      flexWrap: "wrap",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
                      {confidenceBadge(fp.confidence)}
                      <span className="term-path">{fp.path}</span>
                    </div>
                    <div style={{ display: "flex", gap: "var(--space-2)" }}>
                      <button type="button" className="term-btn" onClick={() => handleCopyPath(fp.path)}>
                        Copiar ruta
                      </button>
                      <button type="button" className="term-btn" onClick={() => handleOpenExplorer(fp.path)}>
                        Explorador
                      </button>
                    </div>
                  </div>
                ))}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Inventario de programas instalados en el sistema */}
      <section className="bento__card" aria-label="Inventario de programas instalados">
        <div className="term-row-card__header" style={{ marginBottom: "var(--space-3)" }}>
          <div>
            <div className="bento__label">
              Programas instalados en el sistema ({filteredInstalled.length} de{" "}
              {state.data.installed.length})
            </div>
            <p className="bento__detail">
              Inspecciona todas las carpetas que un programa creó al instalarse (incluidos AppData y
              temporales) o desinstálalo del sistema limpiando sus restos.
            </p>
          </div>
          <input
            type="search"
            className="term-input"
            style={{ maxWidth: 320 }}
            placeholder="Filtrar por nombre, editor o ruta…"
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            aria-label="Filtrar programas instalados"
          />
        </div>

        <ul className="term-list">
          {filteredInstalled.map((prog) => {
            const isExpanded = expandedProgram === prog.name;
            const footprints = liveFootprints[prog.name] ?? prog.footprints;
            const liveTotal =
              footprints.length > 0
                ? Math.max(
                    prog.total_bytes,
                    footprints.reduce((s, f) => s + f.size_bytes, 0),
                  )
                : prog.total_bytes;

            return (
              <li key={`${prog.name}-${prog.version ?? ""}`} className="term-row-card">
                <div className="term-row-card__header">
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
                      <strong style={{ fontSize: 15 }}>{prog.name}</strong>
                      {prog.version && (
                        <span className="term-badge term-badge--safe">v{prog.version}</span>
                      )}
                      {prog.publisher && (
                        <span className="bento__detail">· {prog.publisher}</span>
                      )}
                    </div>
                    {prog.install_location && (
                      <div className="term-path" style={{ marginTop: 4, color: "var(--color-text-secondary)" }}>
                        {prog.install_location}
                      </div>
                    )}
                  </div>

                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontWeight: 700,
                        fontSize: 16,
                        marginRight: "var(--space-2)",
                      }}
                    >
                      {liveTotal > 0 ? formatGb(liveTotal) : "Tamaño bajo demanda"}
                    </span>
                    <button
                      type="button"
                      className="term-btn"
                      onClick={() => inspectProgram(prog)}
                    >
                      {isExpanded ? "Ocultar huella" : "Inspeccionar huella y Temp"}
                    </button>
                    {prog.install_location && (
                      <>
                        <button
                          type="button"
                          className="term-btn"
                          onClick={() => handleCopyPath(prog.install_location!)}
                        >
                          {copiedPath === prog.install_location ? "✓ Copiada" : "Copiar ruta"}
                        </button>
                        <button
                          type="button"
                          className="term-btn"
                          onClick={() => handleOpenExplorer(prog.install_location!)}
                        >
                          Explorador
                        </button>
                      </>
                    )}
                    {prog.uninstall_string && (
                      <button
                        type="button"
                        className="term-btn term-btn--danger"
                        disabled={uninstallingName === prog.name}
                        onClick={() => handleUninstallAndScan(prog)}
                      >
                        {uninstallingName === prog.name
                          ? "Desinstalando…"
                          : "Desinstalar + Limpiar rastro"}
                      </button>
                    )}
                  </div>
                </div>

                {isExpanded && (
                  <div
                    style={{
                      marginTop: "var(--space-2)",
                      paddingTop: "var(--space-3)",
                      borderTop: "1px solid var(--color-border)",
                      display: "flex",
                      flexDirection: "column",
                      gap: "var(--space-2)",
                    }}
                  >
                    {prog.uninstall_string && (
                      <div className="term-cmd-box">
                        <span style={{ color: "#94a3b8" }}>
                          Desinstalador registrado:{" "}
                          <code className="term-cmd-box__code">{prog.uninstall_string}</code>
                        </span>
                        <button
                          type="button"
                          className="term-btn"
                          onClick={() => handleCopyPath(prog.uninstall_string!)}
                        >
                          {copiedPath === prog.uninstall_string ? "✓ Copiado" : "Copiar comando"}
                        </button>
                      </div>
                    )}

                    {loadingFootprintFor === prog.name ? (
                      <p className="bento__detail">
                        Midiendo carpetas en Program Files, AppData, LocalAppData\Programs y Temp…
                      </p>
                    ) : footprints.length === 0 ? (
                      <p className="bento__detail">
                        No se detectaron carpetas adicionales en AppData ni Temp para este programa.
                      </p>
                    ) : (
                      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
                        <div className="bento__label">
                          Huella detectada en el sistema ({footprints.length} carpeta
                          {footprints.length === 1 ? "" : "s"} ·{" "}
                          {formatGb(footprints.reduce((s, f) => s + f.size_bytes, 0))})
                        </div>
                        {footprints.map((fp) => (
                          <div
                            key={fp.path}
                            style={{
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "space-between",
                              gap: "var(--space-2)",
                              flexWrap: "wrap",
                              padding: "var(--space-2)",
                              background: "var(--color-bg)",
                              borderRadius: "var(--radius-sm)",
                            }}
                          >
                            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap", minWidth: 0 }}>
                              {confidenceBadge(fp.confidence)}
                              <span className="term-path">{fp.path}</span>
                              <span className="bento__detail">({reasonLabel(fp.reason)})</span>
                            </div>
                            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                              <strong style={{ fontFamily: "var(--font-mono)" }}>
                                {formatGb(fp.size_bytes)}
                              </strong>
                              <button
                                type="button"
                                className="term-btn"
                                onClick={() => handleCopyPath(fp.path)}
                              >
                                {copiedPath === fp.path ? "✓" : "Copiar"}
                              </button>
                              <button
                                type="button"
                                className="term-btn"
                                onClick={() => handleOpenExplorer(fp.path)}
                              >
                                Abrir
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </section>
    </main>
  );
}
