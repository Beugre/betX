"""
Tests pour le modèle Basketball.
"""

import pytest

from betx.models.basketball_model import BasketballModel, BasketTeamStats, BasketPrediction


class TestBasketballModel:
    """Tests du modèle basket."""

    def setup_method(self):
        self.model = BasketballModel()

    def test_predict_returns_prediction(self):
        """Le modèle retourne un BasketPrediction."""
        home = BasketTeamStats(name="Lakers")
        away = BasketTeamStats(name="Celtics")
        pred = self.model.predict(home, away)
        assert isinstance(pred, BasketPrediction)

    def test_scores_positive(self):
        """Les scores projetés doivent être positifs."""
        home = BasketTeamStats(name="A")
        away = BasketTeamStats(name="B")
        pred = self.model.predict(home, away)
        assert pred.projected_home_score > 50
        assert pred.projected_away_score > 50

    def test_total_is_sum(self):
        """Le total projeté = home + away."""
        home = BasketTeamStats(name="A")
        away = BasketTeamStats(name="B")
        pred = self.model.predict(home, away)
        expected_total = pred.projected_home_score + pred.projected_away_score
        assert abs(pred.projected_total - expected_total) < 0.2

    def test_probabilities_sum_to_one(self):
        """P(home) + P(away) ≈ 1."""
        home = BasketTeamStats(name="A")
        away = BasketTeamStats(name="B")
        pred = self.model.predict(home, away)
        assert abs(pred.p_home_win + pred.p_away_win - 1.0) < 0.01

    def test_home_court_advantage(self):
        """Équipes égales → domicile légèrement favori."""
        home = BasketTeamStats(name="A", offensive_rating=110, defensive_rating=110)
        away = BasketTeamStats(name="B", offensive_rating=110, defensive_rating=110)
        pred = self.model.predict(home, away)
        assert pred.p_home_win > 0.50

    def test_strong_offense(self):
        """Équipe très offensive → score plus élevé."""
        strong = BasketTeamStats(name="A", offensive_rating=120, defensive_rating=108)
        weak = BasketTeamStats(name="B", offensive_rating=105, defensive_rating=115)
        pred = self.model.predict(strong, weak)
        assert pred.projected_home_score > pred.projected_away_score

    def test_back_to_back_penalty(self):
        """Back-to-back réduit le score."""
        normal = BasketTeamStats(name="A", is_back_to_back=False)
        b2b = BasketTeamStats(name="A_b2b", is_back_to_back=True)
        away = BasketTeamStats(name="B")

        pred_normal = self.model.predict(normal, away)
        pred_b2b = self.model.predict(b2b, away)
        assert pred_b2b.projected_home_score < pred_normal.projected_home_score

    def test_over_under_probabilities(self):
        """Les probabilités O/U sont dans [0, 1]."""
        home = BasketTeamStats(name="A")
        away = BasketTeamStats(name="B")
        pred = self.model.predict(home, away)
        for line, prob in pred.p_over_total.items():
            assert 0 <= prob <= 1, f"Over {line}: {prob}"
