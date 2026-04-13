"""
betX – Modèle Tennis : ELO surface-spécifique + stats de service.

Ce modèle combine :
1. ELO global + ELO par surface (hard, clay, grass, indoor)
2. Stats de service et retour
3. Historique H2H pondéré
4. Forme récente

Outputs :
- P(victoire joueur A)
- P(handicap jeux)
- P(over/under jeux)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from betx.config import settings
from betx.logger import get_logger

log = get_logger("models.tennis")


# =============================================================================
# Structures
# =============================================================================
@dataclass
class TennisPlayerStats:
    """Statistiques d'un joueur de tennis."""
    name: str
    elo_global: float = 1500.0
    elo_hard: float = 1500.0
    elo_clay: float = 1500.0
    elo_grass: float = 1500.0
    elo_indoor: float = 1500.0
    # Stats de service (%)
    serve_win_pct: float = 0.62  # % de points gagnés au service
    return_win_pct: float = 0.38  # % de points gagnés au retour
    ace_per_game: float = 0.5
    double_fault_per_game: float = 0.2
    first_serve_pct: float = 0.63
    first_serve_win_pct: float = 0.72
    second_serve_win_pct: float = 0.52
    break_point_convert_pct: float = 0.40
    break_point_save_pct: float = 0.60
    # Forme récente
    recent_results: list[str] = field(default_factory=list)  # ["W","W","L","W","L"]
    # Fatigue
    matches_last_7_days: int = 0
    matches_last_14_days: int = 0

    def elo_for_surface(self, surface: str) -> float:
        """Retourne l'ELO pour une surface donnée."""
        surface_map = {
            "hard": self.elo_hard,
            "clay": self.elo_clay,
            "grass": self.elo_grass,
            "indoor": self.elo_indoor,
        }
        return surface_map.get(surface.lower(), self.elo_global)


@dataclass
class TennisPrediction:
    """Résultat de la prédiction tennis."""
    player_a: str
    player_b: str
    surface: str
    # Probabilité de victoire
    p_win_a: float
    p_win_b: float
    # Jeux
    expected_games_a: float
    expected_games_b: float
    expected_total_games: float
    # Over/Under jeux
    p_over_games: dict[str, float] = field(default_factory=dict)  # {"20.5": 0.65, ...}
    # Handicap
    projected_game_spread: float = 0.0  # >0 = A favori
    # Metadata
    elo_a: float = 0.0
    elo_b: float = 0.0
    model_version: str = "tennis_elo_service_v1"

    def to_dict(self) -> dict:
        return {
            "player_a": self.player_a,
            "player_b": self.player_b,
            "surface": self.surface,
            "p_win_a": round(self.p_win_a, 4),
            "p_win_b": round(self.p_win_b, 4),
            "expected_total_games": round(self.expected_total_games, 1),
            "projected_game_spread": round(self.projected_game_spread, 1),
            "p_over_games": {k: round(v, 4) for k, v in self.p_over_games.items()},
        }


