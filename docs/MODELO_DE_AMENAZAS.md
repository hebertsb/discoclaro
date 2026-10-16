# Modelo de amenazas

Documento para quien contribuya al código o revise la seguridad de discoclaro. Para reportar un
problema, ver [SECURITY.md](../SECURITY.md).

discoclaro lee todo el disco, puede pedir permisos de administrador y, desde la versión 1.0, mueve
y borra archivos con confirmación. Es justo el tipo de programa que alguien querría abusar, así que
se diseña pensando en eso desde el principio y no al final.

> **Estado:** este documento describe el diseño. discoclaro está en desarrollo y todavía no hay
> versiones publicadas. La columna «Se construye en» dice en qué versión se implementa cada
> mitigación. Las versiones `0.1` y `0.2` son internas; la primera pública será la `1.0.0`.

## Qué se protege

- **Los archivos del usuario:** que nada se borre, mueva o altere fuera de lo que el usuario
  confirmó.
- **Los privilegios del usuario:** que la herramienta no sea un camino para ejecutar código ajeno
  ni para subir a administrador.
- **La privacidad:** la base de escaneos guarda rutas, tamaños y fechas, que pueden revelar datos
  personales. Nunca se guarda el contenido de los archivos.
- **La integridad de la propia herramienta:** su código, sus reglas y su configuración.

## Entradas hostiles

Toda cadena que viene de afuera se trata como hostil: nombres de archivo y rutas, nombres y editores
de programas leídos del registro, contenido de reglas y de configuración, cabeceras y peticiones a
la API local, y la salida de otros programas. Se valida el tipo y el largo, se normaliza y se escapa
según dónde se muestre (HTML, XML de las notificaciones, terminal, registros).

## Amenazas y mitigaciones

| # | Amenaza | Cómo podría ocurrir | Mitigación | Se construye en |
|---|---|---|---|---|
| 1 | Un archivo de reglas o de configuración malicioso hace borrar o ejecutar algo | Se descarga, llega en un pull request o lo edita un malware | Reglas y configuración son **datos, nunca código**. Validador estricto; lista cerrada de comandos; las zonas protegidas viven en el código, no en los datos; las reglas del usuario salen marcadas «no verificadas» | 0.1 (validador de reglas), 1.0 (zonas protegidas, catálogo de tareas) |
| 2 | Una página web o un programa local llama a la API para borrar cosas | CSRF o *DNS rebinding* contra `127.0.0.1` | Solo `127.0.0.1`, token por sesión, comprobación de `Host` y `Origin`, sin CORS, plan de dos pasos con confirmación en la interfaz | 0.2 (API), 1.0 (plan de dos pasos) |
| 3 | Inyección SQL con nombres de archivo hostiles | Una carpeta llamada `'; DROP TABLE nodes;--` | Solo consultas parametrizadas; prueba con nombres hostiles; la API usa una conexión de solo lectura; el linter de seguridad y una prueba automática prohíben armar SQL con texto | 0.1 |
| 4 | Nombres con caracteres especiales rompen la interfaz | `<img onerror=...>` en un nombre (XSS), caracteres de XML en una notificación, secuencias de control en la terminal, saltos de línea que falsean un registro | Saneado de todo texto externo; en el dashboard, siempre como texto (nunca HTML) y con CSP estricta | 0.1 (terminal, notificaciones, registros), 0.2 (dashboard) |
| 5 | Inyección de comandos | Una ruta con espacios, `;` o `&` llega a un shell | `subprocess` con lista de argumentos, sin shell, con la ruta completa del ejecutable y un tiempo máximo | 0.1 |
| 6 | Escalada de privilegios mediante la elevación | Código en una carpeta que el usuario puede escribir se ejecuta como administrador | Elevación opcional con punto de entrada fijo; verificación de integridad del paquete antes de elevar; solo tareas del catálogo cerrado; todo se revalida dentro del proceso elevado | 0.1 (verificación de integridad), 1.0 (tareas elevadas) |
| 7 | Dependencia o actualización comprometida | Un paquete malicioso o un nombre parecido (*typosquatting*) | Dependencias mínimas con versión exacta y hashes, auditoría, `npm ci` sin scripts, **sin actualización automática**, publicación con *Trusted Publishing* y sumas de verificación | 0.1 (versiones y hashes), 1.0 (publicación) |
| 8 | La tarea programada se altera para ejecutar otra cosa | Un malware cambia la acción de la tarea | Tarea con el usuario normal (sin elevar) y ruta absoluta del intérprete del entorno; `schedule status` compara con lo esperado y avisa | 0.1 |
| 9 | La herramienta se usa como arma (borrado masivo) | Alguien consigue que ejecute un plan enorme | Topes por acción, zonas protegidas, confirmación escrita, sin cifrado ni nada parecido, sin red, modo solo lectura y registro de acciones | 1.0 |
| 10 | Fuga de datos | La base guarda rutas, que pueden revelar datos personales | Solo local, sin red, permisos de la carpeta del usuario, nunca se guarda contenido de archivos y `--anonymize` para exportar | 0.1 (local, sin contenido), 1.0 (`--anonymize`) |
| 11 | Carreras (*TOCTOU*) y enlaces que desvían un borrado | Entre la vista previa y la ejecución, una carpeta se cambia por un enlace a otro lugar | Revalidar justo antes de actuar; no seguir enlaces ni *junctions*; comprobar que la ruta real sigue dentro de lo planeado | 1.0 |
| 12 | Un antivirus lo confunde con un programa malicioso | Borra archivos, pide administrador y trae listas de comandos peligrosos | Código abierto y sin ofuscación, sumas de verificación publicadas, y los textos de prueba con comandos de ransomware se arman por partes (ya ocurrió con un archivo de pruebas) | 1.0 |

