"""
betX – Collecteur de cotes via The Odds API (gratuit, ~500 req/mois).

Récupère les cotes Betclic, Pinnacle, Winamax pour les matchs CdM.
Cache 2h pour économiser le quota.

Usage :
    from betx.data.odds_api_collector import fetch_wc_odds
    odds = fetch_wc_odds()  # dict {(home, away): {bookmaker: {h, d, a, o25, u25}}}
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

from betx.logger import get_logger

log = get_logger("data.odds_api")

CACHE_FILE = Path("data/cache/odds_api_wc.json")
CACHE_TTL = 2 * 3600  # 2h

# Bookmakers préférés (ordre de priorité — Pinnacle = le plus efficient)
PREFERRED_BOOKMAKERS = ["pinnacle", "betclic_fr", "winamax_fr", "unibet_fr", "marathonbet"]

# Normalisation noms The Odds API → ESPN
TEAM_NAME_MAP_ODDS: dict[str, str] = {
    "Bosnia & Herzegovina": "Bosnia-Herzegovina",
    "United States": "USA",
    "South Korea": "South Korea",
    "DR Congo": "Congo DR",
    "Ivory Coast": "Ivory Coast",
    "Cape Verde": "Cape Verde",
    "Iran": "Iran",
    "Türkiye": "Türkiye",
}


def _normalize(name: str) -> str:
    return TEAM_NAME_MAP_ODDS.get(name, name)


def _load_cache() -> dict | None:
    try:
        if CACHE_FILE.exists():
            data = json.loads(CACHE_FILE.read_text())
            if time.time() - data.get("timestamp", 0) < CACHE_TTL:
                return data.get("odds", {})
    except Exception:
        pass
    return None


def _save_cache(odds: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps({
        "timestamp": time.time(),
        "odds": odds,
    }, ensure_ascii=False, indent=2))


def fetch_wc_odds(force: bool = False) -> dict[str, dict]:
    """
    Récupère les cotes CdM depuis The Odds API.

    Retourne:
        {
            "Mexico_South Africa": {
                "bookmaker": "betclic_fr",
                "odds_home": 1.43,
                "odds_draw": 4.40,
                "odds_away": 8.50,
                "over_25": 1.85,
                "under_25": 1.95,
            },
            ...
        }
    """
    if not force:
        cached = _load_cache()
        if cached:
            log.debug(f"Cache Odds API utilisé ({len(cached)} matchs)")
            return cached

    api_key = os.getenv("ODDS_API_KEY", "")
    if not api_key:
        log.warning("ODDS_API_KEY manquante — cotes EU indisponibles")
        return {}

    base_url = os.getenv("ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4")

    try:
        r = httpx.get(
            f"{base_url}/sports/soccer_fifa_world_cup/odds/",
            params={
                "apiKey": api_key,
                "regions": "eu",
                "markets": "h2h,totals",
                "oddsFormat": "decimal",
            },
            timeout=15,
        )
        r.raise_for_status()
        events = r.json()
        remaining = r.headers.get("x-requests-remaining", "?")
        log.info(f"Odds API CdM: {len(events)} matchs | quota restant: {remaining}")
    except Exception as e:
        log.warning(f"Odds API erreur: {e}")
        return {}

    if isinstance(events, dict):
        log.warning(f"Odds API réponse inattendue: {events}")
        return {}

    result: dict[str, dict] = {}

    for event in events:
        home = _normalize(event.get("home_team", ""))
        away = _normalize(event.get("away_team", ""))
        key = f"{home}_{away}"

        best: dict = {}
        best_priority = 99

        for bm in event.get("bookmakers", []):
            bm_key = bm["key"]
            priority = PREFERRED_BOOKMAKERS.index(bm_key) if bm_key in PREFERRED_BOOKMAKERS else 99
            if priority >= best_priority and best:
                continue

            # Extraire H2H
            h2h = next((m for m in bm.get("markets", []) if m["key"] == "h2h"), None)
            totals = next((m for m in bm.get("markets", []) if m["key"] == "totals"), None)

            if not h2h:
                continue

            outcomes = {o["name"]: o["price"] for o in h2h.get("outcomes", [])}
            odds_home = outcomes.get(event["home_team"]) or outcomes.get(home)
            odds_away = outcomes.get(event["away_team"]) or outcomes.get(away)
            # Le draw est la 3e issue (ni home ni away)
            odds_draw = next((o["price"] for o in h2h.get("outcomes", [])
                              if o["name"] not in (event["home_team"], event["away_team"],
                                                    home, away)), None)

            over_25 = under_25 = None
            if totals:
                for o in totals.get("outcomes", []):
                    if abs(o.get("point", 0) - 2.5) < 0.1:
                        if o["name"] == "Over":
                            over_25 = o["price"]
                        elif o["name"] == "Under":
                            under_25 = o["price"]

            if odds_home and odds_away:
                best = {
                    "bookmaker": bm_key,
                    "odds_home": odds_home,
                    "odds_draw": odds_draw or 0,
                    "odds_away": odds_away,
                    "over_25": over_25,
                    "under_25": under_25,
                }
                best_priority = priority

        if best:
            result[key] = best

    _save_cache(result)
    log.info(f"Cotes EU récupérées: {len(result)} matchs (bookmaker: {set(v['bookmaker'] for v in result.values())})")
    return result