# =============================================================================
# Modèle Tennis
# =============================================================================
class TennisModel:
    """
    Modèle probabiliste tennis basé sur ELO surface + stats de service.

    La clé du tennis est le % de points gagnés au service (SPW) et au retour (RPW).
    Un joueur gagne un match si SPW + RPW > 1.0 (en simplifié).
    """

    def __init__(self) -> None:
        self.cfg = settings.tennis
        self.base_elo_weight = self.cfg.global_weight
        self.surface_elo_weight = self.cfg.surface_weight

    def predict(
        self,
        player_a: TennisPlayerStats,
        player_b: TennisPlayerStats,
        surface: str = "hard",
        best_of: int = 3,  # 3 sets (ATP) ou 5 sets (Grand Slam)
        h2h_record: tuple[int, int] | None = None,  # (wins_a, wins_b)
    ) -> TennisPrediction:
        """
        Prédit le résultat d'un match de tennis.

        Args:
            player_a: Stats joueur A
            player_b: Stats joueur B
            surface: Surface du match
            best_of: 3 ou 5 sets
            h2h_record: Historique confrontations (wins_a, wins_b)
        """
        # ── 1. Probabilité ELO ──
        elo_a = self._combined_elo(player_a, surface)
        elo_b = self._combined_elo(player_b, surface)

        p_elo_a = 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400.0))

        # ── 2. Probabilité basée sur les stats de service ──
        p_service = self._service_based_probability(player_a, player_b)

        # ── 3. Combinaison ELO + Service stats ──
        # On pondère 60% ELO, 40% stats de service
        p_combined = 0.60 * p_elo_a + 0.40 * p_service

        # ── 4. Ajustement H2H ──
        if h2h_record and sum(h2h_record) >= 3:
            total_h2h = sum(h2h_record)
            h2h_pct = h2h_record[0] / total_h2h
            # Léger ajustement (max ±3%)
            h2h_weight = min(0.10, total_h2h / 50)
            p_combined = (1 - h2h_weight) * p_combined + h2h_weight * h2h_pct

        # ── 5. Ajustement forme récente ──
        form_a = self._form_factor(player_a.recent_results)
        form_b = self._form_factor(player_b.recent_results)
        form_ratio = form_a / max(form_b, 0.5)
        p_combined *= form_ratio
        p_combined = max(0.05, min(0.95, p_combined))

        # ── 6. Ajustement fatigue ──
        fatigue_a = self._fatigue_factor(player_a)
        fatigue_b = self._fatigue_factor(player_b)
        p_combined *= fatigue_a / max(fatigue_b, 0.5)
        p_combined = max(0.05, min(0.95, p_combined))

        # ── 7. Conversion en probabilité de match (sets) ──
        p_match_a = self._set_probability(p_combined, best_of)
        p_match_b = 1 - p_match_a

        # ── 8. Estimation nombre de jeux ──
        games_a, games_b = self._estimate_games(player_a, player_b, p_match_a, best_of)
        total_games = games_a + games_b

        # ── 9. Over/Under jeux ──
        p_over_games = self._over_under_games(total_games, best_of)

        # ── 10. Spread jeux ──
        projected_spread = games_a - games_b

        prediction = TennisPrediction(
            player_a=player_a.name,
            player_b=player_b.name,
            surface=surface,
            p_win_a=p_match_a,
            p_win_b=p_match_b,
            expected_games_a=games_a,
            expected_games_b=games_b,
            expected_total_games=total_games,
            p_over_games=p_over_games,
            projected_game_spread=projected_spread,
            elo_a=elo_a,
            elo_b=elo_b,
        )

        log.info(
            f"{player_a.name} vs {player_b.name} ({surface}): "
            f"P(A)={p_match_a:.3f} P(B)={p_match_b:.3f} "
            f"Games={total_games:.1f}"
        )
        return prediction

    # ─── Composants du modèle ────────────────────────────────────

    def _combined_elo(self, player: TennisPlayerStats, surface: str) -> float:
        """ELO combiné global + surface."""
        surface_elo = player.elo_for_surface(surface)
        return (
            self.base_elo_weight * player.elo_global
            + self.surface_elo_weight * surface_elo
        )

    @staticmethod
    def _service_based_probability(
        player_a: TennisPlayerStats,
        player_b: TennisPlayerStats,
    ) -> float:
        """
        Probabilité basée sur les stats de service.

        Formule simplifiée : la probabilité qu'un joueur gagne un point
        au service est une fonction de son SPW et du RPW de l'adversaire.
        """
        # Point gagné au service de A = f(serve_A, return_B)
        spw_a = (player_a.serve_win_pct + (1 - player_b.return_win_pct)) / 2
        spw_b = (player_b.serve_win_pct + (1 - player_a.return_win_pct)) / 2

        # Probabilité de hold pour chaque joueur
        p_hold_a = _game_win_prob(spw_a)
        p_hold_b = _game_win_prob(spw_b)

        # Probabilité de break
        p_break_a = 1 - p_hold_b  # A breake B
        p_break_b = 1 - p_hold_a  # B breake A

        # Si A hold mieux et breake plus → favori
        # Score simplifié : ratio de dominance
        a_strength = p_hold_a + p_break_a
        b_strength = p_hold_b + p_break_b

        p_a = a_strength / (a_strength + b_strength)
        return max(0.1, min(0.9, p_a))

    @staticmethod
    def _set_probability(p_point: float, best_of: int) -> float:
        """
        Convertit une probabilité de point/jeu en probabilité de match.

        Utilise une approximation basée sur le fait qu'un avantage subtil
        par point s'amplifie sur un set, puis sur un match.
        """
        # Plus le match est long, plus le favori a un avantage
        # Approximation exponentielle
        sets_to_win = (best_of + 1) // 2
        if sets_to_win == 2:  # Best of 3
            # Amplification modérée
            return p_point ** 1.3 / (p_point ** 1.3 + (1 - p_point) ** 1.3)
        else:  # Best of 5
            # Plus grande amplification
            return p_point ** 1.7 / (p_point ** 1.7 + (1 - p_point) ** 1.7)

    def _estimate_games(
        self,
        player_a: TennisPlayerStats,
        player_b: TennisPlayerStats,
        p_win_a: float,
        best_of: int,
    ) -> tuple[float, float]:
        """Estime le nombre de jeux pour chaque joueur."""
        sets_to_win = (best_of + 1) // 2

        # Estimation du nombre de sets
        # Si p_win_a élevé → match plus court
        if p_win_a > 0.70:
            expected_sets = sets_to_win + 0.3
        elif p_win_a > 0.55:
            expected_sets = sets_to_win + 0.8
        else:
            expected_sets = sets_to_win + 1.2
        expected_sets = min(expected_sets, best_of)

        # Jeux par set (typiquement ~10 pour un set serré, ~8 pour un set dominé)
        avg_games_per_set = 9.5 if abs(p_win_a - 0.5) < 0.15 else 8.5

        total_games = expected_sets * avg_games_per_set

        # Répartition
        games_a = total_games * (0.5 + (p_win_a - 0.5) * 0.3)
        games_b = total_games - games_a

        return round(games_a, 1), round(games_b, 1)

    @staticmethod
    def _over_under_games(expected_total: float, best_of: int) -> dict[str, float]:
        """Probabilités over/under pour différents seuils de jeux."""
        # Utilise une distribution normale centrée sur expected_total
        std_dev = 4.0 if best_of == 3 else 6.0
        from scipy.stats import norm

        thresholds = {
            3: [19.5, 20.5, 21.5, 22.5, 23.5],
            5: [33.5, 35.5, 37.5, 39.5, 41.5],
        }

        result = {}
        for t in thresholds.get(best_of, thresholds[3]):
            p_over = 1 - norm.cdf(t, loc=expected_total, scale=std_dev)
            result[str(t)] = max(0.05, min(0.95, p_over))

        return result

    @staticmethod
    def _form_factor(results: list[str]) -> float:
        """Facteur de forme (identique au football mais adapté)."""
        if not results:
            return 1.0
        pts = {"W": 1.0, "L": 0.0}
        weights = [1.0, 0.85, 0.70, 0.55, 0.40]
        total_w = 0.0
        total_pts = 0.0
        for i, r in enumerate(results[:5]):
            w = weights[i] if i < len(weights) else 0.3
            total_pts += pts.get(r.upper(), 0.5) * w
            total_w += w
        ratio = total_pts / max(total_w, 0.1)
        return 0.95 + ratio * 0.10

    @staticmethod
    def _fatigue_factor(player: TennisPlayerStats) -> float:
        """Facteur de fatigue basé sur le nombre de matchs récents."""
        if player.matches_last_7_days >= 4:
            return 0.92
        elif player.matches_last_7_days >= 3:
            return 0.96
        elif player.matches_last_14_days >= 6:
            return 0.97
        return 1.0

    # ─── ELO Update ──────────────────────────────────────────────
    def update_elo(
        self,
        winner: TennisPlayerStats,
        loser: TennisPlayerStats,
        surface: str,
        sets_winner: int = 2,
        sets_loser: int = 0,
    ) -> tuple[float, float]:
        """Met à jour les ELO après un match."""
        k = self.cfg.elo_k_factor

        # Dominance factor
        dominance = 1.0 + (sets_winner - sets_loser) * 0.1

        # ELO surface
        elo_w = winner.elo_for_surface(surface)
        elo_l = loser.elo_for_surface(surface)

        expected_w = 1.0 / (1.0 + 10 ** ((elo_l - elo_w) / 400.0))

        delta = k * dominance * (1 - expected_w)

        new_w = elo_w + delta
        new_l = elo_l - delta

        # Mise à jour des attributs
        surface_attr = f"elo_{surface.lower()}"
        if hasattr(winner, surface_attr):
            setattr(winner, surface_attr, new_w)
        if hasattr(loser, surface_attr):
            setattr(loser, surface_attr, new_l)

        # ELO global
        winner.elo_global += delta * 0.5
        loser.elo_global -= delta * 0.5

        return new_w, new_l


# =============================================================================
# Utilitaires
# =============================================================================
def _game_win_prob(p_point: float) -> float:
    """
    Probabilité de gagner un jeu étant donné p(point gagné au service).

    Formule exacte basée sur les règles du tennis (deuce model).
    """
    p = p_point
    q = 1 - p

    # Probabilité d'atteindre 40-40 (deuce)
    p_deuce = 20 * (p ** 3) * (q ** 3)

    # Probabilité de gagner depuis deuce
    p_win_from_deuce = p ** 2 / (p ** 2 + q ** 2) if (p ** 2 + q ** 2) > 0 else 0.5

    # Probabilité de gagner le jeu sans deuce
    p_win_no_deuce = (
        p ** 4  # 40-0
        + 4 * p ** 4 * q  # 40-15
        + 10 * p ** 4 * q ** 2  # 40-30
    )

    return p_win_no_deuce + p_deuce * p_win_from_deuce
