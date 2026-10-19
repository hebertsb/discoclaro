import { useState } from "react";
import Resumen from "./components/Resumen";
import QueOcupa from "./components/QueOcupa";

// Navegación mínima de dos pestañas: la barra lateral/inferior real (brief
// de T3.2, sección 13.2) llega cuando haya más de dos o tres vistas.
type Screen = "resumen" | "que-ocupa";

const SCREENS: { id: Screen; label: string }[] = [
  { id: "resumen", label: "Resumen" },
  { id: "que-ocupa", label: "Qué ocupa" },
];

export default function App() {
  const [screen, setScreen] = useState<Screen>("resumen");

  return (
    <>
      <header
        style={{
          padding: "var(--space-5) var(--space-5) 0",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-5)",
          flexWrap: "wrap",
        }}
      >
        <h1 style={{ margin: 0, fontSize: 24 }}>discoclaro</h1>
        <nav aria-label="Pantallas" style={{ display: "flex", gap: "var(--space-2)" }}>
          {SCREENS.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setScreen(item.id)}
              aria-current={screen === item.id ? "page" : undefined}
              style={{
                minHeight: 44,
                padding: "0 var(--space-4)",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--color-border)",
                background: screen === item.id ? "var(--color-accent)" : "transparent",
                color: screen === item.id ? "#fff" : "var(--color-text-primary)",
                fontSize: 14,
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              {item.label}
            </button>
          ))}
        </nav>
      </header>
      {screen === "resumen" ? <Resumen /> : <QueOcupa />}
    </>
  );
}
