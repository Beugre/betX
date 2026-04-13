"""Affiche les données du dernier scan depuis le JSON, classées par P(modèle)."""
import json
from pathlib import Path

data = json.load(open(Path(__file__).parent / "data" / "daily_bets.json"))
bets = data["bets"]
bets.sort(key=lambda b: b["model_prob"], reverse=True)

BT = {"away": "🟢", "draw": "🟢", "home": "🟡"}

print(f'\n📊 Données du scan: {data["scan_time"]} — {len(bets)} value bets\n')
print(f'{"#":>3} | {"Match":<42} | {"Sél.":<12} | {"BT":^4} | {"P(modèle)":>10} | {"Cote":>6} | {"Edge":>8} | {"Mise":>8} | {"Gain est.":>10} | {"Bookmaker":<18}')
print("-" * 150)

tm, tg = 0.0, 0.0
for i, b in enumerate(bets, 1):
    m = f"{b['home_team']} vs {b['away_team']}"[:40]
    g = b["stake"] * (b["odds"] - 1)
    tm += b["stake"]
    tg += g
    bt = BT.get(b["selection"], "  ")
    print(
        f"{i:>3} | {m:<42} | {b['selection'].title():<12} | {bt:^4} | "
        f"{b['model_prob']:>9.1%} | {b['odds']:>6.2f} | "
        f"{b['edge']:>7.1%} | {b['stake']:>6.0f}€ | "
        f"+{g:>8.1f}€ | {b['bookmaker']:<18}"
    )

print("=" * 150)
print(
    f"\nTop {len(bets)} paris classés par P(modèle) ↓ | "
    f"Mise: {tm:.0f}€ | Gain: +{tg:.0f}€ | Retour: {tm + tg:.0f}€"
)
