"""
betX – Point d'entrée principal.

Usage:
    python -m betx              # Lance le pipeline quotidien
    python -m betx --dashboard  # Lance le dashboard Streamlit
    python -m betx --backtest   # Lance le backtesting
    python -m betx --init-db    # Initialise la base de données
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description="betX – Système d'analyse de paris sportifs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  python -m betx                  # Pipeline quotidien
  python -m betx --dashboard      # Dashboard Streamlit
  python -m betx --init-db        # Créer les tables
  python -m betx --backtest       # Lancer le backtesting
        """,
    )

    parser.add_argument("--dashboard", action="store_true", help="Lance le dashboard Streamlit")
    parser.add_argument("--backtest", action="store_true", help="Lance le backtesting")
    parser.add_argument("--init-db", action="store_true", help="Initialise la base de données")
    parser.add_argument("--site-benchmark", action="store_true", help="Benchmark des sites de pronostics")
    parser.add_argument("--history-days", type=int, default=30, help="Historique en jours pour benchmark")
    parser.add_argument("--benchmark-scheduler", action="store_true", help="Lance le scheduler benchmark")
    parser.add_argument("--date", type=str, help="Date cible (YYYY-MM-DD)", default=None)

    args = parser.parse_args()

    if args.init_db:
        from betx.database import init_db
        print("🗄  Initialisation de la base de données...")
        init_db()
        print("✅ Base de données initialisée avec succès.")
        return

    if args.dashboard:
        import subprocess
        from pathlib import Path

        app_path = Path(__file__).parent / "dashboard" / "app.py"
        print(f"🚀 Lancement du dashboard Streamlit...")
        subprocess.run([sys.executable, "-m", "streamlit", "run", str(app_path)])
        return

    if args.backtest:
        print("🧪 Module de backtesting")
        print("   Utilisez: python -m betx.backtest.backtester")
        return

    if args.site_benchmark:
        from betx.pipeline.site_benchmark import SiteBenchmarkPipeline

        pipeline = SiteBenchmarkPipeline()
        summary = pipeline.run(history_days=args.history_days)
        print("🌐 Benchmark sites terminé")
        print(summary)
        return

    if args.benchmark_scheduler:
        from betx.pipeline.benchmark_scheduler import BenchmarkScheduler

        BenchmarkScheduler().run()
        return

    # Par défaut : pipeline quotidien
    from betx.pipeline.daily import DailyPipeline
    from datetime import date

    target = date.fromisoformat(args.date) if args.date else date.today()
    pipeline = DailyPipeline()
    pipeline.run(target)


if __name__ == "__main__":
    main()