## Políticas principales

- **Base de datos:** consultas parametrizadas siempre; nombres de tabla y de columna fijos en el
  código; comodines de `LIKE` escapados; claves foráneas activadas; la API usa una conexión de solo
  lectura; todo el SQL vive en un único módulo.
- **Procesos:** lista de argumentos, nunca `shell=True`, ejecutable con su ruta completa, tiempo
  máximo y tamaño máximo de salida. Están prohibidos `eval`, `exec`, `pickle`, `os.system` y
  `yaml.load`.
- **Archivos y rutas:** se normalizan y resuelven antes de usarlas. Se rechazan `..`, rutas UNC,
  prefijos `\\?\` inesperados, nombres de dispositivo reservados (`CON`, `NUL`…), puntos y espacios
  al final, nombres cortos 8.3 y diferencias de mayúsculas. No se siguen enlaces simbólicos ni
  *junctions*.
- **Zonas protegidas** (definidas en el código, no editables por reglas): raíces de disco, raíz del
  perfil del usuario, `Windows`, `Program Files`, `Program Files (x86)`, `ProgramData`,
  `System Volume Information`, `$Recycle.Bin` (salvo vaciarla a propósito), la base y la
  configuración de discoclaro, y toda carpeta con una regla `do_not_touch`, como las copias de
  seguridad.
- **API local:** solo `127.0.0.1`; token aleatorio por sesión, que no se escribe en disco; `Host` y
  `Origin` en una lista permitida; sin CORS; sin documentación pública de la API; cabeceras
  `Content-Security-Policy` estricta (sin *scripts* en línea), `X-Content-Type-Options` y
  `Referrer-Policy`; límite de tamaño de las peticiones; errores genéricos hacia afuera y detalle
  solo en el registro.
- **Dependencias:** pocas, con versión exacta y hashes, y auditadas. En el frontend, `npm ci` con
  los scripts desactivados. El paquete no ejecuta código al instalarse.
- **Secretos:** ninguno en el repositorio ni en la base. La herramienta no pide ni guarda
  credenciales.

## Permisos de administrador

La elevación es opcional y solo se usa para tareas puntuales.

1. El dashboard y la API **siguen siendo del usuario normal**. Lo que se eleva es solo una tarea.
2. Una tarea que necesita administrador está en un **catálogo cerrado**, con un identificador fijo,
   un comando fijo y parámetros validados. No existe una opción para ejecutar un comando libre como
   administrador.
3. La interfaz muestra **el comando exacto** y por qué hace falta. El usuario decide si pide el
   permiso.
4. Windows muestra su propia ventana de permiso (UAC). discoclaro nunca ve ni guarda una
   contraseña.
5. Se lanza un punto de entrada fijo que recibe **solo el identificador de la tarea** y la ruta de
   un plan. **Antes de elevar**, el proceso normal verifica la integridad del paquete comparando los
   hashes de sus archivos con los que registró la instalación.
6. Dentro del proceso elevado se **revalida todo**: que la tarea esté en el catálogo, que los
   parámetros cumplan las reglas, que la ruta no sea una zona protegida y que el plan no haya
   cambiado.
7. El resultado vuelve por un registro, y la interfaz **verifica** el efecto antes de decir
   «hecho».

## Lo que discoclaro nunca hará

- Cifrar, comprimir con contraseña, ocultar o renombrar en masa los archivos del usuario.
- Borrar o mover sin plan, vista previa y confirmación, ni actuar sobre una zona protegida o una
  carpeta `do_not_touch`.
- Borrar copias de seguridad, puntos de restauración o registros de eventos de Windows.
- Ejecutar un comando escrito por el usuario o leído de un archivo de reglas, descargar código,
  hacer llamadas a internet o actualizarse sola.
- Correr el servidor del dashboard como administrador.
- Dejar un proceso oculto o residente.

## Límites honestos

Si ya hay un malware corriendo con los permisos del usuario, puede intentar disparar la ventana de
permiso de administrador o modificar archivos del paquete. La verificación de integridad y la
revalidación suben mucho la dificultad, pero no la vuelven imposible. Por eso la elevación es
opcional y está apagada mientras no se use.
