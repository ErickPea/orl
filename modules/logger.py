# -*- coding: utf-8 -*-
"""
Logging estructurado centralizado para OSINT-Auto.
Escribe eventos INFO/WARNING/ERROR con rotación en `data/logs/osint_auto.log`
y envía alertas WARNING+ a consola (ajustable vía `OSINT_LOG_LEVEL`).
"""
import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT_DIR / "data" / "logs"
LOG_FILE = LOG_DIR / "osint_auto.log"

_LOG_LOCK = threading.Lock()
_CONFIGURED = False


def _configure_root_logger() -> logging.Logger:
    global _CONFIGURED
    root = logging.getLogger("osint_auto")
    if _CONFIGURED:
        return root

    with _LOG_LOCK:
        if _CONFIGURED:
            return root

        level_name = os.environ.get("OSINT_LOG_LEVEL", "WARNING").strip().upper()
        console_level = getattr(logging, level_name, logging.WARNING)
        root.setLevel(logging.INFO)
        root.propagate = False

        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        stream_h = logging.StreamHandler()
        stream_h.setLevel(console_level)
        stream_h.setFormatter(fmt)
        root.addHandler(stream_h)

        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            file_h = RotatingFileHandler(
                str(LOG_FILE),
                maxBytes=2_000_000,
                backupCount=3,
                encoding="utf-8",
            )
            file_h.setLevel(logging.INFO)
            file_h.setFormatter(fmt)
            root.addHandler(file_h)
        except OSError as exc:
            root.warning("No se pudo inicializar RotatingFileHandler en %s: %s", LOG_FILE, exc)

        _CONFIGURED = True
        return root


def get_logger(name: str = "osint_auto") -> logging.Logger:
    """Devuelve un logger hijo bajo el namespace `osint_auto`."""
    _configure_root_logger()
    clean = (name or "osint_auto").strip()
    if clean == "osint_auto" or clean.startswith("osint_auto."):
        return logging.getLogger(clean)
    return logging.getLogger(f"osint_auto.{clean}")
