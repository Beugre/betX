"""
betX — Mise à jour automatique du dataset martj42 avec les résultats CdM ESPN.

À chaque match terminé, injecte le résultat dans worldcup_2026_matches.json
puis force le rechargement du cache.

Usage :
    python betx/data/update_wc_dataset.py
    → appelé automatiquement par predict_wc_groups.py
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import httpx

log = logging.getLogger("betx.data.update_wc_dataset")

DATASET_FILE = Path("data/worldcup_2026_matches.json")
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"

# Mapping noms ESPN → noms dans le dataset martj42
ESPN_TO_MARTJ42: dict[str, str] = {
    "United States":        "United States",
    "Mexico":               "Mexico",
    "Canada":               "Canada",
    "South Africa":         "South Africa",
    "South Korea":          "South Korea",
    "Czechia":              "Czech Republic",  # martj42 utilise "Czech Republic"
    "Czech Republic":       "Czech Republic",
    "Bosnia-Herzegovina":   "Bosnia and Herzegovina",
    "Bosnia-Herz":          "Bosnia and Herzegovina",
    "Ivory Coast":          "Ivory Coast",
    "Türkiye":              "Turkey",
    "Saudi Arabia":         "Saudi Arabia",
    "New Zealand":          "New Zealand",
    "Congo DR":             "DR Congo",
    "Cape Verde":           "Cape Verde",
    "Curaçao":              "Curaçao",
}


def _espn_to_martj42(name: str) -> str:
    return ESPN_TO_MARTJ42.get(name, name)


def fetch_finished_matches(days_back: int = 3) -> list[dict]:
    """Récupère les matchs CdM terminés des N derniers jours via ESPN."""
    results = []
    today = date.today()
    for offset in range(days_back + 1):
        d = today - timedelta(days=offset)
        try:
            r = httpx.get(ESPN_BASE, params={"dates": d.strftime("%Y%m%d")}, timeout=15)
            events = r.json().get("events", [])
            for e in events:
                comp = e.get("competitions", [{}])[0]
                status = comp.get("status", {}).get("type", {}).get("name", "")
                if status not in ("STATUS_FINAL", "STATUS_FULL_TIME"):
                    continue
                teams = comp.get("competitors", [])
                home = next((t for t in teams if t.get("homeAway") == "home"), {})
                away = next((t for t in teams if t.get("homeAway") == "away"), {})
                h_name = home.get("team", {}).get("displayName", "")
                a_name = away.get("team", {}).get("displayName", "")
                h_score = home.get("score")
                a_score = away.get("score")
                if h_score is None or a_score is None:
                    continue
                match_date = e.get("date", "")[:10]
                results.append({
                    "date": match_date,
                    "home": h_name,
                    "away": a_name,
                    "home_score": int(h_score),
                    "away_score": int(a_score),
                    "tournament": "FIFA World Cup",
                })
        except Exception as ex:
            log.debug(f"ESPN {d}: {ex}")
    return results


def _result(team_score: int, opp_score: int) -> str:
    if team_score > opp_score:
        return "W"
    elif team_score < opp_score:
        return "L"
    return "D"


def update_dataset(matches: list[dict]) -> int:
    """
    Ajoute les matchs terminés dans worldcup_2026_matches.json.
    Retourne le nombre de nouveaux matchs ajoutés.
    """
    if not DATASET_FILE.exists() or not matches:
        return 0

    data = json.loads(DATASET_FILE.read_text())
    teams_list = data.get("teams", [])

    # Index par nom d'équipe martj42
    teams_index: dict[str, dict] = {}
    for entry in teams_list:
        teams_index[entry["team"]] = entry

    added = 0
    for m in matches:
        home_espn = m["home"]
        away_espn = m["away"]
        home_martj42 = _espn_to_martj42(home_espn)
        away_martj42 = _espn_to_martj42(away_espn)
        match_date = m["date"]

        for team_martj42, is_home in [(home_martj42, True), (away_martj42, False)]:
            if team_martj42 not in teams_index:
                # Créer l'entrée si équipe inconnue
                teams_index[team_martj42] = {
                    "team": team_martj42,
                    "dataset_team_name": team_martj42,
                    "match_count": 0,
                    "matches": [],
                }
                teams_list.append(teams_index[team_martj42])

            entry = teams_index[team_martj42]
            existing_keys = {
                (mx["date"], mx["home_score"], mx["away_score"],
                 mx.get("is_home", True))
                for mx in entry["matches"]
            }

            # Déduplication robuste : même score à ±1 jour (gère les décalages UTC)
            from datetime import date as _date
            match_d = _date.fromisoformat(match_date)
            score_key = (team_score, opp_score)
            is_dup = any(
                abs((_date.fromisoformat(mx["date"]) - match_d).days) <= 1
                and (mx["team_score"], mx["opponent_score"]) == score_key
                for mx in entry["matches"]
            )
            if is_dup:
                continue  # déjà présent

            opp = away_espn if is_home else home_espn
            team_score = m["home_score"] if is_home else m["away_score"]
            opp_score = m["away_score"] if is_home else m["home_score"]

            new_match = {
                "date": match_date,
                "team": team_martj42,
                "opponent": _espn_to_martj42(opp),
                "is_home": is_home,
                "home_team": home_espn,
                "away_team": away_espn,
                "home_score": m["home_score"],
                "away_score": m["away_score"],
                "team_score": team_score,
                "opponent_score": opp_score,
                "result": _result(team_score, opp_score),
                "tournament": "FIFA World Cup",
                "city": "",
                "country": "United States",
                "neutral": True,
            }

            # Insérer en tête (plus récent en premier)
            entry["matches"].insert(0, new_match)
            entry["match_count"] = len(entry["matches"])
            added += 1

    if added:
        data["metadata"]["generated_at"] = date.today().isoformat()
        DATASET_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        log.info(f"Dataset CdM mis à jour : +{added} entrées")

    return added


def run() -> int:
    """Point d'entrée : récupère + injecte les résultats CdM récents."""
    finished = fetch_finished_matches(days_back=3)
    if not finished:
        return 0
    n = update_dataset(finished)
    if n:
        # Forcer le rechargement du cache
        try:
            from betx.data.martj42_loader import load_into_cache
            load_into_cache(force=True)
        except Exception:
            pass
    return n


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    n = run()
    print(f"{n} nouveaux résultats CdM ajoutés au dataset")
    sys.exit(0)
