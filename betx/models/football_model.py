"""
betX – Modèle Football : Poisson + Dixon-Coles + ELO + xG.

Ce modèle hybride combine :
1. Distribution de Poisson pour simuler les scores
2. Correction Dixon-Coles pour les scores faibles (0-0, 1-0, 0-1, 1-1)
3. ELO dynamique (forme récente, domicile/extérieur)
4. Intégration xG (quand disponible)

Outputs :
- P(1), P(X), P(2) → marché 1X2
- P(Over 2.5), P(Over 3.5)
- P(BTTS)
- Scores exacts (top 10)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.stats import poisson
from scipy.optimize import minimize

from betx.config import settings
from betx.logger import get_logger

log = get_logger("models.football")


# =============================================================================
# Structures de données
# =============================================================================
@dataclass
class TeamStats:
    """Statistiques d'une équipe pour le modèle."""
    name: str
    avg_goals_scored: float = 1.3
    avg_goals_conceded: float = 1.3
    xg_for: float = 1.3
    xg_against: float = 1.3
    elo: float = 1500.0
    home_elo: float = 1500.0
    away_elo: float = 1500.0
    recent_form: list[str] = field(default_factory=list)  # ["W","W","L","D","W"]
    rest_days: int = 7
    match_importance: float = 1.0  # 1.0 normal, 1.2 derby, 1.5 finale


@dataclass
class FootballPrediction:
    """Résultat de la prédiction football."""
    home_team: str
    away_team: str
    # Lambda Poisson
    lambda_home: float
    lambda_away: float
    # Probabilités 1X2
    p_home: float
    p_draw: float
    p_away: float
    # Over/Under
    p_over_15: float
    p_over_25: float
    p_over_35: float
    # BTTS
    p_btts: float
    # Scores exacts (top N)
    exact_scores: dict[str, float] = field(default_factory=dict)
    # Metadata
    model_version: str = "poisson_dc_elo_xg_v1"

    def to_dict(self) -> dict:
        return {
            "home_team": self.home_team,
            "away_team": self.away_team,
            "lambda_home": round(self.lambda_home, 4),
            "lambda_away": round(self.lambda_away, 4),
            "p_home": round(self.p_home, 4),
            "p_draw": round(self.p_draw, 4),
            "p_away": round(self.p_away, 4),
            "p_over_15": round(self.p_over_15, 4),
            "p_over_25": round(self.p_over_25, 4),
            "p_over_35": round(self.p_over_35, 4),
            "p_btts": round(self.p_btts, 4),
            "exact_scores": {k: round(v, 4) for k, v in self.exact_scores.items()},
            "model_version": self.model_version,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


# =============================================================================
# ELO System
# =============================================================================
class EloSystem:
    """Système ELO dynamique pour le football."""

    def __init__(
        self,
        k_factor: float = 20.0,
        home_advantage: float = 100.0,
    ):
        self.k = k_factor
        self.home_adv = home_advantage

    def expected_score(self, elo_a: float, elo_b: float) -> float:
        """Probabilité de victoire de A vs B."""
        return 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400.0))

    def update(
        self,
        elo_home: float,
        elo_away: float,
        result: str,  # "home", "draw", "away"
        goal_diff: int = 1,
    ) -> tuple[float, float]:
        """Met à jour les ELO après un match."""
        # Score réel
        scores = {"home": 1.0, "draw": 0.5, "away": 0.0}
        actual = scores.get(result, 0.5)

        # Multiplicateur pour écart de buts
        gd_mult = max(1.0, math.log(abs(goal_diff) + 1) + 1) if goal_diff > 0 else 1.0

        # Expected
        exp_home = self.expected_score(elo_home + self.home_adv, elo_away)
        exp_away = 1.0 - exp_home

        # Update
        new_home = elo_home + self.k * gd_mult * (actual - exp_home)
        new_away = elo_away + self.k * gd_mult * ((1 - actual) - exp_away)

        return new_home, new_away

    def predict(self, elo_home: float, elo_away: float) -> dict[str, float]:
        """Prédit les probabilités 1X2 à partir des ELO."""
        p_home_win = self.expected_score(elo_home + self.home_adv, elo_away)
        # Estimation du nul basée sur la différence ELO
        elo_diff = abs((elo_home + self.home_adv) - elo_away)
        # Plus les équipes sont proches, plus le nul est probable
        p_draw = max(0.15, 0.30 - elo_diff / 2000)
        p_draw = min(p_draw, 0.35)

        # Ajustement
        remaining = 1.0 - p_draw
        p_home = p_home_win * remaining
        p_away = (1.0 - p_home_win) * remaining

        return {"home": p_home, "draw": p_draw, "away": p_away}


