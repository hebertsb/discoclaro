import { useEffect, useState } from "react";
import { ApiError, fetchSummary, runTerminalCommand } from "../api/client";
import type { TerminalCommandOut, VolumeSummaryOut } from "../api/types";
import { formatGb } from "../format";
import "../styles/bento.css";
import "../styles/terminal.css";

interface HistoryEntry {
  id: number;
  timestamp: string;
  cwd: string;
  command: string;
  status: "running" | "done" | "error";
  result?: TerminalCommandOut;
  errorText?: string;
}

const QUICK_COMMANDS: { label: string; command: string; note: string }[] = [
  {
    label: "Listar archivos (dir)",
    command: "Get-ChildItem | Format-Table Name, Length, LastWriteTime -AutoSize",
    note: "Muestra los archivos y carpetas en el disco/directorio actual",
  },
  {
    label: "Espacio en discos",
    command: "Get-PSDrive -PSProvider FileSystem",
    note: "Muestra espacio usado y libre en C:, D:, etc.",
  },
  {
    label: "Estado de tareas",
    command: "discoclaro schedule status",
    note: "Verifica las 4 tareas programadas en segundo plano",
  },
  {
    label: "Revisar programas",
    command: "discoclaro programs-check",
    note: "Detecta instalaciones nuevas o restos de desinstalaciones",
  },
  {
    label: "Espacio Docker",
    command: "docker system df",
    note: "Muestra imágenes, contenedores y volúmenes reclamables",
  },
  {
    label: "Limpiar caché pip",
    command: "pip cache purge",
    note: "Vacía los paquetes descargados en la caché de pip",
  },
  {
    label: "Limpiar caché uv",
    command: "uv cache clean",
    note: "Vacía la caché de paquetes de uv",
  },
  {
    label: "Limpiar caché npm",
    command: "npm cache clean --force",
    note: "Vacía la caché local de paquetes npm",
  },
];

