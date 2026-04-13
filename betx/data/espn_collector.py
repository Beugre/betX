"""
betX – Enrichissement ESPN API.

Source GRATUITE, sans clé, données saison en cours (2025-26).
Fournit classements complets (20 teams), matchs + scores,
et permet de calculer les stats home/away par équipe.

Ligues couvertes : PL, Serie A, La Liga, Bundesliga, Ligue 1,
                   Eredivisie, Primeira Liga.

Endpoints utilisés :
  - standings  : classement (GF, GA, GP, points)
  - scoreboard : matchs du jour
  - schedule   : historique matchs d'une équipe (home/away + scores)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from rich.console import Console

from betx.logger import get_logger

if TYPE_CHECKING:
    from betx.models.football_model import TeamStats

log = get_logger("espn")
console = Console()

# ─── Cache disque ─────────────────────────────────────────────────────
_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_ESPN_CACHE_FILE = _CACHE_DIR / "espn_league_stats.json"
_ESPN_CACHE_TTL = 6 * 3600  # 6h – les matchs se terminent dans la journée

# ─── Mapping slug ESPN → label ────────────────────────────────────────
ESPN_LEAGUE_MAP: dict[str, str] = {
    "soccer_epl":                "eng.1",
    "soccer_spain_la_liga":      "esp.1",
    "soccer_italy_serie_a":      "ita.1",
    "soccer_germany_bundesliga": "ger.1",
    "soccer_france_ligue_one":   "fra.1",
    "soccer_netherlands_eredivisie":   "ned.1",
    "soccer_portugal_primeira_liga":   "por.1",
    # Coupes d'Europe
    "soccer_uefa_champs_league": "uefa.champions",
    "soccer_uefa_europa_league": "uefa.europa",
}

# Compétitions européennes (pas de standings domestiques)
ESPN_EURO_COMPS: set[str] = {"uefa.champions", "uefa.europa"}

_ESPN_BASE = "https://site.api.espn.com/apis"

# ─── Aliases : noms Odds API / API-Football → noms ESPN ─────────────
_TEAM_NAME_ALIASES: dict[str, str] = {
    # Premier League
    "wolverhampton wanderers": "wolverhampton wanderers",
    "brighton and hove albion": "brighton & hove albion",
    "brighton": "brighton & hove albion",
    "newcastle": "newcastle united",
    "tottenham": "tottenham hotspur",
    "west ham": "west ham united",
    "nottingham forest": "nottingham forest",
    "leeds": "leeds united",
    "wolves": "wolverhampton wanderers",
    "bournemouth": "afc bournemouth",
    "afc bournemouth": "afc bournemouth",
    "sunderland afc": "sunderland",
    # Bundesliga
    "monchengladbach": "borussia mönchengladbach",
    "borussia monchengladbach": "borussia mönchengladbach",
    "fc st. pauli": "st. pauli",
    "1. fc heidenheim": "1. fc heidenheim 1846",
    "1. fc köln": "1. fc köln",
    "fsv mainz 05": "1. fsv mainz 05",
    "mainz 05": "1. fsv mainz 05",
    "mainz": "1. fsv mainz 05",
    "tsg hoffenheim": "tsg hoffenheim",
    "hoffenheim": "tsg hoffenheim",
    "vfb stuttgart": "vfb stuttgart",
    "stuttgart": "vfb stuttgart",
    "vfl wolfsburg": "vfl wolfsburg",
    "wolfsburg": "vfl wolfsburg",
    "sc freiburg": "sc freiburg",
    "freiburg": "sc freiburg",
    "rb leipzig": "rb leipzig",
    "union berlin": "1. fc union berlin",
    "fc augsburg": "fc augsburg",
    "augsburg": "fc augsburg",
    "bayer leverkusen": "bayer 04 leverkusen",
    "leverkusen": "bayer 04 leverkusen",
    "eintracht frankfurt": "eintracht frankfurt",
    "bayern munich": "bayern munich",
    "fc bayern": "bayern munich",
    "werder bremen": "werder bremen",
    "bochum": "vfl bochum 1848",
    # Serie A
    "inter": "internazionale",
    "inter milan": "internazionale",
    "ac milan": "ac milan",
    "as roma": "as roma",
    "roma": "as roma",
    "atalanta bc": "atalanta",
    "atalanta": "atalanta",
    "hellas verona": "hellas verona",
    "ssc napoli": "napoli",
    "napoli": "napoli",
    "us lecce": "lecce",
    "us sassuolo": "sassuolo",
    "como": "como 1907",
    "como 1907": "como 1907",
    "monza": "ac monza",
    # La Liga
    "atlético madrid": "atlético madrid",
    "atletico madrid": "atlético madrid",
    "athletic bilbao": "athletic club",
    "athletic club": "athletic club",
    "ca osasuna": "osasuna",
    "osasuna": "osasuna",
    "rayo vallecano": "rayo vallecano",
    "real betis": "real betis",
    "celta vigo": "celta vigo",
    "rc celta": "celta vigo",
    "girona fc": "girona",
    "girona": "girona",
    "alavés": "alavés",
    "deportivo alavés": "alavés",
    "cd leganés": "leganés",
    "leganes": "leganés",
    "rcd mallorca": "mallorca",
    "mallorca": "mallorca",
    "getafe cf": "getafe",
    "getafe": "getafe",
    "real valladolid": "valladolid",
    "valladolid": "valladolid",
    "ud las palmas": "las palmas",
    "las palmas": "las palmas",
    "real sociedad": "real sociedad",
    "villarreal cf": "villarreal",
    "villarreal": "villarreal",
    "valencia cf": "valencia",
    "valencia": "valencia",
    "sevilla fc": "sevilla",
    "sevilla": "sevilla",
    "espanyol": "espanyol",
    "rcd espanyol": "espanyol",
    "real madrid": "real madrid",
    "barcelona": "barcelona",
    "fc barcelona": "barcelona",
    # Ligue 1
    "paris saint germain": "paris saint-germain",
    "paris saint-germain": "paris saint-germain",
    "psg": "paris saint-germain",
    "rc lens": "lens",
    "lens": "lens",
    "as monaco": "monaco",
    "monaco": "monaco",
    "olympique de marseille": "marseille",
    "marseille": "marseille",
    "olympique lyonnais": "lyon",
    "lyon": "lyon",
    "stade rennais": "stade rennais",
    "rennes": "stade rennais",
    "lille": "lille",
    "losc lille": "lille",
    "ogc nice": "nice",
    "nice": "nice",
    "rc strasbourg alsace": "strasbourg",
    "strasbourg": "strasbourg",
    "stade brestois 29": "stade brestois 29",
    "brest": "stade brestois 29",
    "fc nantes": "nantes",
    "nantes": "nantes",
    "toulouse fc": "toulouse",
    "toulouse": "toulouse",
    "montpellier": "montpellier",
    "montpellier hsc": "montpellier",
    "aj auxerre": "auxerre",
    "auxerre": "auxerre",
    "angers": "angers",
    "angers sco": "angers",
    "le havre ac": "le havre ac",
    "le havre": "le havre ac",
    "saint-etienne": "saint-étienne",
    "as saint-etienne": "saint-étienne",
    "stade de reims": "reims",
    "reims": "reims",
}


@dataclass
class _LeagueTeamData:
    """Données pré-calculées d'une équipe dans sa ligue."""
    espn_id: str
    espn_name: str
    # Standings
    gp: int = 0
    gf: int = 0
    ga: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    points: int = 0
    # Home/Away (calculés depuis schedule)
    home_gp: int = 0
    home_gf: int = 0
    home_ga: int = 0
    away_gp: int = 0
    away_gf: int = 0
    away_ga: int = 0
    # Form
    form: list[str] | None = None  # ["W","D","L","W","W"]


