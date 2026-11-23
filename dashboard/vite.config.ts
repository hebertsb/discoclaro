import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En producción, el mismo servidor de la API sirve el HTML/JS ya construido
// (T3.8): un solo origen, sin CORS. En desarrollo hacen falta dos procesos
// (Vite para recarga en caliente, la API en otro puerto), así que este
// proxy reenvía las llamadas del navegador servidor a servidor, donde CORS
// no aplica, en vez de agregarle CORS a la API real.
const API_TARGET = "http://127.0.0.1:8347";
const API_PATHS = [
  "/volumes",
  "/snapshots",
  "/tree",
  "/categories",
  "/growth",
  "/summary",
  "/plan",
  "/recommendations",
  "/programs",
  "/leftovers",
  "/limits",
  "/actions",
  "/search",
  "/duplicates",
  "/schedule",
  "/dev-projects",
  "/vhdx",
  "/history",
  "/migration-assistant",
  "/recycle-bin",
  "/disk-health",
];

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: Object.fromEntries(API_PATHS.map((path) => [path, API_TARGET])),
  },
});
