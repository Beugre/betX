"""
Tests pour le Value Engine et le Staking Engine.
"""

import pytest

from betx.engine.value_engine import ValueEngine, ValueBet
from betx.engine.staking_engine import StakingEngine


class TestValueEngine:
    """Tests du moteur de value betting."""

    def setup_method(self):
        self.engine = ValueEngine()

    def test_detect_value_bet(self):
        """Détecte un value bet quand edge > seuil."""
        vb = self.engine.evaluate(
            match_id=1,
            sport="football",
            home_team="A",
            away_team="B",
            market="h2h",
            selection="home",
            model_probability=0.60,
            bookmaker_odds=2.10,
            bookmaker="Bet365",
        )
        assert vb is not None
        assert vb.edge > 0.03

    def test_reject_no_value(self):
        """Rejette un pari sans value."""
        vb = self.engine.evaluate(
            match_id=1,
            sport="football",
            home_team="A",
            away_team="B",
            market="h2h",
            selection="home",
            model_probability=0.45,
            bookmaker_odds=2.10,
            bookmaker="Bet365",
        )
        assert vb is None

    def test_edge_calculation(self):
        """Vérifie le calcul de l'edge."""
        vb = self.engine.evaluate(
            match_id=1,
            sport="football",
            home_team="A",
            away_team="B",
            market="h2h",
            selection="home",
            model_probability=0.60,
            bookmaker_odds=2.00,
            bookmaker="test",
        )
        assert vb is not None
        # edge = 0.60 - 0.50 = 0.10
        assert abs(vb.edge - 0.10) < 0.001

    def test_ev_calculation(self):
        """Vérifie le calcul de l'EV."""
        vb = self.engine.evaluate(
            match_id=1,
            sport="football",
            home_team="A",
            away_team="B",
            market="h2h",
            selection="home",
            model_probability=0.60,
            bookmaker_odds=2.00,
            bookmaker="test",
        )
        assert vb is not None
        # EV = 0.60*(2.00-1) - (1-0.60) = 0.60 - 0.40 = 0.20
        assert abs(vb.ev - 0.20) < 0.001

    def test_odds_filter_too_low(self):
        """Rejette les cotes trop basses."""
        vb = self.engine.evaluate(
            match_id=1,
            sport="football",
            home_team="A",
            away_team="B",
            market="h2h",
            selection="home",
            model_probability=0.95,
            bookmaker_odds=1.05,  # Trop bas
            bookmaker="test",
        )
        assert vb is None

    def test_odds_filter_too_high(self):
        """Rejette les cotes trop hautes."""
        vb = self.engine.evaluate(
            match_id=1,
            sport="football",
            home_team="A",
            away_team="B",
            market="h2h",
            selection="away",
            model_probability=0.20,
            bookmaker_odds=10.0,
            bookmaker="test",
        )
        assert vb is None

    def test_scan_match(self):
        """Scanne un match et trouve des value bets."""
        predictions = {
            "home": 0.55,
            "draw": 0.25,
            "away": 0.20,
            "over_2.5": 0.65,
        }
        odds_by_market = {
            "h2h": {
                "home": [(2.10, "BM1"), (2.05, "BM2")],
                "draw": [(3.40, "BM1")],
                "away": [(3.80, "BM1")],
            },
            "totals": {
                "over_2.5": [(1.70, "BM1")],
            },
        }
        vbs = self.engine.scan_match(
            match_id=1,
            sport="football",
            home_team="A",
            away_team="B",
            predictions=predictions,
            odds_by_market=odds_by_market,
        )
        assert isinstance(vbs, list)
        # Au moins le home à 2.10 avec P=55% devrait être value
        # edge = 0.55 - 1/2.10 = 0.55 - 0.476 = 0.074 > 3%
        assert any(v.selection == "home" for v in vbs)

    def test_confidence_classification(self):
        """Vérifie la classification de confiance."""
        vb = self.engine.evaluate(
            match_id=1, sport="football", home_team="A", away_team="B",
            market="h2h", selection="home",
            model_probability=0.70, bookmaker_odds=2.00, bookmaker="test",
        )
        assert vb is not None
        assert vb.confidence == "high"  # edge = 0.70 - 0.50 = 0.20


class TestStakingEngine:
    """Tests du moteur de staking."""

    def setup_method(self):
        self.engine = StakingEngine()

    def test_kelly_criterion(self):
        """Vérifie le calcul de Kelly."""
        # k = (p*O - 1) / (O - 1) = (0.60*2.0 - 1) / (2.0 - 1) = 0.20
        k = self.engine.kelly_criterion(probability=0.60, odds=2.0)
        assert abs(k - 0.20) < 0.001

    def test_kelly_negative_returns_zero(self):
        """Kelly négatif → 0."""
        k = self.engine.kelly_criterion(probability=0.40, odds=2.0)
        assert k == 0.0

    def test_calculate_stake(self):
        """Calcule la mise correctement."""
        vb = ValueBet(
            match_id=1, sport="football", home_team="A", away_team="B",
            market="h2h", selection="home",
            model_probability=0.60, bookmaker_odds=2.0, bookmaker="test",
            implied_probability=0.50, edge=0.10, ev=0.20,
        )
        suggestion = self.engine.calculate_stake(vb, bankroll=1000.0, method="kelly")
        assert suggestion.stake_amount > 0
        assert suggestion.stake_pct <= 0.03  # Cap max

    def test_flat_stake(self):
        """Flat betting = 1% de la bankroll."""
        vb = ValueBet(
            match_id=1, sport="football", home_team="A", away_team="B",
            market="h2h", selection="home",
            model_probability=0.60, bookmaker_odds=2.0, bookmaker="test",
            implied_probability=0.50, edge=0.10, ev=0.20,
        )
        suggestion = self.engine.calculate_stake(vb, bankroll=1000.0, method="flat")
        assert abs(suggestion.stake_amount - 10.0) < 0.01  # 1% de 1000

    def test_max_stake_cap(self):
        """La mise ne dépasse pas le cap max."""
        vb = ValueBet(
            match_id=1, sport="football", home_team="A", away_team="B",
            market="h2h", selection="home",
            model_probability=0.85, bookmaker_odds=1.50, bookmaker="test",
            implied_probability=0.667, edge=0.183, ev=0.275,
        )
        suggestion = self.engine.calculate_stake(vb, bankroll=1000.0)
        assert suggestion.stake_pct <= 0.03

    def test_batch_exposure_cap(self):
        """L'exposition totale est plafonnée."""
        vbs = []
        for i in range(20):
            vbs.append(ValueBet(
                match_id=i, sport="football", home_team=f"A{i}", away_team=f"B{i}",
                market="h2h", selection="home",
                model_probability=0.65, bookmaker_odds=1.90, bookmaker="test",
                implied_probability=0.526, edge=0.124, ev=0.235,
            ))
        suggestions = self.engine.calculate_stakes_batch(
            vbs, bankroll=1000.0, max_total_exposure=0.10
        )
        total_exposure = sum(s.stake_pct for s in suggestions)
        assert total_exposure <= 0.10 + 0.001  # Tolérance