def _get(url: str, timeout: float = 15.0) -> dict | None:
    """GET avec retry."""
    for attempt in range(3):
        try:
            r = httpx.get(url, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            log.warning(f"ESPN HTTP {r.status_code} for {url}")
        except httpx.TimeoutException:
            log.warning(f"ESPN timeout (attempt {attempt+1}/3) for {url}")
            time.sleep(1)
        except Exception as exc:
            log.warning(f"ESPN error: {exc}")
            break
    return None


# ─── Cache disque ─────────────────────────────────────────────────────

def _load_cache() -> dict[str, dict[str, _LeagueTeamData]] | None:
    """Charge le cache disque {espn_slug: {team_name_lower: _LeagueTeamData}}."""
    if not _ESPN_CACHE_FILE.exists():
        return None
    try:
        raw = json.loads(_ESPN_CACHE_FILE.read_text())
        cached_at = raw.get("_cached_at", 0)
        if time.time() - cached_at > _ESPN_CACHE_TTL:
            return None
        result: dict[str, dict[str, _LeagueTeamData]] = {}
        for slug, teams in raw.get("leagues", {}).items():
            result[slug] = {}
            for key, td in teams.items():
                result[slug][key] = _LeagueTeamData(
                    espn_id=td["espn_id"],
                    espn_name=td["espn_name"],
                    gp=td.get("gp", 0),
                    gf=td.get("gf", 0),
                    ga=td.get("ga", 0),
                    wins=td.get("wins", 0),
                    draws=td.get("draws", 0),
                    losses=td.get("losses", 0),
                    points=td.get("points", 0),
                    home_gp=td.get("home_gp", 0),
                    home_gf=td.get("home_gf", 0),
                    home_ga=td.get("home_ga", 0),
                    away_gp=td.get("away_gp", 0),
                    away_gf=td.get("away_gf", 0),
                    away_ga=td.get("away_ga", 0),
                    form=td.get("form"),
                )
        return result
    except Exception as exc:
        log.warning(f"Cache ESPN invalide: {exc}")
        return None


def _save_cache(data: dict[str, dict[str, _LeagueTeamData]]):
    """Persiste le cache disque."""
    serializable: dict[str, dict[str, dict]] = {}
    for slug, teams in data.items():
        serializable[slug] = {}
        for key, td in teams.items():
            serializable[slug][key] = {
                "espn_id": td.espn_id,
                "espn_name": td.espn_name,
                "gp": td.gp, "gf": td.gf, "ga": td.ga,
                "wins": td.wins, "draws": td.draws, "losses": td.losses,
                "points": td.points,
                "home_gp": td.home_gp, "home_gf": td.home_gf, "home_ga": td.home_ga,
                "away_gp": td.away_gp, "away_gf": td.away_gf, "away_ga": td.away_ga,
                "form": td.form,
            }
    _ESPN_CACHE_FILE.write_text(json.dumps({
        "_cached_at": time.time(),
        "leagues": serializable,
    }, ensure_ascii=False, indent=2))


# ─── Chargement standings ────────────────────────────────────────────

def _fetch_standings(espn_slug: str) -> dict[str, _LeagueTeamData]:
    """Classement complet d'une ligue ESPN → {team_name_lower: _LeagueTeamData}."""
    url = f"{_ESPN_BASE}/v2/sports/soccer/{espn_slug}/standings"
    d = _get(url)
    if not d:
        return {}

    children = d.get("children", [])
    entries = children[0]["standings"]["entries"] if children else []
    if not entries:
        # Alternate structure
        standings = d.get("standings", [])
        entries = standings[0].get("entries", []) if standings else []

    teams: dict[str, _LeagueTeamData] = {}
    for entry in entries:
        team_info = entry.get("team", {})
        team_id = str(team_info.get("id", ""))
        team_name = team_info.get("displayName", "")
        if not team_id or not team_name:
            continue

        stats = {}
        for s in entry.get("stats", []):
            sname = s.get("name", "")
            sval = s.get("value")
            if sname and sval is not None:
                stats[sname] = sval

        td = _LeagueTeamData(
            espn_id=team_id,
            espn_name=team_name,
            gp=int(stats.get("gamesPlayed", 0)),
            gf=int(stats.get("pointsFor", 0)),
            ga=int(stats.get("pointsAgainst", 0)),
            wins=int(stats.get("wins", 0)),
            draws=int(stats.get("ties", 0)),
            losses=int(stats.get("losses", 0)),
            points=int(stats.get("points", 0)),
        )
        teams[team_name.lower()] = td

    return teams


# ─── Chargement schedule pour home/away split ────────────────────────

def _compute_home_away(espn_slug: str, team_id: str) -> tuple[int, int, int, int, int, int, list[str]]:
    """
    Calcule stats home/away depuis le schedule d'une équipe.
    
    Returns:
        (home_gp, home_gf, home_ga, away_gp, away_gf, away_ga, form_last5)
    """
    url = f"{_ESPN_BASE}/site/v2/sports/soccer/{espn_slug}/teams/{team_id}/schedule"
    d = _get(url)
    if not d:
        return 0, 0, 0, 0, 0, 0, []

    events = d.get("events", [])
    home_gp, home_gf, home_ga = 0, 0, 0
    away_gp, away_gf, away_ga = 0, 0, 0
    form: list[str] = []

    for e in events:
        competitions = e.get("competitions", [])
        if not competitions:
            continue
        comp = competitions[0]
        status_name = comp.get("status", {}).get("type", {}).get("name", "")
        if status_name != "STATUS_FULL_TIME":
            continue

        competitors = comp.get("competitors", [])
        if len(competitors) != 2:
            continue

        our_score = None
        opp_score = None
        is_home = None

        for c in competitors:
            tid = str(c.get("team", {}).get("id", ""))
            score_val = c.get("score", {})
            if isinstance(score_val, dict):
                score = float(score_val.get("value", 0))
            else:
                score = float(score_val) if score_val else 0

            if tid == team_id:
                our_score = score
                is_home = c.get("homeAway") == "home"
            else:
                opp_score = score

        if our_score is None or opp_score is None or is_home is None:
            continue

        if is_home:
            home_gf += int(our_score)
            home_ga += int(opp_score)
            home_gp += 1
        else:
            away_gf += int(our_score)
            away_ga += int(opp_score)
            away_gp += 1

        if our_score > opp_score:
            form.append("W")
        elif our_score < opp_score:
            form.append("L")
        else:
            form.append("D")

    return home_gp, home_gf, home_ga, away_gp, away_gf, away_ga, form[-5:]


# ─── Fuzzy matching ──────────────────────────────────────────────────

def _normalize(name: str) -> str:
    """Normalise un nom d'équipe pour le matching."""
    return (
        name.lower()
        .replace("fc ", "").replace(" fc", "")
        .replace("cf ", "").replace(" cf", "")
        .replace("sc ", "").replace(" sc", "")
        .replace("afc ", "").replace(" afc", "")
        .strip()
    )


def find_team_in_league(
    team_name: str,
    league_teams: dict[str, _LeagueTeamData],
) -> _LeagueTeamData | None:
    """
    Recherche une équipe par son nom dans les données ESPN d'une ligue.
    Utilise aliases, matching exact, inclusion, et fuzzy SequenceMatcher.
    """
    query = team_name.lower().strip()

    # 1. Exact match sur la clé (qui est déjà en lowercase)
    if query in league_teams:
        return league_teams[query]

    # 2. Alias
    alias = _TEAM_NAME_ALIASES.get(query)
    if alias and alias.lower() in league_teams:
        return league_teams[alias.lower()]

    # 3. Normalized match
    query_norm = _normalize(query)
    for key, td in league_teams.items():
        if _normalize(key) == query_norm:
            return td

    # 4. Inclusion (substring)
    for key, td in league_teams.items():
        if query_norm in _normalize(key) or _normalize(key) in query_norm:
            return td

    # 5. Fuzzy SequenceMatcher
    best_ratio = 0.0
    best_td = None
    for key, td in league_teams.items():
        ratio = SequenceMatcher(None, query_norm, _normalize(key)).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_td = td
    if best_ratio >= 0.65:
        return best_td

    # 6. Dernier mot
    last_word = query_norm.split()[-1] if query_norm.split() else ""
    if len(last_word) >= 4:
        for key, td in league_teams.items():
            if last_word in _normalize(key):
                return td

    return None


# ─── API principale : charger toutes les ligues ──────────────────────

def load_all_leagues(
    sport_keys: set[str] | None = None,
    with_home_away: bool = True,
) -> dict[str, dict[str, _LeagueTeamData]]:
    """
    Charge les standings (+ home/away optionnel) de toutes les ligues ESPN.
    
    Args:
        sport_keys: Filtrer par sport_key Odds API (ex: {"soccer_epl"}).
                    None = toutes les ligues.
        with_home_away: Si True, calcule home/away via schedule (plus lent).
    
    Returns:
        {espn_slug: {team_name_lower: _LeagueTeamData}}
    """
    # Essayer le cache
    cached = _load_cache()
    if cached:
        # Vérifier que les ligues demandées sont dans le cache
        needed_slugs = set()
        for sk in (sport_keys or ESPN_LEAGUE_MAP.keys()):
            slug = ESPN_LEAGUE_MAP.get(sk)
            if slug and slug not in ESPN_EURO_COMPS:
                needed_slugs.add(slug)
        if needed_slugs.issubset(set(cached.keys())):
            total = sum(len(t) for t in cached.values())
            console.print(f"  📦 Cache ESPN chargé ({total} équipes, {len(cached)} ligues)")
            return cached

    console.print("  🔄 Chargement des données ESPN (saison en cours)...")
    result: dict[str, dict[str, _LeagueTeamData]] = {}

    for sport_key, slug in ESPN_LEAGUE_MAP.items():
        if sport_keys and sport_key not in sport_keys:
            continue
        # Pas de standings pour les compétitions européennes
        if slug in ESPN_EURO_COMPS:
            continue

        teams = _fetch_standings(slug)
        if not teams:
            log.warning(f"ESPN: pas de données pour {slug}")
            continue

        if with_home_away:
            # Enrichir avec home/away depuis les schedules
            for key, td in teams.items():
                hgp, hgf, hga, agp, agf, aga, form = _compute_home_away(slug, td.espn_id)
                td.home_gp = hgp
                td.home_gf = hgf
                td.home_ga = hga
                td.away_gp = agp
                td.away_gf = agf
                td.away_ga = aga
                td.form = form
                time.sleep(0.05)  # être gentil avec ESPN

        result[slug] = teams
        n = len(teams)
        console.print(f"    ✅ {slug}: {n} équipes {'(+ home/away)' if with_home_away else ''}")

    if result:
        _save_cache(result)
        console.print(f"  💾 Cache ESPN sauvegardé ({_ESPN_CACHE_TTL // 3600}h TTL)")

    return result


# ─── Moyennes de ligue ────────────────────────────────────────────────

def compute_league_averages(
    league_teams: dict[str, _LeagueTeamData],
) -> tuple[float, float, float]:
    """
    Calcule les vraies moyennes de la ligue depuis les données ESPN.

    Returns:
        (avg_home_goals_per_match, avg_away_goals_per_match, avg_total)
    """
    total_home_gf = sum(td.home_gf for td in league_teams.values())
    total_home_gp = sum(td.home_gp for td in league_teams.values())
    total_away_gf = sum(td.away_gf for td in league_teams.values())
    total_away_gp = sum(td.away_gp for td in league_teams.values())

    avg_home = total_home_gf / total_home_gp if total_home_gp > 0 else 1.5
    avg_away = total_away_gf / total_away_gp if total_away_gp > 0 else 1.2

    return avg_home, avg_away, avg_home + avg_away


# ─── Stats Euro (UCL / Europa) depuis le schedule de la compétition ───

def compute_euro_team_stats(
    euro_slug: str,
    team_espn_id: str,
    team_name: str,
) -> _LeagueTeamData | None:
    """
    Calcule les stats d'une équipe dans la compétition européenne
    (UCL / Europa) via le schedule de l'équipe sur cette compétition.

    Retourne un _LeagueTeamData avec les stats H/A en compétition euro,
    ou None si pas de données.
    """
    url = (
        f"{_ESPN_BASE}/site/v2/sports/soccer/"
        f"{euro_slug}/teams/{team_espn_id}/schedule"
    )
    d = _get(url)
    if not d:
        return None

    events = d.get("events", [])
    gp = gf = ga = 0
    home_gp = home_gf = home_ga = 0
    away_gp = away_gf = away_ga = 0
    form: list[str] = []

    for e in events:
        competitions = e.get("competitions", [])
        if not competitions:
            continue
        comp = competitions[0]
        status_name = comp.get("status", {}).get("type", {}).get("name", "")
        if status_name != "STATUS_FULL_TIME":
            continue

        competitors = comp.get("competitors", [])
        if len(competitors) != 2:
            continue

        our_score = opp_score = None
        is_home = None

        for c in competitors:
            tid = str(c.get("team", {}).get("id", ""))
            score_val = c.get("score", {})
            if isinstance(score_val, dict):
                score = float(score_val.get("value", 0))
            else:
                score = float(score_val) if score_val else 0

            if tid == team_espn_id:
                our_score = score
                is_home = c.get("homeAway") == "home"
            else:
                opp_score = score

        if our_score is None or opp_score is None or is_home is None:
            continue

        gp += 1
        gf += int(our_score)
        ga += int(opp_score)

        if is_home:
            home_gf += int(our_score)
            home_ga += int(opp_score)
            home_gp += 1
        else:
            away_gf += int(our_score)
            away_ga += int(opp_score)
            away_gp += 1

        if our_score > opp_score:
            form.append("W")
        elif our_score < opp_score:
            form.append("L")
        else:
            form.append("D")

    if gp < 2:
        return None  # pas assez de matchs en compétition euro

    return _LeagueTeamData(
        espn_id=team_espn_id,
        espn_name=team_name,
        gp=gp, gf=gf, ga=ga,
        wins=sum(1 for f in form if f == "W"),
        draws=sum(1 for f in form if f == "D"),
        losses=sum(1 for f in form if f == "L"),
        points=0,
        home_gp=home_gp, home_gf=home_gf, home_ga=home_ga,
        away_gp=away_gp, away_gf=away_gf, away_ga=away_ga,
        form=form[-5:],
    )


def compute_euro_league_averages(
    euro_slug: str,
    team_ids: list[tuple[str, str]],
) -> tuple[float, float, float]:
    """
    Calcule les moyennes de buts en compétition européenne
    à partir des stats de toutes les équipes passées.

    Args:
        euro_slug: slug UEFA (ex: "uefa.champions")
        team_ids: liste de (espn_id, team_name)

    Returns:
        (avg_home, avg_away, avg_total)
    """
    total_home_gf = total_home_gp = 0
    total_away_gf = total_away_gp = 0

    for tid, tname in team_ids:
        td = compute_euro_team_stats(euro_slug, tid, tname)
        if td:
            total_home_gf += td.home_gf
            total_home_gp += td.home_gp
            total_away_gf += td.away_gf
            total_away_gp += td.away_gp

    avg_home = total_home_gf / total_home_gp if total_home_gp > 0 else 1.5
    avg_away = total_away_gf / total_away_gp if total_away_gp > 0 else 1.2

    return avg_home, avg_away, avg_home + avg_away


# ─── Conversion → TeamStats ──────────────────────────────────────────

def to_team_stats(
    td: _LeagueTeamData,
    is_home: bool,
) -> "TeamStats":
    """
    Convertit les données ESPN en TeamStats pour le modèle Poisson.
    
    Utilise le split home/away si disponible, sinon les totaux.
    """
    from betx.models.football_model import TeamStats

    if is_home and td.home_gp >= 3:
        avg_scored = td.home_gf / td.home_gp
        avg_conceded = td.home_ga / td.home_gp
    elif not is_home and td.away_gp >= 3:
        avg_scored = td.away_gf / td.away_gp
        avg_conceded = td.away_ga / td.away_gp
    else:
        # Fallback totaux si pas assez de matchs home/away
        avg_scored = td.gf / td.gp if td.gp > 0 else 1.2
        avg_conceded = td.ga / td.gp if td.gp > 0 else 1.2

    # Sécurité plancher
    avg_scored = max(avg_scored, 0.3)
    avg_conceded = max(avg_conceded, 0.3)

    return TeamStats(
        name=td.espn_name,
        avg_goals_scored=avg_scored,
        avg_goals_conceded=avg_conceded,
        xg_for=avg_scored,       # Proxy xG = buts réels
        xg_against=avg_conceded,
        elo=1500.0,              # Sera ajusté par predict_football()
        home_elo=1500.0,
        away_elo=1500.0,
        recent_form=td.form or [],
        rest_days=7,             # Non dispo via ESPN
        match_importance=1.0,
    )


# ═══════════════════════════════════════════════════════════════════════
# Fixtures + Cotes du jour  (scoreboard ESPN → DraftKings moneyline)
# ═══════════════════════════════════════════════════════════════════════

# Reverse map : ESPN slug → sport_key
_REVERSE_LEAGUE_MAP: dict[str, str] = {v: k for k, v in ESPN_LEAGUE_MAP.items()}

# Slugs des ligues principales (stats + standings)
ESPN_MAIN_LEAGUES: list[str] = ["eng.1", "esp.1", "ita.1", "ger.1", "fra.1"]

# Toutes les compétitions à scanner pour les fixtures
ESPN_FIXTURE_LEAGUES: list[str] = [
    "eng.1", "esp.1", "ita.1", "ger.1", "fra.1",
    "uefa.champions", "uefa.europa",
]

ESPN_LEAGUE_LABELS: dict[str, str] = {
    "eng.1": "⚽ Premier League",
    "esp.1": "⚽ La Liga",
    "ita.1": "⚽ Serie A",
    "ger.1": "⚽ Bundesliga",
    "fra.1": "⚽ Ligue 1",
    "ned.1": "⚽ Eredivisie",
    "por.1": "⚽ Primeira Liga",
    "uefa.champions": "🏆 Champions League",
    "uefa.europa": "🟠 Europa League",
}


def _us_to_decimal(ml: int | float | str) -> float:
    """Convertit moneyline US (+290 / -110 / EVEN) → cote décimale européenne."""
    ml_str = str(ml).strip().replace("+", "")
    if ml_str.upper() == "EVEN":
        return 2.0  # EVEN = +100 = cote 2.00
    try:
        ml_val = float(ml_str)
    except ValueError:
        return 1.0
    if ml_val > 0:
        return round(1 + ml_val / 100, 2)
    elif ml_val < 0:
        return round(1 + 100 / abs(ml_val), 2)
    return 1.0


@dataclass
class ESPNFixture:
    """Un match du jour avec cotes, extrait du scoreboard ESPN."""
    espn_slug: str           # "eng.1"
    sport_key: str           # "soccer_epl"
    league_label: str        # "⚽ Premier League"
    home_team: str
    away_team: str
    home_espn_id: str
    away_espn_id: str
    commence_time: str       # ISO datetime
    espn_event_id: str = "" # ID ESPN event (pour summary/H2H)
    # Cotes décimales (DraftKings)
    odds_home: float = 0.0
    odds_draw: float = 0.0
    odds_away: float = 0.0
    over_under: float = 0.0  # ligne O/U (ex: 2.5)
    odds_over: float = 0.0
    odds_under: float = 0.0
    bookmaker: str = "DraftKings"
    has_odds: bool = False


def _enrich_odds_from_pickcenter(fix: ESPNFixture) -> ESPNFixture:
    """
    Fallback : récupère les cotes 1X2 via summary/pickcenter.

    Nécessaire pour les compétitions européennes (UCL, Europa)
    où le scoreboard ne contient pas homeTeamOdds/awayTeamOdds.
    Coût : 1 requête HTTP supplémentaire par match.
    """
    url = (
        f"{_ESPN_BASE}/site/v2/sports/soccer/"
        f"{fix.espn_slug}/summary?event={fix.espn_event_id}"
    )
    sd = _get(url)
    if not sd:
        return fix

    for pc in sd.get("pickcenter", []):
        hto = pc.get("homeTeamOdds", {})
        ato = pc.get("awayTeamOdds", {})
        dto = pc.get("drawOdds", {})

        h_ml = hto.get("moneyLine") if isinstance(hto, dict) else None
        a_ml = ato.get("moneyLine") if isinstance(ato, dict) else None
        d_ml = dto.get("moneyLine") if isinstance(dto, dict) else dto if isinstance(dto, (int, float)) else None

        if h_ml is not None and a_ml is not None and d_ml is not None:
            fix.odds_home = _us_to_decimal(h_ml)
            fix.odds_away = _us_to_decimal(a_ml)
            fix.odds_draw = _us_to_decimal(d_ml)
            fix.has_odds = True
            fix.bookmaker = pc.get("provider", {}).get("name", "DraftKings")

            # Over/Under (si pas encore récupéré)
            if not fix.over_under:
                fix.over_under = float(pc.get("overUnder", 0) or 0)
            break

    return fix


def fetch_today_fixtures() -> list[ESPNFixture]:
    """
    Récupère les matchs du jour + cotes depuis ESPN scoreboard.

    - 100% gratuit, sans clé API
    - Cotes DraftKings (moneyline US → convertie en décimale)
    - 1 requête HTTP par ligue (5 ligues = 5 appels)

    Returns:
        Liste de ESPNFixture avec cotes pour les matchs non commencés.
    """
    fixtures: list[ESPNFixture] = []

    for slug in ESPN_FIXTURE_LEAGUES:
        url = f"{_ESPN_BASE}/site/v2/sports/soccer/{slug}/scoreboard"
        d = _get(url)
        if not d:
            continue

        sport_key = _REVERSE_LEAGUE_MAP.get(slug, f"soccer_{slug}")
        label = ESPN_LEAGUE_LABELS.get(slug, f"⚽ {slug}")

        for ev in d.get("events", []):
            comp = ev.get("competitions", [{}])[0]
            status = comp.get("status", {}).get("type", {}).get("name", "")
            # Ne garder que les matchs pas encore commencés
            if status != "STATUS_SCHEDULED":
                continue

            teams = comp.get("competitors", [])
            home = next((t for t in teams if t.get("homeAway") == "home"), {})
            away = next((t for t in teams if t.get("homeAway") == "away"), {})

            fix = ESPNFixture(
                espn_slug=slug,
                sport_key=sport_key,
                league_label=label,
                home_team=home.get("team", {}).get("displayName", ""),
                away_team=away.get("team", {}).get("displayName", ""),
                home_espn_id=str(home.get("team", {}).get("id", "")),
                away_espn_id=str(away.get("team", {}).get("id", "")),
                commence_time=comp.get("date", ""),
                espn_event_id=str(ev.get("id", "")),
            )

            # Parser les cotes depuis le scoreboard
            odds_list = comp.get("odds", [])
            if odds_list:
                o = odds_list[0]
                fix.bookmaker = o.get("provider", {}).get("name", "DraftKings")

                # Moneyline 1X2 (via scoreboard)
                ml = o.get("moneyline", {})
                h_close = ml.get("home", {}).get("close", {}).get("odds", "")
                a_close = ml.get("away", {}).get("close", {}).get("odds", "")
                d_ml = o.get("drawOdds", {}).get("moneyLine")

                if h_close and a_close and d_ml:
                    fix.odds_home = _us_to_decimal(h_close)
                    fix.odds_away = _us_to_decimal(a_close)
                    fix.odds_draw = _us_to_decimal(d_ml)
                    fix.has_odds = True

                # Over/Under
                fix.over_under = float(o.get("overUnder", 0) or 0)
                total = o.get("total", {})
                over_close = total.get("over", {}).get("close", {}).get("odds", "")
                under_close = total.get("under", {}).get("close", {}).get("odds", "")
                if over_close:
                    fix.odds_over = _us_to_decimal(over_close)
                if under_close:
                    fix.odds_under = _us_to_decimal(under_close)

            # ── Fallback pickcenter (UCL/Europa : cotes absentes du scoreboard) ──
            if not fix.has_odds and fix.espn_event_id:
                fix = _enrich_odds_from_pickcenter(fix)

            if fix.home_team and fix.away_team:
                fixtures.append(fix)

    return fixtures


# ═══════════════════════════════════════════════════════════════════════
# Match Context : H2H, Form, Classement, Pression
# (endpoint /summary?event=ID — 1 appel par match)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class _FormEvent:
    """Un match récent d'une équipe."""
    date: str            # "2026-02-22"
    result: str          # "W" / "D" / "L"
    is_home: bool
    score: str           # "2-1"
    opponent: str        # "Crystal Palace"
    league: str          # "Premier League"


@dataclass
class _TeamContext:
    """Contexte d'analyse d'une équipe."""
    name: str
    espn_id: str
    rank: int = 0            # Position au classement
    points: int = 0
    form_str: str = ""       # "WDLLW"
    form_events: list[_FormEvent] = field(default_factory=list)
    zone: str = ""           # "title", "europe", "mid", "relegation"
    pressure: float = 1.0    # Multiplicateur d'importance (1.0–1.3)


@dataclass
class MatchContext:
    """Contexte complet d'un match : H2H, forme, classement, pression."""
    home: _TeamContext
    away: _TeamContext
    # H2H (saison en cours)
    h2h_games: list[dict] = field(default_factory=list)  # [{date, score, home_team, away_team, winner}]
    h2h_summary: str = ""    # "1V 0N 0D" résumé
    # Nombre d'équipes dans la ligue (pour calculer zones)
    league_size: int = 20


def _classify_zone(rank: int, league_size: int) -> tuple[str, float]:
    """
    Détermine la zone et le facteur de pression au classement.

    Returns:
        (zone_label, pressure_multiplier)
    """
    if league_size <= 0:
        return "mid", 1.0

    pct = rank / league_size  # 0.05 = 1st/20, 1.0 = 20th/20

    if rank <= 1:
        return "🏆 leader", 1.20
    elif pct <= 0.20:  # Top 4 for 20-team league
        return "🌟 title race", 1.15
    elif pct <= 0.35:  # 5th-7th = Europe
        return "🇪🇺 europe", 1.10
    elif pct >= 0.90:  # Bottom 2 = direct relegation
        return "🔴 relégation", 1.25
    elif pct >= 0.80:  # 17th = barrage
        return "🟠 barrage", 1.15
    elif pct >= 0.70:
        return "🟡 menacé", 1.05
    else:
        return "⚪ mid-table", 1.0


def _parse_form_events(team_id: str, events: list[dict]) -> list[_FormEvent]:
    """Parse les events de forme depuis boxscore.form."""
    result: list[_FormEvent] = []
    for e in events:
        opp = e.get("opponent", {})
        opp_name = opp.get("displayName", "?") if isinstance(opp, dict) else str(opp)
        home_id = str(e.get("homeTeamId", ""))
        is_home = home_id == str(team_id)
        result.append(_FormEvent(
            date=e.get("gameDate", "")[:10],
            result=e.get("gameResult", "?"),
            is_home=is_home,
            score=e.get("score", "?"),
            opponent=opp_name,
            league=e.get("leagueAbbreviation", ""),
        ))
    return result


def fetch_match_context(fixture: ESPNFixture) -> MatchContext | None:
    """
    Récupère le contexte complet d'un match via ESPN /summary.

    Données récupérées :
    - H2H saison en cours (headToHeadGames)
    - Forme détaillée (5 derniers matchs avec adversaire + score)
    - Position au classement + zone (title/europe/mid/relegation)
    - Facteur de pression (motivation)

    Coût : 1 requête HTTP par match.
    """
    if not fixture.espn_event_id:
        return None

    url = (
        f"{_ESPN_BASE}/site/v2/sports/soccer/"
        f"{fixture.espn_slug}/summary?event={fixture.espn_event_id}"
    )
    sd = _get(url)
    if not sd:
        return None

    home_id = fixture.home_espn_id
    away_id = fixture.away_espn_id

    # ─── Standings / Rank ───
    rank_map: dict[str, tuple[int, int]] = {}  # id → (rank, points)
    league_size = 20
    groups = sd.get("standings", {}).get("groups", [])
    if groups:
        entries = groups[0].get("standings", {}).get("entries", [])
        league_size = len(entries)
        for entry in entries:
            eid = str(entry.get("id", ""))
            stats_d = {s["name"]: s.get("value", 0) for s in entry.get("stats", [])}
            rank_map[eid] = (int(stats_d.get("rank", 0)), int(stats_d.get("points", 0)))

    home_rank, home_pts = rank_map.get(home_id, (0, 0))
    away_rank, away_pts = rank_map.get(away_id, (0, 0))
    home_zone, home_pressure = _classify_zone(home_rank, league_size)
    away_zone, away_pressure = _classify_zone(away_rank, league_size)

    # ─── Forme détaillée (boxscore.form) ───
    home_form_events: list[_FormEvent] = []
    away_form_events: list[_FormEvent] = []
    home_form_str = ""
    away_form_str = ""

    for f in sd.get("boxscore", {}).get("form", []):
        tid = str(f.get("team", {}).get("id", ""))
        events = f.get("events", [])
        if tid == home_id:
            home_form_events = _parse_form_events(tid, events)
            home_form_str = "".join(fe.result for fe in home_form_events)
        elif tid == away_id:
            away_form_events = _parse_form_events(tid, events)
            away_form_str = "".join(fe.result for fe in away_form_events)

    # ─── H2H ───
    h2h_games: list[dict] = []
    h2h_raw = sd.get("headToHeadGames", [])
    # headToHeadGames peut contenir des teams (structure variable)
    # Vérifier s'il y a des competitions à l'intérieur
    for g in h2h_raw:
        comps = g.get("competitions", [])
        if comps:
            for comp in comps:
                teams_c = comp.get("competitors", [])
                if len(teams_c) == 2:
                    t1, t2 = teams_c[0], teams_c[1]
                    h2h_games.append({
                        "date": comp.get("date", "")[:10],
                        "home_team": t1.get("team", {}).get("displayName", ""),
                        "away_team": t2.get("team", {}).get("displayName", ""),
                        "home_score": t1.get("score", "?"),
                        "away_score": t2.get("score", "?"),
                        "winner": "home" if t1.get("winner") else "away" if t2.get("winner") else "draw",
                    })

    # Résumé H2H
    h2h_home_wins = sum(1 for g in h2h_games
                        if (g["winner"] == "home" and g["home_team"] == fixture.home_team)
                        or (g["winner"] == "away" and g["away_team"] == fixture.home_team))
    h2h_away_wins = sum(1 for g in h2h_games
                        if (g["winner"] == "home" and g["home_team"] == fixture.away_team)
                        or (g["winner"] == "away" and g["away_team"] == fixture.away_team))
    h2h_draws = len(h2h_games) - h2h_home_wins - h2h_away_wins
    h2h_summary = f"{h2h_home_wins}V {h2h_draws}N {h2h_away_wins}D" if h2h_games else "pas de H2H"

    return MatchContext(
        home=_TeamContext(
            name=fixture.home_team,
            espn_id=home_id,
            rank=home_rank,
            points=home_pts,
            form_str=home_form_str,
            form_events=home_form_events,
            zone=home_zone,
            pressure=home_pressure,
        ),
        away=_TeamContext(
            name=fixture.away_team,
            espn_id=away_id,
            rank=away_rank,
            points=away_pts,
            form_str=away_form_str,
            form_events=away_form_events,
            zone=away_zone,
            pressure=away_pressure,
        ),
        h2h_games=h2h_games,
        h2h_summary=h2h_summary,
        league_size=league_size,
    )
