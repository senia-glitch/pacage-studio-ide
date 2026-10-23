"""Путь к встроенному UI (офлайн, без CDN)."""

from pathlib import Path

UI_DIR = Path(__file__).parent
STATIC_DIR = UI_DIR / "static"
INDEX = STATIC_DIR / "index.html"
