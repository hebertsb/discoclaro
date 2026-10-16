# Política de seguridad

discoclaro lee todo el disco y, desde la versión 1.0, podrá mover y borrar archivos con tu
confirmación. Por eso la seguridad se piensa desde el diseño. El modelo de amenazas está en
[docs/MODELO_DE_AMENAZAS.md](docs/MODELO_DE_AMENAZAS.md).

## Versiones con soporte

Todavía no hay versiones publicadas. Las versiones `0.x` son internas y no reciben arreglos por
separado.

Cuando se publique la `1.0.0`, recibirá arreglos de seguridad solo la última versión publicada.

## Cómo reportar una vulnerabilidad

**No abras un issue público** ni publiques el detalle antes de que exista un arreglo.

Usa el reporte privado de vulnerabilidades de GitHub: en la pestaña **Security** del repositorio,
elige **Report a vulnerability**.

Si puedes, incluye:

- la versión de discoclaro y la de Windows que usas,
- los pasos que reproducen el problema,
- el impacto: qué podría hacer alguien que lo aproveche.

Por privacidad, no adjuntes tu base de datos de escaneos ni rutas reales de tu equipo: contienen
información personal. Un ejemplo con carpetas inventadas es suficiente.

## Qué esperar

discoclaro es un proyecto personal que mantiene una sola persona, así que no hay plazos
garantizados. Se responde en cuanto se puede, se confirma si el problema se reproduce y se avisa
cuando haya un arreglo. Se pide no divulgar el detalle hasta entonces.

## Qué cuenta como vulnerabilidad

Por ejemplo:

- Que una regla, un archivo de configuración o un nombre de archivo consiga ejecutar un comando o
  código.
- Que la herramienta borre o mueva algo que el usuario no confirmó, o algo dentro de una zona
  protegida.
- Que una página web u otro programa consiga usar la API local del dashboard.
- Una inyección (SQL, HTML, XML o secuencias de terminal) mediante nombres de archivo o de
  programa.
- Una escalada de privilegios mediante la elevación a administrador.
- Que la herramienta envíe datos a internet. No debe hacer ninguna llamada de red.
- Una dependencia comprometida.

Un tamaño mal calculado en un informe no es una vulnerabilidad: abre un issue normal.

## Límite conocido

Si ya hay un malware corriendo con los permisos del usuario, puede intentar aprovechar la
herramienta, por ejemplo disparando la ventana de permiso de administrador. Las defensas lo
dificultan, pero no lo hacen imposible; por eso la elevación es opcional y está apagada mientras no
se use. El detalle está en el modelo de amenazas.