# =============================================================================
# Modèle Poisson + Dixon-Coles
# =============================================================================
class FootballModel:
    """
    Modèle hybride Poisson + Dixon-Coles + ELO + xG.

    Le modèle calcule les λ (intensités d'attaque/défense) en combinant :
    - Moyennes historiques de buts
    - xG si disponible
    - Forme récente
    - ELO
    - Avantage domicile
    - Fatigue (jours de repos)
    """

    def __init__(self) -> None:
        self.cfg = settings.football
        self.elo = EloSystem(
            k_factor=self.cfg.elo_k_factor,
            home_advantage=100.0,
        )
        # Moyennes de la ligue (valeurs par défaut, à calibrer)
        self.league_avg_goals: float = 2.7  # ~2.7 buts/match en moyenne
        self.league_avg_home_goals: float = 1.5
        self.league_avg_away_goals: float = 1.2
        # Dixon-Coles rho parameter
        self.rho: float = -0.13  # Typiquement entre -0.2 et 0

    def set_league_averages(
        self,
        avg_goals: float,
        avg_home: float,
        avg_away: float,
    ) -> None:
        """Met à jour les moyennes de la ligue."""
        self.league_avg_goals = avg_goals
        self.league_avg_home_goals = avg_home
        self.league_avg_away_goals = avg_away
        log.info(
            f"Moyennes ligue: {avg_goals:.2f} total, "
            f"{avg_home:.2f} dom, {avg_away:.2f} ext"
        )

    def compute_lambdas(
        self,
        home: TeamStats,
        away: TeamStats,
    ) -> tuple[float, float]:
        """
        Calcule les paramètres λ_home et λ_away du modèle Poisson.

        Combine buts réels et xG selon la pondération configurée.
        Intègre ELO, forme récente, et fatigue.
        """
        xg_w = self.cfg.xg_weight
        goals_w = self.cfg.goals_weight

        # ── Force d'attaque et de défense (Dixon-Coles standard) ──
        #
        # ATTAQUE : buts marqués / moyenne de la ligue pour cette position
        #   home_attack = home_scored / μ_home
        #   away_attack = away_scored / μ_away
        #
        # DÉFENSE : buts encaissés / moyenne de CE QUE LE CAMP ADVERSE MARQUE
        #   away_defense = away_conceded / μ_home  (ce que les away encaissent
        #       doit être comparé à ce que les home marquent en moyenne)
        #   home_defense = home_conceded / μ_away  (ce que les home encaissent
        #       doit être comparé à ce que les away marquent en moyenne)
        #
        mu_h = max(self.league_avg_home_goals, 0.5)
        mu_a = max(self.league_avg_away_goals, 0.5)

        # Home attack : combien cette équipe marque à domicile vs la moyenne dom
        home_att_goals = home.avg_goals_scored / mu_h
        home_att_xg = home.xg_for / mu_h
        home_attack = goals_w * home_att_goals + xg_w * home_att_xg

        # Away defense : combien cette équipe encaisse à l'extérieur
        # vs ce que les équipes à domicile marquent en moyenne (= μ_home)
        away_def_goals = away.avg_goals_conceded / mu_h
        away_def_xg = away.xg_against / mu_h
        away_defense = goals_w * away_def_goals + xg_w * away_def_xg

        # Away attack : combien cette équipe marque à l'extérieur vs la moyenne ext
        away_att_goals = away.avg_goals_scored / mu_a
        away_att_xg = away.xg_for / mu_a
        away_attack = goals_w * away_att_goals + xg_w * away_att_xg

        # Home defense : combien cette équipe encaisse à domicile
        # vs ce que les équipes à l'extérieur marquent en moyenne (= μ_away)
        home_def_goals = home.avg_goals_conceded / mu_a
        home_def_xg = home.xg_against / mu_a
        home_defense = goals_w * home_def_goals + xg_w * home_def_xg

        # ── Lambda de base ──
        lambda_home = home_attack * away_defense * self.league_avg_home_goals
        lambda_away = away_attack * home_defense * self.league_avg_away_goals

        # ── Ajustement ELO ──
        elo_diff = (home.elo - away.elo) / 400.0
        elo_factor = 10 ** (elo_diff / 2)  # Facteur multiplicatif subtil
        elo_factor = max(0.7, min(1.4, elo_factor))  # Cap
        lambda_home *= (1 + (elo_factor - 1) * 0.15)
        lambda_away *= (1 + (1 / elo_factor - 1) * 0.15)

        # ── Ajustement forme récente ──
        home_form = self._form_factor(home.recent_form)
        away_form = self._form_factor(away.recent_form)
        lambda_home *= home_form
        lambda_away *= away_form

        # ── Avantage domicile ──
        lambda_home *= (1 + self.cfg.home_advantage)

        # ── Fatigue (jours de repos) ──
        lambda_home *= self._fatigue_factor(home.rest_days)
        lambda_away *= self._fatigue_factor(away.rest_days)

        # ── Importance du match ──
        lambda_home *= (1 + (home.match_importance - 1) * 0.05)
        lambda_away *= (1 + (away.match_importance - 1) * 0.05)

        # Cap pour éviter les valeurs aberrantes
        lambda_home = max(0.3, min(4.5, lambda_home))
        lambda_away = max(0.2, min(4.0, lambda_away))

        return lambda_home, lambda_away

    def predict(self, home: TeamStats, away: TeamStats) -> FootballPrediction:
        """
        Génère une prédiction complète pour un match.

        Returns:
            FootballPrediction avec toutes les probabilités
        """
        lam_h, lam_a = self.compute_lambdas(home, away)
        log.info(
            f"{home.name} vs {away.name}: λ_home={lam_h:.3f}, λ_away={lam_a:.3f}"
        )

        # ── Matrice de scores (Poisson) + Dixon-Coles ──
        max_goals = 8
        score_matrix = np.zeros((max_goals, max_goals))

        for i in range(max_goals):
            for j in range(max_goals):
                p = poisson.pmf(i, lam_h) * poisson.pmf(j, lam_a)
                # Correction Dixon-Coles pour scores faibles
                p *= self._dixon_coles_correction(i, j, lam_h, lam_a, self.rho)
                score_matrix[i, j] = p

        # Normaliser
        score_matrix /= score_matrix.sum()

        # ── Probabilités 1X2 ──
        p_home = 0.0
        p_draw = 0.0
        p_away = 0.0
        for i in range(max_goals):
            for j in range(max_goals):
                if i > j:
                    p_home += score_matrix[i, j]
                elif i == j:
                    p_draw += score_matrix[i, j]
                else:
                    p_away += score_matrix[i, j]

        # ── Over/Under ──
        p_over_15 = sum(
            score_matrix[i, j]
            for i in range(max_goals)
            for j in range(max_goals)
            if i + j > 1
        )
        p_over_25 = sum(
            score_matrix[i, j]
            for i in range(max_goals)
            for j in range(max_goals)
            if i + j > 2
        )
        p_over_35 = sum(
            score_matrix[i, j]
            for i in range(max_goals)
            for j in range(max_goals)
            if i + j > 3
        )

        # ── BTTS ──
        p_btts = sum(
            score_matrix[i, j]
            for i in range(1, max_goals)
            for j in range(1, max_goals)
        )

        # ── Scores exacts (top 10) ──
        scores_flat = {}
        for i in range(max_goals):
            for j in range(max_goals):
                scores_flat[f"{i}-{j}"] = score_matrix[i, j]
        top_scores = dict(
            sorted(scores_flat.items(), key=lambda x: x[1], reverse=True)[:10]
        )

        prediction = FootballPrediction(
            home_team=home.name,
            away_team=away.name,
            lambda_home=lam_h,
            lambda_away=lam_a,
            p_home=p_home,
            p_draw=p_draw,
            p_away=p_away,
            p_over_15=p_over_15,
            p_over_25=p_over_25,
            p_over_35=p_over_35,
            p_btts=p_btts,
            exact_scores=top_scores,
        )

        log.info(
            f"  → P(1)={p_home:.3f} P(X)={p_draw:.3f} P(2)={p_away:.3f} "
            f"O2.5={p_over_25:.3f} BTTS={p_btts:.3f}"
        )
        return prediction

    # ─── Dixon-Coles Correction ──────────────────────────────────
    @staticmethod
    def _dixon_coles_correction(
        x: int, y: int, lam1: float, lam2: float, rho: float
    ) -> float:
        """
        Correction Dixon-Coles pour les scores faibles.
        Ajuste les probabilités de 0-0, 1-0, 0-1, 1-1.
        """
        if x == 0 and y == 0:
            return 1 - lam1 * lam2 * rho
        elif x == 1 and y == 0:
            return 1 + lam2 * rho
        elif x == 0 and y == 1:
            return 1 + lam1 * rho
        elif x == 1 and y == 1:
            return 1 - rho
        return 1.0

    # ─── Facteurs d'ajustement ───────────────────────────────────
    @staticmethod
    def _form_factor(recent_form: list[str]) -> float:
        """
        Calcule un facteur basé sur la forme récente.
        W=3, D=1, L=0. Pondération dégressive.
        """
        if not recent_form:
            return 1.0
        points_map = {"W": 3, "D": 1, "L": 0}
        weights = [1.0, 0.9, 0.8, 0.7, 0.6]  # Plus récent = plus de poids
        total_w = 0.0
        total_pts = 0.0
        for i, result in enumerate(recent_form[: len(weights)]):
            w = weights[i] if i < len(weights) else 0.5
            total_pts += points_map.get(result.upper(), 1) * w
            total_w += w * 3  # Max 3 pts par match

        ratio = total_pts / max(total_w, 1)
        # Transformer en facteur autour de 1.0
        # ratio=1 → bonne forme → 1.05 ; ratio=0 → mauvaise → 0.90
        return 0.90 + ratio * 0.15

    @staticmethod
    def _fatigue_factor(rest_days: int) -> float:
        """
        Facteur de fatigue basé sur les jours de repos.
        <3 jours : fatigue ; >7 : normal
        """
        if rest_days <= 2:
            return 0.92
        elif rest_days == 3:
            return 0.96
        elif rest_days <= 5:
            return 0.99
        return 1.0

    # ─── Calibration ─────────────────────────────────────────────
    def calibrate_rho(
        self,
        results: list[tuple[int, int, float, float]],
    ) -> float:
        """
        Calibre le paramètre rho de Dixon-Coles sur des données historiques.

        Args:
            results: Liste de (home_goals, away_goals, lambda_home, lambda_away)

        Returns:
            rho optimal
        """

        def neg_log_likelihood(rho_val):
            ll = 0.0
            r = rho_val[0]
            for hg, ag, lh, la in results:
                p = poisson.pmf(hg, lh) * poisson.pmf(ag, la)
                p *= self._dixon_coles_correction(hg, ag, lh, la, r)
                if p > 0:
                    ll += math.log(p)
            return -ll

        res = minimize(neg_log_likelihood, x0=[-0.1], bounds=[(-0.3, 0.1)])
        self.rho = res.x[0]
        log.info(f"Rho calibré: {self.rho:.4f}")
        return self.rho
