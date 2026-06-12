"""
betX – Collecteur de données équipes nationales (Coupe du Monde).

Source : API-Football (plan Free, 100 req/jour max).
         Saisons accessibles : 2022, 2023, 2024.

Cache JSON 24h pour économiser le quota (data/cache/national_teams.json).

Données collectées par équipe :
  - Derniers matchs 2022-2024 (amicaux + éliminatoires + CAN/Copa/etc.)
  - H2H entre deux équipes nationales
  - Forme en matchs compétitifs (≠ amicaux)
  - ELO estimé depuis le bilan W/D/L pondéré
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from betx.logger import get_logger

log = get_logger("data.national_teams")


# ─── Normalisation noms ESPN → API-Football ───────────────────────────────────

TEAM_NAME_MAP: dict[str, str] = {
    # ESPN utilise les noms anglais modernes, API-Football parfois différent
    "czechia":          "Czech Republic",
    "korea republic":   "South Korea",
    "republic of korea": "South Korea",
    "côte d'ivoire":    "Ivory Coast",
    "cote d'ivoire":    "Ivory Coast",
    "united states":    "USA",
    "u.s.a.": "USA",
    "dr congo":         "DR Congo",
    "guinea bissau":    "Guinea-Bissau",
    "trinidad & tobago": "Trinidad and Tobago",
    "north macedonia":  "North Macedonia",
    "cape verde":       "Cape Verde Islands",
}


def normalize_team_name(name: str) -> str:
    """Normalise un nom d'équipe ESPN vers le format API-Football."""
    return TEAM_NAME_MAP.get(name.lower().strip(), name)


# ─── Normalisation noms ESPN → API-Football ─────────────────────────────────────

TEAM_NAME_MAP: dict[str, str] = {
    "czechia":              "Czech Republic",
    "korea republic":       "South Korea",
    "republic of korea":    "South Korea",
    "côte d'ivoire":        "Ivory Coast",
    "cote d'ivoire":        "Ivory Coast",
    "united states":        "USA",
    "u.s.a.":               "USA",
    "dr congo":             "DR Congo",
    "guinea bissau":        "Guinea-Bissau",
    "trinidad & tobago":    "Trinidad and Tobago",
    "north macedonia":      "North Macedonia",
    "cape verde":           "Cape Verde Islands",
}


def normalize_team_name(name: str) -> str:
    """Normalise un nom d'équipe ESPN vers le format API-Football."""
    return TEAM_NAME_MAP.get(name.lower().strip(), name)


# ─── IDs compétitions importantes ──────────────────────────────────────────────

COMPETITION_IDS = {
    "world_cup":                    1,
    "friendlies":                  10,
    "afcon":                        6,
    "afcon_qualifiers":            36,
    "caf_wc_qualifiers":           29,
    "cosafa_cup":                 859,
    "copa_america":                 9,
    "concacaf_gold_cup":           22,
    "concacaf_nations_league":    536,
    "concacaf_wc_qualifiers":      31,
    "afc_wc_qualifiers":           26,
    "conmebol_wc_qualifiers":      34,
    "uefa_nations_league":          8,
    "uefa_wc_qualifiers":          32,
    "confederations_cup":          21,
}

# IDs compétitions compétitives par confédération (hors amicaux)
CONFEDERATION_COMP_IDS: dict[str, set[int]] = {
    "africa":   {6, 29, 36, 859, 21},
    "concacaf": {9, 22, 31, 536},
    "conmebol": {9, 34},
    "uefa":     {4, 8, 32},
    "afc":      {7, 26, 30},   # 7=Asian Cup, 26=qualifs old, 30=WC Qual Asia
}

# Toutes les compétitions compétitives (union)
ALL_COMPETITIVE_IDS: set[int] = set().union(*CONFEDERATION_COMP_IDS.values())

# Poids par type de compétition (importance pour la prédiction)
# Formule finale : w = w_recency * w_match_type
MATCH_TYPE_WEIGHTS: dict[int, float] = {
    1:   1.8,   # World Cup
    6:   1.6,   # AFCON
    7:   1.5,   # Asian Cup
    9:   1.6,   # Copa America
    21:  1.5,   # Confederations Cup
    29:  1.2,   # WC Qualifiers Africa (adversaires souvent faibles)
    30:  1.3,   # WC Qualifiers Asia
    31:  1.4,   # WC Qualifiers CONCACAF
    32:  1.4,   # WC Qualifiers UEFA
    34:  1.4,   # WC Qualifiers CONMEBOL
    26:  1.3,   # WC Qualifiers AFC
    36:  1.1,   # AFCON Qualifiers (adversaires très variés)
    8:   1.3,   # UEFA Nations League
    22:  1.3,   # CONCACAF Gold Cup
    4:   1.3,   # Euro
    536: 1.2,   # CONCACAF Nations League
    859: 0.8,   # COSAFA Cup (adversaires faibles, impact réduit)
    10:  0.6,   # Friendlies
}

