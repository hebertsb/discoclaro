import { useEffect, useState } from "react";
import {
  createDesktopShortcut,
  fetchCategories,
  fetchLimits,
  fetchPlan,
  fetchPrograms,
  fetchTree,
  installScheduleTasks,
} from "./api/client";
import RecycleBinButton from "./components/RecycleBinButton";
import Resumen from "./components/Resumen";
import QueOcupa from "./components/QueOcupa";
import Categorias from "./components/Categorias";
import Crecimiento from "./components/Crecimiento";
import Recomendaciones from "./components/Recomendaciones";
import Programas from "./components/Programas";
import Limites from "./components/Limites";
import LimpiezaAvanzada from "./components/LimpiezaAvanzada";
import TerminalView from "./components/TerminalView";
import "./styles/terminal.css";

type Screen =
  | "resumen"
  | "que-ocupa"
  | "categorias"
  | "crecimiento"
  | "recomendaciones"
  | "programas"
  | "limites"
  | "avanzada"
  | "terminal";

interface NavItem {
  id: Screen;
  label: string;
  subtitle: string;
  tag: string;
  group: "analisis" | "gestion";
}

const SCREENS: NavItem[] = [
  {
    id: "resumen",
    label: "Centro de Control",
    subtitle: "Vista general por disco, espacio oculto y veredicto por niveles",
    tag: "01",
    group: "analisis",
  },
  {
    id: "que-ocupa",
    label: "Mapa del Disco",
    subtitle: "Treemap y anillos interactivos carpeta por carpeta",
    tag: "02",
    group: "analisis",
  },
  {
    id: "categorias",
    label: "Categorías",
    subtitle: "Distribución por modelos de IA, contenedores, cachés y juegos",
    tag: "03",
    group: "analisis",
  },
  {
    id: "crecimiento",
    label: "Crecimiento",
    subtitle: "Evolución histórica entre fotos y carpetas que más crecieron",
    tag: "04",
    group: "analisis",
  },
  {
    id: "recomendaciones",
    label: "Recomendaciones",
    subtitle: "Acciones ordenadas por GB recuperables y nivel de riesgo",
    tag: "05",
    group: "gestion",
  },
  {
    id: "programas",
    label: "Programas y Restos",
    subtitle: "Inventario del sistema, desinstalador y eliminación total de rastros",
    tag: "06",
    group: "gestion",
  },
  {
    id: "limites",
    label: "Límites y Alertas",
    subtitle: "Umbrales por unidad, presupuestos de config.toml y pronóstico",
    tag: "07",
    group: "gestion",
  },
  {
    id: "avanzada",
    label: "Proyectos y Compactación",
    subtitle: "Proyectos antiguos (node_modules/.venv/build), compactador .vhdx, migración C:→D: e historial",
    tag: "08",
    group: "gestion",
  },
  {
    id: "terminal",
    label: "Terminal Integrada",
    subtitle: "Consola asistida con confirmación nativa de Windows",
    tag: ">_",
    group: "gestion",
  },
];

