# betX – Système d'Analyse de Paris Sportifs

## 🎯 Objectif

Système automatisé d'analyse de paris sportifs multi-sports (⚽ Football, 🎾 Tennis, 🏀 Basket) avec :
- Collecte de données + cotes
- Estimation de probabilités réelles
- Identification de value bets
- Optimisation de mises (Kelly fractionné)
- Suivi de performance (ROI, CLV, drawdown)

> ⚠️ Le système **ne place pas** les paris. L'exécution reste manuelle.

## 📦 Installation

```bash
# Créer un environnement virtuel
python3.11 -m venv .venv
source .venv/bin/activate

# Installer les dépendances
pip install -e ".[dev]"

# Configurer l'environnement
cp .env.example .env
# Éditer .env avec vos clés API
```

## 🏗 Architecture

```
betX/
├── config/          # Configuration globale
├── data/            # Collecte, nettoyage, normalisation
├── models/          # Modèles statistiques par sport
├── engine/          # Value betting + staking
├── backtest/        # Backtesting walk-forward
├── analytics/       # KPIs, CLV, performance
├── dashboard/       # Interface Streamlit
├── database/        # ORM + migrations
├── pipeline/        # Pipeline quotidien
└── tests/           # Tests unitaires
```

## 🚀 Utilisation

```bash
# Lancer le pipeline quotidien
python -m betx.pipeline.daily

# Lancer le dashboard
streamlit run betx/dashboard/app.py

# Lancer le backtest
python -m betx.backtest.backtester

# Lancer les tests
pytest

# Benchmark des sites de pronostics externes (avec historique)
python -m betx --site-benchmark --history-days 60

# Lancer le scheduler auto (refresh + grading)
python -m betx --benchmark-scheduler
```

## 📊 Sports & Marchés

| Sport | Marchés |
|-------|---------|
| ⚽ Football | 1X2, Over/Under 2.5/3.5, BTTS, Asian Handicap |
| 🎾 Tennis | Match Winner, Handicap jeux, Over/Under jeux |
| 🏀 Basket | Moneyline, Handicap points, Over/Under total |

## 📈 Objectif Performance

- Edge moyen : 4–6%
- Volume : 60–150 bets/mois
- Objectif mensuel : **+20% bankroll**
- Méthode de staking : Kelly fractionné (0.25)

## ⚠️ Avertissement

Les paris sportifs comportent des risques. Ce système est un outil d'aide à la décision.
Même avec un edge réel, la variance peut entraîner des pertes sur 2–3 mois consécutifs.

## 🔥 Firebase (coût optimisé)

Si vous utilisez Firebase pour exposer un snapshot dashboard, appliquez la stratégie
`docs/FIREBASE_FREE_TIER.md` pour rester en coût nul (Spark) et déporter l'historique sur VPS.