# Saisons accessibles en plan Free
AVAILABLE_SEASONS = [2024, 2023, 2022]

# TTL du cache : 24h
CACHE_TTL_SECONDS = 86_400


# ─── Structures de données ─────────────────────────────────────────────────────

@dataclass
class MatchRecord:
    """Un match du point de vue d'une équipe cible."""
    date: str
    competition: str
    competition_id: int
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    is_home: bool  # du point de vue de l'équipe cible

    @property
    def result(self) -> str:
        """W / D / L pour l'équipe cible."""
        scored = self.home_goals if self.is_home else self.away_goals
        conceded = self.away_goals if self.is_home else self.home_goals
        if scored > conceded:
            return "W"
        elif scored == conceded:
            return "D"
        return "L"

    @property
    def goals_scored(self) -> int:
        return self.home_goals if self.is_home else self.away_goals

    @property
    def goals_conceded(self) -> int:
        return self.away_goals if self.is_home else self.home_goals

    @property
    def opponent(self) -> str:
        return self.away_team if self.is_home else self.home_team

    @property
    def is_competitive(self) -> bool:
        return self.competition_id in ALL_COMPETITIVE_IDS


@dataclass
class NationalTeamProfile:
    """Profil complet d'une équipe nationale basé sur l'historique réel."""
    team_name: str
    team_id: int
    recent_matches: list[MatchRecord] = field(default_factory=list)
    h2h_matches: list[MatchRecord] = field(default_factory=list)

    # ── Poids composites ─────────────────────────────────────────────────

    @staticmethod
    def _composite_weight(rank: int, competition_id: int,
                          match_date: str | None = None) -> float:
        """
        Poids composite : récence × type de compétition.

        Si match_date fourni (YYYY-MM-DD) : récence = exp(-0.023 × Δjours)
          → 30j : 0.50 | 90j : 0.12 | 180j : 0.015
        Sinon (fallback) : récence = exp(-0.07 × rank)

        Formule complète : w = w_recency × w_type
        """
        if match_date:
            try:
                from datetime import date as _date
                d = _date.fromisoformat(match_date)
                delta_days = (_date.today() - d).days
                w_recency = math.exp(-0.023 * max(delta_days, 0))
            except Exception:
                w_recency = math.exp(-0.07 * rank)
        else:
            w_recency = math.exp(-0.07 * rank)
        w_type = MATCH_TYPE_WEIGHTS.get(competition_id, 1.0)
        return w_recency * w_type

    # ── Forme ─────────────────────────────────────────────────────────────

    @property
    def recent_form(self) -> list[str]:
        """Derniers 5 résultats (toutes compétitions)."""
        return [m.result for m in self.recent_matches[:5]]

    @property
    def competitive_form(self) -> list[str]:
        """Derniers 5 résultats en matchs compétitifs uniquement."""
        return [m.result for m in self.recent_matches if m.is_competitive][:5]

    def form_score(self, n: int = 10, official_only: bool = False) -> float:
        """
        Score de forme pondéré (récence × compétition) entre -1 et +1.

        W=+1, D=0, L=-1. Pondération composite.
        """
        matches = self.recent_matches[:n]
        if official_only:
            matches = [m for m in self.recent_matches if m.is_competitive][:n]
        if not matches:
            return 0.0
        result_map = {"W": 1.0, "D": 0.0, "L": -1.0}
        total_w = 0.0
        total_pts = 0.0
        for rank, m in enumerate(matches):
            w = self._composite_weight(rank, m.competition_id, m.date)
            total_pts += result_map[m.result] * w
            total_w += w
        return total_pts / total_w if total_w > 0 else 0.0

    # ── Statistiques offensives / défensives ──────────────────────────────

    def weighted_lambda_scored(self, n: int = 15, official_only: bool = False) -> float:
        """
        Buts marqués pondérés (récence × type compétition).
        Représente le λ d'attaque calibré de l'équipe.
        """
        matches = self.recent_matches[:n]
        if official_only:
            matches = [m for m in self.recent_matches if m.is_competitive][:n]
        if not matches:
            return 1.2
        total_w = 0.0
        total_goals = 0.0
        for rank, m in enumerate(matches):
            # λ buts : poids comp uniquement (force adversaire gère le biais)
            w = self._composite_weight(rank, m.competition_id)
            total_goals += m.goals_scored * w
            total_w += w
        return total_goals / total_w if total_w > 0 else 1.2

    def weighted_lambda_conceded(self, n: int = 15, official_only: bool = False) -> float:
        """
        Buts encaissés pondérés (récence × type compétition).
        Représente le λ de défense (faiblesses) de l'équipe.
        """
        matches = self.recent_matches[:n]
        if official_only:
            matches = [m for m in self.recent_matches if m.is_competitive][:n]
        if not matches:
            return 1.2
        total_w = 0.0
        total_goals = 0.0
        for rank, m in enumerate(matches):
            # λ buts : poids comp uniquement (force adversaire gère le biais)
            w = self._composite_weight(rank, m.competition_id)
            total_goals += m.goals_conceded * w
            total_w += w
        return total_goals / total_w if total_w > 0 else 1.2

    # Rétro-compatibilité
    @property
    def avg_goals_scored(self) -> float:
        return self.weighted_lambda_scored(15)

    @property
    def avg_goals_conceded(self) -> float:
        return self.weighted_lambda_conceded(15)

    @property
    def competitive_avg_scored(self) -> float:
        return self.weighted_lambda_scored(10, official_only=True)

    @property
    def competitive_avg_conceded(self) -> float:
        return self.weighted_lambda_conceded(10, official_only=True)

    # ── ELO estimé ────────────────────────────────────────────────────────

    @property
    def elo_estimate(self) -> float:
        """
        ELO estimé avec poids composite (récence × type compétition).

        Un nul contre une équipe forte en CdM vaut plus qu'une victoire
        en amical contre une équipe faible.
        """
        matches = self.recent_matches[:20]
        if not matches:
            return 1500.0
        pts_map = {"W": 1.0, "D": 0.4, "L": 0.0}
        total_w = 0.0
        total_pts = 0.0
        for rank, m in enumerate(matches):
            w = self._composite_weight(rank, m.competition_id, m.date)
            total_pts += pts_map[m.result] * w
            total_w += w * 1.0  # max pts par match = 1.0
        ratio = total_pts / total_w if total_w > 0 else 0.5
        # 0 → 1300, 0.5 → 1550, 1.0 → 1800
        return 1300 + ratio * 500

    # ── H2H ───────────────────────────────────────────────────────────────

    @property
    def h2h_score(self) -> float:
        """Score H2H pondéré (récence × compétition) : + = avantage pour cette équipe."""
        if not self.h2h_matches:
            return 0.0
        result_map = {"W": 1.0, "D": 0.0, "L": -1.0}
        total_w = 0.0
        total_pts = 0.0
        for rank, m in enumerate(self.h2h_matches):
            w = self._composite_weight(rank, m.competition_id, m.date)
            total_pts += result_map[m.result] * w
            total_w += w
        return total_pts / total_w if total_w > 0 else 0.0

    def h2h_stats(self) -> dict:
        """Statistiques H2H détaillées."""
        if not self.h2h_matches:
            return {
                "count": 0, "win_rate": 0.0, "draw_rate": 0.0, "loss_rate": 0.0,
                "avg_scored": 0.0, "avg_conceded": 0.0, "bias": 0.0,
            }
        n = len(self.h2h_matches)
        wins = sum(1 for m in self.h2h_matches if m.result == "W")
        draws = sum(1 for m in self.h2h_matches if m.result == "D")
        losses = sum(1 for m in self.h2h_matches if m.result == "L")
        return {
            "count": n,
            "win_rate": wins / n,
            "draw_rate": draws / n,
            "loss_rate": losses / n,
            "avg_scored": sum(m.goals_scored for m in self.h2h_matches) / n,
            "avg_conceded": sum(m.goals_conceded for m in self.h2h_matches) / n,
            "bias": self.h2h_score,
        }

    # ── Résumé texte ──────────────────────────────────────────────────────

    def summary(self) -> str:
        form_icons = {"W": "✅", "D": "🟡", "L": "❌"}
        form_str = " ".join(form_icons.get(r, "?") for r in self.recent_form)
        n = min(len(self.recent_matches), 15)
        return (
            f"{self.team_name}: {n} matchs | "
            f"λ atk={self.weighted_lambda_scored():.2f} | λ def={self.weighted_lambda_conceded():.2f} | "
            f"ELO~{self.elo_estimate:.0f} | Forme: {form_str}"
        )

    def competition_breakdown(self) -> str:
        """Résumé par compétition."""
        by_comp: dict[str, list[MatchRecord]] = {}
        for m in self.recent_matches:
            by_comp.setdefault(m.competition, []).append(m)
        lines = []
        for comp, matches in sorted(by_comp.items()):
            w_sum = sum(1 for m in matches if m.result == "W")
            d_sum = sum(1 for m in matches if m.result == "D")
            l_sum = sum(1 for m in matches if m.result == "L")
            gf = sum(m.goals_scored for m in matches)
            ga = sum(m.goals_conceded for m in matches)
            lines.append(f"  {comp}: {len(matches)}J {w_sum}V/{d_sum}N/{l_sum}D | {gf}-{ga}")
        return "\n".join(lines)


