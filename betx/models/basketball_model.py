"""
betX – Modèle Basketball : Régression bayésienne + Pace factor.

Ce modèle projette les scores en combinant :
1. Offensive/Defensive Rating de chaque équipe
2. Pace factor (possessions par match)
3. eFG%, 3P%, Turnovers
4. Back-to-back games
5. Home court advantage
6. Gradient boosting ou régression bayésienne

Outputs :
- Score projeté (home, away)
- Spread projeté
- Total points projeté
- P(moneyline home/away)
- P(over/under total)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.stats import norm

from betx.config import settings
from betx.logger import get_logger

log = get_logger("models.basket")


# =============================================================================
# Structures
# =============================================================================
@dataclass
class BasketTeamStats:
    """Statistiques d'une équipe de basket."""
    name: str
    # Core ratings (per 100 possessions)
    offensive_rating: float = 110.0
    defensive_rating: float = 110.0
    # Pace
    pace: float = 100.0  # possessions par match
    # Shooting
    efg_pct: float = 0.52  # Effective Field Goal %
    three_point_pct: float = 0.36
    free_throw_pct: float = 0.77
    # Turnovers
    turnover_pct: float = 0.13
    # Rebounding
    offensive_rebound_pct: float = 0.25
    defensive_rebound_pct: float = 0.75
    # Forme
    recent_results: list[str] = field(default_factory=list)
    win_pct: float = 0.50
    # Fatigue
    is_back_to_back: bool = False
    rest_days: int = 2
    # ELO
    elo: float = 1500.0


@dataclass
class BasketPrediction:
    """Résultat de la prédiction basket."""
    home_team: str
    away_team: str
    # Scores projetés
    projected_home_score: float
    projected_away_score: float
    projected_total: float
    projected_spread: float  # >0 = home favori
    # Probabilités
    p_home_win: float
    p_away_win: float
    # Over/Under
    p_over_total: dict[str, float] = field(default_factory=dict)
    # Handicap
    p_home_cover: dict[str, float] = field(default_factory=dict)
    # Metadata
    model_version: str = "basket_regression_pace_v1"

    def to_dict(self) -> dict:
        return {
            "home_team": self.home_team,
            "away_team": self.away_team,
            "projected_home_score": round(self.projected_home_score, 1),
            "projected_away_score": round(self.projected_away_score, 1),
            "projected_total": round(self.projected_total, 1),
            "projected_spread": round(self.projected_spread, 1),
            "p_home_win": round(self.p_home_win, 4),
            "p_away_win": round(self.p_away_win, 4),
            "p_over_total": {k: round(v, 4) for k, v in self.p_over_total.items()},
            "p_home_cover": {k: round(v, 4) for k, v in self.p_home_cover.items()},
        }


