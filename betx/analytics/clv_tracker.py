"""
betX – CLV (Closing Line Value) Tracker.

Le CLV est considéré comme le meilleur indicateur de compétence
dans le domaine des paris sportifs.

CLV = (cote_prise - cote_fermeture) / cote_fermeture

Un CLV positif constant signifie que vous battez le marché.
Même avec de la variance, un CLV >1% est signe d'edge réel.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from sqlalchemy.orm import Session

from betx.database import Bet, Odds
from betx.logger import get_logger

log = get_logger("analytics.clv")


@dataclass
class CLVReport:
    """Rapport CLV."""
    period: str
    total_bets: int = 0
    bets_with_clv: int = 0
    avg_clv: float = 0.0
    median_clv: float = 0.0
    pct_positive_clv: float = 0.0
    clv_by_sport: dict[str, float] = None
    clv_by_market: dict[str, float] = None

    def __post_init__(self):
        self.clv_by_sport = self.clv_by_sport or {}
        self.clv_by_market = self.clv_by_market or {}

    @property
    def summary(self) -> str:
        return (
            f"📈 CLV Report ({self.period}) │ "
            f"Bets with CLV: {self.bets_with_clv}/{self.total_bets} │ "
            f"Avg CLV: {self.avg_clv:+.2f}% │ "
            f"Median: {self.median_clv:+.2f}% │ "
            f"CLV+ rate: {self.pct_positive_clv:.1f}%"
        )


class CLVTracker:
    """
    Suit le Closing Line Value de chaque pari.

    Le CLV mesure si nos cotes prises sont meilleures que les cotes
    de fermeture (juste avant le match).
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def compute_clv(self, bet: Bet) -> float | None:
        """
        Calcule le CLV pour un pari donné.

        CLV = (cote_prise - cote_fermeture) / cote_fermeture × 100

        Returns:
            CLV en % ou None si pas de cote de fermeture
        """
        if bet.closing_odds and bet.closing_odds > 1.0:
            clv = (bet.bookmaker_odds - bet.closing_odds) / bet.closing_odds * 100
            return clv

        # Chercher la cote de fermeture en DB
        closing = (
            self.session.query(Odds)
            .filter(
                Odds.match_id == bet.match_id,
                Odds.market == bet.market,
                Odds.selection == bet.selection,
                Odds.is_closing == True,
            )
            .first()
        )

        if closing and closing.odds_value > 1.0:
            clv = (bet.bookmaker_odds - closing.odds_value) / closing.odds_value * 100
            bet.closing_odds = closing.odds_value
            bet.clv = clv
            return clv

        return None

    def update_closing_odds(self, bet: Bet, closing_odds: float) -> float:
        """Met à jour la cote de fermeture et calcule le CLV."""
        bet.closing_odds = closing_odds
        clv = (bet.bookmaker_odds - closing_odds) / closing_odds * 100
        bet.clv = clv
        log.info(
            f"CLV {bet.selection} @ {bet.bookmaker_odds:.2f} → "
            f"close={closing_odds:.2f} CLV={clv:+.2f}%"
        )
        return clv

    def generate_report(self, period: str = "all") -> CLVReport:
        """Génère un rapport CLV."""
        end = date.today()
        if period == "weekly":
            start = end - timedelta(days=7)
        elif period == "monthly":
            start = end - timedelta(days=30)
        else:
            start = date(2020, 1, 1)

        bets = (
            self.session.query(Bet)
            .filter(
                Bet.created_at >= str(start),
                Bet.status.in_(["won", "lost"]),
            )
            .all()
        )

        report = CLVReport(period=period, total_bets=len(bets))

        # Calculer les CLV manquants
        clv_values = []
        for bet in bets:
            if bet.clv is None:
                self.compute_clv(bet)
            if bet.clv is not None:
                clv_values.append(bet.clv)

        report.bets_with_clv = len(clv_values)

        if clv_values:
            report.avg_clv = float(np.mean(clv_values))
            report.median_clv = float(np.median(clv_values))
            report.pct_positive_clv = sum(1 for c in clv_values if c > 0) / len(clv_values) * 100

            # Par sport
            sports = set(b.sport for b in bets if b.clv is not None)
            for s in sports:
                sport_clvs = [b.clv for b in bets if b.sport == s and b.clv is not None]
                if sport_clvs:
                    report.clv_by_sport[s] = round(float(np.mean(sport_clvs)), 2)

            # Par marché
            markets = set(b.market for b in bets if b.clv is not None)
            for m in markets:
                market_clvs = [b.clv for b in bets if b.market == m and b.clv is not None]
                if market_clvs:
                    report.clv_by_market[m] = round(float(np.mean(market_clvs)), 2)

        self.session.flush()
        log.info(report.summary)
        return report

    def is_clv_positive(self, min_bets: int = 50) -> bool:
        """
        Vérifie si le CLV global est positif (signe d'un edge réel).

        Args:
            min_bets: Nombre minimum de paris avec CLV pour être significatif
        """
        bets = (
            self.session.query(Bet)
            .filter(Bet.clv.isnot(None))
            .all()
        )
        if len(bets) < min_bets:
            log.warning(f"Pas assez de bets avec CLV ({len(bets)}/{min_bets})")
            return False

        avg_clv = np.mean([b.clv for b in bets])
        return avg_clv > 0
