"""Punto de entrada standalone para el ejecutable empaquetado `DiscoClaro.exe`.

Inicia el ícono en la Bandeja del Sistema (junto al reloj de Windows), abre el
navegador en `http://127.0.0.1:8347` y levanta el servidor local sin consola.
"""

from __future__ import annotations

import threading
import webbrowser

from discoclaro.interfaces import api
from discoclaro.platform import tray


def main() -> None:
    port = api.DEFAULT_PORT
    tray.launch_system_tray(port=port)
    url = f"http://{api.DEFAULT_HOST}:{port}"
    threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    api.run_api_server(host=api.DEFAULT_HOST, port=port)


if __name__ == "__main__":
    main()
