#!/usr/bin/env python3
"""Vérifie quelle saison on utilise réellement et ce que l'API permet."""
import httpx, json, time

KEY = "00cf09cd1be2036d506aeaa696cfb657"
H = {"x-apisports-key": KEY}
BASE = "https://v3.football.api-sports.io"

# 1. Quelles saisons sont dispo pour la Premier League ?
print("=== Saisons disponibles pour la EPL (league 39) ===")
r = httpx.get(f"{BASE}/leagues", params={"id": 39}, headers=H, timeout=15)
d = r.json()
seasons = d.get("response", [{}])[0].get("seasons", [])
for s in seasons[-5:]:  # 5 dernières
    year = s["year"]
    start = s.get("start", "?")
    end = s.get("end", "?")
    current = s.get("coverage", {})
    is_current = "✅ CURRENT" if s.get("current") else ""
    print(f"  Season {year}: {start} → {end} {is_current}")
print()

# 2. Tester explicitement les stats saison 2025 (Everton)
time.sleep(7)
print("=== Test stats Everton saison 2025 ===")
r2 = httpx.get(f"{BASE}/teams/statistics", params={"team": 45, "league": 39, "season": 2025}, headers=H, timeout=15)
d2 = r2.json()
print(f"Errors: {d2.get('errors', {})}")
resp2 = d2.get("response", {})
if resp2:
    league = resp2.get("league", {})
    print(f"League: {league.get('name')} saison {league.get('season')}")
    goals = resp2.get("goals", {})
    gf = goals.get("for", {}).get("average", {})
    print(f"Goals scored: {gf}")
    form = resp2.get("form", "")
    print(f"Form: {form}")
print()

# 3. Comparer avec saison 2024
time.sleep(7)
print("=== Stats Everton saison 2024 (celle qu'on utilise) ===")
r3 = httpx.get(f"{BASE}/teams/statistics", params={"team": 45, "league": 39, "season": 2024}, headers=H, timeout=15)
d3 = r3.json()
print(f"Errors: {d3.get('errors', {})}")
resp3 = d3.get("response", {})
if resp3:
    league = resp3.get("league", {})
    print(f"League: {league.get('name')} saison {league.get('season')}")
    goals = resp3.get("goals", {})
    gf = goals.get("for", {}).get("average", {})
    ga = goals.get("against", {}).get("average", {})
    print(f"Goals scored: {gf}")
    print(f"Goals conceded: {ga}")
    form = resp3.get("form", "")
    fixtures = resp3.get("fixtures", {})
    played = fixtures.get("played", {})
    print(f"Form: {form}")
    print(f"Played: {played}")
    print(f"Fixtures total: {fixtures}")
print()

# 4. Tester Man Utd aussi sur 2025
time.sleep(7)
print("=== Stats Man Utd saison 2025 ===")
r4 = httpx.get(f"{BASE}/teams/statistics", params={"team": 33, "league": 39, "season": 2025}, headers=H, timeout=15)
d4 = r4.json()
print(f"Errors: {d4.get('errors', {})}")
resp4 = d4.get("response", {})
if resp4:
    league = resp4.get("league", {})
    print(f"League: {league.get('name')} saison {league.get('season')}")
    goals = resp4.get("goals", {})
    print(f"Goals scored: {goals.get('for', {}).get('average', {})}")
    form = resp4.get("form", "")
    print(f"Form: {form}")
print()

# 5. Quota restant
time.sleep(7)
r5 = httpx.get(f"{BASE}/status", headers=H, timeout=15)
d5 = r5.json()
print(f"Quota: {d5.get('response', {}).get('requests', {})}")
