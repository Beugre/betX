"""
betX – Configuration centralisée.

Charge les paramètres depuis .env et fournit des valeurs par défaut.
Singleton : ``from betx.config import settings``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Charger .env depuis la racine du projet
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


# ─── Sous-configurations ─────────────────────────────────────────────────


@dataclass
class PathsConfig:
    """Chemins du projet."""
    PROJECT_ROOT: Path = _PROJECT_ROOT
    DATA_DIR: Path = field(default_factory=lambda: _PROJECT_ROOT / "data")
    LOGS_DIR: Path = field(default_factory=lambda: _PROJECT_ROOT / "logs")
    EXPORTS_DIR: Path = field(default_factory=lambda: _PROJECT_ROOT / "exports")

    def __post_init__(self):
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class DatabaseConfig:
    """Configuration de la base de données."""
    url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///data/betx.db")
    )


@dataclass
class BankrollConfig:
    """Configuration de la gestion de bankroll et staking."""
    initial_bankroll: float = field(
        default_factory=lambda: float(os.getenv("INITIAL_BANKROLL", "1000.0"))
    )
    kelly_fraction: float = 0.25
    flat_pct: float = 0.02
    max_stake_pct: float = 0.01
    default_method: str = "kelly"


@dataclass
class ValueConfig:
    """Configuration du value betting.
    
    Backtest 2024-25 (1385 matchs, 180j) :
      - edge ≥ 8% + 1X2 only → +952% ROI, +4.76% yield, Sharpe 1.48
      - Over/Under toxiques → exclu
      - Cotes > 5.0 → variance trop élevée
    """
    min_edge: float = 0.08
    min_ev: float = 0.01
    min_odds: float = 1.20
    max_odds: float = 5.00


@dataclass
class FootballModelConfig:
    """Configuration du modèle football."""
    leagues: list[int] = field(default_factory=lambda: [39, 140, 135, 78, 61, 2])
    elo_k_factor: float = 32.0
    home_advantage: float = 0.25
    xg_weight: float = 0.3
    goals_weight: float = 0.7


@dataclass
class TelegramConfig:
    """Configuration des alertes Telegram."""
    enabled: bool = field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "").startswith("your") is False
        and os.getenv("TELEGRAM_BOT_TOKEN", "") != ""
    )
    bot_token: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "")
    )
    chat_id: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", "")
    )


# ─── Configuration principale ────────────────────────────────────────────


@dataclass
class Settings:
    """Configuration globale betX."""
    paths: PathsConfig = field(default_factory=PathsConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    bankroll: BankrollConfig = field(default_factory=BankrollConfig)
    value: ValueConfig = field(default_factory=ValueConfig)
    football: FootballModelConfig = field(default_factory=FootballModelConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO")
    )


# Singleton
settings = Settings()
