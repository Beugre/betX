#!/usr/bin/env python3
"""Debug: vérifie les stats et le calcul du modèle pour Everton vs Man Utd."""
import json, sys, math
sys.path.insert(0, ".")

cache = json.loads(open("data/cache/api_football_stats.json").read())
st = cache.get("stats", {})

# Stats saison 2024 (la seule dispo sur Free plan)
for key, label in [("45_39_2024", "EVERTON"), ("33_39_2024", "MAN UTD")]:
    entry = st.get(key, {})
    if not entry:
        print(f"{label}: PAS EN CACHE")
        continue
    team = entry.get("team", {})
    goals = entry.get("goals", {})
    gf = goals.get("for", {}).get("average", {})
    ga = goals.get("against", {}).get("average", {})
    form = (entry.get("form", "") or "")[-10:]
    
    print(f"=== {label} ({team.get('name', '?')}) ===")
    print(f"  Scored:   home={gf.get('home')}, away={gf.get('away')}, total={gf.get('total')}")
    print(f"  Conceded: home={ga.get('home')}, away={ga.get('away')}, total={ga.get('total')}")
    print(f"  Form (last 10): {form}")
    
    # Goal Difference
    scored_h = float(gf.get('home', 0) or 0)
    scored_a = float(gf.get('away', 0) or 0)
    conc_h = float(ga.get('home', 0) or 0)
    conc_a = float(ga.get('away', 0) or 0)
    gd = scored_h + scored_a - conc_h - conc_a
    print(f"  GD (approx): {gd:+.1f}")
    print()

# Maintenant simuler le modèle
print("=" * 60)
print("SIMULATION MODELE")
print("=" * 60)

from betx.models.football_model import FootballModel, TeamStats

ev_entry = st.get("45_39_2024", {})
mu_entry = st.get("33_39_2024", {})

def build_stats(entry, name, is_home):
    goals = entry.get("goals", {})
    gf = goals.get("for", {}).get("average", {})
    ga = goals.get("against", {}).get("average", {})
    if is_home:
        scored = float(gf.get("home", 0) or 0) or 1.2
        conceded = float(ga.get("home", 0) or 0) or 1.2
    else:
        scored = float(gf.get("away", 0) or 0) or 1.2
        conceded = float(ga.get("away", 0) or 0) or 1.2
    form_str = (entry.get("form", "") or "")[-5:]
    return TeamStats(
        name=name,
        avg_goals_scored=scored,
        avg_goals_conceded=conceded,
        xg_for=scored,
        xg_against=conceded,
        recent_form=list(form_str),
        rest_days=7,
    )

ev_stats = build_stats(ev_entry, "Everton", is_home=True)
mu_stats = build_stats(mu_entry, "Manchester United", is_home=False)

# ELO depuis GD (comme le modèle le fait)
home_gd = ev_stats.avg_goals_scored - ev_stats.avg_goals_conceded
away_gd = mu_stats.avg_goals_scored - mu_stats.avg_goals_conceded
gd_diff = home_gd - away_gd

ev_stats.elo = 1500 + gd_diff * 100
ev_stats.home_elo = ev_stats.elo + 50
mu_stats.elo = 1500 - gd_diff * 100
mu_stats.away_elo = mu_stats.elo

print(f"\nEverton (home): scored={ev_stats.avg_goals_scored}, conceded={ev_stats.avg_goals_conceded}")
print(f"  GD home = {home_gd:+.2f}")
print(f"  Form: {ev_stats.recent_form}")
print(f"  ELO: {ev_stats.elo:.0f} (home: {ev_stats.home_elo:.0f})")

print(f"\nMan Utd (away): scored={mu_stats.avg_goals_scored}, conceded={mu_stats.avg_goals_conceded}")
print(f"  GD away = {away_gd:+.2f}")
print(f"  Form: {mu_stats.recent_form}")
print(f"  ELO: {mu_stats.elo:.0f} (away: {mu_stats.away_elo:.0f})")

model = FootballModel()
pred = model.predict(ev_stats, mu_stats)

print(f"\nλ_home = {pred.lambda_home:.3f}")
print(f"λ_away = {pred.lambda_away:.3f}")
print(f"\nP(Everton) = {pred.p_home:.1%}")
print(f"P(Draw)    = {pred.p_draw:.1%}")
print(f"P(Man Utd) = {pred.p_away:.1%}")

# Calibration
CALIB = 0.97
p_cal = 0.5 + (pred.p_home - 0.5) * CALIB
print(f"\nAprès calibration (0.97): P(Everton) = {p_cal:.1%}")

# Bookmaker
odds_home = 3.98
implied = 1.0 / odds_home
edge = p_cal - implied
print(f"\nCote bookmaker: {odds_home}")
print(f"P(implicite): {implied:.1%}")
print(f"Edge: {edge:+.1%}")
print(f"\n{'⚠️  PROBLÈME' if edge > 0.25 else '✅ OK'}: edge de {edge:.1%} {'est anormalement élevé!' if edge > 0.25 else ''}")
