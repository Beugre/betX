"""
betX – Modèles de base de données (SQLAlchemy ORM).

Tables :
- teams, players, matches, odds, predictions, bets, bankroll_history
- prediction_sites, external_predictions, site_scores
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from sqlalchemy import (
    Column,
    Integer,
    Float,
    String,
    DateTime,
    Date,
    Boolean,
    ForeignKey,
    Text,
    Enum as SAEnum,
    Index,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    Session,
    sessionmaker,
)

from betx.config import settings


# =============================================================================
# Base
# =============================================================================
class Base(DeclarativeBase):
    pass


# =============================================================================
# Teams
# =============================================================================
class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(50), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sport: Mapped[str] = mapped_column(String(20), nullable=False)  # football, tennis, basket
    country: Mapped[Optional[str]] = mapped_column(String(100))
    league: Mapped[Optional[str]] = mapped_column(String(200))
    # ELO ratings
    elo_rating: Mapped[float] = mapped_column(Float, default=1500.0)
    elo_home: Mapped[float] = mapped_column(Float, default=1500.0)
    elo_away: Mapped[float] = mapped_column(Float, default=1500.0)
    # Football specific
    avg_goals_scored: Mapped[Optional[float]] = mapped_column(Float)
    avg_goals_conceded: Mapped[Optional[float]] = mapped_column(Float)
    avg_xg_for: Mapped[Optional[float]] = mapped_column(Float)
    avg_xg_against: Mapped[Optional[float]] = mapped_column(Float)
    # Basket specific
    offensive_rating: Mapped[Optional[float]] = mapped_column(Float)
    defensive_rating: Mapped[Optional[float]] = mapped_column(Float)
    pace: Mapped[Optional[float]] = mapped_column(Float)
    efg_pct: Mapped[Optional[float]] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (Index("ix_teams_sport", "sport"),)


# =============================================================================
# Players (Tennis principalement)
# =============================================================================
class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(50), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sport: Mapped[str] = mapped_column(String(20), default="tennis")
    country: Mapped[Optional[str]] = mapped_column(String(100))
    ranking: Mapped[Optional[int]] = mapped_column(Integer)
    # ELO
    elo_global: Mapped[float] = mapped_column(Float, default=1500.0)
    elo_hard: Mapped[float] = mapped_column(Float, default=1500.0)
    elo_clay: Mapped[float] = mapped_column(Float, default=1500.0)
    elo_grass: Mapped[float] = mapped_column(Float, default=1500.0)
    elo_indoor: Mapped[float] = mapped_column(Float, default=1500.0)
    # Stats de service
    serve_win_pct: Mapped[Optional[float]] = mapped_column(Float)
    return_win_pct: Mapped[Optional[float]] = mapped_column(Float)
    break_point_convert_pct: Mapped[Optional[float]] = mapped_column(Float)
    # Stats par surface
    hard_win_pct: Mapped[Optional[float]] = mapped_column(Float)
    clay_win_pct: Mapped[Optional[float]] = mapped_column(Float)
    grass_win_pct: Mapped[Optional[float]] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


# =============================================================================
# Matches
# =============================================================================
class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True)
    sport: Mapped[str] = mapped_column(String(20), nullable=False)
    league: Mapped[Optional[str]] = mapped_column(String(200))
    season: Mapped[Optional[str]] = mapped_column(String(20))
    match_date: Mapped[date] = mapped_column(Date, nullable=False)
    kickoff_time: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Participants
    home_team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"))
    home_player_id: Mapped[Optional[int]] = mapped_column(ForeignKey("players.id"))
    away_player_id: Mapped[Optional[int]] = mapped_column(ForeignKey("players.id"))

    home_name: Mapped[str] = mapped_column(String(200), nullable=False)
    away_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Résultats
    status: Mapped[str] = mapped_column(String(20), default="scheduled")  # scheduled, live, finished, postponed
    home_score: Mapped[Optional[int]] = mapped_column(Integer)
    away_score: Mapped[Optional[int]] = mapped_column(Integer)
    # Football specific
    home_xg: Mapped[Optional[float]] = mapped_column(Float)
    away_xg: Mapped[Optional[float]] = mapped_column(Float)
    # Tennis specific
    surface: Mapped[Optional[str]] = mapped_column(String(20))
    sets_score: Mapped[Optional[str]] = mapped_column(String(50))  # "6-3 7-5"
    total_games: Mapped[Optional[int]] = mapped_column(Integer)
    # Basket specific
    home_q1: Mapped[Optional[int]] = mapped_column(Integer)
    home_q2: Mapped[Optional[int]] = mapped_column(Integer)
    home_q3: Mapped[Optional[int]] = mapped_column(Integer)
    home_q4: Mapped[Optional[int]] = mapped_column(Integer)
    away_q1: Mapped[Optional[int]] = mapped_column(Integer)
    away_q2: Mapped[Optional[int]] = mapped_column(Integer)
    away_q3: Mapped[Optional[int]] = mapped_column(Integer)
    away_q4: Mapped[Optional[int]] = mapped_column(Integer)

    # Metadata
    rest_days_home: Mapped[Optional[int]] = mapped_column(Integer)
    rest_days_away: Mapped[Optional[int]] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relations
    home_team = relationship("Team", foreign_keys=[home_team_id])
    away_team = relationship("Team", foreign_keys=[away_team_id])
    home_player = relationship("Player", foreign_keys=[home_player_id])
    away_player = relationship("Player", foreign_keys=[away_player_id])

    __table_args__ = (
        Index("ix_matches_date", "match_date"),
        Index("ix_matches_sport", "sport"),
        Index("ix_matches_status", "status"),
    )


# =============================================================================
# Odds
# =============================================================================
class Odds(Base):
    __tablename__ = "odds"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False)
    bookmaker: Mapped[str] = mapped_column(String(100), nullable=False)
    market: Mapped[str] = mapped_column(String(50), nullable=False)  # 1x2, over_under, btts, moneyline, handicap
    selection: Mapped[str] = mapped_column(String(100), nullable=False)  # home, draw, away, over_2.5, etc.
    odds_value: Mapped[float] = mapped_column(Float, nullable=False)
    # Pour tracking CLV
    is_opening: Mapped[bool] = mapped_column(Boolean, default=False)
    is_closing: Mapped[bool] = mapped_column(Boolean, default=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    match = relationship("Match", backref="odds_entries")

    __table_args__ = (
        Index("ix_odds_match", "match_id"),
        Index("ix_odds_market", "market"),
    )


# =============================================================================
# Predictions
# =============================================================================
class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    market: Mapped[str] = mapped_column(String(50), nullable=False)
    selection: Mapped[str] = mapped_column(String(100), nullable=False)
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    # Données additionnelles du modèle
    model_details: Mapped[Optional[str]] = mapped_column(Text)  # JSON avec détails
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    match = relationship("Match", backref="predictions")

    __table_args__ = (
        Index("ix_predictions_match", "match_id"),
    )


# =============================================================================
# Bets (paris identifiés / placés)
# =============================================================================
class Bet(Base):
    __tablename__ = "bets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False)
    prediction_id: Mapped[Optional[int]] = mapped_column(ForeignKey("predictions.id"))
    sport: Mapped[str] = mapped_column(String(20), nullable=False)
    market: Mapped[str] = mapped_column(String(50), nullable=False)
    selection: Mapped[str] = mapped_column(String(100), nullable=False)
    # Value
    model_probability: Mapped[float] = mapped_column(Float, nullable=False)
    bookmaker_odds: Mapped[float] = mapped_column(Float, nullable=False)
    closing_odds: Mapped[Optional[float]] = mapped_column(Float)
    edge: Mapped[float] = mapped_column(Float, nullable=False)
    ev: Mapped[float] = mapped_column(Float, nullable=False)
    # Staking
    stake: Mapped[float] = mapped_column(Float, nullable=False)
    stake_pct: Mapped[float] = mapped_column(Float, nullable=False)
    kelly_raw: Mapped[Optional[float]] = mapped_column(Float)
    staking_method: Mapped[str] = mapped_column(String(20), default="kelly")
    # Résultat
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, won, lost, void, push
    pnl: Mapped[Optional[float]] = mapped_column(Float)
    # CLV
    clv: Mapped[Optional[float]] = mapped_column(Float)
    # Bookmaker
    bookmaker: Mapped[Optional[str]] = mapped_column(String(100))
    placed: Mapped[bool] = mapped_column(Boolean, default=False)
    placed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    match = relationship("Match", backref="bets")
    prediction = relationship("Prediction")

    __table_args__ = (
        Index("ix_bets_status", "status"),
        Index("ix_bets_sport", "sport"),
        Index("ix_bets_date", "created_at"),
    )


# =============================================================================
# Bankroll History
# =============================================================================
class BankrollHistory(Base):
    __tablename__ = "bankroll_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    bankroll: Mapped[float] = mapped_column(Float, nullable=False)
    daily_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    total_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    n_bets: Mapped[int] = mapped_column(Integer, default=0)
    n_wins: Mapped[int] = mapped_column(Integer, default=0)
    n_losses: Mapped[int] = mapped_column(Integer, default=0)
    roi_pct: Mapped[float] = mapped_column(Float, default=0.0)
    drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_bankroll_date", "date"),
    )


# =============================================================================
# External prediction benchmarking
# =============================================================================
class PredictionSite(Base):
    __tablename__ = "prediction_sites"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[str] = mapped_column(String(300), nullable=False)
    sport: Mapped[str] = mapped_column(String(20), default="football")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_prediction_sites_active", "is_active"),
    )


class ExternalPrediction(Base):
    __tablename__ = "external_predictions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("prediction_sites.id"), nullable=False)
    match_id: Mapped[Optional[int]] = mapped_column(ForeignKey("matches.id"))

    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    source_prediction_id: Mapped[Optional[str]] = mapped_column(String(180))
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    sport: Mapped[str] = mapped_column(String(20), default="football")
    league: Mapped[Optional[str]] = mapped_column(String(200))
    kickoff_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    home_name: Mapped[str] = mapped_column(String(200), nullable=False)
    away_name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_home: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_away: Mapped[str] = mapped_column(String(200), nullable=False)

    market: Mapped[str] = mapped_column(String(50), default="1x2")
    predicted_selection: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    raw_prediction: Mapped[Optional[str]] = mapped_column(Text)

    result_status: Mapped[str] = mapped_column(String(20), default="pending")
    grade_points: Mapped[Optional[float]] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    site = relationship("PredictionSite", backref="predictions")
    match = relationship("Match", backref="external_predictions")

    __table_args__ = (
        UniqueConstraint("site_id", "source_url", "source_prediction_id", name="uq_ext_pred_src"),
        Index("ix_ext_predictions_site", "site_id"),
        Index("ix_ext_predictions_match", "match_id"),
        Index("ix_ext_predictions_status", "result_status"),
        Index("ix_ext_predictions_kickoff", "kickoff_time"),
    )


class SiteScore(Base):
    __tablename__ = "site_scores"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("prediction_sites.id"), nullable=False)
    score_date: Mapped[date] = mapped_column(Date, nullable=False)
    league: Mapped[str] = mapped_column(String(200), default="all")
    window_days: Mapped[int] = mapped_column(Integer, default=60)

    predictions_count: Mapped[int] = mapped_column(Integer, default=0)
    graded_count: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)

    hit_rate: Mapped[float] = mapped_column(Float, default=0.0)
    roi_flat: Mapped[float] = mapped_column(Float, default=0.0)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    site = relationship("PredictionSite", backref="scores")

    __table_args__ = (
        UniqueConstraint("site_id", "score_date", "league", "window_days", name="uq_site_score_window"),
        Index("ix_site_scores_date", "score_date"),
        Index("ix_site_scores_quality", "quality_score"),
    )


# =============================================================================
# Engine / Session
# =============================================================================
_engine = None
_SessionFactory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.database.url,
            echo=False,
            pool_pre_ping=True,
        )
    return _engine


def get_session() -> Session:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine())
    return _SessionFactory()


def init_db() -> None:
    """Crée toutes les tables."""
    engine = get_engine()
    Base.metadata.create_all(engine)
