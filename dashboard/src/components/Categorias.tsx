import { useEffect, useState } from "react";
import {
  ApiError,
  fetchCategories,
  fetchCategoriesDetail,
  fetchRecommendations,
  fetchSummary,
  openInExplorer,
  runTerminalCommand,
  trashPaths,
} from "../api/client";
import type {
  CategoriesDetailOut,
  CategorizedFolderOut,
  VolumeSummaryOut,
} from "../api/types";
import { categoryColor } from "../colors";
import { formatGb, formatPercent } from "../format";
import "../styles/bento.css";
import "../styles/categorias.css";
import "../styles/terminal.css";

type Loadable<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

const CATEGORY_METADATA: Record<
  string,
  { title: string; whatIsIt: string; whatCanYouDo: string }
> = {
  cache: {
    title: "Cachés de desarrollo y paquetes",
    whatIsIt:
      "Copias locales de paquetes ya descargados (pip, uv, npm, Gradle, VS Code). No son tu código fuente.",
    whatCanYouDo:
      "Puedes vaciarlas con seguridad para recuperar GB de inmediato; cada herramienta vuelve a bajar solo lo que vuelva a necesitar.",
  },
  ai_models: {
    title: "Modelos de Inteligencia Artificial local",
    whatIsIt:
      "Pesos de modelos grandes de IA (Ollama, Hugging Face, GGUF o el modelo integrado de Chrome).",
    whatCanYouDo:
      "Puedes borrar modelos que ya no uses o cambiar su carpeta a otro disco secundario.",
  },
  containers: {
    title: "Contenedores y discos virtuales (Docker / WSL)",
    whatIsIt:
      "Imágenes, contenedores y el disco virtual docker_data.vhdx donde corre Linux para Docker Desktop.",
    whatCanYouDo:
      "Puedes limpiar imágenes sin uso con Docker y compactar el archivo .vhdx para reducir su tamaño en disco.",
  },
  app_vms: {
    title: "Máquinas virtuales de aplicaciones",
    whatIsIt:
      "Entornos virtuales o bundles pesados que algunas aplicaciones de escritorio crean en AppData.",
    whatCanYouDo:
      "Revisa qué aplicación la usa; con la app cerrada se puede limpiar o reubicar.",
  },
  dependencies: {
    title: "Dependencias de proyectos (node_modules)",
    whatIsIt:
      "Librerías instaladas dentro de tus carpetas de código. Se pueden regenerar con npm/pnpm install.",
    whatCanYouDo:
      "En proyectos viejos o entregados puedes enviar node_modules a la Papelera para liberar varios GB.",
  },
  temp: {
    title: "Archivos temporales (%TEMP%)",
    whatIsIt:
      "Archivos de trabajo transitorios que los instaladores y programas dejan atrás.",
    whatCanYouDo:
      "Se pueden limpiar con seguridad (los archivos que estén abiertos ahora mismo se omiten).",
  },
  games: {
    title: "Juegos y bibliotecas (Steam)",
    whatIsIt:
      "Instalaciones de juegos que suelen ocupar decenas de GB cada uno.",
    whatCanYouDo:
      "Si están en el disco principal (C:), puedes moverlos a una biblioteca en tu segundo disco (D:).",
  },
  recycle_bin: {
    title: "Papelera de reciclaje ($Recycle.Bin)",
    whatIsIt:
      "Archivos que ya borraste pero siguen ocupando espacio hasta que vacíes la Papelera.",
    whatCanYouDo:
      "Revisa su contenido en el Explorador o vacía la Papelera para recuperar ese espacio definitivamente.",
  },
  installers: {
    title: "Instaladores descargados (.exe, .msi, .iso, .zip)",
    whatIsIt:
      "Instaladores en tu carpeta de Descargas que ya ejecutaste en el pasado.",
    whatCanYouDo:
      "Envía a la Papelera los instaladores de programas que ya tienes instalados.",
  },
  dev_tools: {
    title: "SDKs y emuladores de desarrollo (Android SDK)",
    whatIsIt:
      "Imágenes de sistema y emuladores de Android Studio / Flutter.",
    whatCanYouDo:
      "Si compilas directo en tu celular por cable USB, no necesitas las imágenes de emulador.",
  },
  backups: {
    title: "Copias de seguridad del sistema",
    whatIsIt: "Respaldos de Windows guardados en el disco.",
    whatCanYouDo:
      "No se borran directamente: gestiónalos desde el Panel de control de Copias de seguridad de Windows.",
  },
  system: {
    title: "Archivos críticos del sistema (hiberfil.sys, pagefile.sys, Installer)",
    whatIsIt:
      "Memoria virtual, hibernación y caché de Windows Installer. Protegidos por regla [NO TOCAR].",
    whatCanYouDo:
      "No se borran a mano: se ajustan desde Configuración avanzada del sistema o con powercfg /h off.",
  },
};

