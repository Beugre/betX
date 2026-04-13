#!/usr/bin/env python3
"""
betX – Backtest Historique

Backteste le système de value betting sur les matchs terminés
de la saison en cours (données API-Football).

Usage :
    python run_backtest.py                    # 90 jours, toutes ligues
    python run_backtest.py --days 30          # 30 derniers jours
    python run_backtest.py --days 180         # 6 mois
    python run_backtest.py --edge 0.05        # Edge minimum 5%
    python run_backtest.py --bankroll 5000    # Bankroll 5000€
    python run_backtest.py --best-only        # 1 seul pari par match
"""

import argparse
import logging
import sys

# Supprimer les logs verbeux pour un affichage propre
logging.disable(logging.WARNING)

from betx.pipeline.backtest_scan import run_backtest, display_backtest_results


def main():
    parser = argparse.ArgumentParser(
        description="betX – Backtest historique du système de value betting"
    )
    parser.add_argument(
        "--days", type=int, default=90,
        help="Nombre de jours de recul (défaut: 90)"
    )
    parser.add_argument(
        "--bankroll", type=float, default=1000.0,
        help="Bankroll initiale en € (défaut: 1000)"
    )
    parser.add_argument(
        "--edge", type=float, default=0.03,
        help="Edge minimum pour parier (défaut: 0.03 = 3%%)"
    )
    parser.add_argument(
        "--min-matches", type=int, default=5,
        help="Matchs minimum par équipe avant de parier (défaut: 5)"
    )
    parser.add_argument(
        "--kelly", type=float, default=0.25,
        help="Fraction Kelly (défaut: 0.25 = quart-Kelly)"
    )
    parser.add_argument(
        "--max-stake", type=float, default=0.03,
        help="Mise max en %% de la bankroll (défaut: 0.03 = 3%%)"
    )
    parser.add_argument(
        "--best-only", action="store_true",
        help="Un seul pari par match (meilleur edge)"
    )
    parser.add_argument(
        "--markets", nargs="*", default=None,
        choices=["1x2", "over"],
        help="Marchés autorisés : 1x2 (Home/Draw/Away), over (Over/Under)"
    )

    args = parser.parse_args()

    result = run_backtest(
        days_back=args.days,
        initial_bankroll=args.bankroll,
        min_edge=args.edge,
        min_team_matches=args.min_matches,
        kelly_fraction=args.kelly,
        max_stake_pct=args.max_stake,
        best_bet_only=args.best_only,
        markets=args.markets,
    )

    if result:
        display_backtest_results(result)
    else:
        print("❌ Le backtest n'a pas pu s'exécuter.")
        sys.exit(1)


if __name__ == "__main__":
    main()
