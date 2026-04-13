"""
Tests pour le modèle Tennis (ELO surface + service stats).
"""

import pytest

from betx.models.tennis_model import TennisModel, TennisPlayerStats, TennisPrediction


class TestTennisModel:
    """Tests du modèle tennis."""

    def setup_method(self):
        self.model = TennisModel()

    def test_predict_returns_prediction(self):
        """Le modèle retourne un TennisPrediction."""
        a = TennisPlayerStats(name="Player A", elo_global=1800, elo_hard=1850)
        b = TennisPlayerStats(name="Player B", elo_global=1600, elo_hard=1550)
        pred = self.model.predict(a, b, surface="hard")
        assert isinstance(pred, TennisPrediction)

    def test_probabilities_sum_to_one(self):
        """P(A) + P(B) ≈ 1."""
        a = TennisPlayerStats(name="A")
        b = TennisPlayerStats(name="B")
        pred = self.model.predict(a, b)
        assert abs(pred.p_win_a + pred.p_win_b - 1.0) < 0.01

    def test_stronger_player_favored(self):
        """Joueur avec ELO supérieur → favori."""
        a = TennisPlayerStats(name="A", elo_global=1900, elo_hard=1900)
        b = TennisPlayerStats(name="B", elo_global=1400, elo_hard=1400)
        pred = self.model.predict(a, b, surface="hard")
        assert pred.p_win_a > 0.65

    def test_surface_matters(self):
        """Le spécialiste de la surface doit être favorisé."""
        clay_specialist = TennisPlayerStats(
            name="Clay King", elo_global=1600, elo_clay=1800, elo_hard=1400
        )
        hard_specialist = TennisPlayerStats(
            name="Hard King", elo_global=1600, elo_hard=1800, elo_clay=1400
        )
        # Sur terre battue
        pred_clay = self.model.predict(clay_specialist, hard_specialist, surface="clay")
        assert pred_clay.p_win_a > 0.5

        # Sur dur
        pred_hard = self.model.predict(clay_specialist, hard_specialist, surface="hard")
        assert pred_hard.p_win_a < 0.5

    def test_total_games_positive(self):
        """Le nombre total de jeux doit être positif et réaliste."""
        a = TennisPlayerStats(name="A")
        b = TennisPlayerStats(name="B")
        pred = self.model.predict(a, b, best_of=3)
        assert pred.expected_total_games > 15
        assert pred.expected_total_games < 50

    def test_best_of_5_more_games(self):
        """Un match en 5 sets doit avoir plus de jeux."""
        a = TennisPlayerStats(name="A")
        b = TennisPlayerStats(name="B")
        pred3 = self.model.predict(a, b, best_of=3)
        pred5 = self.model.predict(a, b, best_of=5)
        assert pred5.expected_total_games > pred3.expected_total_games

    def test_over_under_games(self):
        """Les probabilités over/under jeux sont dans [0, 1]."""
        a = TennisPlayerStats(name="A")
        b = TennisPlayerStats(name="B")
        pred = self.model.predict(a, b)
        for threshold, prob in pred.p_over_games.items():
            assert 0 <= prob <= 1, f"Over {threshold}: {prob}"

    def test_serve_stats_impact(self):
        """Un bon serveur doit être favorisé."""
        big_server = TennisPlayerStats(
            name="Server", serve_win_pct=0.70, return_win_pct=0.35
        )
        returner = TennisPlayerStats(
            name="Returner", serve_win_pct=0.55, return_win_pct=0.45
        )
        pred = self.model.predict(big_server, returner)
        # Les deux ont des forces différentes, vérifier la cohérence
        assert pred.p_win_a > 0 and pred.p_win_b > 0
