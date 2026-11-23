# DiscoClaro

[![Release](https://img.shields.io/github/v/release/hebertsb/discoclaro?label=Release&color=2563eb)](https://github.com/hebertsb/discoclaro/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-10b981.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011-0284c7.svg)](https://github.com/hebertsb/discoclaro/releases)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776ab.svg)](pyproject.toml)
[![Frontend](https://img.shields.io/badge/Frontend-React%20%2B%20TypeScript%20%2B%20Vite-3b82f6.svg)](dashboard/package.json)

**DiscoClaro** es una aplicación Open Source de análisis de almacenamiento, monitoreo histórico de crecimiento, diagnóstico físico **S.M.A.R.T. NVMe** y limpieza segura para **Windows 10 y 11**.

Responde de forma visual, rápida y auditable a una pregunta concreta: **¿por qué se llena mi disco y qué puedo liberar sin romper mi sistema?**

> **[Descargar DiscoClaro.exe (Windows v1.0.0)](https://github.com/hebertsb/discoclaro/releases/download/v1.0.0/DiscoClaro.exe)** — Ejecutable standalone (sin instalar Python ni Node.js).

---

## Tabla de contenidos

- [Características principales](#características-principales)
- [Seguridad y privacidad](#seguridad-y-privacidad)
- [Arquitectura del proyecto](#arquitectura-del-proyecto)
- [Instalación y uso](#instalación-y-uso)
- [Compilación del ejecutable (`DiscoClaro.exe`)](#compilación-del-ejecutable-discoclaroexe)
- [Pruebas y calidad de código](#pruebas-y-calidad-de-código)
- [Contribuir](#contribuir)
- [Licencia](#licencia)

---

## Características principales

| Módulo | Descripción |
| :--- | :--- |
| **01 · Centro de Control** | Estado en vivo de todas las unidades (`C:`, `D:`, etc.), espacio no explicado, pronóstico de llenado, mayor crecimiento reciente y **Salud Física S.M.A.R.T.** con modal técnico por disco (vida útil restante `%`, desgaste, temperatura `°C`, horas de encendido, TB escritos y bloques de reserva). |
| **02 · Mapa del Disco** | Explorador interactivo por carpetas, archivos grandes, archivos recientes y **detector de duplicados** mediante huella rápida de bloques. |
| **03 · Categorías** | Clasificación por reglas declarativas (`TOML`) para ecosistemas de desarrollo y uso diario (`Docker`, `Ollama`, `pip`, `uv`, `npm`, `Gradle`, `Steam`, navegadores, temporales, respaldos). |
| **04 · Crecimiento** | Comparación histórica entre capturas periódicas en SQLite para identificar carpetas que crecen silenciosamente con el tiempo. |
| **05 · Recomendaciones** | Plan priorizado por nivel de riesgo (**Seguro**, **Revisar**, **Mover a otro disco**) con limpieza segura individual o en lote. |
| **06 · Programas y Restos** | Inventario de software instalado y detección de carpetas residuales tras desinstalar aplicaciones. |
| **07 · Límites y Alertas** | Configuración de umbrales mínimos por volumen, presupuestos por herramienta y gestión de tareas programadas silenciosas en Windows (`pythonw.exe`). |
| **08 · Proyectos y Compactación** | Escáner de proyectos inactivos (`node_modules`, `.venv`, `target`, `build`), compactación asistida de discos virtuales WSL2/Docker (`.vhdx`), simulador de migración `C: → D:` e historial auditable exportable a CSV/HTML. |
| **`>_` Terminal Integrada** | Consola PowerShell embebida con cambio de unidad (`C:\` ↔ `D:\`), historial de comandos y confirmación nativa previa. |
| **Papelera y Bandeja del Sistema** | Vaciamiento en vivo de la Papelera de reciclaje (`SHQueryRecycleBinW` / `SHEmptyRecycleBinW`) e ícono residente en la bandeja de Windows sin ventanas de consola. |

---

## Seguridad y privacidad

DiscoClaro está diseñado con principios estrictos de seguridad defensiva:

- **100 % local y sin telemetría:** el servidor FastAPI escucha únicamente en `127.0.0.1:8347`; no realiza llamadas externas ni recopila métricas de uso.
- **Confirmación nativa obligatoria:** toda operación destructiva (enviar a papelera, vaciar papelera, ejecutar comandos o desinstalar) exige aprobación explícita en un diálogo nativo de Windows (`MessageBoxW`) fuera del navegador.
- **Ejecución segura de procesos:** ningún comando se invoca con `shell=True`; se valida la resolución de ejecutables y se bloquean rutas protegidas del sistema (`Windows`, `System32`, raíz de disco, etc.).
- **Lectura S.M.A.R.T. NVMe sin elevación (cero UAC):** consulta el registro `NVMe SMART/Health Information Log (0x02)` mediante la API nativa de Windows `DeviceIoControl(IOCTL_STORAGE_QUERY_PROPERTY)` con acceso de consulta sin privilegios de administrador.
- **Privacidad de datos:** la base local SQLite almacena exclusivamente metadatos estructurales (rutas, tamaños y marcas de tiempo), nunca el contenido de los archivos.

---

## Arquitectura del proyecto

El backend sigue una arquitectura limpia por capas y el frontend se sirve compilado directamente desde el servidor local:

```text
discoclaro/
├── src/discoclaro/
│   ├── domain/        # Modelos, políticas de riesgo, clasificación indexada y categorías
│   ├── services/      # Casos de uso (escaneo, crecimiento, duplicados, S.M.A.R.T., acciones, límites)
│   ├── adapters/      # Persistencia SQLite, configuración TOML e inspectores (Docker, Steam, pip, etc.)
│   ├── platform/      # Integración nativa Windows (Win32, Papelera, Bandeja del sistema, ícono .ico)
│   ├── interfaces/    # API REST FastAPI y CLI (`discoclaro`)
│   ├── rules/         # Catálogo declarativo de reglas por categoría (*.toml)
│   └── locales/       # Textos de interfaz (es / en)
├── dashboard/         # SPA en React 19 + TypeScript + Vite
├── scripts/           # Empaquetado PyInstaller (`build_windows_exe.ps1`, `launcher.py`, `installer.iss`)
└── tests/             # Suite automatizada con pytest
```

---

## Instalación y uso

### Opción 1: Ejecutable standalone (recomendado para usuarios finales)

1. Descarga **`DiscoClaro.exe`** desde el último release:  
   https://github.com/hebertsb/discoclaro/releases/latest
2. Haz doble clic en `DiscoClaro.exe`.
3. Se abrirá el dashboard automáticamente en `http://127.0.0.1:8347` y aparecerá el ícono en la bandeja del sistema.

> **Nota sobre Windows SmartScreen:** al tratarse de un binario Open Source sin firma comercial de pago, la primera ejecución en una PC nueva puede mostrar el aviso estándar de Windows Defender SmartScreen. Selecciona **«Más información» → «Ejecutar de todas formas»**, o compílalo tú mismo desde el código fuente.

### Opción 2: Ejecutar desde el código fuente (desarrolladores)

**Requisitos:** Windows 10/11, **Python 3.12+** y **Node.js 20+**.

```powershell
git clone https://github.com/hebertsb/discoclaro.git
cd discoclaro

# 1. Crear entorno virtual e instalar en modo editable
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 2. Compilar el frontend React
npm --prefix dashboard install
npm --prefix dashboard run build

# 3. Escanear la unidad principal y abrir el dashboard
.\.venv\Scripts\python.exe -m discoclaro scan C:
.\.venv\Scripts\python.exe -m discoclaro dashboard
```

---

## Compilación del ejecutable (`DiscoClaro.exe`)

Para generar tu propio binario portable (sin ventana de consola y con el dashboard embebido):

```powershell
.\scripts\build_windows_exe.ps1
```

El script genera `dist\DiscoClaro.exe`. Si cuentas con [Inno Setup](https://jrsoftware.org/isinfo.php) (`iscc`) en el `PATH`, también compilará el instalador `dist\DiscoClaro_Setup.exe` mediante `scripts/installer.iss`.

---

## Pruebas y calidad de código

```powershell
# Análisis estático (linting)
.\.venv\Scripts\ruff.exe check src tests scripts

# Pruebas automatizadas
.\.venv\Scripts\pytest.exe

# Verificación de tipos y compilación del dashboard
npm --prefix dashboard run build
```

---

## Contribuir

Las contribuciones, reportes de errores y sugerencias de nuevas reglas de clasificación (`src/discoclaro/rules/*.toml`) son bienvenidos:

1. Haz un **Fork** del repositorio.
2. Crea una rama descriptiva (`git checkout -b feat/mi-mejora`).
3. Asegúrate de que `ruff check src tests scripts`, `pytest` y `npm --prefix dashboard run build` pasen sin errores.
4. Abre un **Pull Request** describiendo el cambio y cómo probarlo.

---

## Licencia

Distribuido bajo licencia **MIT**. Consulta el archivo [LICENSE](LICENSE) para más detalles.
