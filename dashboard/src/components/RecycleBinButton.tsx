import { useEffect, useState } from "react";
import {
  ApiError,
  emptyRecycleBinNow,
  fetchRecycleBinStatus,
} from "../api/client";
import type { RecycleBinStatusOut } from "../api/types";
import { formatGb } from "../format";

const BYTES_PER_GB = 1024 ** 3;

function computeFillPercent(sizeBytes: number, itemCount: number): number {
  if (sizeBytes <= 0 && itemCount <= 0) return 0;
  const gb = sizeBytes / BYTES_PER_GB;
  // Escala visual progresiva: algo de basura se ve desde 18%, 10 GB+ llena el 100%
  if (gb < 0.05) return Math.min(24, Math.max(14, itemCount * 2));
  return Math.min(100, Math.round(18 + (Math.min(gb, 10) / 10) * 82));
}

function fillColorForGb(sizeBytes: number): string {
  const gb = sizeBytes / BYTES_PER_GB;
  if (gb >= 5) return "#ef4444"; // rojo intenso
  if (gb >= 1) return "#ff622e"; // naranja ámbar
  return "#f59e0b"; // dorado cálido
}

export default function RecycleBinButton({
  onBanner,
}: {
  onBanner: (msg: string | null) => void;
}) {
  const [binStatus, setBinStatus] = useState<RecycleBinStatusOut>({
    size_bytes: 0,
    item_count: 0,
  });
  const [phase, setPhase] = useState<"idle" | "filling" | "emptying" | "done">(
    "idle",
  );
  const [visualFill, setVisualFill] = useState(0);

  const loadBin = (animateFill = false) => {
    fetchRecycleBinStatus()
      .then((res) => {
        setBinStatus((prev) => {
          const grew =
            res.size_bytes > prev.size_bytes ||
            res.item_count > prev.item_count;
          if (animateFill || grew) {
            setPhase("filling");
            window.setTimeout(() => setPhase("idle"), 900);
          }
          return res;
        });
        setVisualFill(computeFillPercent(res.size_bytes, res.item_count));
      })
      .catch(() => {});
  };

  useEffect(() => {
    loadBin(false);
    const onTrashUpdated = () => loadBin(true);
    window.addEventListener("discoclaro:trash-updated", onTrashUpdated);
    // Refresca periódicamente por si el usuario borró archivos fuera de la app
    const interval = window.setInterval(() => loadBin(false), 15_000);
    return () => {
      window.removeEventListener("discoclaro:trash-updated", onTrashUpdated);
      window.clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleEmptyClick() {
    onBanner(null);
    if (binStatus.size_bytes === 0 && binStatus.item_count === 0) {
      onBanner(
        "✓ La Papelera de reciclaje de tu laptop ya está completamente vacía (0 GB).",
      );
      return;
    }

    setPhase("emptying");
    try {
      const res = await emptyRecycleBinNow();
      if (res.succeeded) {
        // Animación progresiva de vaciado del nivel de basura hasta 0%
        setVisualFill(0);
        setPhase("done");
        setBinStatus({ size_bytes: res.after_bytes, item_count: 0 });
        onBanner(
          `✓ Papelera vaciada por completo: ${res.before_items.toLocaleString("es")} elemento(s) eliminados · ${formatGb(res.freed_bytes)} recuperados en tus discos.`,
        );
        window.setTimeout(() => {
          setPhase("idle");
          loadBin(false);
        }, 1600);
      } else {
        setPhase("idle");
        onBanner(
          "⚠ No se pudo vaciar por completo la Papelera (puede haber algún archivo bloqueado).",
        );
      }
    } catch (error: unknown) {
      setPhase("idle");
      if (error instanceof ApiError && error.detail === "action_declined") {
        onBanner(
          "Cancelaste el vaciado de la Papelera en la ventana nativa de Windows.",
        );
      } else {
        onBanner("No se pudo vaciar la Papelera de reciclaje.");
      }
    }
  }

  const hasTrash = binStatus.size_bytes > 0 || binStatus.item_count > 0;
  const accentColor =
    phase === "done" ? "#10b981" : fillColorForGb(binStatus.size_bytes);
  // Coordenada Y del relleno dentro del cuerpo del basurero SVG (cuerpo va de y=9 a y=21, altura=12)
  const fillHeightSvg = (visualFill / 100) * 12.2;
  const fillYSvg = 21 - fillHeightSvg;

  return (
    <button
      type="button"
      className={`bin-btn bin-btn--${phase}${hasTrash ? " bin-btn--has-trash" : ""}`}
      disabled={phase === "emptying"}
      onClick={handleEmptyClick}
      title={
        hasTrash
          ? `Papelera con ${formatGb(binStatus.size_bytes)} (${binStatus.item_count} elementos). Haz clic para vaciarla.`
          : "La Papelera de reciclaje está vacía"
      }
    >
      {/* Relleno animado tipo nivel de carga en el fondo del botón */}
      <span
        className="bin-btn__bg-fill"
        aria-hidden="true"
        style={{
          height: `${visualFill}%`,
          background: accentColor,
        }}
      />

      {/* Basurero SVG interactivo con tapa articulada, relleno ondulado y partículas */}
      <span className="bin-btn__icon-wrap" aria-hidden="true">
        <svg
          width="22"
          height="22"
          viewBox="0 0 24 24"
          fill="none"
          className="bin-svg"
        >
          <defs>
            <clipPath id="discoclaro-bin-clip">
              <path d="M6.2 8.8 L17.8 8.8 L16.9 20.5 C16.85 21.1 16.35 21.5 15.75 21.5 L8.25 21.5 C7.65 21.5 7.15 21.1 7.1 20.5 Z" />
            </clipPath>
          </defs>

          {/* Partículas que saltan/caen al llenar o vaciar */}
          {(phase === "emptying" || phase === "filling") && (
            <g className="bin-particles">
              <circle cx="9.5" cy="5.5" r="1.1" fill={accentColor} />
              <circle cx="14.2" cy="4.2" r="1.2" fill="#fbbf24" />
              <circle cx="12" cy="6.2" r="0.9" fill="#ffffff" />
            </g>
          )}

          {/* Tapa del basurero (se abre al pasar el mouse o al vaciar/llenar) */}
          <g className="bin-lid">
            <path
              d="M9 5.2 L9.7 3.6 C9.9 3.2 10.3 3 10.7 3 L13.3 3 C13.7 3 14.1 3.2 14.3 3.6 L15 5.2"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
            />
            <line
              x1="4.2"
              y1="6.4"
              x2="19.8"
              y2="6.4"
              stroke="currentColor"
              strokeWidth="1.9"
              strokeLinecap="round"
            />
          </g>

          {/* Relleno dinámico dentro del basurero */}
          <g clipPath="url(#discoclaro-bin-clip)">
            <rect
              x="5"
              y={fillYSvg}
              width="14"
              height={fillHeightSvg + 2}
              fill={accentColor}
              fillOpacity="0.88"
              className="bin-fill-rect"
            />
            {visualFill > 4 && (
              <path
                d={`M4 ${fillYSvg} Q8 ${fillYSvg - 1.2} 12 ${fillYSvg} T20 ${fillYSvg} L20 23 L4 23 Z`}
                fill={accentColor}
                className="bin-wave"
              />
            )}
          </g>

          {/* Contorno del cuerpo del basurero + costillas */}
          <path
            d="M6 8.5 L18 8.5 L17 20.5 C16.95 21.15 16.4 21.6 15.75 21.6 L8.25 21.6 C7.6 21.6 7.05 21.15 7 20.5 Z"
            stroke="currentColor"
            strokeWidth="1.65"
            strokeLinejoin="round"
          />
          <line
            x1="10"
            y1="11.5"
            x2="10.3"
            y2="18.6"
            stroke="currentColor"
            strokeWidth="1.2"
            strokeLinecap="round"
            strokeOpacity="0.55"
          />
          <line
            x1="14"
            y1="11.5"
            x2="13.7"
            y2="18.6"
            stroke="currentColor"
            strokeWidth="1.2"
            strokeLinecap="round"
            strokeOpacity="0.55"
          />
        </svg>
      </span>

      <span className="bin-btn__text">
        {phase === "emptying" ? (
          <>Vaciando Papelera…</>
        ) : phase === "done" ? (
          <>✓ Papelera Vacía</>
        ) : hasTrash ? (
          <>
            Vaciar Papelera{" "}
            <strong className="bin-btn__amount">
              {formatGb(binStatus.size_bytes)}
            </strong>
            <span className="bin-btn__count">({binStatus.item_count})</span>
          </>
        ) : (
          <>Papelera · Vacía</>
        )}
      </span>
    </button>
  );
}