export default function TerminalView({
  initialCommand = "",
}: {
  initialCommand?: string;
}) {
  const [volumes, setVolumes] = useState<VolumeSummaryOut[]>([]);
  const [cwd, setCwd] = useState("C:\\");
  const [command, setCommand] = useState(initialCommand);
  const [running, setRunning] = useState(false);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [cmdHistory, setCmdHistory] = useState<string[]>([]);
  const [cmdCursor, setCmdCursor] = useState<number>(-1);

  useEffect(() => {
    fetchSummary()
      .then((s) => setVolumes(s.volumes))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (initialCommand) {
      setCommand(initialCommand);
    }
  }, [initialCommand]);

  async function handleSwitchDrive(driveName: string) {
    const root = driveName.endsWith("\\") ? driveName : `${driveName}\\`;
    setCwd(root);
    setHistory((prev) => [
      {
        id: Date.now(),
        timestamp: new Date().toLocaleTimeString(),
        cwd: root,
        command: driveName,
        status: "done",
        result: {
          command: `cd ${root}`,
          exit_code: 0,
          stdout: `Unidad activa cambiada a ${root}`,
          stderr: "",
          cwd: root,
        },
      },
      ...prev,
    ]);
  }

  async function handleExecute(cmdToRun: string) {
    const trimmed = cmdToRun.trim();
    if (!trimmed || running) return;

    if (trimmed.toLowerCase() === "cls" || trimmed.toLowerCase() === "clear") {
      setHistory([]);
      setCommand("");
      return;
    }

    setCmdHistory((prev) => [trimmed, ...prev.slice(0, 49)]);
    setCmdCursor(-1);

    const entryId = Date.now();
    const nowStr = new Date().toLocaleTimeString();
    const activeCwd = cwd;
    setRunning(true);
    setHistory((prev) => [
      {
        id: entryId,
        timestamp: nowStr,
        cwd: activeCwd,
        command: trimmed,
        status: "running",
      },
      ...prev,
    ]);

    try {
      const output = await runTerminalCommand(trimmed, activeCwd);
      if (output.cwd) {
        setCwd(output.cwd);
      }
      setCommand("");
      setHistory((prev) =>
        prev.map((item) =>
          item.id === entryId ? { ...item, status: "done", result: output } : item,
        ),
      );
    } catch (error: unknown) {
      const detail =
        error instanceof ApiError
          ? error.detail === "action_declined"
            ? "Ejecución cancelada por el usuario en la ventana nativa de Windows."
            : error.detail === "terminal_directory_not_found"
              ? "La carpeta o disco indicado no existe."
              : `Error (${error.status}): ${error.detail}`
          : "No se pudo conectar con la API local.";
      setHistory((prev) =>
        prev.map((item) =>
          item.id === entryId ? { ...item, status: "error", errorText: detail } : item,
        ),
      );
    } finally {
      setRunning(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (cmdHistory.length === 0) return;
      const nextIdx = Math.min(cmdCursor + 1, cmdHistory.length - 1);
      setCmdCursor(nextIdx);
      setCommand(cmdHistory[nextIdx]);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (cmdCursor <= 0) {
        setCmdCursor(-1);
        setCommand("");
      } else {
        const nextIdx = cmdCursor - 1;
        setCmdCursor(nextIdx);
        setCommand(cmdHistory[nextIdx]);
      }
    }
  }

  const activeDrive = cwd.slice(0, 2).toUpperCase();

  return (
    <main
      style={{ padding: "var(--space-5)", maxWidth: 1280, margin: "0 auto" }}
      aria-label="Terminal asistida"
    >
      <section className="term-window">
        <div className="term-window__bar">
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
            <span className="term-window__dots" aria-hidden="true">
              <span className="term-window__dot" style={{ background: "#ef4444" }} />
              <span className="term-window__dot" style={{ background: "#f59e0b" }} />
              <span className="term-window__dot" style={{ background: "#22c55e" }} />
            </span>
            <span>discoclaro :: PowerShell integrada (cualquier comando · confirmación nativa)</span>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            {/* Selector rápido de disco C: / D: */}
            <div style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <span style={{ fontSize: 11, color: "#94a3b8" }}>Disco activo:</span>
              {(volumes.length > 0
                ? volumes
                : [
                    { volume: { name: "C:", free_bytes: 0 } } as VolumeSummaryOut,
                    { volume: { name: "D:", free_bytes: 0 } } as VolumeSummaryOut,
                  ]
              ).map((v) => {
                const isSelected = activeDrive === v.volume.name.toUpperCase();
                return (
                  <button
                    key={v.volume.name}
                    type="button"
                    className={`term-btn${isSelected ? " term-btn--primary" : ""}`}
                    style={{ minHeight: 30, padding: "0 10px", fontSize: 11.5 }}
                    onClick={() => handleSwitchDrive(v.volume.name)}
                    title={`Cambiar directorio de trabajo a ${v.volume.name}\\`}
                  >
                    {v.volume.name}\
                    {v.volume.free_bytes > 0 && (
                      <span style={{ opacity: 0.85 }}>
                        ({formatGb(v.volume.free_bytes)})
                      </span>
                    )}
                  </button>
                );
              })}
            </div>

            {history.length > 0 && (
              <button
                type="button"
                className="term-btn"
                style={{ minHeight: 30, padding: "0 10px", fontSize: 11.5 }}
                onClick={() => setHistory([])}
              >
                Limpiar salida
              </button>
            )}
          </div>
        </div>

        <div className="term-window__body">
          <p style={{ color: "#94a3b8", marginTop: 0, fontSize: 12 }}>
            # Acepta cualquier comando o cmdlet de PowerShell (<code>dir</code>, <code>cd D:\</code>,{" "}
            <code>Remove-Item</code>, <code>del</code>, <code>docker</code>, <code>pip</code>,{" "}
            <code>git</code>, <code>cls</code>…). Usa ↑/↓ para tu historial. Cada comando pide
            confirmación nativa de Windows mostrando el disco activo.
          </p>

          {/* Atajos rápidos */}
          <div
            style={{
              display: "flex",
              gap: "var(--space-2)",
              flexWrap: "wrap",
              marginBottom: "var(--space-4)",
            }}
          >
            {QUICK_COMMANDS.map((q) => (
              <button
                key={q.label}
                type="button"
                className="term-btn"
                title={q.note}
                onClick={() => setCommand(q.command)}
              >
                &gt; {q.label}
              </button>
            ))}
          </div>

          {/* Entrada de comando */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleExecute(command);
            }}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2)",
              flexWrap: "wrap",
              background: "#0f172a",
              padding: "var(--space-2) var(--space-3)",
              borderRadius: "var(--radius-sm)",
              border: "1px solid #334155",
            }}
          >
            <span style={{ color: "#38bdf8", fontWeight: 700 }}>
              PS {cwd}&gt;
            </span>
            <input
              type="text"
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Escribe cualquier comando (ej. dir, D:, cd carpeta, del archivo, Remove-Item, docker system df)…"
              aria-label="Comando a ejecutar"
              style={{
                flex: 1,
                minWidth: 200,
                background: "transparent",
                border: "none",
                color: "#f8fafc",
                fontFamily: "var(--font-mono)",
                fontSize: 13,
                outline: "none",
              }}
            />
            <button
              type="submit"
              className="term-btn term-btn--primary"
              disabled={running || !command.trim()}
            >
              {running ? "Esperando confirmación…" : "Ejecutar"}
            </button>
          </form>

          {/* Historial de ejecuciones */}
          {history.length > 0 && (
            <div
              style={{
                marginTop: "var(--space-4)",
                display: "flex",
                flexDirection: "column",
                gap: "var(--space-3)",
              }}
            >
              {history.map((entry) => (
                <div
                  key={entry.id}
                  style={{
                    borderTop: "1px solid #1e293b",
                    paddingTop: "var(--space-3)",
                  }}
                >
                  <div style={{ color: "#38bdf8", marginBottom: 4 }}>
                    <span style={{ color: "#64748b" }}>[{entry.timestamp}]</span>{" "}
                    <span style={{ color: "#22c55e" }}>PS {entry.cwd}&gt;</span>{" "}
                    {entry.command}
                  </div>
                  {entry.status === "running" && (
                    <div style={{ color: "#fbbf24" }}>
                      Esperando confirmación en la ventana nativa de Windows…
                    </div>
                  )}
                  {entry.status === "error" && (
                    <div style={{ color: "#f87171" }}>
                      [BLOQUEADO / ERROR] {entry.errorText}
                    </div>
                  )}
                  {entry.status === "done" && entry.result && (
                    <div>
                      <div style={{ color: "#94a3b8", fontSize: 11, marginBottom: 4 }}>
                        Ejecutado: {entry.result.command} · código de salida:{" "}
                        {entry.result.exit_code}
                      </div>
                      {entry.result.stdout && (
                        <pre
                          style={{
                            margin: 0,
                            whiteSpace: "pre-wrap",
                            overflowWrap: "anywhere",
                            color: "#e2e8f0",
                          }}
                        >
                          {entry.result.stdout}
                        </pre>
                      )}
                      {entry.result.stderr && (
                        <pre
                          style={{
                            margin: "4px 0 0",
                            whiteSpace: "pre-wrap",
                            overflowWrap: "anywhere",
                            color: "#fca5a5",
                          }}
                        >
                          {entry.result.stderr}
                        </pre>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </main>
  );
}
