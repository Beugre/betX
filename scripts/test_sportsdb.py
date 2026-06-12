"""
betX — Test TheSportsDB pour données récentes 2025-2026.
Usage: python /tmp/betx_sportsdb_test.py
"""
import httpx, json

BASE = "https://www.thesportsdb.com/api/v1/json/3"

# Équipes CdM à tester (IDs TheSportsDB)
# On cherche d'abord les IDs
TEAMS_TO_TEST = ["France", "Mexico", "South Africa", "Argentina", "Ecuador", "Ivory Coast"]

print("=== Recherche IDs équipes ===")
team_ids = {}
for name in TEAMS_TO_TEST:
    r = httpx.get(f"{BASE}/searchteams.php", params={"t": name}, timeout=10)
    teams = r.json().get("teams") or []
    # Filtrer équipes nationales de football
    nat = [t for t in teams if t.get("strSport") == "Soccer" and t.get("strCountry")]
    if nat:
        t = nat[0]
        team_ids[name] = t["idTeam"]
        print(f"  {name}: {t['idTeam']} ({t.get('strTeam')})")

print("\n=== Matchs récents (eventslast) ===")
for name, tid in team_ids.items():
    r = httpx.get(f"{BASE}/eventslast.php", params={"id": tid}, timeout=10)
    events = r.json().get("results") or []
    print(f"\n{name}: {len(events)} matchs récents")
    for e in events[:5]:
        ts = (e.get("strTimestamp") or e.get("dateEvent") or "")[:10]
        evt = e.get("strEvent", "")
        hs = e.get("intHomeScore", "?")
        aws = e.get("intAwayScore", "?")
        league = e.get("strLeague", "")
        print(f"  {ts} | {evt} | {hs}-{aws} | {league}")

print("\n=== Saison 2025-2026 ===")
for name, tid in list(team_ids.items())[:3]:
    r = httpx.get(f"{BASE}/eventsseason.php", params={"id": tid, "s": "2025-2026"}, timeout=10)
    events = r.json().get("results") or []
    done = [e for e in events if e.get("intHomeScore") is not None]
    print(f"\n{name} 2025-2026: {len(events)} matchs ({len(done)} avec scores)")
    for e in done[:5]:
        ts = (e.get("strTimestamp") or e.get("dateEvent") or "")[:10]
        evt = e.get("strEvent", "")
        hs = e.get("intHomeScore", "?")
        aws = e.get("intAwayScore", "?")
        league = e.get("strLeague", "")
        print(f"  {ts} | {evt} | {hs}-{aws} | {league}")