# =============================================================================
# Modèle Basketball
# =============================================================================
class BasketballModel:
    """
    Modèle de projection de scores basket.

    La méthodologie repose sur les four factors de Dean Oliver :
    1. eFG% (shooting)
    2. Turnover rate
    3. Offensive rebounding
    4. Free throw rate

    Combinés avec le pace factor pour projeter les possessions et scores.
    """

    def __init__(self) -> None:
        self.cfg = settings.basket
        self.league_avg_pace = self.cfg.league_avg_pace
        self.league_avg_ortg = self.cfg.league_avg_rating
        self.home_advantage = self.cfg.home_advantage
        # Écart-type des scores (pour les probabilités)
        self.score_std_dev = 11.0  # ~11 points en NBA

    def predict(
        self,
        home: BasketTeamStats,
        away: BasketTeamStats,
    ) -> BasketPrediction:
        """Projette les scores et calcule les probabilités."""

        # ── 1. Pace du match ──
        game_pace = self._project_pace(home.pace, away.pace)

        # ── 2. Offensive/Defensive efficiency ajustées ──
        home_ortg = self._adjusted_rating(
            home.offensive_rating, away.defensive_rating, is_offense=True
        )
        away_ortg = self._adjusted_rating(
            away.offensive_rating, home.defensive_rating, is_offense=True
        )

        # ── 3. Projection de score brut ──
        home_score = (home_ortg / 100.0) * (game_pace / 2)
        away_score = (away_ortg / 100.0) * (game_pace / 2)

        # ── 4. Ajustements ──
        # Home court advantage
        home_score += self.home_advantage / 2
        away_score -= self.home_advantage / 2

        # Back-to-back
        if home.is_back_to_back:
            home_score *= 0.97
        if away.is_back_to_back:
            away_score *= 0.97

        # Fatigue (jours de repos)
        home_score *= self._rest_factor(home.rest_days)
        away_score *= self._rest_factor(away.rest_days)

        # Forme récente
        home_form = self._form_factor(home.recent_results)
        away_form = self._form_factor(away.recent_results)
        home_score *= home_form
        away_score *= away_form

        # ── 5. Probabilités ──
        spread = home_score - away_score
        total = home_score + away_score

        # Moneyline (utilise la distribution normale de la différence de score)
        p_home_win = self._moneyline_probability(spread)
        p_away_win = 1 - p_home_win

        # Over/Under total
        p_over = self._over_under_probabilities(total)

        # Handicap/Spread
        p_cover = self._spread_probabilities(spread)

        prediction = BasketPrediction(
            home_team=home.name,
            away_team=away.name,
            projected_home_score=round(home_score, 1),
            projected_away_score=round(away_score, 1),
            projected_total=round(total, 1),
            projected_spread=round(spread, 1),
            p_home_win=p_home_win,
            p_away_win=p_away_win,
            p_over_total=p_over,
            p_home_cover=p_cover,
        )

        log.info(
            f"{home.name} vs {away.name}: "
            f"{home_score:.1f}-{away_score:.1f} "
            f"(spread={spread:+.1f}, total={total:.1f}) "
            f"P(home)={p_home_win:.3f}"
        )
        return prediction

    # ─── Composants ──────────────────────────────────────────────

    def _project_pace(self, pace_home: float, pace_away: float) -> float:
        """
        Projette le pace du match.
        Pace = (pace_home * pace_away) / league_avg
        """
        if self.league_avg_pace <= 0:
            return (pace_home + pace_away) / 2
        return (pace_home * pace_away) / self.league_avg_pace

    def _adjusted_rating(
        self,
        team_rating: float,
        opponent_rating: float,
        is_offense: bool = True,
    ) -> float:
        """
        Ajuste le rating offensif/défensif en fonction de l'adversaire.

        Si une équipe a un ORTG de 115 mais affronte une défense à 105 DRTG
        (contre une moyenne de ligue de 110), elle sera légèrement réduite.
        """
        if is_offense:
            # Plus l'adversaire est bon défensivement, moins on marque
            adjustment = opponent_rating / self.league_avg_ortg
            return team_rating * adjustment
        else:
            adjustment = opponent_rating / self.league_avg_ortg
            return team_rating * adjustment

    def _moneyline_probability(self, spread: float) -> float:
        """
        Convertit un spread en probabilité de victoire.
        Utilise une distribution normale.
        """
        # P(home wins) = P(home_score - away_score > 0)
        # = P(N(spread, std) > 0)
        p = norm.cdf(spread / self.score_std_dev)
        return max(0.02, min(0.98, p))

    def _over_under_probabilities(self, projected_total: float) -> dict[str, float]:
        """Calcule les probabilités over pour différents totaux."""
        # Écart-type du total (~13 points en NBA)
        total_std = self.score_std_dev * 1.2

        # Lignes typiques
        lines = []
        base = round(projected_total / 0.5) * 0.5
        for offset in [-5.0, -2.5, -0.5, 0.5, 2.5, 5.0]:
            lines.append(base + offset)

        result = {}
        for line in sorted(set(lines)):
            p_over = 1 - norm.cdf(line, loc=projected_total, scale=total_std)
            result[str(line)] = max(0.03, min(0.97, p_over))

        return result

    def _spread_probabilities(self, projected_spread: float) -> dict[str, float]:
        """Calcule les probabilités de cover pour différents spreads."""
        lines = []
        base = round(projected_spread / 0.5) * 0.5
        for offset in [-5.0, -2.5, -0.5, 0.5, 2.5, 5.0]:
            lines.append(base + offset)

        result = {}
        for line in sorted(set(lines)):
            # P(home team covers -line) = P(margin > line)
            p_cover = norm.cdf((projected_spread - line) / self.score_std_dev)
            result[str(line)] = max(0.03, min(0.97, p_cover))

        return result

    @staticmethod
    def _rest_factor(rest_days: int) -> float:
        """Facteur basé sur les jours de repos."""
        if rest_days <= 0:
            return 0.95  # back-to-back
        elif rest_days == 1:
            return 0.98
        elif rest_days == 2:
            return 1.0
        elif rest_days >= 5:
            return 0.99  # Légère rouille si trop de repos
        return 1.0

    @staticmethod
    def _form_factor(results: list[str]) -> float:
        """Facteur de forme récente."""
        if not results:
            return 1.0
        pts = {"W": 1.0, "L": 0.0}
        weights = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]
        total_w = 0.0
        total_pts = 0.0
        for i, r in enumerate(results[:10]):
            w = weights[i] if i < len(weights) else 0.1
            total_pts += pts.get(r.upper(), 0.5) * w
            total_w += w
        ratio = total_pts / max(total_w, 0.1)
        return 0.97 + ratio * 0.06  # Range: 0.97 to 1.03

    # ─── ELO ─────────────────────────────────────────────────────
    def update_elo(
        self,
        winner: BasketTeamStats,
        loser: BasketTeamStats,
        margin: int,
    ) -> tuple[float, float]:
        """Met à jour les ELO après un match."""
        k = 20.0
        # Multiplicateur de marge
        mult = np.log(abs(margin) + 1) * 0.7 + 0.8

        elo_diff = winner.elo - loser.elo
        expected = 1.0 / (1.0 + 10 ** (-elo_diff / 400.0))

        delta = k * mult * (1 - expected)

        winner.elo += delta
        loser.elo -= delta

        return winner.elo, loser.elo
