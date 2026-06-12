"""
betX — Chargeur du dataset martj42 (30 derniers matchs par équipe CdM 2026).

Source : worldcup_2026_matches.json (CC0-1.0)
         github.com/martj42/international_results

Intègre automatiquement dans le cache NationalTeamCollector
au premier appel, puis recharger si le fichier est plus récent.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("betx.data.martj42_loader")

DATASET_FILE = Path("data/worldcup_2026_matches.json")
CACHE_FILE = Path("data/cache/national_teams.json")

TOURNAMENT_MAP = {
    "FIFA World Cup":                   1,
    "World Cup":                        1,
    "UEFA Nations League":              8,
    "CONCACAF Nations League":          536,
    "Copa America":                     9,
    "African Cup of Nations":           6,
    "AFC Asian Cup":                    7,
    "Friendly":                         10,
    "Gold Cup":                         22,
    "CONCACAF Gold Cup":                22,
    "UEFA Euro":                        4,
    "Africa Cup of Nations":            6,
    "Arab Cup":                         656,
    "WAFF Championship":                656,
    "Gulf Cup":                         656,
}


def _get_comp_id(tournament: str) -> int:
    if not tournament:
        return 10
    tl = tournament.lower()
    for k, v in TOURNAMENT_MAP.items():
        if k.lower() in tl:
            return v
    if "world cup" in tl and "qualif" in tl:
        return 29
    if "nation" in tl and "league" in tl:
        return 8
    return 10


def _synthetic_id(team_name: str) -> int:
    """ID synthétique stable basé sur le nom."""
    return abs(hash(team_name)) % 100000 + 900000


def load_into_cache(force: bool = False) -> int:
    """
    Charge le dataset martj42 dans le cache NationalTeamCollector.
    Retourne le nombre d'équipes intégrées.
    """
    if not DATASET_FILE.exists():
        log.warning(f"Dataset non trouvé : {DATASET_FILE}")
        return 0

    # Vérifier si déjà chargé (clé spéciale dans le cache)
    cache = {}
    if CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text())
        except Exception:
            pass

    dataset_mtime = DATASET_FILE.stat().st_mtime
    last_loaded = cache.get("_martj42_loaded_at", 0)
    if not force and last_loaded >= dataset_mtime:
        log.debug("Dataset martj42 déjà chargé (cache à jour)")
        return 0

    raw = json.loads(DATASET_FILE.read_text())
    teams_data = raw.get("teams", [])

    team_ids = cache.setdefault("team_ids", {})
    fixtures = cache.setdefault("fixtures", {})

    integrated = 0
    import time

    for entry in teams_data:
        team_name = entry["team"]
        key = team_name.lower().strip()
        matches = [m for m in entry["matches"] if m.get("home_score") is not None]
        if not matches:
            continue

        # ID : préférer l'ID API réel si connu, sinon synthétique
        existing_entry = team_ids.get(key, {})
        real_id = existing_entry.get("id")
        use_id = real_id if real_id else _synthetic_id(team_name)

        if not real_id:
            team_ids[key] = {"id": use_id, "name": team_name}

        # Construire les fixtures au format API-Football
        new_fixtures = []
        for m in matches:
            new_fixtures.append({
                "fixture": {
                    "date": m["date"] + "T12:00:00+00:00",
                    "status": {"short": "FT"},
                },
                "league": {
                    "id": _get_comp_id(m.get("tournament", "")),
                    "name": m.get("tournament", "Friendly"),
                },
                "teams": {
                    "home": {
                        "id": use_id if m["is_home"] else _synthetic_id(m["opponent"]),
                        "name": m["home_team"],
                    },
                    "away": {
                        "id": _synthetic_id(m["opponent"]) if m["is_home"] else use_id,
                        "name": m["away_team"],
                    },
                },
                "goals": {"home": m["home_score"], "away": m["away_score"]},
            })

        if force:
            # En mode force, les fixtures martj42 remplacent entièrement les anciennes
            # (nécessaire pour propager les corrections de comp_id)
            existing_api = [
                f for f in fixtures.get(str(use_id), {}).get("data", [])
                if f.get("_source") == "api"  # conserver les matchs live API
            ]
            merged = sorted(
                existing_api + new_fixtures,
                key=lambda x: x["fixture"]["date"],
                reverse=True,
            )
        else:
            existing_data = fixtures.get(str(use_id), {}).get("data", [])
            existing_keys = {
                (f["fixture"]["date"][:10], f["teams"]["home"]["name"], f["teams"]["away"]["name"])
                for f in existing_data
            }
            to_add = [
                f for f in new_fixtures
                if (f["fixture"]["date"][:10], f["teams"]["home"]["name"], f["teams"]["away"]["name"])
                not in existing_keys
            ]
            merged = sorted(
                existing_data + to_add,
                key=lambda x: x["fixture"]["date"],
                reverse=True,
            )
        fixtures[str(use_id)] = {"data": merged, "timestamp": time.time()}
        integrated += 1

    cache["_martj42_loaded_at"] = dataset_mtime
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False))
    log.info(f"Dataset martj42 intégré : {integrated} équipes")
    return integrated
