"""
betX – Helpers base de données : opérations CRUD courantes.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session

from betx.database import (
    Match, Team, Player, Odds, Prediction, Bet, BankrollHistory, get_session,
)


# =============================================================================
# Teams
# =============================================================================
def get_or_create_team(
    session: Session,
    name: str,
    sport: str,
    external_id: str | None = None,
    **kwargs,
) -> Team:
    """Récupère ou crée une équipe."""
    if external_id:
        team = session.query(Team).filter_by(external_id=external_id).first()
        if team:
            return team
    team = session.query(Team).filter_by(name=name, sport=sport).first()
    if team:
        return team
    team = Team(name=name, sport=sport, external_id=external_id, **kwargs)
    session.add(team)
    session.flush()
    return team


def get_or_create_player(
    session: Session,
    name: str,
    sport: str = "tennis",
    external_id: str | None = None,
    **kwargs,
) -> Player:
    """Récupère ou crée un joueur."""
    if external_id:
        player = session.query(Player).filter_by(external_id=external_id).first()
        if player:
            return player
    player = session.query(Player).filter_by(name=name, sport=sport).first()
    if player:
        return player
    player = Player(name=name, sport=sport, external_id=external_id, **kwargs)
    session.add(player)
    session.flush()
    return player


# =============================================================================
# Matches
# =============================================================================
def get_matches_by_date(
    session: Session,
    target_date: date,
    sport: str | None = None,
) -> list[Match]:
    """Récupère tous les matchs pour une date."""
    q = session.query(Match).filter(Match.match_date == target_date)
    if sport:
        q = q.filter(Match.sport == sport)
    return q.all()


def get_upcoming_matches(
    session: Session,
    sport: str | None = None,
) -> list[Match]:
    """Matchs pas encore joués."""
    q = session.query(Match).filter(Match.status == "scheduled")
    if sport:
        q = q.filter(Match.sport == sport)
    return q.order_by(Match.match_date).all()


# =============================================================================
# Bankroll
# =============================================================================
def get_current_bankroll(session: Session) -> float:
    """Retourne le dernier montant de bankroll."""
    from betx.config import settings

    last = (
        session.query(BankrollHistory)
        .order_by(BankrollHistory.date.desc())
        .first()
    )
    if last:
        return last.bankroll
    return settings.bankroll.initial_bankroll


def record_bankroll(
    session: Session,
    bankroll: float,
    daily_pnl: float,
    total_pnl: float,
    n_bets: int = 0,
    n_wins: int = 0,
    n_losses: int = 0,
    roi_pct: float = 0.0,
    drawdown_pct: float = 0.0,
    target_date: date | None = None,
) -> BankrollHistory:
    """Enregistre un snapshot de bankroll."""
    entry = BankrollHistory(
        date=target_date or date.today(),
        bankroll=bankroll,
        daily_pnl=daily_pnl,
        total_pnl=total_pnl,
        n_bets=n_bets,
        n_wins=n_wins,
        n_losses=n_losses,
        roi_pct=roi_pct,
        drawdown_pct=drawdown_pct,
    )
    session.add(entry)
    session.flush()
    return entry


# =============================================================================
# Pending bets
# =============================================================================
def get_pending_bets(session: Session) -> list[Bet]:
    return session.query(Bet).filter(Bet.status == "pending").all()


def settle_bet(session: Session, bet: Bet, result: str) -> None:
    """Résout un pari. result = 'won', 'lost', 'void', 'push'."""
    bet.status = result
    if result == "won":
        bet.pnl = bet.stake * (bet.bookmaker_odds - 1)
    elif result == "lost":
        bet.pnl = -bet.stake
    elif result in ("void", "push"):
        bet.pnl = 0.0
    session.flush()
