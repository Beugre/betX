"""
betX – Feature engineering et prédiction pour matchs équipes nationales.

Architecture :
  NationalTeamProfile (historique brut)
        ↓
  NationalTeamFeatureSet (vecteur complet de features pondérées)
        ↓
  NationalMatchPredictor  →  Poisson calibré  →  Monte Carlo 10 000 sims
        ↓
  ScoreProbabilities
    P(score exact), P(1N2), P(over/under), P(BTTS), top 5 scores

Pondération appliquée partout :
  w = exp(-0.07 * rank) × match_type_weight
  rank=0 = plus récent, rank=N = le plus ancien.
  Résultats en compétition officielle > amicaux.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.stats import poisson

from betx.logger import get_logger

log = get_logger("data.national_features")

# Moyenne internationale de buts par équipe par match (2022-2024)
INTL_AVG_GOALS_PER_TEAM: float = 1.20   # ~2.40 total par match
INTL_AVG_HOME: float = 1.25             # légère tendance "home" même en CdM
INTL_AVG_AWAY: float = 1.15


# ─── Feature Set ───────────────────────────────────────────────────────────────

@dataclass
class NationalTeamFeatureSet:
    """
    Vecteur de features complet pour un match entre deux équipes nationales.

    Toutes les valeurs numériques sont déjà pondérées (récence × compétition).
    Peut être sérialisé en dict pour un futur pipeline ML (XGBoost/LightGBM).
    """

    # Identifiants
    home_team: str
    away_team: str

    # ── ELO ──────────────────────────────────────────────────────────────
    home_elo: float = 1500.0
    away_elo: float = 1500.0

    @property
    def elo_diff(self) -> float:
        return self.home_elo - self.away_elo

    # ── Forme pondérée ────────────────────────────────────────────────────
    home_form_5: float = 0.0          # -1 à +1
    away_form_5: float = 0.0
    home_form_10: float = 0.0
    away_form_10: float = 0.0
    home_official_form: float = 0.0   # compétitions officielles seulement
    away_official_form: float = 0.0
    home_friendly_form: float = 0.0   # amicaux seulement
    away_friendly_form: float = 0.0

    @property
    def form_diff_5(self) -> float:
        return self.home_form_5 - self.away_form_5

    @property
    def form_diff_10(self) -> float:
        return self.home_form_10 - self.away_form_10

    # ── Buts pondérés ─────────────────────────────────────────────────────
    home_goals_for_10: float = INTL_AVG_GOALS_PER_TEAM
    away_goals_for_10: float = INTL_AVG_GOALS_PER_TEAM
    home_goals_against_10: float = INTL_AVG_GOALS_PER_TEAM
    away_goals_against_10: float = INTL_AVG_GOALS_PER_TEAM
    home_goals_for_5: float = INTL_AVG_GOALS_PER_TEAM
    away_goals_for_5: float = INTL_AVG_GOALS_PER_TEAM

    # ── H2H ───────────────────────────────────────────────────────────────
    h2h_count: int = 0
    h2h_home_win_rate: float = 0.33
    h2h_draw_rate: float = 0.33
    h2h_away_win_rate: float = 0.33
    h2h_avg_goals_home: float = INTL_AVG_GOALS_PER_TEAM
    h2h_avg_goals_away: float = INTL_AVG_GOALS_PER_TEAM
    h2h_bias: float = 0.0   # positif = home team historiquement dominante

    # ── Contexte ──────────────────────────────────────────────────────────
    neutral_ground: bool = True         # toujours True en CdM
    match_importance: float = 1.8       # World Cup = 1.8

    # ── Nombre de matchs dans la base ─────────────────────────────────────
    home_sample_size: int = 0
    away_sample_size: int = 0

    def to_dict(self) -> dict:
        """Sérialise en dict pour logging / futur pipeline ML."""
        return {
            "home_team": self.home_team,
            "away_team": self.away_team,
            "home_elo": round(self.home_elo, 1),
            "away_elo": round(self.away_elo, 1),
            "elo_diff": round(self.elo_diff, 1),
            "home_form_5": round(self.home_form_5, 3),
            "away_form_5": round(self.away_form_5, 3),
            "home_form_10": round(self.home_form_10, 3),
            "away_form_10": round(self.away_form_10, 3),
            "form_diff_5": round(self.form_diff_5, 3),
            "form_diff_10": round(self.form_diff_10, 3),
            "home_official_form": round(self.home_official_form, 3),
            "away_official_form": round(self.away_official_form, 3),
            "home_goals_for_10": round(self.home_goals_for_10, 3),
            "away_goals_for_10": round(self.away_goals_for_10, 3),
            "home_goals_against_10": round(self.home_goals_against_10, 3),
            "away_goals_against_10": round(self.away_goals_against_10, 3),
            "h2h_count": self.h2h_count,
            "h2h_home_win_rate": round(self.h2h_home_win_rate, 3),
            "h2h_draw_rate": round(self.h2h_draw_rate, 3),
            "h2h_away_win_rate": round(self.h2h_away_win_rate, 3),
            "h2h_avg_goals_home": round(self.h2h_avg_goals_home, 3),
            "h2h_avg_goals_away": round(self.h2h_avg_goals_away, 3),
            "h2h_bias": round(self.h2h_bias, 3),
            "neutral_ground": self.neutral_ground,
            "home_sample_size": self.home_sample_size,
            "away_sample_size": self.away_sample_size,
        }


# ─── Score Probabilities ───────────────────────────────────────────────────────

@dataclass
class ScoreProbabilities:
    """
    Distribution complète des probabilités de score pour un match.
    Produit final du prédicteur.
    """
    home_team: str
    away_team: str
    lambda_home: float
    lambda_away: float

    # Distribution des scores exacts (clé = "H-A")
    exact_scores: dict[str, float] = field(default_factory=dict)

    # 1N2
    p_home_win: float = 0.0
    p_draw: float = 0.0
    p_away_win: float = 0.0

    # Over/Under
    p_over_15: float = 0.0
    p_over_25: float = 0.0
    p_over_35: float = 0.0
    p_under_15: float = 0.0
    p_under_25: float = 0.0

    # BTTS (Both Teams To Score)
    p_btts: float = 0.0
    p_btts_no: float = 0.0

    @property
    def top_scores(self) -> list[tuple[str, float]]:
        """Top 5 scores les plus probables."""
        return sorted(self.exact_scores.items(), key=lambda x: x[1], reverse=True)[:5]

    @property
    def most_likely_score(self) -> str:
        """Score le plus probable."""
        if not self.exact_scores:
            return "1-0"
        return max(self.exact_scores, key=self.exact_scores.get)  # type: ignore

    def display(self) -> str:
        """Résumé lisible pour la console."""
        icons = {0: "🥇", 1: "🥈", 2: "🥉", 3: "4️⃣", 4: "5️⃣"}
        lines = [
            f"{'─' * 50}",
            f"  {self.home_team} vs {self.away_team}",
            f"  λ home={self.lambda_home:.2f}  λ away={self.lambda_away:.2f}",
            "",
            "  🎯 Top 5 scores :",
        ]
        for i, (score, prob) in enumerate(self.top_scores):
            lines.append(f"    {icons.get(i, '  ')} {score} : {prob * 100:.1f}%")
        lines += [
            "",
            "  📊 Probabilités :",
            f"    {self.home_team} gagne : {self.p_home_win:.0%}",
            f"    Nul                   : {self.p_draw:.0%}",
            f"    {self.away_team} gagne : {self.p_away_win:.0%}",
            "",
            f"    Over 1.5 : {self.p_over_15:.0%}  │  Over 2.5 : {self.p_over_25:.0%}  │  Over 3.5 : {self.p_over_35:.0%}",
            f"    BTTS     : {self.p_btts:.0%}",
            f"{'─' * 50}",
        ]
        return "\n".join(lines)


# ─── Feature Builder ───────────────────────────────────────────────────────────

def _fifa_elo(team_name: str) -> float | None:
    """
    Retourne l'ELO basé sur le classement FIFA 2026, ou None si inconnu.
    Utilisé comme régularisateur pour éviter les sur-gonflages liés
    à des victoires vs adversaires faibles (qualifs africaines, etc.).
    """
    try:
        from predict_wc_groups import fifa_elo, FIFA_RANKING_2026
        key = team_name.lower().strip()
        if any(k in key or key in k for k in [k2.lower() for k2 in FIFA_RANKING_2026]):
            return fifa_elo(team_name)
    except Exception:
        pass
    return None


def _fifa_expected_lambda(team_name: str) -> tuple[float, float] | None:
    """
    λ attendu (atk, def) basé sur le classement FIFA.
    Ancre les λ calculés pour les équipes dont les données
    viennent majoritairement de confrontations déséquilibrées.

    Modèle linéaire calibré sur les CdM 2018-2022 :
      Rang 1  → atk=1.65, def=0.80
      Rang 25 → atk=1.20, def=1.20 (moyenne internationale)
      Rang 50 → atk=0.90, def=1.50
    """
    try:
        from predict_wc_groups import FIFA_RANKING_2026
        key = team_name.lower().strip()
        rank = None
        for k, v in FIFA_RANKING_2026.items():
            if k.lower() in key or key in k.lower():
                rank = v
                break
        if rank is None:
            return None
        n = max(1, min(rank, 50))
        lam_atk = round(1.65 - (n - 1) * (0.75 / 49), 3)
        lam_def = round(0.80 + (n - 1) * (0.70 / 49), 3)
        return lam_atk, lam_def
    except Exception:
        return None


def build_features(
    home_profile: "NationalTeamProfile",
    away_profile: "NationalTeamProfile",
    neutral: bool = True,
    match_importance: float = 1.8,
) -> NationalTeamFeatureSet:
    """
    Construit le vecteur de features complet depuis deux profils.

    ELO : blend 40% FIFA ranking + 60% ELO calculé (résultats).
    Le FIFA ranking ancre l'ELO calculé pour éviter les sur-gonflages
    liés à des victoires contre des adversaires structurellement faibles
    (qualifs africaines vs Rwanda/Lesotho, etc.).
    """
    h = home_profile
    a = away_profile
    h2h_stats = h.h2h_stats()

    # ── Étape 1 : ELO réel (eloratings.net) ──────────────────────────────
    def _best_elo(profile: "NationalTeamProfile") -> float:
        """
        Priorité :
          1. ELO officiel eloratings.net (source la plus fiable)
          2. Blend 60% FIFA + 40% calculé (fallback si pas dans eloratings)
        """
        from betx.data.elo_loader import get_elo as _get_elo
        official = _get_elo(profile.team_name)
        if official is not None:
            # Blend léger avec l'ELO calculé pour ne pas ignorer la forme récente
            computed = profile.elo_estimate
            return round(0.80 * official + 0.20 * computed, 1)
        # Fallback FIFA
        computed = profile.elo_estimate
        fifa = _fifa_elo(profile.team_name)
        if fifa is not None:
            return round(0.60 * fifa + 0.40 * computed, 1)
        return computed

    # ── Étape 2 : λ pondérés par force adversaire ─────────────────────────
    def _opponent_weighted_lambda(profile: "NationalTeamProfile", is_atk: bool) -> float:
        """
        Calcule les λ en pondérant chaque but par la force de l'adversaire.

        w_adversaire = (ELO_adv / 1750)^0.5
        → Victoire 3-0 vs Zimbabwe (ELO 1400) : poids 0.89
        → Victoire 1-0 vs Argentina (ELO 2100) : poids 1.10

        Empêche les λ gonflés par des victoires faciles en AFCON/qualifs.
        """
        from betx.data.elo_loader import get_elo as _get_elo
        from betx.data.national_team_collector import MATCH_TYPE_WEIGHTS
        REF_ELO = 1750.0

        # Utiliser les 20 matchs les plus récents parmi les 30 disponibles.
        # La pondération exp(-0.07 * rank) atténue naturellement les anciens
        # (rank=19 → poids ×0.26 vs rank=0 → 1.0) : pas de biais d'arbitraire.
        matches = profile.recent_matches[:20]
        if not matches:
            return 1.20

        total_w = 0.0
        total_goals = 0.0
        for rank, m in enumerate(matches):
            # Poids récence × compétition (déjà existant)
            # Poids récence × compétition (sans date — force adversaire est le correctif)
            w_comp = profile._composite_weight(rank, m.competition_id)
            # Poids force adversaire
            opp_name = m.opponent
            opp_elo = _get_elo(opp_name)
            if opp_elo is None:
                # Adversaire inconnu = probablement une petite équipe régionale
                # 1500 plutôt que 1750 pour éviter de surestimer leur impact
                opp_elo = 1500.0
            w_opp = (opp_elo / REF_ELO) ** 0.7
            # CdM = terrain neutre : décote légère des matchs joués à domicile
            # (l'avantage domicile historique ne s'applique pas en sol américain)
            w_location = 0.85 if m.is_home else 1.0
            w_total = w_comp * w_opp * w_location

            goals = m.goals_scored if is_atk else m.goals_conceded
            total_goals += goals * w_total
            total_w += w_total

        raw = total_goals / total_w if total_w > 0 else 1.20

        # Blend 65% FIFA attendu + 35% données pondérées.
        # CdM 2026 J1-J2 confirme des matchs plus fermés — FIFA ancre mieux.
        fifa_vals = _fifa_expected_lambda(profile.team_name)
        if fifa_vals is not None:
            fifa_val = fifa_vals[0] if is_atk else fifa_vals[1]
            return round(0.65 * fifa_val + 0.35 * raw, 3)
        return round(raw, 3)

    h_elo = _best_elo(h)
    a_elo = _best_elo(a)

    feats = NationalTeamFeatureSet(
        home_team=h.team_name,
        away_team=a.team_name,

        # ELO (blended FIFA + calculé)
        home_elo=h_elo,
        away_elo=a_elo,

        # Forme (fenêtres 5 et 10)
        home_form_5=h.form_score(n=5),
        away_form_5=a.form_score(n=5),
        home_form_10=h.form_score(n=10),
        away_form_10=a.form_score(n=10),
        home_official_form=h.form_score(n=10, official_only=True),
        away_official_form=a.form_score(n=10, official_only=True),
        home_friendly_form=_friendly_form(h),
        away_friendly_form=_friendly_form(a),

        # Buts pondérés par force adversaire (étapes 1+2 combinées)
        home_goals_for_10=_opponent_weighted_lambda(h, is_atk=True),
        away_goals_for_10=_opponent_weighted_lambda(a, is_atk=True),
        home_goals_against_10=_opponent_weighted_lambda(h, is_atk=False),
        away_goals_against_10=_opponent_weighted_lambda(a, is_atk=False),
        home_goals_for_5=_opponent_weighted_lambda(h, is_atk=True),
        away_goals_for_5=_opponent_weighted_lambda(a, is_atk=True),

        # H2H
        h2h_count=h2h_stats["count"],
        h2h_home_win_rate=h2h_stats["win_rate"],
        h2h_draw_rate=h2h_stats["draw_rate"],
        h2h_away_win_rate=h2h_stats["loss_rate"],
        h2h_avg_goals_home=h2h_stats["avg_scored"],
        h2h_avg_goals_away=h2h_stats["avg_conceded"],
        h2h_bias=h2h_stats["bias"],

        # Contexte
        neutral_ground=neutral,
        match_importance=match_importance,

        # Taille de l'échantillon
        home_sample_size=len(h.recent_matches),
        away_sample_size=len(a.recent_matches),
    )
    return feats


def _friendly_form(profile: "NationalTeamProfile") -> float:
    """Forme sur amicaux uniquement (10 derniers amicaux pondérés)."""
    from betx.data.national_team_collector import MATCH_TYPE_WEIGHTS
    FRIENDLY_ID = 10
    friendlies = [m for m in profile.recent_matches if m.competition_id == FRIENDLY_ID][:10]
    if not friendlies:
        return 0.0
    result_map = {"W": 1.0, "D": 0.0, "L": -1.0}
    total_w = total_pts = 0.0
    for rank, m in enumerate(friendlies):
        w = math.exp(-0.07 * rank) * MATCH_TYPE_WEIGHTS[FRIENDLY_ID]
        total_pts += result_map[m.result] * w
        total_w += w
    return total_pts / total_w if total_w > 0 else 0.0


# ─── Prédicteur (Poisson calibré + Monte Carlo) ────────────────────────────────

class NationalMatchPredictor:
    """
    Prédicteur de score pour matchs d'équipes nationales.

    Niveau 1 : calcul des lambdas calibrés (attack × defense × ELO × forme × H2H)
    Niveau 2 : distribution analytique Poisson + correction Dixon-Coles
    Niveau 3 : Monte Carlo 10 000 simulations pour les probabilités
    """

    MAX_GOALS: int = 7              # Grille 0..6 (analytique)
    N_SIMULATIONS: int = 10_000     # Simulations Monte Carlo

    RHO: float = -0.18              # Paramètre Dixon-Coles — recalibré CdM 2026 (45% nuls vs 25% historique)

    # Seuil de confiance selon la taille de l'échantillon
    MIN_SAMPLE_CONFIDENT: int = 10

    def compute_lambdas(self, feats: NationalTeamFeatureSet) -> tuple[float, float]:
        """
        Calcule λ_home et λ_away depuis le vecteur de features.

        Formule Dixon-Coles internationale :
          λ_home = (home_attack / intl_avg) × (away_defense / intl_avg) × intl_avg
          λ_away = (away_attack / intl_avg) × (home_defense / intl_avg) × intl_avg

        Ajustements : ELO diff, forme relative, H2H, terrain neutre.
        """
        avg = INTL_AVG_GOALS_PER_TEAM

        # ── Ratios attaque/défense ──
        # Combiner buts sur 5 et 10 matchs (5 plus récent = poids +50%)
        home_att = (feats.home_goals_for_10 * 1.0 + feats.home_goals_for_5 * 0.5) / 1.5
        away_att = (feats.away_goals_for_10 * 1.0 + feats.away_goals_for_5 * 0.5) / 1.5
        home_def = feats.home_goals_against_10
        away_def = feats.away_goals_against_10

        # Ratios normalisés par rapport à la moyenne internationale
        r_home_att = home_att / avg
        r_away_att = away_att / avg
        r_home_def = home_def / avg   # > 1 = défense poreuse
        r_away_def = away_def / avg

        # Lambdas de base (modèle Dixon-Coles)
        lambda_home = r_home_att * r_away_def * avg
        lambda_away = r_away_att * r_home_def * avg

        # ── Ajustement ELO (modèle Elo-Bradley-Terry) ──
        elo_diff = feats.elo_diff
        # P(home win) selon ELO pur : formule Bradley-Terry
        # Calibrée sur CdM 2022 : ΔELO=200 → ~60%, ΔELO=400 → ~78%
        elo_p_home = 1 / (1 + 10 ** (-elo_diff / 400.0))
        elo_p_away = 1 - elo_p_home

        # Ratio λ implicite dans la proba ELO
        # Si P(home)=0.78, λ_home/λ_away ≈ 2.0 (calibré Poisson)
        # Facteur = sqrt(elo_p_home / elo_p_away) pour être moins agressif
        import math
        elo_ratio = math.sqrt(max(0.1, elo_p_home / max(0.01, elo_p_away)))

        # Ajustement : multiplier home par elo_ratio, diviser away
        # Poids 0.60 sur ELO (fort signal en phase de poules CdM)
        # ELO_WEIGHT réduit de 0.60 à 0.50 : CdM 2026 montre plusieurs surprises
        # indiquant que la dynamique récente compte autant que le classement ELO
        ELO_WEIGHT = 0.50
        elo_factor = elo_ratio ** ELO_WEIGHT
        lambda_home *= elo_factor
        lambda_away /= elo_factor

        # ── Ajustement forme relative ──
        # Différence de forme (officielle si assez de données)
        form_diff = (
            feats.home_official_form - feats.away_official_form
            if feats.home_sample_size >= self.MIN_SAMPLE_CONFIDENT
            and feats.away_sample_size >= self.MIN_SAMPLE_CONFIDENT
            else feats.form_diff_5
        )
        lambda_home *= (1.0 + form_diff * 0.08)
        lambda_away *= (1.0 - form_diff * 0.08)

        # ── Ajustement H2H ──
        # Impact modéré : le passé guide, mais ne détermine pas
        if feats.h2h_count >= 3:
            lambda_home *= (1.0 + feats.h2h_bias * 0.08)
            lambda_away *= (1.0 - feats.h2h_bias * 0.08)

        # ── Terrain neutre : pas d'avantage domicile ──
        if feats.neutral_ground:
            # Légère régression vers la moyenne des deux (terrain neutre absorbe l'HA)
            lam_mid = (lambda_home + lambda_away) / 2
            lambda_home = lambda_home * 0.90 + lam_mid * 0.10
            lambda_away = lambda_away * 0.90 + lam_mid * 0.10

        # ── Régression vers la moyenne (shrinkage) ──
        # Actif seulement si l'on a de vraies données historiques (sample>0)
        # mais en petit nombre. Le fallback FIFA (sample=0) n'est PAS shrinkagé
        # car ses λ proviennent déjà d'un classement calibré.
        total_sample = feats.home_sample_size + feats.away_sample_size
        if total_sample > 0:
            # Shrinkage renforcé : CdM 2026 montre des matchs fermés (moy ~2.4 buts)
            # On cible 60 matchs combinés pour confiance totale (était 40)
            confidence = min(1.0, total_sample / 60)
            lambda_home = lambda_home * confidence + avg * (1 - confidence)
            lambda_away = lambda_away * confidence + avg * (1 - confidence)
        # else : fallback FIFA pur → pas de shrinkage, les λ reflètent le ranking

        # Caps — plafond réduit à 2.50 par équipe (CdM 2026 : matchs fermés)
        lambda_home = max(0.30, min(2.50, lambda_home))
        lambda_away = max(0.30, min(2.50, lambda_away))

        # WC_SHRINK dynamique selon l'écart ELO :
        #   match équilibré (ΔELO <50)  → 0.85 (plus défensif)
        #   match moyen (ΔELO 50-150)  → 0.90
        #   grand favori (ΔELO >150)   → 0.95 (le favori peut scorer librement)
        elo_diff_abs = abs(feats.elo_diff)
        WC_SHRINK = 0.85 if elo_diff_abs < 50 else (0.90 if elo_diff_abs < 150 else 0.95)
        lambda_home = round(lambda_home * WC_SHRINK, 3)
        lambda_away = round(lambda_away * WC_SHRINK, 3)

        log.debug(
            f"λ {feats.home_team}={lambda_home:.3f} "
            f"{feats.away_team}={lambda_away:.3f} "
            f"(elo_diff={elo_diff:+.0f}, form_diff={form_diff:+.3f}, "
            f"h2h={feats.h2h_bias:+.2f})"
        )
        return lambda_home, lambda_away

    def _dixon_coles(self, x: int, y: int, lh: float, la: float) -> float:
        """Correction Dixon-Coles pour les scores faibles (0-0, 1-0, 0-1, 1-1)."""
        rho = self.RHO
        if x == 0 and y == 0:
            return 1.0 - lh * la * rho
        elif x == 1 and y == 0:
            return 1.0 + la * rho
        elif x == 0 and y == 1:
            return 1.0 + lh * rho
        elif x == 1 and y == 1:
            return 1.0 - rho
        return 1.0

    def predict_analytical(
        self, lambda_home: float, lambda_away: float,
        home_team: str = "", away_team: str = ""
    ) -> ScoreProbabilities:
        """
        Prédiction analytique via grille de Poisson + Dixon-Coles.
        Rapide (< 1ms), précis pour les scores faibles.
        """
        max_g = self.MAX_GOALS
        matrix = np.zeros((max_g, max_g))

        for i in range(max_g):
            for j in range(max_g):
                p = poisson.pmf(i, lambda_home) * poisson.pmf(j, lambda_away)
                p *= self._dixon_coles(i, j, lambda_home, lambda_away)
                matrix[i, j] = max(0.0, p)

        matrix /= matrix.sum()

        # Scores exacts
        exact: dict[str, float] = {
            f"{i}-{j}": float(matrix[i, j])
            for i in range(max_g)
            for j in range(max_g)
        }

        # 1N2
        p_home = float(np.sum([matrix[i, j] for i in range(max_g) for j in range(max_g) if i > j]))
        p_draw = float(np.sum([matrix[i, i] for i in range(max_g)]))
        p_away = 1.0 - p_home - p_draw

        # Over/Under
        p_o15 = float(np.sum([matrix[i, j] for i in range(max_g) for j in range(max_g) if i + j > 1]))
        p_o25 = float(np.sum([matrix[i, j] for i in range(max_g) for j in range(max_g) if i + j > 2]))
        p_o35 = float(np.sum([matrix[i, j] for i in range(max_g) for j in range(max_g) if i + j > 3]))

        # BTTS
        p_btts = float(np.sum([matrix[i, j] for i in range(1, max_g) for j in range(1, max_g)]))

        return ScoreProbabilities(
            home_team=home_team,
            away_team=away_team,
            lambda_home=round(lambda_home, 3),
            lambda_away=round(lambda_away, 3),
            exact_scores=exact,
            p_home_win=round(p_home, 4),
            p_draw=round(p_draw, 4),
            p_away_win=round(p_away, 4),
            p_over_15=round(p_o15, 4),
            p_over_25=round(p_o25, 4),
            p_over_35=round(p_o35, 4),
            p_under_15=round(1 - p_o15, 4),
            p_under_25=round(1 - p_o25, 4),
            p_btts=round(p_btts, 4),
            p_btts_no=round(1 - p_btts, 4),
        )

    def predict_monte_carlo(
        self, lambda_home: float, lambda_away: float,
        home_team: str = "", away_team: str = ""
    ) -> ScoreProbabilities:
        """
        Prédiction Monte Carlo : 10 000 simulations Poisson.

        Plus robuste pour les distributions atypiques.
        Résultats cohérents avec la version analytique (± 0.5%).
        """
        rng = np.random.default_rng(seed=42)
        h_goals = rng.poisson(lambda_home, self.N_SIMULATIONS)
        a_goals = rng.poisson(lambda_away, self.N_SIMULATIONS)

        # Construire la distribution empirique des scores
        score_counts: dict[str, int] = {}
        for h, a in zip(h_goals, a_goals):
            key = f"{h}-{a}"
            score_counts[key] = score_counts.get(key, 0) + 1

        n = self.N_SIMULATIONS
        exact = {k: v / n for k, v in score_counts.items()}

        # 1N2
        p_home = sum(v for k, v in exact.items()
                     if int(k.split("-")[0]) > int(k.split("-")[1]))
        p_draw = sum(v for k, v in exact.items()
                     if int(k.split("-")[0]) == int(k.split("-")[1]))
        p_away = 1.0 - p_home - p_draw

        # Over/Under
        p_o15 = sum(v for k, v in exact.items()
                    if int(k.split("-")[0]) + int(k.split("-")[1]) > 1)
        p_o25 = sum(v for k, v in exact.items()
                    if int(k.split("-")[0]) + int(k.split("-")[1]) > 2)
        p_o35 = sum(v for k, v in exact.items()
                    if int(k.split("-")[0]) + int(k.split("-")[1]) > 3)

        # BTTS
        p_btts = sum(v for k, v in exact.items()
                     if int(k.split("-")[0]) > 0 and int(k.split("-")[1]) > 0)

        return ScoreProbabilities(
            home_team=home_team,
            away_team=away_team,
            lambda_home=round(lambda_home, 3),
            lambda_away=round(lambda_away, 3),
            exact_scores=exact,
            p_home_win=round(p_home, 4),
            p_draw=round(p_draw, 4),
            p_away_win=round(p_away, 4),
            p_over_15=round(p_o15, 4),
            p_over_25=round(p_o25, 4),
            p_over_35=round(p_o35, 4),
            p_under_15=round(1 - p_o15, 4),
            p_under_25=round(1 - p_o25, 4),
            p_btts=round(p_btts, 4),
            p_btts_no=round(1 - p_btts, 4),
        )

    def predict(
        self,
        feats: NationalTeamFeatureSet,
        use_monte_carlo: bool = True,
    ) -> ScoreProbabilities:
        """
        Pipeline complet : features → lambdas → prédiction.

        Args:
            feats           : feature set du match
            use_monte_carlo : True (MC 10k) | False (analytique pur)
        """
        lambda_home, lambda_away = self.compute_lambdas(feats)

        if use_monte_carlo:
            result = self.predict_monte_carlo(
                lambda_home, lambda_away,
                home_team=feats.home_team,
                away_team=feats.away_team,
            )
        else:
            result = self.predict_analytical(
                lambda_home, lambda_away,
                home_team=feats.home_team,
                away_team=feats.away_team,
            )

        log.info(
            f"{feats.home_team} vs {feats.away_team} — "
            f"P(1)={result.p_home_win:.0%} P(X)={result.p_draw:.0%} P(2)={result.p_away_win:.0%} "
            f"| Score: {result.most_likely_score} | O2.5={result.p_over_25:.0%} BTTS={result.p_btts:.0%}"
        )
        return result


# ─── Raccourci ─────────────────────────────────────────────────────────────────

def predict_national_match(
    home_profile: "NationalTeamProfile",
    away_profile: "NationalTeamProfile",
    neutral: bool = True,
    match_importance: float = 1.8,
    use_monte_carlo: bool = True,
) -> tuple[NationalTeamFeatureSet, ScoreProbabilities]:
    """
    Raccourci : profils → (features, probabilités).

    Returns:
        feats   : vecteur de features (pour logging/ML)
        probs   : distribution complète des probabilités
    """
    feats = build_features(home_profile, away_profile, neutral, match_importance)
    predictor = NationalMatchPredictor()
    probs = predictor.predict(feats, use_monte_carlo=use_monte_carlo)
    return feats, probs
