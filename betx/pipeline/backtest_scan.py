"""
betX – Backtest Historique

Backteste le système de value betting sur les matchs terminés
de la saison en cours.

Workflow (sans look-ahead bias) :
1. Récupérer les fixtures terminées via API-Football
2. Construire les stats équipes de manière incrémentale (match par match)
3. Relancer le modèle Poisson sur chaque match (pré-résultat)
4. Générer des cotes de marché synthétiques (ELO naïf + marge bookmaker)
5. Détecter les value bets (modèle Poisson vs marché ELO)
6. Vérifier le résultat réel → P&L
7. Calculer ROI, Yield, Drawdown, Sharpe, calibration

Pourquoi des cotes synthétiques ?
  Le modèle Poisson est notre "alpha". Le marché ELO naïf simule
  ce qu'un bookmaker basique proposerait. Si Poisson > ELO → value bet.
  C'est exactement le principe du value betting : battre le marché.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional

import httpx
import numpy as np
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import track

from betx.config import settings
from betx.models.football_model import FootballModel, TeamStats
from betx.logger import get_logger

log = get_logger("backtest_scan")
console = Console()


# ─── Leagues ─────────────────────────────────────────────────────────────

LEAGUES = {
    39: "Premier League 🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    140: "La Liga 🇪🇸",
    135: "Serie A 🇮🇹",
    78: "Bundesliga 🇩🇪",
    61: "Ligue 1 🇫🇷",
    2: "Champions League 🏆",
    3: "Europa League 🏆",
}

BOOKMAKER_MARGIN = 0.06  # 6% overround typique


# ─── Data Structures ─────────────────────────────────────────────────────

@dataclass
class HistoricalFixture:
    """Un match historique terminé."""
    fixture_id: int
    date: str          # YYYY-MM-DD
    league_id: int
    league_name: str
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    result: str = ""       # "home", "draw", "away"
    total_goals: int = 0

    def __post_init__(self):
        if self.home_goals > self.away_goals:
            self.result = "home"
        elif self.home_goals == self.away_goals:
            self.result = "draw"
        else:
            self.result = "away"
        self.total_goals = self.home_goals + self.away_goals


@dataclass
class TeamRunningStats:
    """
    Stats cumulatives d'une équipe, mises à jour match par match.
    Aucun look-ahead : on ne connaît que les matchs déjà joués.
    """
    name: str
    goals_scored: list[int] = field(default_factory=list)
    goals_conceded: list[int] = field(default_factory=list)
    home_scored: list[int] = field(default_factory=list)
    home_conceded: list[int] = field(default_factory=list)
    away_scored: list[int] = field(default_factory=list)
    away_conceded: list[int] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    elo: float = 1500.0
    matches_played: int = 0

    def update(self, goals_for: int, goals_against: int, is_home: bool):
        """Ajouter un match aux stats (APRÈS la prédiction)."""
        self.goals_scored.append(goals_for)
        self.goals_conceded.append(goals_against)
        if is_home:
            self.home_scored.append(goals_for)
            self.home_conceded.append(goals_against)
        else:
            self.away_scored.append(goals_for)
            self.away_conceded.append(goals_against)
        if goals_for > goals_against:
            self.results.append("W")
        elif goals_for == goals_against:
            self.results.append("D")
        else:
            self.results.append("L")
        self.matches_played += 1

    @property
    def avg_scored(self) -> float:
        if not self.goals_scored:
            return 1.3
        return float(np.mean(self.goals_scored[-10:]))

    @property
    def avg_conceded(self) -> float:
        if not self.goals_conceded:
            return 1.2
        return float(np.mean(self.goals_conceded[-10:]))

    @property
    def win_rate(self) -> float:
        if not self.results:
            return 0.33
        recent = self.results[-10:]
        return recent.count("W") / len(recent)

    def to_team_stats(self, is_home: bool) -> TeamStats:
        """Convertir en TeamStats pour le modèle Poisson."""
        if self.matches_played < 3:
            return TeamStats(
                name=self.name,
                avg_goals_scored=1.3,
                avg_goals_conceded=1.2,
                xg_for=1.3,
                xg_against=1.2,
                elo=self.elo,
                home_elo=self.elo + (50 if is_home else 0),
                away_elo=self.elo - (0 if is_home else 50),
                recent_form=self.results[-5:],
            )

        scored = float(np.mean(self.goals_scored[-10:]))
        conceded = float(np.mean(self.goals_conceded[-10:]))

        return TeamStats(
            name=self.name,
            avg_goals_scored=scored,
            avg_goals_conceded=conceded,
            xg_for=scored,
            xg_against=conceded,
            elo=self.elo,
            home_elo=self.elo + (50 if is_home else 0),
            away_elo=self.elo - (0 if is_home else 50),
            recent_form=self.results[-5:],
        )


@dataclass
class BacktestBet:
    """Un pari individuel dans le backtest."""
    date: str
    league: str
    home_team: str
    away_team: str
    selection: str
    model_prob: float
    market_odds: float
    edge: float
    stake: float
    bet_won: bool
    pnl: float
    score: str


# ─── API-Football Data Fetcher ───────────────────────────────────────────

def _api_get(endpoint: str, params: dict) -> dict:
    """Appel API-Football v3."""
    url = f"{settings.api.football_base_url}{endpoint}"
    headers = {"x-apisports-key": settings.api.football_key}
    try:
        resp = httpx.get(url, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        errors = data.get("errors", {})
        if errors and isinstance(errors, dict) and any(errors.values()):
            log.warning(f"API-Football errors: {errors}")
        return data
    except Exception as e:
        log.warning(f"API-Football {endpoint}: {e}")
        return {}


def fetch_season_fixtures(
    league_ids: list[int],
    season: int,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[HistoricalFixture]:
    """Récupère tous les matchs terminés pour les ligues données."""
    fixtures: list[HistoricalFixture] = []

    for lid in league_ids:
        league_name = LEAGUES.get(lid, f"League {lid}")
        params: dict = {
            "league": lid,
            "season": season,
            "status": "FT",
        }
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        console.print(f"  📥 {league_name}...", end=" ")
        data = _api_get("/fixtures", params)
        response = data.get("response", [])

        count = 0
        for fix in response:
            fixture_info = fix.get("fixture", {})
            teams = fix.get("teams", {})
            goals = fix.get("goals", {})
            league_info = fix.get("league", {})

            fix_date = fixture_info.get("date", "")
            if fix_date:
                fix_date = fix_date[:10]

            h_goals = goals.get("home")
            a_goals = goals.get("away")
            if h_goals is None or a_goals is None:
                continue

            fixtures.append(HistoricalFixture(
                fixture_id=fixture_info.get("id", 0),
                date=fix_date,
                league_id=lid,
                league_name=league_info.get("name", league_name),
                home_team=teams.get("home", {}).get("name", ""),
                away_team=teams.get("away", {}).get("name", ""),
                home_goals=int(h_goals),
                away_goals=int(a_goals),
            ))
            count += 1

        console.print(f"[green]{count} matchs[/green]")

    fixtures.sort(key=lambda f: f.date)
    return fixtures


# ─── ELO System (market proxy) ──────────────────────────────────────────

def update_elo(
    elo_a: float,
    elo_b: float,
    result: str,
    k: float = 25.0,
    home_adv: float = 65.0,
) -> tuple[float, float]:
    """Mise à jour ELO après un match (le marché naïf)."""
    exp_a = 1.0 / (1.0 + 10 ** ((elo_b - elo_a - home_adv) / 400.0))
    scores = {"home": 1.0, "draw": 0.5, "away": 0.0}
    actual = scores.get(result, 0.5)

    new_a = elo_a + k * (actual - exp_a)
    new_b = elo_b + k * ((1 - actual) - (1 - exp_a))
    return new_a, new_b


def elo_to_probs(
    elo_home: float,
    elo_away: float,
    home_adv: float = 65.0,
) -> dict[str, float]:
    """Convertit les ELO en probabilités 1X2 (= cotes du marché naïf)."""
    p_home_win = 1.0 / (1.0 + 10 ** ((elo_away - elo_home - home_adv) / 400.0))
    elo_diff = abs((elo_home + home_adv) - elo_away)
    p_draw = max(0.18, 0.30 - elo_diff / 1800)
    p_draw = min(p_draw, 0.32)

    remaining = 1.0 - p_draw
    p_home = p_home_win * remaining
    p_away = (1 - p_home_win) * remaining

    return {"home": p_home, "draw": p_draw, "away": p_away}


def probs_to_odds(probs: dict[str, float], margin: float = BOOKMAKER_MARGIN) -> dict[str, float]:
    """Convertit des probabilités en cotes bookmaker (avec marge)."""
    n = len(probs)
    margin_per = margin / n if n > 0 else 0
    odds: dict[str, float] = {}
    for sel, prob in probs.items():
        adjusted = prob + margin_per
        adjusted = max(adjusted, 0.05)
        odds[sel] = round(1.0 / adjusted, 2)
    return odds


def poisson_over_prob(avg_total: float, threshold: float) -> float:
    """P(total goals > threshold) via Poisson CDF."""
    if avg_total <= 0:
        return 0.5
    k_max = int(threshold)
    p_under = sum(
        (avg_total ** k * math.exp(-avg_total)) / math.factorial(k)
        for k in range(k_max + 1)
    )
    return 1.0 - p_under


# ─── Main Backtest Engine ────────────────────────────────────────────────

def run_backtest(
    days_back: int = 90,
    leagues: list[int] | None = None,
    initial_bankroll: float = 1000.0,
    min_edge: float = 0.03,
    min_team_matches: int = 5,
    kelly_fraction: float = 0.25,
    max_stake_pct: float = 0.03,
    best_bet_only: bool = False,
    markets: list[str] | None = None,
) -> dict:
    """
    Backtest complet sur les matchs terminés.

    Args:
        days_back: Nombre de jours de recul
        leagues: IDs des ligues à tester
        initial_bankroll: Bankroll de départ
        min_edge: Edge minimum pour parier
        min_team_matches: Nb matchs min avant de parier sur une équipe
        kelly_fraction: Fraction Kelly
        max_stake_pct: Mise max (% bankroll)
        best_bet_only: Si True, un seul pari par match (le meilleur edge)
        markets: Liste des marchés autorisés (ex: ["1x2"] ou ["1x2", "over"])
                 None = tous les marchés

    Returns:
        Dict avec tous les résultats
    """
    leagues = leagues or list(LEAGUES.keys())

    today = datetime.now()
    # API-Football plan gratuit : saisons 2022 à 2024 uniquement
    # On utilise la saison 2024-25 pour le backtest
    season = 2024
    # Période : derniers N jours de la saison 2024-25 (août 2024 → mai 2025)
    season_end = datetime(2025, 5, 25)
    to_date = min(today, season_end).strftime("%Y-%m-%d")
    from_date = (datetime.strptime(to_date, "%Y-%m-%d") - timedelta(days=days_back)).strftime("%Y-%m-%d")
    # S'assurer qu'on est dans la saison
    season_start = "2024-08-01"
    if from_date < season_start:
        from_date = season_start

    console.print(Panel(
        f"[bold cyan]betX Backtest Historique[/bold cyan]\n\n"
        f"  📅 Période      : {from_date} → {to_date} ({days_back} jours)\n"
        f"  ⚽ Ligues        : {len(leagues)} ({', '.join(LEAGUES.get(l, '?').split()[0] for l in leagues)})\n"
        f"  💰 Bankroll      : {initial_bankroll:.0f}€\n"
        f"  🎯 Edge min      : {min_edge:.0%}\n"
        f"  📊 Kelly fraction: {kelly_fraction}\n"
        f"  🔒 Mise max      : {max_stake_pct:.0%} de la bankroll\n"
        f"  🏷️  Marchés       : {', '.join(markets) if markets else 'tous (1X2 + Over/Under)'}",
        title="⚙️ Configuration",
        border_style="cyan",
    ))

    # ── Phase 1 : Récupérer les fixtures ──
    console.print("\n[bold]📥 Phase 1/3 : Récupération des résultats historiques...[/bold]\n")
    fixtures = fetch_season_fixtures(leagues, season, from_date, to_date)

    if not fixtures:
        console.print("[red]❌ Aucun match trouvé. Vérifiez la clé API-Football.[/red]")
        return {}

    console.print(
        f"\n  [bold green]✅ {len(fixtures)} matchs terminés récupérés "
        f"({fixtures[0].date} → {fixtures[-1].date})[/bold green]\n"
    )

    # ── Phase 2 : Construction incrémentale des stats ──
    console.print("[bold]🧠 Phase 2/3 : Construction des stats & ELO (walk-forward)...[/bold]\n")

    team_tracker: dict[str, TeamRunningStats] = {}
    elo_tracker: dict[str, float] = defaultdict(lambda: 1500.0)

    # ── Phase 3 : Simulation des paris ──
    console.print("[bold]🎰 Phase 3/3 : Simulation des paris...[/bold]\n")

    model = FootballModel()
    bankroll = initial_bankroll
    peak_bankroll = bankroll
    bets: list[BacktestBet] = []
    bankroll_history = [bankroll]

    max_drawdown = 0.0
    losing_streak = 0
    max_losing_streak = 0
    daily_pnl: dict[str, float] = defaultdict(float)

    fixtures_evaluated = 0

    for fix in track(fixtures, description="  Simulation...", console=console):
        home_name = fix.home_team
        away_name = fix.away_team

        # Init trackers
        if home_name not in team_tracker:
            team_tracker[home_name] = TeamRunningStats(name=home_name)
        if away_name not in team_tracker:
            team_tracker[away_name] = TeamRunningStats(name=away_name)

        home_tr = team_tracker[home_name]
        away_tr = team_tracker[away_name]

        # On ne parie que si on a assez de données sur les deux équipes
        can_bet = (
            home_tr.matches_played >= min_team_matches
            and away_tr.matches_played >= min_team_matches
        )

        if can_bet:
            fixtures_evaluated += 1

            # ── Stats AVANT ce match (pas de look-ahead) ──
            home_stats = home_tr.to_team_stats(is_home=True)
            away_stats = away_tr.to_team_stats(is_home=False)

            # Injecter l'ELO courant
            home_stats.elo = elo_tracker[home_name]
            home_stats.home_elo = elo_tracker[home_name] + 50
            away_stats.elo = elo_tracker[away_name]
            away_stats.away_elo = elo_tracker[away_name]

            # ── Modèle Poisson (notre alpha) ──
            pred = model.predict(home_stats, away_stats)
            model_preds = {
                "home": pred.p_home,
                "draw": pred.p_draw,
                "away": pred.p_away,
                "over_2.5": pred.p_over_25,
                "over_1.5": pred.p_over_15,
                "over_3.5": pred.p_over_35,
            }

            # ── Cotes marché naïf (ELO simple + marge) ──
            market_probs = elo_to_probs(
                elo_tracker[home_name], elo_tracker[away_name]
            )
            market_odds = probs_to_odds(market_probs)

            # Over/Under : marché naïf basé sur la moyenne de buts
            avg_total = (home_tr.avg_scored + away_tr.avg_conceded +
                         away_tr.avg_scored + home_tr.avg_conceded) / 2.0
            for threshold, label in [(1.5, "over_1.5"), (2.5, "over_2.5"), (3.5, "over_3.5")]:
                p_over = poisson_over_prob(avg_total, threshold)
                # Ajouter marge bookmaker
                market_odds[label] = round(
                    1.0 / max(p_over + BOOKMAKER_MARGIN / 2, 0.05), 2
                )

            # ── Filtrer les marchés autorisés ──
            allowed_selections = set(model_preds.keys())
            if markets:
                filtered = set()
                for m in markets:
                    if m.lower() in ("1x2", "h2h", "match"):
                        filtered.update({"home", "draw", "away"})
                    elif m.lower() in ("over", "totals", "over_under", "ou"):
                        filtered.update({s for s in model_preds if s.startswith("over")})
                allowed_selections &= filtered

            # ── Détecter les value bets ──
            match_bets: list[tuple[str, float, float, float]] = []  # (sel, prob, odds, edge)

            for selection, model_prob in model_preds.items():
                if selection not in allowed_selections:
                    continue
                if selection not in market_odds:
                    continue
                odds = market_odds[selection]
                if odds <= 1.0 or odds > 15.0:
                    continue

                implied = 1.0 / odds
                edge = model_prob - implied
                ev = model_prob * (odds - 1) - (1 - model_prob)

                if edge >= min_edge and ev > 0 and model_prob > 0.05:
                    match_bets.append((selection, model_prob, odds, edge))

            # Si best_bet_only, ne garder que le meilleur edge
            if best_bet_only and match_bets:
                match_bets = [max(match_bets, key=lambda x: x[3])]

            # ── Placer les paris ──
            for selection, model_prob, odds, edge in match_bets:
                # Kelly fractionné
                kelly = (model_prob * odds - 1) / (odds - 1)
                kelly = max(0, kelly)
                stake_pct = kelly * kelly_fraction
                stake_pct = min(stake_pct, max_stake_pct)

                if stake_pct < 0.002:
                    continue

                stake = bankroll * stake_pct
                if stake < 1.0:
                    continue

                # ── Vérifier le résultat réel ──
                bet_won = False
                if selection == "home":
                    bet_won = fix.result == "home"
                elif selection == "draw":
                    bet_won = fix.result == "draw"
                elif selection == "away":
                    bet_won = fix.result == "away"
                elif selection == "over_1.5":
                    bet_won = fix.total_goals >= 2
                elif selection == "over_2.5":
                    bet_won = fix.total_goals >= 3
                elif selection == "over_3.5":
                    bet_won = fix.total_goals >= 4

                # P&L
                if bet_won:
                    pnl = stake * (odds - 1)
                    losing_streak = 0
                else:
                    pnl = -stake
                    losing_streak += 1
                    max_losing_streak = max(max_losing_streak, losing_streak)

                bankroll += pnl
                daily_pnl[fix.date] += pnl

                # Drawdown
                if bankroll > peak_bankroll:
                    peak_bankroll = bankroll
                dd = (peak_bankroll - bankroll) / peak_bankroll * 100 if peak_bankroll > 0 else 0
                max_drawdown = max(max_drawdown, dd)

                bets.append(BacktestBet(
                    date=fix.date,
                    league=fix.league_name,
                    home_team=home_name,
                    away_team=away_name,
                    selection=selection,
                    model_prob=model_prob,
                    market_odds=odds,
                    edge=edge,
                    stake=round(stake, 2),
                    bet_won=bet_won,
                    pnl=round(pnl, 2),
                    score=f"{fix.home_goals}-{fix.away_goals}",
                ))

                bankroll_history.append(bankroll)

        # ── MISE À JOUR des stats APRÈS le match (walk-forward) ──
        home_tr.update(fix.home_goals, fix.away_goals, is_home=True)
        away_tr.update(fix.away_goals, fix.home_goals, is_home=False)

        # Mise à jour ELO
        new_h, new_a = update_elo(
            elo_tracker[home_name],
            elo_tracker[away_name],
            fix.result,
        )
        elo_tracker[home_name] = new_h
        elo_tracker[away_name] = new_a

    # ── Compilation des résultats ──
    wins = sum(1 for b in bets if b.bet_won)
    losses = sum(1 for b in bets if not b.bet_won)
    total_staked = sum(b.stake for b in bets)
    total_pnl = sum(b.pnl for b in bets)

    # Sharpe ratio
    pnl_values = list(daily_pnl.values())
    sharpe = 0.0
    if len(pnl_values) > 1:
        pnl_arr = np.array(pnl_values)
        if pnl_arr.std() > 0:
            sharpe = float((pnl_arr.mean() / pnl_arr.std()) * np.sqrt(252))

    return {
        "period": f"{from_date} → {to_date}",
        "days_back": days_back,
        "fixtures_total": len(fixtures),
        "fixtures_evaluated": fixtures_evaluated,
        "total_bets": len(bets),
        "wins": wins,
        "losses": losses,
        "winrate": wins / len(bets) * 100 if bets else 0,
        "initial_bankroll": initial_bankroll,
        "final_bankroll": round(bankroll, 2),
        "total_staked": round(total_staked, 2),
        "total_pnl": round(total_pnl, 2),
        "roi": round((bankroll - initial_bankroll) / initial_bankroll * 100, 2),
        "yield": round(total_pnl / total_staked * 100, 2) if total_staked > 0 else 0,
        "max_drawdown": round(max_drawdown, 2),
        "max_losing_streak": max_losing_streak,
        "avg_edge": round(float(np.mean([b.edge for b in bets])), 4) if bets else 0,
        "avg_odds": round(float(np.mean([b.market_odds for b in bets])), 2) if bets else 0,
        "sharpe": round(sharpe, 2),
        "bets": bets,
        "bankroll_history": bankroll_history,
        "daily_pnl": dict(daily_pnl),
    }


# ─── Affichage des résultats ─────────────────────────────────────────────

def display_backtest_results(result: dict):
    """Affiche les résultats complets du backtest avec Rich."""
    if not result:
        return

    bets: list[BacktestBet] = result["bets"]
    roi_color = "green" if result["roi"] > 0 else "red"
    pnl_color = "green" if result["total_pnl"] > 0 else "red"

    # ── Panneau récapitulatif ──
    console.print(Panel(
        f"[bold cyan]Résultats du Backtest[/bold cyan]\n\n"
        f"  📅 Période           : {result['period']}\n"
        f"  ⚽ Matchs récupérés  : {result['fixtures_total']}\n"
        f"  🔍 Matchs évalués    : {result['fixtures_evaluated']}\n"
        f"  🎯 Paris placés      : {result['total_bets']}\n"
        f"  ✅ Gagnés            : {result['wins']}\n"
        f"  ❌ Perdus            : {result['losses']}\n"
        f"  📊 Winrate           : {result['winrate']:.1f}%\n"
        f"\n"
        f"  💰 Bankroll initiale : {result['initial_bankroll']:.0f}€\n"
        f"  💰 Bankroll finale   : [{roi_color}]{result['final_bankroll']:.2f}€[/{roi_color}]\n"
        f"  [{pnl_color}]💵 P&L total         : {result['total_pnl']:+.2f}€[/{pnl_color}]\n"
        f"  💸 Mise totale        : {result['total_staked']:.2f}€\n"
        f"\n"
        f"  📈 ROI               : [{roi_color}]{result['roi']:+.2f}%[/{roi_color}]\n"
        f"  📈 Yield             : [{roi_color}]{result['yield']:+.2f}%[/{roi_color}]\n"
        f"  📉 Max Drawdown      : {result['max_drawdown']:.2f}%\n"
        f"  🔥 Losing streak max : {result['max_losing_streak']}\n"
        f"  📊 Sharpe Ratio      : {result['sharpe']:.2f}\n"
        f"\n"
        f"  🎯 Edge moyen        : {result['avg_edge']:.2%}\n"
        f"  🎰 Cote moyenne      : {result['avg_odds']:.2f}",
        title="📊 betX Backtest",
        border_style="green" if result["roi"] > 0 else "red",
    ))

    if not bets:
        console.print("[yellow]Aucun pari placé durant la période.[/yellow]")
        return

    # ── Sparkline bankroll ──
    history = result["bankroll_history"]
    if len(history) > 2:
        # Downsample si trop de points
        if len(history) > 60:
            step = max(1, len(history) // 60)
            sampled = history[::step]
        else:
            sampled = history
        min_val = min(sampled)
        max_val = max(sampled)
        range_val = max_val - min_val if max_val > min_val else 1
        chars = "▁▂▃▄▅▆▇█"
        sparkline = ""
        for v in sampled:
            idx = int((v - min_val) / range_val * (len(chars) - 1))
            idx = max(0, min(idx, len(chars) - 1))
            sparkline += chars[idx]
        console.print(f"\n  📈 Bankroll: [cyan]{sparkline}[/cyan]")
        console.print(
            f"     {result['initial_bankroll']:.0f}€ → "
            f"[{'green' if result['roi'] > 0 else 'red'}]"
            f"{result['final_bankroll']:.2f}€[/{'green' if result['roi'] > 0 else 'red'}]\n"
        )

    # ── Détail par type de pari ──
    sel_stats: dict[str, dict] = defaultdict(
        lambda: {"bets": 0, "wins": 0, "pnl": 0.0, "staked": 0.0}
    )
    for b in bets:
        s = sel_stats[b.selection]
        s["bets"] += 1
        s["wins"] += int(b.bet_won)
        s["pnl"] += b.pnl
        s["staked"] += b.stake

    table_sel = Table(title="📊 Détail par type de pari", show_lines=True)
    table_sel.add_column("Sélection", style="cyan", width=15)
    table_sel.add_column("Paris", justify="right", width=8)
    table_sel.add_column("Win%", justify="right", width=8)
    table_sel.add_column("P&L", justify="right", width=12)
    table_sel.add_column("Yield%", justify="right", width=10)

    for sel, stats in sorted(sel_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
        wr = stats["wins"] / stats["bets"] * 100 if stats["bets"] > 0 else 0
        yld = stats["pnl"] / stats["staked"] * 100 if stats["staked"] > 0 else 0
        pnl_style = "green" if stats["pnl"] > 0 else "red"
        table_sel.add_row(
            sel.replace("_", " ").title(),
            str(stats["bets"]),
            f"{wr:.1f}%",
            f"[{pnl_style}]{stats['pnl']:+.2f}€[/{pnl_style}]",
            f"[{pnl_style}]{yld:+.1f}%[/{pnl_style}]",
        )

    console.print(table_sel)

    # ── Détail par ligue ──
    league_stats: dict[str, dict] = defaultdict(
        lambda: {"bets": 0, "wins": 0, "pnl": 0.0, "staked": 0.0}
    )
    for b in bets:
        s = league_stats[b.league]
        s["bets"] += 1
        s["wins"] += int(b.bet_won)
        s["pnl"] += b.pnl
        s["staked"] += b.stake

    table_league = Table(title="📊 Détail par ligue", show_lines=True)
    table_league.add_column("Ligue", style="cyan", width=25)
    table_league.add_column("Paris", justify="right", width=8)
    table_league.add_column("Win%", justify="right", width=8)
    table_league.add_column("P&L", justify="right", width=12)
    table_league.add_column("Yield%", justify="right", width=10)

    for league, stats in sorted(league_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
        wr = stats["wins"] / stats["bets"] * 100 if stats["bets"] > 0 else 0
        yld = stats["pnl"] / stats["staked"] * 100 if stats["staked"] > 0 else 0
        pnl_style = "green" if stats["pnl"] > 0 else "red"
        table_league.add_row(
            league,
            str(stats["bets"]),
            f"{wr:.1f}%",
            f"[{pnl_style}]{stats['pnl']:+.2f}€[/{pnl_style}]",
            f"[{pnl_style}]{yld:+.1f}%[/{pnl_style}]",
        )

    console.print(table_league)

    # ── Évolution mensuelle ──
    month_stats: dict[str, dict] = defaultdict(
        lambda: {"bets": 0, "wins": 0, "pnl": 0.0, "staked": 0.0}
    )
    for b in bets:
        month = b.date[:7]
        s = month_stats[month]
        s["bets"] += 1
        s["wins"] += int(b.bet_won)
        s["pnl"] += b.pnl
        s["staked"] += b.stake

    table_month = Table(title="📊 Évolution mensuelle", show_lines=True)
    table_month.add_column("Mois", style="cyan", width=10)
    table_month.add_column("Paris", justify="right", width=8)
    table_month.add_column("Win%", justify="right", width=8)
    table_month.add_column("P&L", justify="right", width=12)
    table_month.add_column("Yield%", justify="right", width=10)
    table_month.add_column("Bankroll", justify="right", width=15)

    cumulative_pnl = 0.0
    for month in sorted(month_stats.keys()):
        stats = month_stats[month]
        wr = stats["wins"] / stats["bets"] * 100 if stats["bets"] > 0 else 0
        yld = stats["pnl"] / stats["staked"] * 100 if stats["staked"] > 0 else 0
        cumulative_pnl += stats["pnl"]
        pnl_style = "green" if stats["pnl"] > 0 else "red"
        table_month.add_row(
            month,
            str(stats["bets"]),
            f"{wr:.1f}%",
            f"[{pnl_style}]{stats['pnl']:+.2f}€[/{pnl_style}]",
            f"[{pnl_style}]{yld:+.1f}%[/{pnl_style}]",
            f"{result['initial_bankroll'] + cumulative_pnl:.2f}€",
        )

    console.print(table_month)

    # ── Calibration du modèle ──
    cal_table = Table(title="🎯 Calibration du modèle Poisson", show_lines=True)
    cal_table.add_column("P(modèle)", width=15)
    cal_table.add_column("Nb paris", justify="right", width=10)
    cal_table.add_column("Win% réel", justify="right", width=12)
    cal_table.add_column("Win% attendu", justify="right", width=12)
    cal_table.add_column("Écart", justify="right", width=10)

    buckets = [
        (0.25, 0.35), (0.35, 0.45), (0.45, 0.55),
        (0.55, 0.65), (0.65, 0.75), (0.75, 0.85),
        (0.85, 0.95), (0.95, 1.0),
    ]
    for low, high in buckets:
        bucket_bets = [b for b in bets if low <= b.model_prob < high]
        if not bucket_bets:
            continue
        actual_wr = sum(1 for b in bucket_bets if b.bet_won) / len(bucket_bets) * 100
        expected_wr = float(np.mean([b.model_prob for b in bucket_bets])) * 100
        diff = actual_wr - expected_wr
        diff_style = "green" if diff >= 0 else "red"
        cal_table.add_row(
            f"{low:.0%} – {high:.0%}",
            str(len(bucket_bets)),
            f"{actual_wr:.1f}%",
            f"{expected_wr:.1f}%",
            f"[{diff_style}]{diff:+.1f}%[/{diff_style}]",
        )

    console.print(cal_table)

    # ── 20 derniers paris ──
    table_bets = Table(title="📋 20 derniers paris", show_lines=True)
    table_bets.add_column("#", width=4)
    table_bets.add_column("Date", width=10)
    table_bets.add_column("Match", width=32)
    table_bets.add_column("Sélection", width=12)
    table_bets.add_column("P(modèle)", justify="right", width=10)
    table_bets.add_column("Cote", justify="right", width=7)
    table_bets.add_column("Edge", justify="right", width=8)
    table_bets.add_column("Mise", justify="right", width=8)
    table_bets.add_column("Score", justify="center", width=6)
    table_bets.add_column("Résultat", justify="right", width=12)

    for i, b in enumerate(bets[-20:], 1):
        res_style = "green" if b.bet_won else "red"
        res_emoji = "✅" if b.bet_won else "❌"
        table_bets.add_row(
            str(i),
            b.date,
            f"{b.home_team} vs {b.away_team}",
            b.selection.replace("_", " ").title(),
            f"{b.model_prob:.1%}",
            f"{b.market_odds:.2f}",
            f"{b.edge:.1%}",
            f"{b.stake:.0f}€",
            b.score,
            f"[{res_style}]{res_emoji} {b.pnl:+.1f}€[/{res_style}]",
        )

    console.print(table_bets)

    # ── Edge sensitivity analysis ──
    console.print()
    edge_table = Table(title="🔬 Analyse de sensibilité (Edge minimum)", show_lines=True)
    edge_table.add_column("Edge min", width=10)
    edge_table.add_column("Paris", justify="right", width=8)
    edge_table.add_column("Win%", justify="right", width=8)
    edge_table.add_column("Yield%", justify="right", width=10)
    edge_table.add_column("P&L", justify="right", width=12)

    for threshold in [0.01, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20]:
        filtered = [b for b in bets if b.edge >= threshold]
        if not filtered:
            continue
        f_wins = sum(1 for b in filtered if b.bet_won)
        f_staked = sum(b.stake for b in filtered)
        f_pnl = sum(b.pnl for b in filtered)
        f_wr = f_wins / len(filtered) * 100 if filtered else 0
        f_yield = f_pnl / f_staked * 100 if f_staked > 0 else 0
        pnl_style = "green" if f_pnl > 0 else "red"

        marker = " ◀" if abs(threshold - result.get("_min_edge_used", 0.03)) < 0.001 else ""
        edge_table.add_row(
            f"{threshold:.0%}{marker}",
            str(len(filtered)),
            f"{f_wr:.1f}%",
            f"[{pnl_style}]{f_yield:+.1f}%[/{pnl_style}]",
            f"[{pnl_style}]{f_pnl:+.2f}€[/{pnl_style}]",
        )

    console.print(edge_table)

    # ── Verdict final ──
    console.print()
    if result["roi"] > 0:
        console.print(Panel(
            f"[bold green]✅ RENTABLE sur {result['days_back']} jours[/bold green]\n\n"
            f"  ROI: {result['roi']:+.2f}% │ Yield: {result['yield']:+.2f}% │ "
            f"Sharpe: {result['sharpe']:.2f}\n"
            f"  {result['total_bets']} paris │ Winrate: {result['winrate']:.1f}% │ "
            f"Max DD: {result['max_drawdown']:.1f}%",
            title="🏆 Verdict",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"[bold red]❌ DÉFICITAIRE sur {result['days_back']} jours[/bold red]\n\n"
            f"  ROI: {result['roi']:+.2f}% │ Yield: {result['yield']:+.2f}% │ "
            f"Sharpe: {result['sharpe']:.2f}\n"
            f"  {result['total_bets']} paris │ Winrate: {result['winrate']:.1f}% │ "
            f"Max DD: {result['max_drawdown']:.1f}%",
            title="⚠️ Verdict",
            border_style="red",
        ))
