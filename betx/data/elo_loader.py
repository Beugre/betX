"""
betX – ELO ratings officiels (eloratings.net).

Chargé depuis data/elo_ratings.json (mis à jour ~1x/mois).
Utilisé comme ancre principale pour les prédictions équipes nationales.

Fallback : FIFA ranking si équipe non trouvée.
"""

from __future__ import annotations

import json
from pathlib import Path

_ELO_FILE = Path("data/elo_ratings.json")
_ratings_cache: dict[str, int] | None = None

# Normalisation noms ESPN/API-Football → noms eloratings.net
_NAME_MAP: dict[str, str] = {
    "ivory coast":          "Ivory Coast",
    "cote d'ivoire":        "Ivory Coast",
    "usa":                  "USA",
    "united states":        "USA",
    "czechia":              "Czechia",
    "czech republic":       "Czechia",
    "türkiye":              "Türkiye",
    "turkey":               "Türkiye",
    "south korea":          "South Korea",
    "korea republic":       "South Korea",
    "bosnia-herzegovina":   "Bosnia-Herzegovina",
    "bosnia & herzegovina": "Bosnia-Herzegovina",
    "congo dr":             "Congo DR",
    "dr congo":             "Congo DR",
    "cape verde":           "Cape Verde",
    "cape verde islands":   "Cape Verde",
    "england":              "England",
    "curacao":              "Curaçao",
    "curaçao":              "Curaçao",
    "new zealand":          "New Zealand",
    "scotland":             "Scotland",
}


def _load() -> dict[str, int]:
    global _ratings_cache
    if _ratings_cache is not None:
        return _ratings_cache
    try:
        if _ELO_FILE.exists():
            data = json.loads(_ELO_FILE.read_text())
            _ratings_cache = data.get("ratings", {})
            return _ratings_cache
    except Exception:
        pass
    _ratings_cache = {}
    return _ratings_cache


def get_elo(team_name: str) -> int | None:
    """Retourne l'ELO officiel d'une équipe nationale, ou None si inconnue."""
    ratings = _load()
    # Essai direct
    if team_name in ratings:
        return ratings[team_name]
    # Normalisation
    normalized = _NAME_MAP.get(team_name.lower())
    if normalized and normalized in ratings:
        return ratings[normalized]
    # Recherche partielle (premier mot)
    key_lower = team_name.lower()
    for k, v in ratings.items():
        if k.lower() == key_lower or k.lower() in key_lower or key_lower in k.lower():
            return v
    return None


def all_ratings() -> dict[str, int]:
    """Retourne tous les ratings disponibles."""
    return _load().copy()