# ─── Collecteur ────────────────────────────────────────────────────────────────

class NationalTeamCollector:
    """
    Collecte et met en cache les données des équipes nationales.

    Budget : 100 req/jour (plan Free API-Football).
    Chaque équipe coûte ~3 req (3 saisons). H2H : 1 req.
    → 4 équipes (2 matchs CdM) = 13 req/jour max.
    """

    CACHE_FILE = Path("data/cache/national_teams.json")

    def __init__(self) -> None:
        self.api_key = os.getenv("API_FOOTBALL_KEY", "")
        self.base_url = os.getenv(
            "API_FOOTBALL_BASE_URL", "https://v3.football.api-sports.io"
        )
        self._cache: dict = self._load_cache()

    # ─── Cache ───────────────────────────────────────────────────────────

    def _load_cache(self) -> dict:
        try:
            if self.CACHE_FILE.exists():
                return json.loads(self.CACHE_FILE.read_text())
        except Exception:
            pass
        return {"team_ids": {}, "fixtures": {}, "h2h": {}}

    def _save_cache(self) -> None:
        self.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.CACHE_FILE.write_text(json.dumps(self._cache, indent=2, default=str))

    def _is_fresh(self, key: str, namespace: str) -> bool:
        entry = self._cache.get(namespace, {}).get(key)
        if not entry or "timestamp" not in entry:
            return False
        return (time.time() - entry["timestamp"]) < CACHE_TTL_SECONDS

    # ─── API ─────────────────────────────────────────────────────────────

    def _get(self, endpoint: str, params: dict) -> dict:
        if not self.api_key:
            log.warning("API_FOOTBALL_KEY manquante — données nationales indisponibles.")
            return {}
        headers = {"x-apisports-key": self.api_key}
        try:
            r = httpx.get(
                f"{self.base_url}/{endpoint}",
                params=params,
                headers=headers,
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("errors"):
                log.debug(f"API-Football {endpoint} errors: {data['errors']}")
                return {}
            return data
        except Exception as e:
            log.warning(f"API-Football ({endpoint}): {e}")
            return {}

    # ─── Team ID ─────────────────────────────────────────────────────────

    def get_team_id(self, team_name: str) -> int | None:
        """Résout le nom d'équipe en ID (cache permanent — aucune expiration)."""
        key = team_name.lower().strip()
        cached = self._cache.get("team_ids", {}).get(key)
        if cached and cached.get("id"):
            return cached["id"]

        # Normaliser le nom (ESPN → API-Football)
        api_name = normalize_team_name(team_name)

        # Tentative avec le nom normalisé
        data = self._get("teams", {"name": api_name})
        results = [r for r in data.get("response", []) if r["team"].get("national") is True]

        # Fallback : nom original si normalisé n'a rien donné
        if not results and api_name != team_name:
            data = self._get("teams", {"name": team_name})
            results = [r for r in data.get("response", []) if r["team"].get("national") is True]

        # Fallback : premier mot
        if not results:
            data = self._get("teams", {"name": api_name.split()[0]})
            results = [r for r in data.get("response", []) if r["team"].get("national") is True]

        if not results:
            log.warning(f"Équipe nationale introuvable : '{team_name}' (essayé: '{api_name}')")
            return None

        team = results[0]["team"]
        self._cache.setdefault("team_ids", {})[key] = {"id": team["id"], "name": team["name"]}
        self._save_cache()
        log.info(f"Team ID résolu : '{team_name}' → {team['id']} ({team['name']})")
        return team["id"]

    # ─── Fixtures ────────────────────────────────────────────────────────

    def fetch_fixtures(self, team_id: int) -> list[dict]:
        """Récupère tous les matchs 2022-2024 (avec cache 24h)."""
        key = str(team_id)
        if self._is_fresh(key, "fixtures"):
            log.debug(f"Cache fixtures team {team_id} (frais)")
            return self._cache["fixtures"][key]["data"]

        all_fixtures: list[dict] = []
        for season in AVAILABLE_SEASONS:
            data = self._get("fixtures", {"team": team_id, "season": season})
            all_fixtures.extend(data.get("response", []))

        # Tri décroissant (plus récent en premier)
        all_fixtures.sort(key=lambda x: x["fixture"]["date"], reverse=True)

        self._cache.setdefault("fixtures", {})[key] = {
            "data": all_fixtures,
            "timestamp": time.time(),
        }
        self._save_cache()
        log.info(f"Fixtures team {team_id} : {len(all_fixtures)} matchs (2022-2024)")
        return all_fixtures

    # ─── H2H ─────────────────────────────────────────────────────────────

    def fetch_h2h(self, team1_id: int, team2_id: int) -> list[dict]:
        """Récupère le H2H historique (avec cache 24h)."""
        key = f"{min(team1_id, team2_id)}-{max(team1_id, team2_id)}"
        if self._is_fresh(key, "h2h"):
            log.debug(f"Cache H2H {key} (frais)")
            return self._cache["h2h"][key]["data"]

        data = self._get("fixtures/headtohead", {"h2h": f"{team1_id}-{team2_id}"})
        fixtures = data.get("response", [])
        fixtures.sort(key=lambda x: x["fixture"]["date"], reverse=True)

        self._cache.setdefault("h2h", {})[key] = {
            "data": fixtures,
            "timestamp": time.time(),
        }
        self._save_cache()
        log.info(f"H2H {team1_id} vs {team2_id} : {len(fixtures)} matchs")
        return fixtures

    # ─── Parsing ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_fixture(fixture: dict, team_id: int) -> MatchRecord | None:
        """Convertit un fixture brut en MatchRecord pour l'équipe cible."""
        status = fixture.get("fixture", {}).get("status", {}).get("short", "")
        goals = fixture.get("goals", {})
        if status != "FT" or goals.get("home") is None or goals.get("away") is None:
            return None
        teams = fixture["teams"]
        is_home = teams["home"]["id"] == team_id
        return MatchRecord(
            date=fixture["fixture"]["date"][:10],
            competition=fixture["league"]["name"],
            competition_id=fixture["league"]["id"],
            home_team=teams["home"]["name"],
            away_team=teams["away"]["name"],
            home_goals=int(goals["home"]),
            away_goals=int(goals["away"]),
            is_home=is_home,
        )

    # ─── Profil complet ──────────────────────────────────────────────────

    def get_profile(
        self,
        team_name: str,
        opponent_name: str | None = None,
    ) -> NationalTeamProfile | None:
        """
        Construit le profil complet d'une équipe nationale.

        Args:
            team_name: Nom de l'équipe (ex: "South Africa", "Mexico")
            opponent_name: Adversaire pour enrichir le H2H

        Returns:
            NationalTeamProfile ou None si l'équipe est introuvable
        """
        team_id = self.get_team_id(team_name)
        if team_id is None:
            return None

        # Récupérer et parser les fixtures
        raw = self.fetch_fixtures(team_id)
        recent_matches: list[MatchRecord] = []
        for f in raw:
            rec = self._parse_fixture(f, team_id)
            if rec is not None:
                recent_matches.append(rec)

        # H2H
        h2h_matches: list[MatchRecord] = []
        if opponent_name:
            opp_id = self.get_team_id(opponent_name)
            if opp_id:
                raw_h2h = self.fetch_h2h(team_id, opp_id)
                for f in raw_h2h:
                    rec = self._parse_fixture(f, team_id)
                    if rec is not None:
                        h2h_matches.append(rec)

        return NationalTeamProfile(
            team_name=team_name,
            team_id=team_id,
            recent_matches=recent_matches,
            h2h_matches=h2h_matches,
        )