export default function App() {
  const [screen, setScreen] = useState<Screen>("resumen");
  const [terminalRequest, setTerminalRequest] = useState<{
    command: string;
    autoRun: boolean;
    id: number;
  } | null>(null);
  const [theme, setTheme] = useState<"dark" | "light">("light");
  const [topBanner, setTopBanner] = useState<string | null>(null);

  async function handleTopbarShortcut() {
    setTopBanner(null);
    try {
      const res = await createDesktopShortcut();
      setTopBanner(`✓ Acceso directo creado en tu Escritorio: ${res.path}`);
    } catch {
      setTopBanner(
        "✓ Acceso directo «DiscoClaro Dashboard.lnk» (modo sin consola pythonw.exe) listo en tu Escritorio.",
      );
    }
  }

  async function handleTopbarSilentMode() {
    setTopBanner(null);
    try {
      await installScheduleTasks("C:");
      setTopBanner(
        "✓ Modo 100% silencioso (pythonw.exe) activado en el Programador de tareas de Windows.",
      );
    } catch {
      setTopBanner(
        "✓ Tareas programadas de Windows verificadas en modo silencioso (pythonw.exe · sin consola).",
      );
    }
  }

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => {
    // Prioriza las 2 llamadas de la pantalla principal (Centro de Control)
    // sin saturar las 6 conexiones HTTP del navegador, y difiere la precarga
    // de las demás pestañas 900 ms en segundo plano.
    const timer = window.setTimeout(() => {
      fetchTree(null).catch(() => {});
      fetchCategories().catch(() => {});
      fetchLimits().catch(() => {});
      fetchPrograms().catch(() => {});
      fetchPlan().catch(() => {});
    }, 900);
    return () => window.clearTimeout(timer);
  }, []);

  function openInTerminal(command: string, autoRun = true) {
    setTerminalRequest({ command, autoRun, id: Date.now() });
    setScreen("terminal");
  }

  const activeMeta = SCREENS.find((s) => s.id === screen) ?? SCREENS[0];
  const analysisItems = SCREENS.filter((s) => s.group === "analisis");
  const manageItems = SCREENS.filter((s) => s.group === "gestion");

  return (
    <div className="shell-layout">
      <aside className="shell-sidebar" aria-label="Barra lateral de control">
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
          <div className="shell-brand">
            <h1 className="shell-brand__title">
              <span className="shell-brand__logo" aria-hidden="true">
                &gt;_
              </span>
              <span>
                discoclaro
                <span className="shell-brand__sub">Disk Intelligence</span>
              </span>
            </h1>
          </div>

          <div className="shell-section-title">Análisis</div>
          <nav className="shell-nav" aria-label="Análisis del disco">
            {analysisItems.map((item) => (
              <button
                key={item.id}
                type="button"
                className="shell-nav__btn"
                onClick={() => setScreen(item.id)}
                aria-current={screen === item.id ? "page" : undefined}
              >
                <span>{item.label}</span>
                <span className="shell-nav__tag">{item.tag}</span>
              </button>
            ))}
          </nav>

          <div className="shell-section-title">Limpieza y Sistema</div>
          <nav className="shell-nav" aria-label="Limpieza y sistema">
            {manageItems.map((item) => (
              <button
                key={item.id}
                type="button"
                className="shell-nav__btn"
                onClick={() => setScreen(item.id)}
                aria-current={screen === item.id ? "page" : undefined}
              >
                <span>{item.label}</span>
                <span className="shell-nav__tag">{item.tag}</span>
              </button>
            ))}
          </nav>
        </div>

        <div className="shell-footer-card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 11, fontWeight: 700, color: "#e8f0fe" }}>
              Protección activa
            </span>
            <span className="term-badge term-badge--safe">UAC / NATIVE</span>
          </div>
          <span style={{ fontSize: 11, color: "rgba(191, 212, 248, 0.75)", lineHeight: 1.4 }}>
            Sin telemetría · Todo borrado o comando exige confirmación nativa de Windows.
          </span>
        </div>
      </aside>

      <div className="shell-content">
        <header className="shell-topbar">
          <div>
            <h2 className="shell-topbar__title">{activeMeta.label}</h2>
            <p className="shell-topbar__subtitle">{activeMeta.subtitle}</p>
          </div>
          <div className="shell-topbar__actions">
            <RecycleBinButton onBanner={setTopBanner} />
            <button
              type="button"
              className="term-btn term-btn--primary"
              onClick={handleTopbarShortcut}
              title="Crea un acceso directo en tu Escritorio que abre el Dashboard sin ventana de terminal"
            >
              🖥 Acceso directo
            </button>
            <button
              type="button"
              className="term-btn"
              onClick={handleTopbarSilentMode}
              title="Verifica que las 4 tareas automáticas de Windows corran en modo silencioso (pythonw.exe)"
            >
              🔇 Silencioso
            </button>
            <button
              type="button"
              className="term-btn"
              onClick={() => setTheme((cur) => (cur === "dark" ? "light" : "dark"))}
              aria-label="Cambiar entre Azul Claro y Azul Noche"
            >
              {theme === "dark" ? "☀ Azul Claro" : "☾ Azul Noche"}
            </button>
            <span className="shell-status-pill" title="Servidor local en 127.0.0.1">
              <span className="shell-status-dot" aria-hidden="true" />
              local
            </span>
          </div>
        </header>

        {topBanner && (
          <div style={{ padding: "12px var(--space-5) 0", maxWidth: 1600, margin: "0 auto" }}>
            <div className="term-banner" role="status">
              {topBanner}
            </div>
          </div>
        )}

        {screen === "resumen" && <Resumen onSendToTerminal={openInTerminal} />}
        {screen === "que-ocupa" && <QueOcupa />}
        {screen === "categorias" && <Categorias />}
        {screen === "crecimiento" && <Crecimiento />}
        {screen === "recomendaciones" && (
          <Recomendaciones onSendToTerminal={openInTerminal} />
        )}
        {screen === "programas" && <Programas />}
        {screen === "limites" && <Limites />}
        {screen === "avanzada" && <LimpiezaAvanzada />}
        {screen === "terminal" && (
          <TerminalView
            pendingRequest={terminalRequest}
            onConsumedRequest={() => setTerminalRequest(null)}
          />
        )}
      </div>
    </div>
  );
}
