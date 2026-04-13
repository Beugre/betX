"""
betX – Backtester : simulation historique des stratégies.

Méthodes :
- Walk-forward validation (split par saison)
- Simulation Kelly / Flat
- Analyse de robustesse

Objectifs :
- Vérifier que le modèle a un edge réel
- Éviter l'overfitting
- Tester différents seuils d'edge
- Estimer la variance (drawdowns, losing streaks)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from betx.engine.value_engine import ValueEngine
from betx.engine.staking_engine import StakingEngine
from betx.logger import get_logger

log = get_logger("backtest")


@dataclass
class BacktestResult:
    """Résultat d'un backtest."""
    name: str
    # Paramètres
    min_edge: float
    staking_method: str
    kelly_fraction: float
    initial_bankroll: float
    # Résultats
    final_bankroll: float = 0.0
    total_bets: int = 0
    wins: int = 0
    losses: int = 0
    total_staked: float = 0.0
    total_pnl: float = 0.0
    roi_pct: float = 0.0
    yield_pct: float = 0.0
    winrate_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_amount: float = 0.0
    longest_losing_streak: int = 0
    avg_edge: float = 0.0
    avg_ev: float = 0.0
    sharpe_ratio: float = 0.0
    # Historique
    bankroll_history: list[float] = field(default_factory=list)
    daily_pnl: list[float] = field(default_factory=list)
    bet_results: list[dict] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (
            f"📊 Backtest '{self.name}'\n"
            f"  Bets: {self.total_bets} (W:{self.wins} L:{self.losses})\n"
            f"  ROI: {self.roi_pct:+.2f}% │ Yield: {self.yield_pct:+.2f}%\n"
            f"  Winrate: {self.winrate_pct:.1f}%\n"
            f"  Bankroll: {self.initial_bankroll:.0f} → {self.final_bankroll:.0f}\n"
            f"  Max DD: {self.max_drawdown_pct:.2f}%\n"
            f"  Sharpe: {self.sharpe_ratio:.2f}\n"
            f"  Avg Edge: {self.avg_edge:.2%} │ Avg EV: {self.avg_ev:.2%}"
        )


@dataclass
class BacktestBet:
    """Un pari dans le backtest."""
    date: date
    match_id: int
    sport: str
    home_team: str
    away_team: str
    market: str
    selection: str
    model_prob: float
    odds: float
    edge: float
    ev: float
    stake: float
    result: str  # "won", "lost"
    pnl: float
    # Résultat réel
    actual_home_score: int = 0
    actual_away_score: int = 0


class Backtester:
    """
    Moteur de backtesting walk-forward.

    Simule la stratégie sur des données historiques en respectant
    la chronologie (pas de look-ahead bias).
    """

    def __init__(
        self,
        initial_bankroll: float = 1000.0,
        min_edge: float = 0.03,
        staking_method: str = "kelly",
        kelly_fraction: float = 0.25,
        max_stake_pct: float = 0.03,
    ):
        self.initial_bankroll = initial_bankroll
        self.min_edge = min_edge
        self.staking_method = staking_method
        self.kelly_fraction = kelly_fraction
        self.max_stake_pct = max_stake_pct

    def run(
        self,
        historical_data: pd.DataFrame,
        name: str = "backtest",
    ) -> BacktestResult:
        """
        Exécute un backtest sur des données historiques.

        Args:
            historical_data: DataFrame avec colonnes :
                - date, match_id, sport, home_team, away_team
                - market, selection
                - model_prob (probabilité du modèle)
                - odds (cote du bookmaker)
                - actual_result ("won" ou "lost")
            name: Nom du backtest

        Returns:
            BacktestResult
        """
        result = BacktestResult(
            name=name,
            min_edge=self.min_edge,
            staking_method=self.staking_method,
            kelly_fraction=self.kelly_fraction,
            initial_bankroll=self.initial_bankroll,
        )

        bankroll = self.initial_bankroll
        peak_bankroll = bankroll
        max_dd = 0.0
        max_dd_amt = 0.0
        losing_streak = 0
        max_losing_streak = 0
        edges = []
        evs = []
        daily_pnls = []

        result.bankroll_history.append(bankroll)

        # Trier par date
        df = historical_data.sort_values("date").reset_index(drop=True)

        current_date = None
        day_pnl = 0.0

        for _, row in df.iterrows():
            # Vérifier l'edge
            prob = row["model_prob"]
            odds = row["odds"]
            implied = 1.0 / odds
            edge = prob - implied
            ev = prob * (odds - 1) - (1 - prob)

            if edge < self.min_edge or ev <= 0:
                continue

            # Calculer la mise
            if self.staking_method == "kelly":
                k = (prob * odds - 1) / (odds - 1)
                k = max(0, k)
                stake_pct = k * self.kelly_fraction
            else:
                stake_pct = 0.01  # Flat 1%

            stake_pct = min(stake_pct, self.max_stake_pct)
            stake = bankroll * stake_pct

            if stake < 1.0:  # Mise minimale
                continue

            # Résultat
            actual = row["actual_result"]
            if actual == "won":
                pnl = stake * (odds - 1)
                result.wins += 1
                losing_streak = 0
            elif actual == "lost":
                pnl = -stake
                result.losses += 1
                losing_streak += 1
                max_losing_streak = max(max_losing_streak, losing_streak)
            else:
                pnl = 0.0
                losing_streak = 0

            bankroll += pnl
            result.total_staked += stake
            result.total_pnl += pnl
            result.total_bets += 1
            edges.append(edge)
            evs.append(ev)

            # Tracking date
            bet_date = row.get("date")
            if bet_date != current_date:
                if current_date is not None:
                    daily_pnls.append(day_pnl)
                current_date = bet_date
                day_pnl = 0.0
            day_pnl += pnl

            # Drawdown
            if bankroll > peak_bankroll:
                peak_bankroll = bankroll
            dd = (peak_bankroll - bankroll) / peak_bankroll * 100
            if dd > max_dd:
                max_dd = dd
                max_dd_amt = peak_bankroll - bankroll

            result.bankroll_history.append(bankroll)

            # Log du bet
            result.bet_results.append({
                "date": str(bet_date),
                "match": f"{row.get('home_team', '')} vs {row.get('away_team', '')}",
                "selection": row.get("selection", ""),
                "odds": odds,
                "prob": prob,
                "edge": round(edge, 4),
                "stake": round(stake, 2),
                "result": actual,
                "pnl": round(pnl, 2),
                "bankroll": round(bankroll, 2),
            })

        # Dernier jour
        if day_pnl != 0:
            daily_pnls.append(day_pnl)

        # Métriques finales
        result.final_bankroll = round(bankroll, 2)
        result.roi_pct = (bankroll - self.initial_bankroll) / self.initial_bankroll * 100
        result.max_drawdown_pct = max_dd
        result.max_drawdown_amount = max_dd_amt
        result.longest_losing_streak = max_losing_streak
        result.daily_pnl = daily_pnls

        settled = result.wins + result.losses
        if settled > 0:
            result.winrate_pct = result.wins / settled * 100
        if result.total_staked > 0:
            result.yield_pct = result.total_pnl / result.total_staked * 100
        if edges:
            result.avg_edge = np.mean(edges)
        if evs:
            result.avg_ev = np.mean(evs)

        # Sharpe ratio (sur PnL quotidien)
        if daily_pnls and len(daily_pnls) > 1:
            pnl_arr = np.array(daily_pnls)
            if pnl_arr.std() > 0:
                result.sharpe_ratio = (pnl_arr.mean() / pnl_arr.std()) * np.sqrt(252)

        log.info(f"\n{result.summary}")
        return result


