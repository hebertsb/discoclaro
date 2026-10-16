# discoclaro

Responde una sola pregunta: **¿por qué se llena mi disco?**

discoclaro escanea tus discos, guarda fotos periódicas, clasifica lo que ocupa espacio con reglas
que entienden herramientas de desarrollo (por ejemplo Docker, Ollama, Gradle, pip o Steam) y
detecta qué creció desde la última foto. Más adelante avisará con notificaciones, tendrá un
dashboard y sugerirá qué borrar o mover a otro disco.

## Estado

En desarrollo. Todavía no hay una versión publicada ni instrucciones de instalación.
Windows primero; Linux y macOS vendrán después.

## Principios

- **Solo lectura al inicio:** las primeras versiones únicamente leen el disco; no borran ni mueven
  nada.
- **Sin red y sin telemetría:** no hace llamadas a internet ni envía datos a nadie.
- **Privacidad:** guarda rutas, tamaños y fechas, nunca el contenido de tus archivos.

## Licencia

MIT. Ver [LICENSE](LICENSE).
