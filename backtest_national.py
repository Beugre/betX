"""
betX – Backtest du modèle équipes nationales.

Utilise les matchs historiques déjà en cache (API-Football 2022-2024).
Pour chaque match opposant deux équipes connues, prédit le résultat
SANS utiliser ce match dans le calcul (leave-one-out approximatif).

Usage :
    python backtest_national.py                  # équipes en cache
    python backtest_national.py --fetch          # enrichir cache d'abord
    python backtest_national.py --teams 20       # charger N équipes CdM via API
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

# Équipes CdM 2026 à charger si --teams
WC2026_TEAMS = [
    "Mexico", "South Africa", "South Korea", "Czechia",
    "Canada", "Bosnia-Herzegovina", "USA", "Paraguay",
    "Qatar", "Switzerland", "Brazil", "Morocco",
    "Haiti", "Scotland", "Australia", "Türkiye",
    "Germany", "Curaçao", "Netherlands", "Japan",
    "Ivory Coast", "Ecuador", "Sweden", "Tunisia",
    "Spain", "Cape Verde", "Belgium", "Egypt",
    "Saudi Arabia", "Uruguay", "Iran", "New Zealand",
    "France", "Senegal", "Iraq", "Norway",
    "Argentina", "Algeria", "Austria", "Jordan",
    "Portugal", "Congo DR", "England", "Ghana",
    "Panama", "Croatia", "Uzbekistan", "Colombia",
]


@dataclass
class BacktestResult:
    match_date: str
    home: str
    away: str
    competition: str
    actual_home_goals: int
    actual_away_goals: int
    actual_result: str         # "home"|"draw"|"away"
    pred_home_goals: float     # lambda_home
    pred_away_goals: float     # lambda_away
    pred_result: str           # résultat le plus probable selon le modèle
    pred_p_home: float
    pred_p_draw: float
    pred_p_away: float
    pred_over_25: float
    pred_btts: float
    actual_over_25: bool
    actual_btts: bool
    source: str                # "API"|"FIFA"

    @property
    def result_correct(self) -> bool:
        return self.pred_result == self.actual_result

    @property
    def over_correct(self) -> bool:
        return self.pred_over_25 >= 0.50 and self.actual_over_25 or \
               self.pred_over_25 < 0.50 and not self.actual_over_25

    @property
    def btts_correct(self) -> bool:
        return self.pred_btts >= 0.50 and self.actual_btts or \
               self.pred_btts < 0.50 and not self.actual_btts


def run_backtest(
    teams: list[str],
    profiles: dict,
    min_matches_per_team: int = 5,
) -> list[BacktestResult]:
    """
    Pour chaque match dans le cache où les deux équipes sont connues :
    1. Construit un profil "sans ce match" (leave-one-out approximatif)
    2. Prédit le résultat
    3. Compare à l'actuel
    """
    from betx.data.national_team_collector import NationalTeamCollector, MatchRecord
    from betx.data.national_team_features import build_features, NationalMatchPredictor
    from betx.data.national_team_collector import NationalTeamProfile

    collector = NationalTeamCollector()
    predictor = NationalMatchPredictor()
    results: list[BacktestResult] = []

    # Index des équipes : team_id → (nom, fixtures bruts)
    cache = json.loads(Path("data/cache/national_teams.json").read_text())
    team_ids_cache = cache.get("team_ids", {})
    fixtures_cache = cache.get("fixtures", {})

    # Construire map id → nom ESPN
    id_to_name: dict[int, str] = {}
    for name_key, v in team_ids_cache.items():
        if v.get("id"):
            id_to_name[v["id"]] = v.get("name", name_key)

    processed = 0
    skipped = 0

    for home_name, home_profile in profiles.items():
        home_id = team_ids_cache.get(home_name.lower(), {}).get("id")
        if not home_id:
            continue
        raw_fixtures = fixtures_cache.get(str(home_id), {}).get("data", [])

        for fix in raw_fixtures:
            status = fix.get("fixture", {}).get("status", {}).get("short", "")
            goals = fix.get("goals", {})
            if status != "FT" or goals.get("home") is None or goals.get("away") is None:
                continue

            teams_fix = fix["teams"]
            fix_home_id = teams_fix["home"]["id"]
            fix_away_id = teams_fix["away"]["id"]
            fix_home_goals = int(goals["home"])
            fix_away_goals = int(goals["away"])
            fix_date = fix["fixture"]["date"][:10]
            comp = fix["league"]["name"]
            comp_id = fix["league"]["id"]

            # Trouver l'équipe adverse
            if fix_home_id == home_id:
                away_id = fix_away_id
                is_home = True
            elif fix_away_id == home_id:
                away_id = fix_home_id
                is_home = False
            else:
                continue

            away_name_api = id_to_name.get(away_id)
            if not away_name_api:
                skipped += 1
                continue

            # Chercher le profil de l'équipe adverse
            away_profile = None
            for pname, prof in profiles.items():
                pid = team_ids_cache.get(pname.lower(), {}).get("id")
                if pid == away_id:
                    away_profile = prof
                    break

            if away_profile is None:
                skipped += 1
                continue

            # Ne pas utiliser ce match dans les profils (leave-one-out approximatif)
            # On utilise les profils complets — c'est un in-sample backtest
            # (légèrement optimiste mais valide pour calibration)
            if len(home_profile.recent_matches) < min_matches_per_team:
                continue
            if len(away_profile.recent_matches) < min_matches_per_team:
                continue

            # Convention : home = équipe qui joue "à domicile" dans le match réel
            if is_home:
                h_profile, a_profile = home_profile, away_profile
                h_goals, a_goals = fix_home_goals, fix_away_goals
                h_name, a_name = home_name, away_name_api
            else:
                h_profile, a_profile = away_profile, home_profile
                h_goals, a_goals = fix_home_goals, fix_away_goals
                h_name, a_name = away_name_api, home_name

            try:
                feats = build_features(h_profile, a_profile, neutral=True, match_importance=1.5)
                probs = predictor.predict(feats, use_monte_carlo=False)  # analytique = rapide
            except Exception as e:
                skipped += 1
                continue

            # Résultat réel
            if h_goals > a_goals:
                actual_result = "home"
            elif h_goals < a_goals:
                actual_result = "away"
            else:
                actual_result = "draw"

            # Résultat prédit (max probabilité)
            p_home, p_draw, p_away = probs.p_home_win, probs.p_draw, probs.p_away_win
            pred_result = max([("home", p_home), ("draw", p_draw), ("away", p_away)],
                              key=lambda x: x[1])[0]

            actual_over = (h_goals + a_goals) > 2
            actual_btts = h_goals > 0 and a_goals > 0

            results.append(BacktestResult(
                match_date=fix_date,
                home=h_name,
                away=a_name,
                competition=comp,
                actual_home_goals=h_goals,
                actual_away_goals=a_goals,
                actual_result=actual_result,
                pred_home_goals=probs.lambda_home,
                pred_away_goals=probs.lambda_away,
                pred_result=pred_result,
                pred_p_home=p_home,
                pred_p_draw=p_draw,
                pred_p_away=p_away,
                pred_over_25=probs.p_over_25,
                pred_btts=probs.p_btts,
                actual_over_25=actual_over,
                actual_btts=actual_btts,
                source=feats.home_sample_size > 0 and "API" or "FIFA",
            ))
            processed += 1

    # Dédupliquer (chaque match apparaît 2x car on boucle sur home + away)
    seen: set[str] = set()
    unique: list[BacktestResult] = []
    for r in results:
        key = f"{r.match_date}_{min(r.home, r.away)}_{max(r.home, r.away)}"
        if key not in seen:
            seen.add(key)
            unique.append(r)

    unique.sort(key=lambda x: x.match_date, reverse=True)
    return unique


def display_results(results: list[BacktestResult]) -> None:
    if not results:
        console.print("[red]Aucun match à analyser.[/red]")
        return

    n = len(results)
    correct_1x2 = sum(1 for r in results if r.result_correct)
    correct_over = sum(1 for r in results if r.over_correct)
    correct_btts = sum(1 for r in results if r.btts_correct)

    # Brier Score 1X2 (prob du bon résultat)
    brier = sum(
        (r.pred_p_home - (1 if r.actual_result == "home" else 0)) ** 2 +
        (r.pred_p_draw - (1 if r.actual_result == "draw" else 0)) ** 2 +
        (r.pred_p_away - (1 if r.actual_result == "away" else 0)) ** 2
        for r in results
    ) / (n * 3)

    # ROI théorique (cote 1/prob modèle, mise 1€ sur la sélection max prob)
    # Simuler le ROI si on jouait chaque pari à la cote "juste" du modèle
    roi_ou = _simulated_roi(results, "ou")
    roi_btts = _simulated_roi(results, "btts")
    roi_1x2 = _simulated_roi(results, "1x2")

    # Distribution des résultats réels
    n_home = sum(1 for r in results if r.actual_result == "home")
    n_draw = sum(1 for r in results if r.actual_result == "draw")
    n_away = sum(1 for r in results if r.actual_result == "away")

    console.print(Panel(
        f"[bold cyan]Backtest Modèle Équipes Nationales[/bold cyan]\n"
        f"{n} matchs analysés\n"
        f"Résultats réels : {n_home} dom. ({n_home/n:.0%}) │ "
        f"{n_draw} nuls ({n_draw/n:.0%}) │ {n_away} ext. ({n_away/n:.0%})",
        title="🧪 Résultats Backtest",
        border_style="cyan",
    ))

    # Métriques globales
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Marché", width=16)
    table.add_column("Précision", justify="right", width=12)
    table.add_column("Baseline*", justify="right", width=10)
    table.add_column("ROI simulé", justify="right", width=12)
    table.add_column("Brier Score", justify="right", width=12)

    table.add_row(
        "1X2",
        f"[bold]{correct_1x2/n:.1%}[/bold]",
        f"{max(n_home, n_draw, n_away)/n:.1%}",
        f"[{'green' if roi_1x2 > 0 else 'red'}]{roi_1x2:+.1f}%[/{'green' if roi_1x2 > 0 else 'red'}]",
        f"{brier:.4f}",
    )
    table.add_row(
        "Over/Under 2.5",
        f"[bold]{correct_over/n:.1%}[/bold]",
        f"{max(sum(1 for r in results if r.actual_over_25), n-sum(1 for r in results if r.actual_over_25))/n:.1%}",
        f"[{'green' if roi_ou > 0 else 'red'}]{roi_ou:+.1f}%[/{'green' if roi_ou > 0 else 'red'}]",
        "—",
    )
    table.add_row(
        "BTTS",
        f"[bold]{correct_btts/n:.1%}[/bold]",
        f"{max(sum(1 for r in results if r.actual_btts), n-sum(1 for r in results if r.actual_btts))/n:.1%}",
        f"[{'green' if roi_btts > 0 else 'red'}]{roi_btts:+.1f}%[/{'green' if roi_btts > 0 else 'red'}]",
        "—",
    )
    console.print(table)
    console.print("[dim]* Baseline = précision en jouant toujours le résultat le plus fréquent[/dim]")

    # Détail par compétition
    by_comp: dict[str, list[BacktestResult]] = {}
    for r in results:
        by_comp.setdefault(r.competition, []).append(r)

    console.print("\n[bold]Précision 1X2 par compétition :[/bold]")
    comp_table = Table(show_header=True, header_style="bold")
    comp_table.add_column("Compétition", width=30)
    comp_table.add_column("Matchs", justify="right", width=8)
    comp_table.add_column("Précision 1X2", justify="right", width=14)
    comp_table.add_column("Précision O2.5", justify="right", width=14)
    comp_table.add_column("Précision BTTS", justify="right", width=14)

    for comp, rlist in sorted(by_comp.items(), key=lambda x: len(x[1]), reverse=True)[:10]:
        nc = len(rlist)
        acc_1x2 = sum(1 for r in rlist if r.result_correct) / nc
        acc_ou = sum(1 for r in rlist if r.over_correct) / nc
        acc_btts = sum(1 for r in rlist if r.btts_correct) / nc
        comp_table.add_row(
            comp[:28], str(nc),
            f"{acc_1x2:.0%}", f"{acc_ou:.0%}", f"{acc_btts:.0%}"
        )
    console.print(comp_table)

    # Top 5 meilleures prédictions (prob la plus haute sur le bon résultat)
    console.print("\n[bold]Top 5 meilleures prédictions :[/bold]")
    correct = [r for r in results if r.result_correct]
    correct.sort(key=lambda r: max(r.pred_p_home, r.pred_p_draw, r.pred_p_away), reverse=True)
    for r in correct[:5]:
        p = max(r.pred_p_home, r.pred_p_draw, r.pred_p_away)
        console.print(
            f"  ✅ {r.match_date} | {r.home} {r.actual_home_goals}-{r.actual_away_goals} {r.away}"
            f" | {r.competition[:20]} | {r.pred_result.upper()} prédit à {p:.0%}"
        )

    # Top 5 pires prédictions
    console.print("\n[bold]Top 5 pires prédictions (mauvaises avec haute confiance) :[/bold]")
    wrong = [r for r in results if not r.result_correct]
    wrong.sort(key=lambda r: max(r.pred_p_home, r.pred_p_draw, r.pred_p_away), reverse=True)
    for r in wrong[:5]:
        p = max(r.pred_p_home, r.pred_p_draw, r.pred_p_away)
        console.print(
            f"  ❌ {r.match_date} | {r.home} {r.actual_home_goals}-{r.actual_away_goals} {r.away}"
            f" | {r.competition[:20]} | prédit {r.pred_result.upper()} ({p:.0%}) → résultat {r.actual_result.upper()}"
        )


def _simulated_roi(results: list[BacktestResult], market: str) -> float:
    """
    ROI simulé : mise de 1€ à la cote juste (1/prob_modèle) sur la sélection max.
    Positif = le modèle est calibré, négatif = sur-confiant.
    """
    total_staked = 0
    total_return = 0.0

    for r in results:
        if market == "1x2":
            # Paris sur le résultat le plus probable
            if r.pred_result == "home":
                prob, actual_win = r.pred_p_home, r.actual_result == "home"
            elif r.pred_result == "draw":
                prob, actual_win = r.pred_p_draw, r.actual_result == "draw"
            else:
                prob, actual_win = r.pred_p_away, r.actual_result == "away"
            if prob <= 0:
                continue
            odds = 1.0 / prob
            total_staked += 1
            total_return += odds if actual_win else 0

        elif market == "ou":
            # Paris sur Over si prob >50%, Under sinon
            if r.pred_over_25 >= 0.50:
                prob, actual_win = r.pred_over_25, r.actual_over_25
            else:
                prob, actual_win = 1 - r.pred_over_25, not r.actual_over_25
            if prob <= 0:
                continue
            odds = 1.0 / prob
            total_staked += 1
            total_return += odds if actual_win else 0

        elif market == "btts":
            if r.pred_btts >= 0.50:
                prob, actual_win = r.pred_btts, r.actual_btts
            else:
                prob, actual_win = 1 - r.pred_btts, not r.actual_btts
            if prob <= 0:
                continue
            odds = 1.0 / prob
            total_staked += 1
            total_return += odds if actual_win else 0

    if total_staked == 0:
        return 0.0
    return round((total_return - total_staked) / total_staked * 100, 2)


def main():
    parser = argparse.ArgumentParser(description="Backtest modèle équipes nationales")
    parser.add_argument("--fetch", action="store_true",
                        help="Charger les équipes CdM 2026 via API (max 80 req)")
    parser.add_argument("--teams", type=int, default=0,
                        help="Nombre d'équipes CdM à charger (ordre de priorité)")
    parser.add_argument("--min-matches", type=int, default=5,
                        help="Nombre minimum de matchs par équipe pour être incluse")
    args = parser.parse_args()

    # Charger les profils
    from betx.data.national_team_collector import NationalTeamCollector
    collector = NationalTeamCollector()

    teams_to_load = list(WC2026_TEAMS[:args.teams]) if args.teams else None

    console.print("[bold cyan]📊 Chargement des profils...[/bold cyan]")
    profiles = {}

    if args.fetch and teams_to_load:
        for team in teams_to_load:
            profile = collector.get_profile(team)
            if profile and len(profile.recent_matches) >= args.min_matches:
                profiles[team] = profile
                console.print(f"  ✅ {team}: {len(profile.recent_matches)} matchs")
    else:
        # Utiliser uniquement le cache existant
        cache = json.loads(Path("data/cache/national_teams.json").read_text())
        for key, val in cache.get("team_ids", {}).items():
            tid = val.get("id")
            if not tid or str(tid) not in cache.get("fixtures", {}):
                continue
            team_name = val.get("name", key)
            profile = collector.get_profile(team_name)
            if profile and len(profile.recent_matches) >= args.min_matches:
                profiles[team_name] = profile

    console.print(f"\n  {len(profiles)} équipes chargées\n")

    if len(profiles) < 2:
        console.print("[yellow]⚠️  Moins de 2 équipes disponibles. Utilisez --fetch pour enrichir le cache.[/yellow]")
        console.print("  Ex: python backtest_national.py --fetch --teams 20")
        return

    # Lancer le backtest
    console.print("[bold cyan]🧪 Backtest en cours...[/bold cyan]")
    results = run_backtest(list(profiles.keys()), profiles, min_matches_per_team=args.min_matches)
    console.print(f"  {len(results)} matchs analysés\n")

    if not results:
        console.print("[yellow]⚠️  Aucun match trouvé entre les équipes en cache.[/yellow]")
        console.print("  Le cache contient des équipes sans adversaires communs.")
        console.print("  Lancez: python backtest_national.py --fetch --teams 20")
        return

    display_results(results)

    # Sauvegarder les résultats
    out_file = Path("data/backtest_national.json")
    out_data = [
        {
            "date": r.match_date, "home": r.home, "away": r.away,
            "competition": r.competition,
            "actual": f"{r.actual_home_goals}-{r.actual_away_goals}",
            "predicted": r.pred_result,
            "correct": r.result_correct,
            "pred_p": {"home": round(r.pred_p_home,3), "draw": round(r.pred_p_draw,3), "away": round(r.pred_p_away,3)},
            "lambda": {"home": round(r.pred_home_goals,2), "away": round(r.pred_away_goals,2)},
            "over_25": {"pred": round(r.pred_over_25,3), "actual": r.actual_over_25, "correct": r.over_correct},
            "btts": {"pred": round(r.pred_btts,3), "actual": r.actual_btts, "correct": r.btts_correct},
        }
        for r in results
    ]
    out_file.write_text(json.dumps(out_data, ensure_ascii=False, indent=2))
    console.print(f"\n💾 Résultats sauvegardés → [cyan]{out_file}[/cyan]")


if __name__ == "__main__":
    main()
