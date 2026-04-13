#!/usr/bin/env python3
"""
betX – Top 30 Value Bets du Jour

Classement par probabilité de réussite (P modèle) décroissante.
Règles issues du backtest 2024-25 (1385 matchs, 180 jours) :
  ✅ Marchés 1X2 uniquement (Over/Under exclu = yield -20%)
  ✅ Edge minimum 8%
  ✅ 1 seul pari par match (meilleur edge)
  ✅ Calibration Poisson -15% (shrinkage overconfidence)
  ✅ Away/Draw = signaux les plus rentables (yield +28-30%)
"""
import logging
logging.disable(logging.WARNING)

from betx.pipeline.quick_scan import quick_scan

results = quick_scan(sports=["football"])

# Trier par probabilité modèle décroissante (meilleure proba de réussite en premier)
results.sort(key=lambda x: x[0].model_probability, reverse=True)

# Limiter à 30
results = results[:30]

# Signal backtest par sélection
BT_SIGNAL = {"away": "🟢", "draw": "🟢", "home": "🟡"}

print()
print("=" * 150)
header = (
    f"{'#':>3} | {'Match':<42} | {'Sélection':<12} | {'BT':^4} | {'P(modèle)':>10} | "
    f"{'Cote':>6} | {'Edge':>8} | {'Mise':>8} | {'Gain est.':>10} | {'Bookmaker':<18}"
)
print(header)
print("-" * 150)

total_mise = 0.0
total_gain = 0.0

for i, (vb, stake) in enumerate(results, 1):
    match_name = f"{vb.home_team} vs {vb.away_team}"
    if len(match_name) > 40:
        match_name = match_name[:40]
    sel = vb.selection.replace("_", " ").title()
    bt = BT_SIGNAL.get(vb.selection, "  ")
    gain = stake.stake_amount * (vb.bookmaker_odds - 1)
    total_mise += stake.stake_amount
    total_gain += gain

    print(
        f"{i:>3} | {match_name:<42} | {sel:<12} | {bt:^4} | "
        f"{vb.model_probability:>9.1%} | {vb.bookmaker_odds:>6.2f} | "
        f"{vb.edge:>7.1%} | {stake.stake_amount:>6.0f}€ | "
        f"+{gain:>8.1f}€ | {vb.bookmaker:<18}"
    )

print("=" * 150)
print(
    f"\nTop {len(results)} paris (1x2 only, 1/match, classés par P modèle) | "
    f"Mise totale: {total_mise:.0f}€ | "
    f"Gain potentiel: +{total_gain:.0f}€ | "
    f"Retour si tout passe: {total_mise + total_gain:.0f}€"
)
print("\n🔬 Backtest 2024-25 : +4.76% yield │ Sharpe 1.48 │ 🟢 Away/Draw = high yield │ 🟡 Home = prudence")
