"""
betX – Logging centralisé.
"""

import logging
import sys
from pathlib import Path

from betx.config import settings


def get_logger(name: str) -> logging.Logger:
    """Crée un logger configuré pour le module donné."""
    logger = logging.getLogger(f"betx.{name}")

    if not logger.handlers:
        logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

        # Console
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(
            logging.Formatter(
                "%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(console)

        # Fichier
        log_file = settings.paths.LOGS_DIR / "betx.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(fh)

    return logger