function riskBadge(risk: CategorizedFolderOut["risk"]) {
  if (risk === "safe") {
    return <span className="term-badge term-badge--safe">[SEGURO · REGENERABLE]</span>;
  }
  if (risk === "do_not_touch") {
    return <span className="term-badge term-badge--danger">[PROTEGIDO DEL SISTEMA]</span>;
  }
  return <span className="term-badge term-badge--review">[REVISAR ANTES]</span>;
}

export default function Categorias() {
  const [volumes, setVolumes] = useState<VolumeSummaryOut[]>([]);
  const [activeVolume, setActiveVolume] = useState<string | null>(null);
  const [state, setState] = useState<Loadable<CategoriesDetailOut>>({
    status: "loading",
  });
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [busyPath, setBusyPath] = useState<string | null>(null);

  useEffect(() => {
    loadDetail(null);
    fetchSummary()
      .then((s) => {
        setVolumes(s.volumes);
        const withScan = s.volumes.find((v) => v.snapshot_id !== null);
        if (withScan) {
          setActiveVolume(withScan.volume.name);
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function loadDetail(vol: string | null) {
    setState((prev) => (prev.status === "ready" ? prev : { status: "loading" }));
    fetchCategoriesDetail(vol)
      .then((data) => {
        setState({ status: "ready", data });
        if (data.totals.length > 0) {
          setSelectedCategory((prev) =>
            prev && data.totals.some((t) => t.category === prev)
              ? prev
              : data.totals[0].category,
          );
        } else {
          setSelectedCategory(null);
        }
      })
      .catch(async (error: unknown) => {
        // Si el servidor aún no tenía `/categories/detail`, mostramos `/categories`
        // de inmediato sin esperar a `/recommendations`.
        try {
          const basicTotals = await fetchCategories();
          if (basicTotals.length > 0) {
            const fallbackData: CategoriesDetailOut = {
              volume: vol ?? "C:",
              snapshot_id: 0,
              totals: basicTotals,
              items: [],
            };
            setState({ status: "ready", data: fallbackData });
            setSelectedCategory((prev) =>
              prev && basicTotals.some((t) => t.category === prev)
                ? prev
                : basicTotals[0].category,
            );
            // Carga las carpetas recomendadas en segundo plano sin bloquear la pantalla
            fetchRecommendations()
              .then((recs) => {
                const programToCategory: Record<string, string> = {
                  pip: "cache",
                  uv: "cache",
                  npm: "cache",
                  docker: "app_vms",
                  ollama: "ai_models",
                  steam: "games",
                };
                const fallbackItems: CategorizedFolderOut[] = recs
                  .filter((r) => r.location_path)
                  .map((r) => ({
                    path: r.location_path!,
                    size_bytes: r.size_bytes,
                    n_files: 1,
                    category: programToCategory[r.program] ?? "cache",
                    rule_id: r.rule_id ?? r.program,
                    rule_name: `${r.program} · ${r.name}`,
                    risk: r.risk,
                    explanation: r.explanation,
                    suggested_command: r.suggested_command,
                  }));
                setState({
                  status: "ready",
                  data: { ...fallbackData, items: fallbackItems },
                });
              })
              .catch(() => {});
            return;
          }
        } catch {
          // continúa al mensaje de error real
        }
        const message =
          error instanceof ApiError
            ? error.status === 404
              ? "Todavía no hay ninguna foto de esta unidad. Analízala primero."
              : `No se pudo cargar (${error.status}): ${error.detail}`
            : "No se pudo conectar con la API local.";
        setState({ status: "error", message });
      });
  }

  function handleSelectVolumeTab(volName: string) {
    setActiveVolume(volName);
    loadDetail(volName);
  }

  async function handleRunSuggestedCommand(folder: CategorizedFolderOut) {
    if (!folder.suggested_command) return;
    setStatusMessage(null);
    setBusyPath(folder.path);
    try {
      const out = await runTerminalCommand(folder.suggested_command);
      setStatusMessage(
        `✓ Comando ejecutado (${out.command}) · código ${out.exit_code}.`,
      );
      loadDetail(activeVolume);
    } catch (error: unknown) {
      const msg =
        error instanceof ApiError
          ? error.detail === "action_declined"
            ? "Cancelaste la confirmación en la ventana nativa de Windows."
            : `No se pudo ejecutar (${error.detail}).`
          : "Error al ejecutar comando.";
      setStatusMessage(msg);
    } finally {
      setBusyPath(null);
    }
  }

  async function handleTrashFolder(folder: CategorizedFolderOut) {
    if (folder.risk === "do_not_touch") return;
    setStatusMessage(null);
    setBusyPath(folder.path);
    try {
      const batch = await trashPaths([folder.path]);
      setStatusMessage(
        `✓ Enviado a la Papelera de reciclaje · Espacio liberado: ${formatGb(batch.total_freed_bytes)}.`,
      );
      loadDetail(activeVolume);
    } catch (error: unknown) {
      const msg =
        error instanceof ApiError
          ? error.detail === "action_declined"
            ? "Cancelaste el envío a la Papelera en la ventana nativa de Windows."
            : `No se pudo enviar a la Papelera (${error.detail}).`
          : "Error al enviar a la Papelera.";
      setStatusMessage(msg);
    } finally {
      setBusyPath(null);
    }
  }

  if (state.status === "loading") {
    return (
      <main style={{ padding: "var(--space-5)", maxWidth: 1600, margin: "0 auto" }} aria-label="Cargando categorías">
        <div className="bento__card">
          <div className="skeleton-block" style={{ height: 320, width: "100%" }} />
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
  if (state.data.totals.length === 0) {
    return (
      <div className="bento__state">
        Ninguna carpeta de la última foto coincidió con el catálogo de reglas.
      </div>
    );
  }

  const { totals, items } = state.data;
  const biggest = Math.max(...totals.map((row) => row.size_bytes));
  const totalCategorizedBytes = totals.reduce((s, r) => s + r.size_bytes, 0);
  const activeCat = selectedCategory ?? totals[0].category;
  const meta = CATEGORY_METADATA[activeCat] ?? {
    title: activeCat,
    whatIsIt: "Categoría clasificada por el catálogo de reglas de discoclaro.",
    whatCanYouDo:
      "Inspecciona cada carpeta abajo para abrirla en el Explorador o limpiarla si ya no la necesitas.",
  };
  const foldersInCategory = items.filter((item) => item.category === activeCat);

  return (
    <main style={{ padding: "var(--space-5)", maxWidth: 1600, margin: "0 auto" }} aria-label="Categorías">
      {/* Barra superior explicativa + selector de unidad */}
      <section className="bento__card" style={{ marginBottom: "var(--space-4)", padding: "var(--space-4)" }}>
        <div className="glass-toolbar">
          <div>
            <div className="bento__label">
              ¿Para qué sirve esta vista? · {formatGb(totalCategorizedBytes)} clasificados en {totals.length} categorías
            </div>
            <p className="bento__detail" style={{ marginTop: 4 }}>
              Agrupa automáticamente las carpetas de tu disco según qué herramienta las creó (cachés, modelos de IA,
              máquinas virtuales, temporales, dependencias). <strong>Haz clic en cualquier categoría</strong> para ver
              sus carpetas exactas, abrir dónde están o limpiarlas.
            </p>
          </div>
          {volumes.filter((v) => v.snapshot_id !== null).length > 1 && (
            <div className="glass-tabs">
              {volumes
                .filter((v) => v.snapshot_id !== null)
                .map((v) => (
                  <button
                    key={v.volume.name}
                    type="button"
                    className={`glass-tab${activeVolume === v.volume.name ? " glass-tab--active" : ""}`}
                    onClick={() => handleSelectVolumeTab(v.volume.name)}
                  >
                    Unidad {v.volume.name}
                  </button>
                ))}
            </div>
          )}
        </div>
        {statusMessage && (
          <p style={{ margin: "8px 0 0", fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--color-accent)" }}>
            {statusMessage}
          </p>
        )}
      </section>

      {/* Maquetación de 2 columnas: Izquierda ranking interactivo · Derecha inspector y acciones */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))",
          gap: "var(--space-4)",
          alignItems: "start",
        }}
      >
        {/* Columna izquierda: Lista de categorías clicables */}
        <section className="bento__card" aria-label="Categorías por tamaño">
          <div className="bento__label">1. Selecciona una categoría para inspeccionarla</div>
          <ul className="term-list" style={{ marginTop: "var(--space-2)" }}>
            {totals.map((row, index) => {
              const color = categoryColor(index);
              const widthPercent = biggest > 0 ? (row.size_bytes / biggest) * 100 : 0;
              const sharePercent =
                totalCategorizedBytes > 0
                  ? (row.size_bytes / totalCategorizedBytes) * 100
                  : 0;
              const isSelected = row.category === activeCat;
              const rowMeta = CATEGORY_METADATA[row.category];

              return (
                <li
                  key={row.category}
                  className="term-row-card"
                  onClick={() => setSelectedCategory(row.category)}
                  style={{
                    padding: "var(--space-3) var(--space-4)",
                    cursor: "pointer",
                    borderColor: isSelected ? "var(--color-accent)" : undefined,
                    boxShadow: isSelected ? "0 0 0 1px var(--color-accent)" : undefined,
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "var(--space-2)" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
                      <span
                        aria-hidden="true"
                        style={{
                          width: 12,
                          height: 12,
                          borderRadius: "50%",
                          background: color,
                          flexShrink: 0,
                        }}
                      />
                      <div>
                        <strong style={{ fontSize: 14 }}>
                          {rowMeta ? rowMeta.title : row.category}
                        </strong>
                        <span
                          style={{
                            display: "block",
                            fontFamily: "var(--font-mono)",
                            fontSize: 11,
                            color: "var(--color-text-muted)",
                          }}
                        >
                          [{row.category}] · {row.folders} carpeta{row.folders === 1 ? "" : "s"}
                        </span>
                      </div>
                    </div>

                    <div style={{ textAlign: "right", flexShrink: 0 }}>
                      <strong style={{ fontFamily: "var(--font-mono)", fontSize: 16 }}>
                        {formatGb(row.size_bytes)}
                      </strong>
                      <span
                        style={{
                          display: "block",
                          fontSize: 11,
                          color: "var(--color-text-muted)",
                        }}
                      >
                        {formatPercent(sharePercent)} del total
                      </span>
                    </div>
                  </div>

                  <div className="bento__bar" style={{ height: 7 }}>
                    <div
                      className="bento__bar-fill"
                      style={{ width: `${widthPercent}%`, background: color }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        </section>

        {/* Columna derecha: Detalle de la categoría elegida con sus carpetas y botones de acción */}
        <section className="bento__card" aria-label="Carpetas de la categoría seleccionada">
          <div>
            <div className="bento__label">2. Qué es y qué puedes hacer con «{activeCat}»</div>
            <h3 style={{ margin: "6px 0 4px", fontSize: 18 }}>{meta.title}</h3>
            <p className="bento__detail">{meta.whatIsIt}</p>
            <p
              className="bento__detail"
              style={{
                marginTop: 6,
                padding: "8px 12px",
                borderRadius: 10,
                background: "var(--color-accent-soft)",
                color: "var(--color-text-primary)",
              }}
            >
              💡 <strong>Acción recomendada:</strong> {meta.whatCanYouDo}
            </p>
          </div>

          <div className="bento__label" style={{ marginTop: "var(--space-2)" }}>
            Carpetas detectadas en esta categoría ({foldersInCategory.length})
          </div>

          {foldersInCategory.length === 0 ? (
            <div className="term-row-card">
              <p className="bento__detail">
                Para ver la ruta exacta de cada carpeta individual de «{activeCat}» y habilitar los
                botones de limpieza directa, <strong>reinicia el comando <code>discoclaro dashboard</code> en tu terminal</strong>{" "}
                (el servidor actual sigue corriendo con la versión anterior en memoria), o explora la unidad desde{" "}
                <strong>Mapa del Disco</strong>.
              </p>
            </div>
          ) : (
            <ul className="term-list">
              {foldersInCategory.map((folder) => (
                <li key={folder.path} className="term-row-card">
                  <div className="term-row-card__header">
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                        {riskBadge(folder.risk)}
                        <strong style={{ fontSize: 14 }}>{folder.rule_name}</strong>
                      </div>
                      <div className="term-path" style={{ marginTop: 6 }}>
                        {folder.path}
                      </div>
                      <p className="bento__detail" style={{ marginTop: 4 }}>
                        {folder.explanation} · {folder.n_files.toLocaleString("es")} archivos
                      </p>
                    </div>

                    <strong
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 18,
                        color: "var(--color-accent)",
                        flexShrink: 0,
                      }}
                    >
                      {formatGb(folder.size_bytes)}
                    </strong>
                  </div>

                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                    <button
                      type="button"
                      className="term-btn"
                      onClick={() => openInExplorer(folder.path).catch(() => {})}
                    >
                      Abrir ubicación en Explorador →
                    </button>

                    {folder.suggested_command && (
                      <button
                        type="button"
                        className="term-btn term-btn--primary"
                        disabled={busyPath === folder.path}
                        onClick={() => handleRunSuggestedCommand(folder)}
                      >
                        {busyPath === folder.path
                          ? "Ejecutando…"
                          : `>_ Ejecutar: ${folder.suggested_command}`}
                      </button>
                    )}

                    {folder.risk !== "do_not_touch" && (
                      <button
                        type="button"
                        className="term-btn term-btn--danger"
                        disabled={busyPath === folder.path}
                        onClick={() => handleTrashFolder(folder)}
                      >
                        Enviar a la Papelera
                      </button>
                    )}
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
