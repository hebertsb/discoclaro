"""Permite `python -m discoclaro`, para la tarea programada (T2.8): sin depender de
que el script de consola esté en el PATH, solo del propio intérprete.
"""

from discoclaro.interfaces.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
