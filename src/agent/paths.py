"""Rutas ancladas a la raiz del proyecto, sin importar desde donde se corra.

Antes, el .env se encontraba "subiendo" desde el paquete instalado y
traces/ se creaba relativo al directorio actual: correr el agente desde
otra carpeta regaba traces ahi. Todo lo que dependa de la raiz del
proyecto se resuelve aqui, una sola vez.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
TRACES_DIR = PROJECT_ROOT / "traces"
