"""
Tests pour le modèle Football (Poisson + Dixon-Coles + ELO).
"""

import pytest
import math

from betx.models.football_model import (
    FootballModel,
    EloSystem,
    TeamStats,
    FootballPrediction,
)


class TestEloSystem:
    """Tests du système ELO."""

    def setup_method(self):
        self.elo = EloSystem(k_factor=20.0, home_advantage=100.0)

    def test_expected_score_equal(self):
        """Deux équipes de même ELO → ~50%."""
        p = self.elo.expected_score(1500, 1500)
        assert abs(p - 0.5) < 0.01

    def test_expected_score_stronger(self):
        """Équipe plus forte → probabilité plus élevée."""
        p = self.elo.expected_score(1700, 1500)
        assert p > 0.7

    def test_expected_score_weaker(self):
        """Équipe plus faible → probabilité plus basse."""
        p = self.elo.expected_score(1300, 1500)
        assert p < 0.3

    def test_update_home_win(self):
        """Victoire domicile met à jour les ELO correctement."""
        new_h, new_a = self.elo.update(1500, 1500, "home", goal_diff=2)
        assert new_h > 1500
        assert new_a < 1500

    def test_update_away_win(self):
        """Victoire extérieur met à jour les ELO."""
        new_h, new_a = self.elo.update(1500, 1500, "away", goal_diff=1)
        assert new_h < 1500
        assert new_a > 1500

    def test_update_draw(self):
        """Nul avec avantage domicile → ELO baisse légèrement à domicile."""
        new_h, new_a = self.elo.update(1500, 1500, "draw")
        # Avec home advantage, le nul est considéré comme sous-performance dom
        assert new_h < 1500

    def test_predict_returns_valid_probabilities(self):
        """Les probabilités 1X2 doivent sommer à ~1."""
        probs = self.elo.predict(1500, 1500)
        total = probs["home"] + probs["draw"] + probs["away"]
        assert abs(total - 1.0) < 0.01


class TestFootballModel:
    """Tests du modèle Poisson complet."""

    def setup_method(self):
        self.model = FootballModel()

    def test_predict_returns_prediction(self):
        """Le modèle retourne un objet FootballPrediction."""
        home = TeamStats(name="Team A", avg_goals_scored=1.8, avg_goals_conceded=0.9)
        away = TeamStats(name="Team B", avg_goals_scored=1.2, avg_goals_conceded=1.3)
        pred = self.model.predict(home, away)
        assert isinstance(pred, FootballPrediction)

    def test_probabilities_sum_to_one(self):
        """P(1) + P(X) + P(2) ≈ 1."""
        home = TeamStats(name="A")
        away = TeamStats(name="B")
        pred = self.model.predict(home, away)
        total = pred.p_home + pred.p_draw + pred.p_away
        assert abs(total - 1.0) < 0.01

    def test_probabilities_in_range(self):
        """Toutes les probabilités entre 0 et 1."""
        home = TeamStats(name="A", elo=1700, avg_goals_scored=2.5)
        away = TeamStats(name="B", elo=1300, avg_goals_conceded=2.0)
        pred = self.model.predict(home, away)

        assert 0 < pred.p_home < 1
        assert 0 < pred.p_draw < 1
        assert 0 < pred.p_away < 1
        assert 0 < pred.p_over_25 < 1
        assert 0 < pred.p_btts < 1

    def test_strong_home_team(self):
        """Équipe forte à domicile → P(1) > P(2)."""
        home = TeamStats(name="A", elo=1700, avg_goals_scored=2.2, avg_goals_conceded=0.8)
        away = TeamStats(name="B", elo=1300, avg_goals_scored=0.8, avg_goals_conceded=1.8)
        pred = self.model.predict(home, away)
        assert pred.p_home > pred.p_away

    def test_high_scoring_teams_more_over(self):
        """Équipes offensives → P(Over 2.5) élevé."""
        home = TeamStats(name="A", avg_goals_scored=2.5, avg_goals_conceded=1.5, xg_for=2.5, xg_against=1.5)
        away = TeamStats(name="B", avg_goals_scored=2.0, avg_goals_conceded=1.5, xg_for=2.0, xg_against=1.5)
        pred = self.model.predict(home, away)
        assert pred.p_over_25 > 0.55

    def test_exact_scores_present(self):
        """Le top 10 des scores exacts est rempli."""
        home = TeamStats(name="A")
        away = TeamStats(name="B")
        pred = self.model.predict(home, away)
        assert len(pred.exact_scores) == 10

    def test_to_dict(self):
        """La conversion en dict fonctionne."""
        home = TeamStats(name="A")
        away = TeamStats(name="B")
        pred = self.model.predict(home, away)
        d = pred.to_dict()
        assert "p_home" in d
        assert "p_over_25" in d
        assert "exact_scores" in d

    def test_form_factor(self):
        """Bonne forme → facteur > 1, mauvaise forme → facteur < 1."""
        good = FootballModel._form_factor(["W", "W", "W", "W", "W"])
        bad = FootballModel._form_factor(["L", "L", "L", "L", "L"])
        assert good > 1.0
        assert bad < 1.0

    def test_fatigue_factor(self):
        """Peu de repos → facteur < 1."""
        assert FootballModel._fatigue_factor(2) < 1.0
        assert FootballModel._fatigue_factor(7) == 1.0

    def test_dixon_coles_correction(self):
        """La correction DC ajuste les scores faibles."""
        # Avec rho négatif, P(0-0) augmente
        corr_00 = FootballModel._dixon_coles_correction(0, 0, 1.5, 1.2, -0.13)
        assert corr_00 > 1.0

        # Scores élevés ne sont pas affectés
        corr_33 = FootballModel._dixon_coles_correction(3, 3, 1.5, 1.2, -0.13)
        assert corr_33 == 1.0
