# Dependencias

Cómo se manejan las dependencias de discoclaro y por qué. Es parte de la defensa contra una
dependencia comprometida; ver [MODELO_DE_AMENAZAS.md](MODELO_DE_AMENAZAS.md), amenaza 7.

## Política

- **Biblioteca estándar primero.** Hoy discoclaro no tiene dependencias de ejecución. Antes de
  agregar cualquiera se pregunta si la biblioteca estándar alcanza.
- **Versión exacta (`==`) siempre.** Nunca rangos (`>=`, `~=`, `^`, `*`).
- **Hashes.** Los archivos de requisitos llevan el hash `sha256` de cada archivo, y se instalan con
  `pip install --require-hashes`. Si un archivo no coincide, pip se niega a instalarlo.
- **Pocas y auditadas.** Cada dependencia nueva se justifica y se audita (ver más abajo).
- **Sin actualización automática.** Las versiones cambian solo cuando alguien lo decide, revisa el
  cambio y lo confirma. El paquete tampoco ejecuta código al instalarse.

La prueba `tests/test_dependencies.py` hace cumplir la parte mecánica: falla si algo en
`pyproject.toml` o en los requisitos no tiene `==`, o si una línea de un `requirements*.txt` no
trae hashes.

## Archivos

| Archivo | Qué contiene |
|---|---|
| `pyproject.toml` | Dependencias de ejecución (`dependencies`) y backend de construcción (`build-system`), con `==` |
| `requirements-dev.in` | Herramientas de desarrollo directas (pruebas y revisión de estilo), con `==` |
| `requirements-dev.txt` | Generado desde el `.in`: todas las dependencias, transitivas incluidas, con hashes |
| `requirements-audit.in` | La herramienta de auditoría directa, con `==` |
| `requirements-audit.txt` | Generado desde el `.in`, con todas sus dependencias y hashes |

Los `.txt` no se editan a mano: se regeneran.

## Instalar el entorno de desarrollo

```
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.txt
```

## Agregar o actualizar una dependencia

1. Editar el archivo `.in` (o `pyproject.toml`) con la versión exacta.
2. Regenerar el `.txt` con [uv](https://docs.astral.sh/uv/), que es una herramienta de desarrollo
   y no una dependencia del proyecto:

   ```
   uv pip compile requirements-dev.in --universal --python-version 3.12 --generate-hashes -o requirements-dev.txt
   ```

   `--universal` registra los hashes de todas las plataformas, no solo los de la máquina que lo
   ejecuta.
3. Revisar en el diff qué paquetes cambiaron, correr las pruebas y auditar.

## Auditoría de vulnerabilidades

Se usa `pip-audit`, instalado en su propio entorno para no llenar el de desarrollo con sus
paquetes:

```
py -3.12 -m venv .venv-audit
.venv-audit\Scripts\python.exe -m pip install --require-hashes -r requirements-audit.txt
.venv-audit\Scripts\pip-audit.exe -r requirements-dev.txt --require-hashes --disable-pip
```

Consulta una base pública de vulnerabilidades y le envía nombres y versiones de paquetes públicos,
más lo que revela cualquier petición web (como la dirección IP), nunca rutas ni datos del usuario.
Se ejecuta en la máquina de quien desarrolla, jamás
desde el código de discoclaro. Termina con un código distinto de cero si encuentra algo.

Se audita al cambiar cualquier dependencia, antes de etiquetar una versión y antes de publicar. Si
aparece una vulnerabilidad, se evalúa si afecta a discoclaro y se actualiza a una versión corregida
(con su hash nuevo). Si no se puede actualizar, la excepción se documenta con el motivo.

## Frontend (dashboard)

Todavía no existe. Cuando exista, regirá lo mismo:

- `package.json` con versiones exactas, sin `^` ni `~`.
- `package-lock.json` dentro del repositorio.
- Instalación con `npm ci --ignore-scripts`, para que ningún paquete ejecute código al instalarse.
- `npm audit` junto con `pip-audit`, con la misma frecuencia.

## Límite conocido: dependencias de construcción

Al construir el paquete, pip descarga el backend de construcción (`hatchling`) y sus dependencias en
un entorno aislado, y **no verifica los hashes de ese entorno**. Lo que sí está fijado es la
versión exacta del backend en `pyproject.toml`. Para una construcción completamente verificada, se
instala el backend con hashes en un entorno limpio y se construye con `--no-build-isolation`. Se
evaluará aplicar esa construcción verificada a las versiones publicadas.
