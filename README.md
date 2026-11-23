# discoclaro

Responde una sola pregunta de forma visual, rápida y segura: **¿por qué se llena mi disco?**

**discoclaro** es un analizador de almacenamiento, monitor de crecimiento histórico, inspector de salud física S.M.A.R.T. y asistente de limpieza inteligente para Windows. Funciona 100 % en local, sin telemetría y con confirmación nativa de Windows antes de cualquier borrado o comando.

---

## Características principales

- **Centro de Control (`01`)**: resumen en tiempo real de todas tus unidades (`C:`, `D:`, etc.), espacio libre/usado, pronóstico de llenado, mayor crecimiento reciente y **Salud Física de Discos (S.M.A.R.T.)** con lectura nativa NVMe (sin UAC): vida útil restante (`%`), desgaste, temperatura (`°C`), horas de encendido, TB escritos y bloques de reserva.
- **Mapa del Disco (`02`)**: explorador interactivo por carpetas, archivos grandes, archivos recientes y **detector de archivos duplicados** con huella rápida.
- **Categorías (`03`)**: clasificación automática de cachés y datos de desarrollo (`Docker`, `Ollama`, `pip`, `uv`, `npm`, `Gradle`, `Steam`, navegadores, etc.).
- **Crecimiento (`04`)**: evolución histórica entre fotos periódicas para detectar qué carpeta crece en silencio.
- **Recomendaciones (`05`)**: plan por niveles (`Seguro`, `Revisar`, `Mover a otro disco`) con limpieza segura en lote o individual.
- **Programas y Restos (`06`)**: inventario de aplicaciones instaladas y detección de carpetas residuales tras desinstalar.
- **Límites y Alertas (`07`)**: umbrales de espacio libre por unidad, presupuestos por herramienta y gestor de tareas programadas silenciosas en Windows (`pythonw.exe`).
- **Proyectos y Compactación (`08`)**:
  - Escáner de proyectos antiguos (`node_modules`, `.venv`, `target`, `build`).
  - Compactador asistido de discos virtuales WSL2 / Docker (`.vhdx`).
  - Asistente simulador de migración `C: → D:`.
  - Historial auditable de limpiezas con exportación CSV/HTML.
- **Terminal Integrada (`>_`)**: consola PowerShell integrada con cambio de unidad (`C:\` ↔ `D:\`), historial de comandos y confirmación nativa de Windows.
- **Papelera animada e icono en bandeja del sistema**: vacía la Papelera de reciclaje en vivo desde la barra superior y ejecuta el monitor en segundo plano sin ventanas negras.

---

## Principios de seguridad y privacidad

- **100 % local y sin telemetría**: solo escucha en `127.0.0.1:8347` y no envía datos a internet.
- **Confirmación nativa de Windows**: ninguna acción destructiva (enviar a papelera, vaciar papelera, ejecutar comandos o desinstalar) se ejecuta sin tu aprobación en un diálogo nativo de Windows fuera del navegador.
- **Privacidad**: solo indexa rutas, tamaños y fechas; nunca lee el contenido de tus archivos personales.

---

## Cómo usarlo (Windows 10 / 11)

### Opción 1: Ejecutable standalone (`DiscoClaro.exe`)

Si recibiste o descargaste `DiscoClaro.exe` (desde Releases):
1. Haz doble clic en `DiscoClaro.exe`.
2. Se abrirá automáticamente el dashboard en tu navegador (`http://127.0.0.1:8347`) sin ventanas de consola.

### Opción 2: Ejecutar desde el código fuente

Requisitos: **Python 3.12+** y **Node.js 20+** (solo si deseas recompilar el frontend).

```powershell
git clone <URL_DE_TU_REPOSITORIO>
cd discoclaro

# 1. Crear entorno virtual e instalar dependencias
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 2. Compilar el dashboard React (primera vez)
npm --prefix dashboard install
npm --prefix dashboard run build

# 3. Tomar tu primera foto del disco y abrir el dashboard
.\.venv\Scripts\python.exe -m discoclaro scan C:
.\.venv\Scripts\python.exe -m discoclaro dashboard
```

---

## Cómo compilar tu propio `DiscoClaro.exe`

El proyecto incluye el script de empaquetado con PyInstaller (e Inno Setup opcional):

```powershell
.\scripts\build_windows_exe.ps1
```

Esto genera:
- `dist\DiscoClaro.exe` (ejecutable único sin consola, con icono oficial y frontend integrado).
- Si tienes [Inno Setup](https://jrsoftware.org/isinfo.php) (`iscc`) instalado, también genera el instalador `Setup_DiscoClaro.exe` usando `scripts/installer.iss`.

---

## Pruebas y calidad

```powershell
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\pytest.exe
```

## Licencia

MIT. Ver [LICENSE](LICENSE).
