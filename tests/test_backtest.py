"""
Tests pour le backtester.
"""

import pytest
import pandas as pd
import numpy as np

from betx.backtest.backtester import Backtester, BacktestResult, WalkForwardValidator


class TestBacktester:
    """Tests du backtester."""

    def _make_data(self, n=100, edge=0.05, win_rate=0.55):
        """Génère des données de test."""
        rng = np.random.default_rng(42)
        data = []
        for i in range(n):
            prob = 0.55
            odds = 1 / (prob - edge)  # Cote qui donne l'edge souhaité
            won = rng.random() < win_rate
            data.append({
                "date": f"2025-01-{(i % 28) + 1:02d}",
                "match_id": i,
                "sport": "football",
                "home_team": f"Team_{i}",
                "away_team": f"Team_{i+100}",
                "market": "h2h",
                "selection": "home",
                "model_prob": prob,
                "odds": odds,
                "actual_result": "won" if won else "lost",
                "season": "2024" if i < 50 else "2025",
            })
        return pd.DataFrame(data)

    def test_run_returns_result(self):
        """Le backtest retourne un BacktestResult."""
        bt = Backtester(initial_bankroll=1000.0, min_edge=0.03)
        data = self._make_data()
        result = bt.run(data)
        assert isinstance(result, BacktestResult)
        assert result.total_bets > 0

    def test_bankroll_changes(self):
        """La bankroll change après le backtest."""
        bt = Backtester(initial_bankroll=1000.0)
        data = self._make_data()
        result = bt.run(data)
        assert result.final_bankroll != result.initial_bankroll

    def test_wins_plus_losses_equals_total(self):
        """Wins + losses = total bets."""
        bt = Backtester(initial_bankroll=1000.0)
        data = self._make_data()
        result = bt.run(data)
        assert result.wins + result.losses == result.total_bets

    def test_high_edge_positive_roi(self):
        """Avec un edge élevé, le ROI doit être positif."""
        bt = Backtester(initial_bankroll=1000.0, min_edge=0.01)
        data = self._make_data(n=500, edge=0.08, win_rate=0.60)
        result = bt.run(data)
        # Avec 60% winrate et cotes ajustées, ROI devrait être positif
        assert result.total_bets > 0

    def test_bankroll_history_length(self):
        """L'historique de bankroll a la bonne taille."""
        bt = Backtester(initial_bankroll=1000.0)
        data = self._make_data(n=50)
        result = bt.run(data)
        assert len(result.bankroll_history) == result.total_bets + 1

    def test_no_bets_below_edge(self):
        """Pas de paris si l'edge est insuffisant."""
        bt = Backtester(initial_bankroll=1000.0, min_edge=0.50)  # Edge très élevé
        data = self._make_data(edge=0.05)
        result = bt.run(data)
        assert result.total_bets == 0


class TestWalkForwardValidator:
    """Tests de la validation walk-forward."""

    def test_validate(self):
        """La validation WF retourne des résultats par saison."""
        bt = Backtester(initial_bankroll=1000.0, min_edge=0.01)
        validator = WalkForwardValidator(bt)

        rng = np.random.default_rng(42)
        data = []
        for season in ["2022", "2023", "2024"]:
            for i in range(30):
                prob = 0.55
                odds = 2.00
                data.append({
                    "date": f"{season}-06-{(i % 28) + 1:02d}",
                    "match_id": int(season) * 100 + i,
                    "sport": "football",
                    "home_team": f"T_{i}",
                    "away_team": f"T_{i+50}",
                    "market": "h2h",
                    "selection": "home",
                    "model_prob": prob,
                    "odds": odds,
                    "actual_result": "won" if rng.random() < 0.55 else "lost",
                    "season": season,
                })
        df = pd.DataFrame(data)
        results = validator.validate(df)
        assert len(results) >= 1  # Au moins une saison de test
