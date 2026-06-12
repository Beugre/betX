"""
betX – ELO ratings officiels (source : tableau fourni par l'utilisateur + eloratings.net).

Priorité de chargement :
  1. data/elo_all_teams.json  — 183 équipes (toutes les équipes et adversaires vus dans le dataset)
  2. data/elo_ratings.json    — fallback eloratings.net

Les 5 équipes sans ELO (Basque Country, Guadeloupe, Martinique, Saint Martin, Sint Maarten)
reçoivent ELO = 700 (niveau très faible, équipes non-FIFA).
"""

from __future__ import annotations

import json
from pathlib import Path

_ELO_ALL_FILE = Path("data/elo_all_teams.json")
_ELO_FILE = Path("data/elo_ratings.json")
_ratings_cache: dict[str, float] | None = None

# ELO pour les équipes sans données dans aucune source
_UNKNOWN_ELO: dict[str, float] = {
    "basque country": 700.0,
    "guadeloupe":     700.0,
    "martinique":     700.0,
    "saint martin":   700.0,
    "sint maarten":   700.0,
}

# Normalisation noms → noms canoniques
_NAME_MAP: dict[str, str] = {
    "ivory coast":          "Ivory Coast",
    "cote d'ivoire":        "Ivory Coast",
    "côte d'ivoire":        "Ivory Coast",
    "usa":                  "United States",
    "united states":        "United States",
    "czechia":              "Czechia",
    "czech republic":       "Czechia",
    "türkiye":              "Türkiye",
    "turkey":               "Türkiye",
    "south korea":          "South Korea",
    "korea republic":       "South Korea",
    "bosnia-herzegovina":   "Bosnia and Herzegovina",
    "bosnia & herzegovina": "Bosnia and Herzegovina",
    "congo dr":             "DR Congo",
    "dr congo":             "DR Congo",
    "cape verde":           "Cape Verde",
    "cape verde islands":   "Cape Verde",
    "england":              "England",
    "curacao":              "Curaçao",
    "curaçao":              "Curaçao",
    "new zealand":          "New Zealand",
    "scotland":             "Scotland",
    "republic of ireland":  "Republic of Ireland",
    "ir iran":              "Iran",
    "the gambia":           "Gambia",
    "dpr korea":            "North Korea",
    "north korea":          "North Korea",
}


def _load() -> dict[str, float]:
    global _ratings_cache
    if _ratings_cache is not None:
        return _ratings_cache

    _ratings_cache = {}

    # 1. Charger le fichier complet (188 équipes/adversaires)
    if _ELO_ALL_FILE.exists():
        try:
            data = json.loads(_ELO_ALL_FILE.read_text())
            for name, entry in data.get("all_teams_elo", {}).items():
                elo = entry.get("elo")
                if elo is not None:
                    _ratings_cache[name] = float(elo)
                    # Alias avec le nom officiel eloratings si différent
                    official = entry.get("elo_country_name")
                    if official and official != name:
                        _ratings_cache[official] = float(elo)
        except Exception:
            pass

    # 2. Compléter avec elo_ratings.json si des entrées manquent
    if _ELO_FILE.exists():
        try:
            data2 = json.loads(_ELO_FILE.read_text())
            for name, elo in data2.get("ratings", {}).items():
                if name not in _ratings_cache:
                    _ratings_cache[name] = float(elo)
        except Exception:
            pass

    return _ratings_cache


def get_elo(team_name: str) -> float | None:
    """Retourne l'ELO d'une équipe, ou None si vraiment inconnue."""
    ratings = _load()
    key = team_name.strip()

    # Essai direct
    if key in ratings:
        return ratings[key]

    # Normalisation
    normalized = _NAME_MAP.get(key.lower())
    if normalized and normalized in ratings:
        return ratings[normalized]

    # Équipes sans ELO connues (ELO=700)
    if key.lower() in _UNKNOWN_ELO:
        return _UNKNOWN_ELO[key.lower()]

    # Recherche partielle (insensible à la casse)
    key_lower = key.lower()
    for k, v in ratings.items():
        if k.lower() == key_lower or k.lower() in key_lower or key_lower in k.lower():
            return v

    return None


def all_ratings() -> dict[str, float]:
    """Retourne tous les ratings disponibles."""
    return _load().copy()
