"""
betX – Métriques de performance.

KPIs calculés :
- ROI global et par sport
- Winrate
- Yield (profit / montant total misé)
- Drawdown max
- EV moyen par pari
- Courbe de bankroll
- Sharpe ratio simplifié
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from betx.database import Bet, BankrollHistory
from betx.logger import get_logger

log = get_logger("analytics.performance")


@dataclass
class PerformanceReport:
    """Rapport de performance complet."""
    period: str  # "daily", "weekly", "monthly", "all"
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    # Bets
    total_bets: int = 0
    wins: int = 0
    losses: int = 0
    voids: int = 0
    pending: int = 0
    # Financier
    total_staked: float = 0.0
    total_pnl: float = 0.0
    # KPIs
    roi_pct: float = 0.0
    winrate_pct: float = 0.0
    yield_pct: float = 0.0  # profit / total_staked
    avg_odds: float = 0.0
    avg_edge: float = 0.0
    avg_ev: float = 0.0
    # Risk
    max_drawdown_pct: float = 0.0
    max_drawdown_amount: float = 0.0
    longest_losing_streak: int = 0
    # Bankroll
    bankroll_start: float = 0.0
    bankroll_end: float = 0.0
    bankroll_peak: float = 0.0
    # Par sport
    roi_by_sport: dict[str, float] = field(default_factory=dict)
    bets_by_sport: dict[str, int] = field(default_factory=dict)
    pnl_by_sport: dict[str, float] = field(default_factory=dict)
    # Par marché
    roi_by_market: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "total_bets": self.total_bets,
            "wins": self.wins,
            "losses": self.losses,
            "roi_pct": round(self.roi_pct, 2),
            "winrate_pct": round(self.winrate_pct, 2),
            "yield_pct": round(self.yield_pct, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "total_pnl": round(self.total_pnl, 2),
            "avg_odds": round(self.avg_odds, 2),
            "avg_edge": round(self.avg_edge, 4),
            "avg_ev": round(self.avg_ev, 4),
            "roi_by_sport": {k: round(v, 2) for k, v in self.roi_by_sport.items()},
        }

    @property
    def summary(self) -> str:
        return (
            f"📊 Rapport {self.period} │ "
            f"Bets: {self.total_bets} (W:{self.wins} L:{self.losses}) │ "
            f"ROI: {self.roi_pct:+.2f}% │ "
            f"Winrate: {self.winrate_pct:.1f}% │ "
            f"Yield: {self.yield_pct:+.2f}% │ "
            f"PnL: {self.total_pnl:+.2f}€ │ "
            f"Drawdown: {self.max_drawdown_pct:.2f}%"
        )


class PerformanceTracker:
    """Calcule et suit les métriques de performance."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def generate_report(
        self,
        period: str = "all",
        sport: str | None = None,
    ) -> PerformanceReport:
        """
        Génère un rapport de performance.

        Args:
            period: "daily", "weekly", "monthly", "all"
            sport: Filtre par sport (optionnel)
        """
        # Déterminer la période
        end = date.today()
        if period == "daily":
            start = end
        elif period == "weekly":
            start = end - timedelta(days=7)
        elif period == "monthly":
            start = end - timedelta(days=30)
        else:
            start = date(2020, 1, 1)

        # Requête bets
        q = self.session.query(Bet).filter(
            Bet.created_at >= str(start),
            Bet.status.in_(["won", "lost", "void", "push"]),
        )
        if sport:
            q = q.filter(Bet.sport == sport)

        bets = q.all()

        if not bets:
            return PerformanceReport(period=period, start_date=start, end_date=end)

        # Calculs
        report = PerformanceReport(
            period=period,
            start_date=start,
            end_date=end,
        )

        report.total_bets = len(bets)
        report.wins = sum(1 for b in bets if b.status == "won")
        report.losses = sum(1 for b in bets if b.status == "lost")
        report.voids = sum(1 for b in bets if b.status in ("void", "push"))

        report.total_staked = sum(b.stake for b in bets)
        report.total_pnl = sum(b.pnl or 0 for b in bets)

        # KPIs
        settled = report.wins + report.losses
        if settled > 0:
            report.winrate_pct = (report.wins / settled) * 100
        if report.total_staked > 0:
            report.yield_pct = (report.total_pnl / report.total_staked) * 100

        odds_list = [b.bookmaker_odds for b in bets if b.bookmaker_odds > 0]
        if odds_list:
            report.avg_odds = np.mean(odds_list)

        edges = [b.edge for b in bets if b.edge is not None]
        if edges:
            report.avg_edge = np.mean(edges)

        evs = [b.ev for b in bets if b.ev is not None]
        if evs:
            report.avg_ev = np.mean(evs)

        # ROI par rapport à la bankroll
        bankroll_entries = (
            self.session.query(BankrollHistory)
            .filter(BankrollHistory.date >= start)
            .order_by(BankrollHistory.date)
            .all()
        )
        if bankroll_entries:
            report.bankroll_start = bankroll_entries[0].bankroll
            report.bankroll_end = bankroll_entries[-1].bankroll
            report.bankroll_peak = max(e.bankroll for e in bankroll_entries)

            if report.bankroll_start > 0:
                report.roi_pct = (
                    (report.bankroll_end - report.bankroll_start)
                    / report.bankroll_start * 100
                )

            # Drawdown
            report.max_drawdown_pct, report.max_drawdown_amount = self._calc_drawdown(
                [e.bankroll for e in bankroll_entries]
            )

        # Losing streak
        report.longest_losing_streak = self._longest_streak(bets, "lost")

        # Par sport
        sports = set(b.sport for b in bets)
        for s in sports:
            sport_bets = [b for b in bets if b.sport == s]
            sport_staked = sum(b.stake for b in sport_bets)
            sport_pnl = sum(b.pnl or 0 for b in sport_bets)
            report.bets_by_sport[s] = len(sport_bets)
            report.pnl_by_sport[s] = round(sport_pnl, 2)
            if sport_staked > 0:
                report.roi_by_sport[s] = round(sport_pnl / sport_staked * 100, 2)

        # Par marché
        markets = set(b.market for b in bets)
        for m in markets:
            market_bets = [b for b in bets if b.market == m]
            market_staked = sum(b.stake for b in market_bets)
            market_pnl = sum(b.pnl or 0 for b in market_bets)
            if market_staked > 0:
                report.roi_by_market[m] = round(market_pnl / market_staked * 100, 2)

        log.info(report.summary)
        return report

    @staticmethod
    def _calc_drawdown(bankroll_history: list[float]) -> tuple[float, float]:
        """Calcule le drawdown maximum."""
        if not bankroll_history:
            return 0.0, 0.0

        peak = bankroll_history[0]
        max_dd_pct = 0.0
        max_dd_amt = 0.0

        for b in bankroll_history:
            if b > peak:
                peak = b
            dd = peak - b
            dd_pct = dd / peak * 100 if peak > 0 else 0
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
                max_dd_amt = dd

        return max_dd_pct, max_dd_amt

    @staticmethod
    def _longest_streak(bets: list[Bet], status: str) -> int:
        """Calcule la plus longue série d'un statut donné."""
        max_streak = 0
        current = 0
        for b in sorted(bets, key=lambda x: x.created_at):
            if b.status == status:
                current += 1
                max_streak = max(max_streak, current)
            else:
                current = 0
        return max_streak

    def get_bankroll_curve(self) -> pd.DataFrame:
        """Retourne l'historique complet de la bankroll en DataFrame."""
        entries = (
            self.session.query(BankrollHistory)
            .order_by(BankrollHistory.date)
            .all()
        )
        if not entries:
            return pd.DataFrame(columns=["date", "bankroll", "pnl", "roi"])

        data = [
            {
                "date": e.date,
                "bankroll": e.bankroll,
                "daily_pnl": e.daily_pnl,
                "total_pnl": e.total_pnl,
                "n_bets": e.n_bets,
                "roi_pct": e.roi_pct,
                "drawdown_pct": e.drawdown_pct,
            }
            for e in entries
        ]
        return pd.DataFrame(data)