class WalkForwardValidator:
    """
    Validation walk-forward par saisons.

    Entraîne le modèle sur la saison N, teste sur la saison N+1.
    Évite le look-ahead bias et l'overfitting.
    """

    def __init__(
        self,
        backtester: Backtester,
    ):
        self.backtester = backtester

    def validate(
        self,
        all_data: pd.DataFrame,
        season_column: str = "season",
    ) -> list[BacktestResult]:
        """
        Exécute une validation walk-forward.

        Args:
            all_data: Toutes les données avec une colonne saison
            season_column: Nom de la colonne saison

        Returns:
            Liste de BacktestResult, un par saison de test
        """
        seasons = sorted(all_data[season_column].unique())
        results = []

        if len(seasons) < 2:
            log.warning("Pas assez de saisons pour walk-forward")
            return results

        for i in range(1, len(seasons)):
            train_seasons = seasons[:i]
            test_season = seasons[i]

            train_data = all_data[all_data[season_column].isin(train_seasons)]
            test_data = all_data[all_data[season_column] == test_season]

            log.info(
                f"Walk-forward: train={train_seasons} → test={test_season} "
                f"({len(test_data)} bets)"
            )

            if test_data.empty:
                continue

            result = self.backtester.run(
                test_data,
                name=f"WF_{test_season}",
            )
            results.append(result)

        # Résumé global
        if results:
            total_bets = sum(r.total_bets for r in results)
            total_pnl = sum(r.total_pnl for r in results)
            total_staked = sum(r.total_staked for r in results)
            avg_roi = np.mean([r.roi_pct for r in results])
            avg_yield = (total_pnl / total_staked * 100) if total_staked > 0 else 0

            log.info(
                f"\n{'='*60}\n"
                f"Walk-Forward Summary: {len(results)} seasons\n"
                f"Total bets: {total_bets}\n"
                f"Avg ROI: {avg_roi:+.2f}%\n"
                f"Yield: {avg_yield:+.2f}%\n"
                f"Total PnL: {total_pnl:+.2f}\n"
                f"{'='*60}"
            )

        return results

    def edge_sensitivity_analysis(
        self,
        data: pd.DataFrame,
        edge_thresholds: list[float] | None = None,
    ) -> pd.DataFrame:
        """
        Teste différents seuils d'edge pour trouver l'optimal.

        Returns:
            DataFrame avec résultats par seuil
        """
        if edge_thresholds is None:
            edge_thresholds = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10]

        results = []
        for threshold in edge_thresholds:
            bt = Backtester(
                initial_bankroll=self.backtester.initial_bankroll,
                min_edge=threshold,
                staking_method=self.backtester.staking_method,
                kelly_fraction=self.backtester.kelly_fraction,
            )
            result = bt.run(data, name=f"edge_{threshold:.0%}")
            results.append({
                "min_edge": threshold,
                "total_bets": result.total_bets,
                "roi_pct": result.roi_pct,
                "yield_pct": result.yield_pct,
                "winrate_pct": result.winrate_pct,
                "max_drawdown_pct": result.max_drawdown_pct,
                "sharpe": result.sharpe_ratio,
                "final_bankroll": result.final_bankroll,
            })

        df = pd.DataFrame(results)
        log.info(f"\nSensitivity Analysis:\n{df.to_string(index=False)}")
        return df
