import { useEffect, useState } from "react";
import {
  ApiError,
  cleanSafeBatch,
  fetchRecommendations,
  openInExplorer,
  runTerminalCommand,
  trashPaths,
} from "../api/client";
import type { RecommendationOut } from "../api/types";
import { formatGb } from "../format";
import "../styles/bento.css";
import "../styles/terminal.css";

type Loadable<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

function riskBadge(risk: RecommendationOut["risk"], level: RecommendationOut["level"]) {
  if (level === "move") {
    return <span className="term-badge term-badge--review">[MOVER A OTRO DISCO]</span>;
  }
  if (risk === "safe") {
    return <span className="term-badge term-badge--safe">[SAFE · REGENERABLE]</span>;
  }
  if (risk === "do_not_touch") {
    return <span className="term-badge term-badge--danger">[NO TOCAR]</span>;
  }
  return <span className="term-badge term-badge--review">[REVIEW · REVISAR]</span>;
}

export default function Recomendaciones({
  onSendToTerminal,
}: {
  onSendToTerminal?: (command: string, autoRun?: boolean) => void;
}) {
  const [state, setState] = useState<Loadable<RecommendationOut[]>>({
    status: "loading",
  });
  const [filterLevel, setFilterLevel] = useState<"all" | "safe" | "review" | "move">("all");
  const [copiedTarget, setCopiedTarget] = useState<string | null>(null);
  const [busyTarget, setBusyTarget] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  function loadRecs() {
    setState({ status: "loading" });
    fetchRecommendations()
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
    loadRecs();
  }, []);

  function copyCommand(target: string, command: string) {
    navigator.clipboard?.writeText(command).catch(() => {});
    setCopiedTarget(target);
    setTimeout(() => {
      setCopiedTarget((current) => (current === target ? null : current));
    }, 2000);
  }

  async function handleExecuteNow(item: RecommendationOut) {
    if (!item.suggested_command) return;
    if (onSendToTerminal) {
      onSendToTerminal(item.suggested_command, true);
      return;
    }
    setFeedback(null);
    setBusyTarget(item.target);
    try {
      const out = await runTerminalCommand(item.suggested_command);
      setFeedback(`✓ Ejecutado: ${out.command} (código ${out.exit_code}).`);
      loadRecs();
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

  async function handleTrashFolder(item: RecommendationOut) {
    if (!item.location_path) return;
    setFeedback(null);
    setBusyTarget(item.target);
    try {
      const batch = await trashPaths([item.location_path]);
      setFeedback(
        `✓ Enviado a la Papelera · Espacio liberado: ${formatGb(batch.total_freed_bytes)}.`,
      );
      loadRecs();
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

  if (state.status === "loading") {
    return (
      <main style={{ padding: "var(--space-5)", maxWidth: 1280, margin: "0 auto" }} aria-label="Cargando recomendaciones">
        <div className="bento__card" style={{ marginBottom: "var(--space-4)" }}>
          <div className="skeleton-block" style={{ height: 20, width: 240 }} />
          <div className="skeleton-block" style={{ height: 42, width: 300 }} />
        </div>
        <div className="bento__card">
          <div className="skeleton-block" style={{ height: 80, width: "100%" }} />
          <div className="skeleton-block" style={{ height: 80, width: "100%" }} />
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

  const totalRecoverable = state.data.reduce((sum, item) => sum + item.size_bytes, 0);
  const safeItems = state.data.filter((i) => i.level === "safe");
  const safeBytes = safeItems.reduce((sum, item) => sum + item.size_bytes, 0);
  const filtered =
    filterLevel === "all"
      ? state.data
      : state.data.filter((item) => item.level === filterLevel);

  async function handleCleanSafeBatch() {
    if (safeItems.length === 0) return;
    setFeedback(null);
    setBusyTarget("__batch_safe__");
    try {
      const res = await cleanSafeBatch();
      setFeedback(
        `✓ Limpieza en lote completada: ${res.succeeded_count} caché(s) limpiadas (~${formatGb(res.total_estimated_bytes)} recuperados).`,
      );
      loadRecs();
    } catch {
      setFeedback("Limpieza en lote cancelada o requiere reiniciar el servidor local.");
    } finally {
      setBusyTarget(null);
    }
  }

  return (
    <main style={{ padding: "var(--space-5)", maxWidth: 1280, margin: "0 auto" }} aria-label="Recomendaciones">
      <section className="bento__card" style={{ marginBottom: "var(--space-4)" }}>
        <div className="glass-toolbar">
          <div>
            <div className="bento__label">Plan de recuperación por niveles (T3.7)</div>
            <p className="bento__value">{formatGb(totalRecoverable)} recuperables</p>
            <p className="bento__detail">
              Ejecuta la limpieza directamente (con confirmación nativa de Windows), envía cachés a la
              Papelera o abre la carpeta exacta para mover el elemento a otro disco.
            </p>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            {safeItems.length > 0 && (
              <button
                type="button"
                className="term-btn term-btn--primary"
                disabled={busyTarget !== null}
                onClick={handleCleanSafeBatch}
              >
                {busyTarget === "__batch_safe__"
                  ? "Limpiando…"
                  : `⚡ Limpiar todas las cachés seguras (${formatGb(safeBytes)})`}
              </button>
            )}
            <div className="glass-tabs" role="tablist" aria-label="Filtrar por nivel">
            <button
              type="button"
              className={`glass-tab${filterLevel === "all" ? " glass-tab--active" : ""}`}
              onClick={() => setFilterLevel("all")}
            >
              Todos ({state.data.length})
            </button>
            <button
              type="button"
              className={`glass-tab${filterLevel === "safe" ? " glass-tab--active" : ""}`}
              onClick={() => setFilterLevel("safe")}
            >
              01 · Seguro ({state.data.filter((i) => i.level === "safe").length})
            </button>
            <button
              type="button"
              className={`glass-tab${filterLevel === "review" ? " glass-tab--active" : ""}`}
              onClick={() => setFilterLevel("review")}
            >
              02 · Revisar ({state.data.filter((i) => i.level === "review").length})
            </button>
            <button
              type="button"
              className={`glass-tab${filterLevel === "move" ? " glass-tab--active" : ""}`}
              onClick={() => setFilterLevel("move")}
            >
              03 · Mover ({state.data.filter((i) => i.level === "move").length})
            </button>
            </div>
          </div>
        </div>
        {feedback && (
          <p style={{ margin: "8px 0 0", fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--color-accent)" }}>
            {feedback}
          </p>
        )}
      </section>

      {filtered.length === 0 ? (
        <div className="bento__card">
          <p className="bento__detail">
            No hay recomendaciones pendientes en este filtro.
          </p>
        </div>
      ) : (
        <ul className="term-list" aria-label="Lista de recomendaciones">
          {filtered.map((item) => (
            <li key={item.target} className="term-row-card">
              <div className="term-row-card__header">
                <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0, flex: 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
                    {riskBadge(item.risk, item.level)}
                    <span className="term-path" style={{ fontWeight: 700, fontSize: 15 }}>
                      {item.program} :: {item.name}
                    </span>
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
                    <p className="bento__detail" style={{ marginTop: 2, color: "var(--color-text-primary)" }}>
                      👉 <strong>Guía:</strong> {item.suggested_note}
                    </p>
                  )}
                </div>

                <div
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 20,
                    fontWeight: 700,
                    color:
                      item.level === "safe"
                        ? "var(--color-risk-safe)"
                        : item.level === "move"
                          ? "var(--color-accent)"
                          : "var(--color-risk-review)",
                    flexShrink: 0,
                  }}
                >
                  {formatGb(item.size_bytes)}
                </div>
              </div>

              <div className="term-cmd-box">
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", minWidth: 0 }}>
                  {item.suggested_command ? (
                    <>
                      <span style={{ color: "#22c55e", fontWeight: 700 }}>PS C:\&gt;</span>
                      <code className="term-cmd-box__code">{item.suggested_command}</code>
                    </>
                  ) : (
                    <span style={{ color: "#94a3b8" }}>
                      {item.location_path
                        ? `Ubicación: ${item.location_path}`
                        : "Acción manual en el programa"}
                    </span>
                  )}
                </div>

                <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
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
                    <>
                      <button
                        type="button"
                        className="term-btn"
                        onClick={() => copyCommand(item.target, item.suggested_command!)}
                      >
                        {copiedTarget === item.target ? "✓ Copiado" : "Copiar comando"}
                      </button>
                      <button
                        type="button"
                        className="term-btn term-btn--primary"
                        disabled={busyTarget === item.target}
                        onClick={() => handleExecuteNow(item)}
                      >
                        {busyTarget === item.target
                          ? "Ejecutando…"
                          : ">_ Ejecutar limpieza ahora"}
                      </button>
                      {onSendToTerminal && (
                        <button
                          type="button"
                          className="term-btn"
                          onClick={() => onSendToTerminal(item.suggested_command!)}
                        >
                          Enviar a Terminal
                        </button>
                      )}
                    </>
                  )}

                  {item.location_path && item.level === "safe" && (
                    <button
                      type="button"
                      className="term-btn term-btn--danger"
                      disabled={busyTarget === item.target}
                      onClick={() => handleTrashFolder(item)}
                    >
                      Eliminar carpeta a Papelera
                    </button>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
