"""
betX – Quick Scan : scan rapide des value bets du jour.

Source UNIQUE : ESPN API (gratuit, sans clé).
  - Scoreboard → matchs du jour + cotes DraftKings (1X2, O/U)
  - Standings  → classements + home/away split pour le modèle Poisson

Usage :
    python -m betx.pipeline.quick_scan
    python -m betx.pipeline.quick_scan --min-edge 0.05
"""

from __future__ import annotations

import argparse
import math
from datetime import datetime, timezone
from dataclasses import dataclass, field

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from betx.config import settings
from betx.models.football_model import FootballModel, TeamStats, FootballPrediction
from betx.engine.value_engine import ValueEngine, ValueBet
from betx.engine.staking_engine import StakingEngine, StakeSuggestion
from betx.logger import get_logger

log = get_logger("quick_scan")
console = Console()

# Metadata du dernier scan (exposé pour daily_scan.py)
_last_scan_events_count: int = 0

# Moyennes de ligue ESPN (calculées pendant l'enrichissement)
# {event_key: (avg_home_goals, avg_away_goals)}
_espn_league_averages: dict[str, tuple[float, float]] = {}

# Clés des matchs enrichis en stats euro (UCL/Europa)
_euro_enriched_keys: set[str] = set()

# Contexte des matchs (exposé pour daily_scan.py)
# {event_key: MatchContext}
_match_contexts: dict = {}

# Données d'analyse détaillée (exposé pour daily_scan.py)
# {event_key: dict avec lambdas, probas, edge, etc.}
_match_analysis: dict[str, dict] = {}


# ═══════════════════════════════════════════════════════════════════════
# Parsed Event : structure interne pour le pipeline
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ParsedEvent:
    """Un match parsé depuis ESPN avec cotes."""

    sport: str           # "football"
    sport_label: str     # "⚽ Premier League"
    sport_key: str       # "soccer_epl"
    espn_slug: str       # "eng.1"
    home_team: str
    away_team: str
    home_espn_id: str
    away_espn_id: str
    commence_time: datetime
    espn_event_id: str = ""
    bookmaker: str = "DraftKings"
    # Cotes 1X2 (décimales)
    odds_home: float = 0.0
    odds_draw: float = 0.0
    odds_away: float = 0.0
    # Over/Under
    over_under_line: float = 0.0
    odds_over: float = 0.0
    odds_under: float = 0.0
    has_odds: bool = False
    # Consensus (probabilités implicites normalisées)
    consensus: dict[str, float] = field(default_factory=dict)


def _fixtures_to_events() -> list[ParsedEvent]:
    """
    Récupère les matchs du jour via ESPN et les convertit en ParsedEvent.
    """
    from betx.data.espn_collector import fetch_today_fixtures

    fixtures = fetch_today_fixtures()
    events: list[ParsedEvent] = []

    for fix in fixtures:
        try:
            dt = datetime.fromisoformat(fix.commence_time.replace("Z", "+00:00"))
        except Exception:
            dt = datetime.now(timezone.utc)

        ev = ParsedEvent(
            sport="football",
            sport_label=fix.league_label,
            sport_key=fix.sport_key,
            espn_slug=fix.espn_slug,
            home_team=fix.home_team,
            away_team=fix.away_team,
            home_espn_id=fix.home_espn_id,
            away_espn_id=fix.away_espn_id,
            commence_time=dt,
            espn_event_id=fix.espn_event_id,
            bookmaker=fix.bookmaker,
            odds_home=fix.odds_home,
            odds_draw=fix.odds_draw,
            odds_away=fix.odds_away,
            over_under_line=fix.over_under,
            odds_over=fix.odds_over,
            odds_under=fix.odds_under,
            has_odds=fix.has_odds,
        )

        # Consensus = probabilités implicites normalisées
        if ev.has_odds:
            raw_h = 1 / ev.odds_home if ev.odds_home > 1 else 0.01
            raw_d = 1 / ev.odds_draw if ev.odds_draw > 1 else 0.01
            raw_a = 1 / ev.odds_away if ev.odds_away > 1 else 0.01
            total = raw_h + raw_d + raw_a
            ev.consensus = {
                "home": raw_h / total,
                "draw": raw_d / total,
                "away": raw_a / total,
            }
        events.append(ev)

    return events


# ═══════════════════════════════════════════════════════════════════════
# Enrichissement ESPN : standings + home/away split
# ═══════════════════════════════════════════════════════════════════════

def enrich_football_events(events: list[ParsedEvent]) -> dict[str, dict[str, TeamStats]]:
    """
    Enrichit les matchs avec les données ESPN (saison en cours).

    Pour les compétitions européennes (UCL, Europa), cherche les stats
    de chaque équipe dans sa ligue domestique.

    Returns:
        Dict {event_key: {"home": TeamStats, "away": TeamStats}}
    """
    from betx.data.espn_collector import (
        ESPN_LEAGUE_MAP,
        ESPN_EURO_COMPS,
        ESPN_MAIN_LEAGUES,
        load_all_leagues,
        find_team_in_league,
        to_team_stats,
        compute_league_averages,
        compute_euro_team_stats,
        compute_euro_league_averages,
    )

    enriched: dict[str, dict[str, TeamStats]] = {}
    if not events:
        return enriched

    # Déterminer les ligues nécessaires pour les stats
    needed_sport_keys = {ev.sport_key for ev in events}
    has_euro = any(
        ESPN_LEAGUE_MAP.get(sk) in ESPN_EURO_COMPS
        for sk in needed_sport_keys
    )

    console.print(
        f"\n[bold green]📊 Enrichissement ESPN "
        f"(saison en cours, {len(events)} matchs)...[/bold green]\n"
    )

    # Toujours charger TOUTES les ligues domestiques (nécessaire pour UCL/Europa)
    espn_data = load_all_leagues(with_home_away=True)

    if not espn_data:
        console.print("  [red]❌ ESPN indisponible.[/red]\n")
        return enriched

    # Pré-calculer les moyennes de chaque ligue
    league_avgs: dict[str, tuple[float, float]] = {}
    for slug, teams in espn_data.items():
        if slug in ESPN_EURO_COMPS:
            continue  # pas de moyennes pour les compétitions européennes
        avg_h, avg_a, avg_t = compute_league_averages(teams)
        league_avgs[slug] = (avg_h, avg_a)
        console.print(
            f"    📈 {slug}: moy. ligue = {avg_h:.2f} dom / "
            f"{avg_a:.2f} ext (total {avg_t:.2f})"
        )

    global _espn_league_averages, _euro_enriched_keys
    _espn_league_averages = {}
    _euro_enriched_keys = set()

    matched = 0
    unmatched: set[str] = set()

    for ev in events:
        event_key = f"{ev.home_team}_{ev.away_team}"
        espn_slug = ESPN_LEAGUE_MAP.get(ev.sport_key)
        if not espn_slug:
            continue

        # ── Compétition européenne : stats UCL/Europa directes ──
        if espn_slug in ESPN_EURO_COMPS:
            # Récupérer les stats de chaque équipe DANS la compétition euro
            home_euro_td = compute_euro_team_stats(
                espn_slug, ev.home_espn_id, ev.home_team
            )
            away_euro_td = compute_euro_team_stats(
                espn_slug, ev.away_espn_id, ev.away_team
            )

            if not home_euro_td:
                unmatched.add(f"{ev.home_team} (euro)")
            if not away_euro_td:
                unmatched.add(f"{ev.away_team} (euro)")

            if home_euro_td and away_euro_td:
                enriched[event_key] = {
                    "home": to_team_stats(home_euro_td, is_home=True),
                    "away": to_team_stats(away_euro_td, is_home=False),
                }
                # Moyennes de la compétition euro (calculées à partir des 2 équipes)
                euro_avg = compute_euro_league_averages(
                    espn_slug,
                    [(ev.home_espn_id, ev.home_team),
                     (ev.away_espn_id, ev.away_team)],
                )
                _espn_league_averages[event_key] = (euro_avg[0], euro_avg[1])
                # Marquer comme euro pour le calcul ELO
                _euro_enriched_keys.add(event_key)
                console.print(
                    f"    🏆 {ev.home_team} vs {ev.away_team} → "
                    f"stats {espn_slug} ({home_euro_td.gp}J/{away_euro_td.gp}J)"
                )
                matched += 1
            elif home_euro_td or away_euro_td:
                # Fallback : chercher les stats domestiques pour l'équipe manquante
                for dom_slug, lt in espn_data.items():
                    if dom_slug in ESPN_EURO_COMPS:
                        continue
                    if not home_euro_td:
                        found = find_team_in_league(ev.home_team, lt)
                        if found:
                            home_euro_td = found
                    if not away_euro_td:
                        found = find_team_in_league(ev.away_team, lt)
                        if found:
                            away_euro_td = found
                if home_euro_td and away_euro_td:
                    enriched[event_key] = {
                        "home": to_team_stats(home_euro_td, is_home=True),
                        "away": to_team_stats(away_euro_td, is_home=False),
                    }
                    _espn_league_averages[event_key] = (1.45, 1.20)
                    matched += 1
            continue

        # ── Ligue domestique classique ──
        if espn_slug not in espn_data:
            continue

        league_teams = espn_data[espn_slug]
        try:
            home_td = find_team_in_league(ev.home_team, league_teams)
            away_td = find_team_in_league(ev.away_team, league_teams)

            if not home_td:
                unmatched.add(ev.home_team)
            if not away_td:
                unmatched.add(ev.away_team)

            if home_td and away_td:
                enriched[event_key] = {
                    "home": to_team_stats(home_td, is_home=True),
                    "away": to_team_stats(away_td, is_home=False),
                }
                if espn_slug in league_avgs:
                    _espn_league_averages[event_key] = league_avgs[espn_slug]
                matched += 1
        except Exception as e:
            log.warning(f"Enrichissement échoué pour {event_key}: {e}")

    console.print(f"\n  [bold green]✅ {matched}/{len(events)} matchs enrichis[/bold green]")
    if unmatched:
        sample = sorted(unmatched)[:5]
        extra = f" (+{len(unmatched) - 5} autres)" if len(unmatched) > 5 else ""
        console.print(f"  ⚠️  Non matchés : {', '.join(sample)}{extra}")
    console.print()
    return enriched


# ═══════════════════════════════════════════════════════════════════════
# Prédiction football (Poisson + Dixon-Coles)
# ═══════════════════════════════════════════════════════════════════════

def predict_football(
    ev: ParsedEvent,
    real_stats: dict[str, TeamStats] | None = None,
) -> dict[str, float]:
    """
    Génère les prédictions football.

    Mode enrichi (real_stats dispo) : λ Poisson basé sur les vrais buts
    de la saison ESPN + ELO dérivé du goal difference.
    Mode dégradé : estimation depuis le consensus bookmaker.
    """
    model = FootballModel()
    c = ev.consensus

    p_home = c.get("home", 0.4)
    p_draw = c.get("draw", 0.3)
    p_away = c.get("away", 0.3)
    strength_ratio = math.log(max(p_home, 0.05) / max(p_away, 0.05))

    if real_stats:
        # ══ MODE ENRICHI ══
        home_stats = real_stats["home"]
        away_stats = real_stats["away"]

        # Stats ESPN déjà ventilées home/away → résidu HA minime
        model.cfg.home_advantage = 0.03

        # Vraies moyennes de ligue ESPN
        event_key = f"{ev.home_team}_{ev.away_team}"
        if event_key in _espn_league_averages:
            avg_h, avg_a = _espn_league_averages[event_key]
            model.set_league_averages(avg_h + avg_a, avg_h, avg_a)

        # ELO intelligent basé sur GD + classement
        home_gd = home_stats.avg_goals_scored - home_stats.avg_goals_conceded
        away_gd = away_stats.avg_goals_scored - away_stats.avg_goals_conceded

        # Bonus classement (points/match = proxy de force dans la ligue)
        ctx = _match_contexts.get(event_key)
        home_rank_bonus = 0.0
        away_rank_bonus = 0.0
        if ctx and ctx.home.rank > 0 and ctx.away.rank > 0:
            # ppg = points per game → proxy de force relative
            h_ppg = ctx.home.points / max(ctx.league_size, 1)
            a_ppg = ctx.away.points / max(ctx.league_size, 1)
            home_rank_bonus = h_ppg * 50
            away_rank_bonus = a_ppg * 50

        gd_diff = home_gd - away_gd
        home_stats.elo = 1500 + gd_diff * 80 + home_rank_bonus
        home_stats.home_elo = home_stats.elo + 50
        away_stats.elo = 1500 - gd_diff * 80 + away_rank_bonus
        away_stats.away_elo = away_stats.elo

        # Facteur de pression classement (si contexte disponible)
        ctx = _match_contexts.get(event_key)
        if ctx:
            avg_pressure = (ctx.home.pressure + ctx.away.pressure) / 2
            home_stats.match_importance = avg_pressure
            away_stats.match_importance = avg_pressure

        pred = model.predict(home_stats, away_stats)

        CALIB = 0.97

        def calibrate(p: float) -> float:
            return 0.5 + (p - 0.5) * CALIB

        # Stocker l'analyse détaillée pour le Telegram
        global _match_analysis
        avg_h_used = model.league_avg_home_goals
        avg_a_used = model.league_avg_away_goals
        is_euro = event_key in _euro_enriched_keys
        _match_analysis[event_key] = {
            "home_name": ev.home_team,
            "away_name": ev.away_team,
            "home_scored": home_stats.avg_goals_scored,
            "home_conceded": home_stats.avg_goals_conceded,
            "away_scored": away_stats.avg_goals_scored,
            "away_conceded": away_stats.avg_goals_conceded,
            "home_elo": home_stats.elo,
            "away_elo": away_stats.elo,
            "home_form": home_stats.recent_form,
            "away_form": away_stats.recent_form,
            "avg_home": avg_h_used,
            "avg_away": avg_a_used,
            "lambda_home": pred.lambda_home,
            "lambda_away": pred.lambda_away,
            "p_home": calibrate(pred.p_home),
            "p_draw": calibrate(pred.p_draw),
            "p_away": calibrate(pred.p_away),
            "p_over_25": pred.p_over_25,
            "p_btts": pred.p_btts,
            "odds_home": ev.odds_home,
            "odds_draw": ev.odds_draw,
            "odds_away": ev.odds_away,
            "is_euro": is_euro,
            "enriched": True,
        }

        return {
            "home": calibrate(pred.p_home),
            "draw": calibrate(pred.p_draw),
            "away": calibrate(pred.p_away),
            "over_2.5": pred.p_over_25,
            "over_1.5": pred.p_over_15,
            "over_3.5": pred.p_over_35,
            "btts": pred.p_btts,
        }
    else:
        # ══ MODE DÉGRADÉ ══
        avg_goals = 2.7
        home_goals = avg_goals / 2 * math.exp(strength_ratio * 0.3) + 0.15
        away_goals = avg_goals / 2 * math.exp(-strength_ratio * 0.3)

        home_stats = TeamStats(
            name=ev.home_team,
            avg_goals_scored=home_goals,
            avg_goals_conceded=away_goals,
            xg_for=home_goals,
            xg_against=away_goals,
            elo=1500 + strength_ratio * 150,
            home_elo=1500 + strength_ratio * 150 + 50,
        )
        away_stats = TeamStats(
            name=ev.away_team,
            avg_goals_scored=away_goals,
            avg_goals_conceded=home_goals,
            xg_for=away_goals,
            xg_against=home_goals,
            elo=1500 - strength_ratio * 150,
            away_elo=1500 - strength_ratio * 150,
        )

        pred = model.predict(home_stats, away_stats)
        CALIB = 0.92

        def calibrate(p: float) -> float:
            return 0.5 + (p - 0.5) * CALIB

        return {
            "home": calibrate(pred.p_home),
            "draw": calibrate(pred.p_draw),
            "away": calibrate(pred.p_away),
            "over_2.5": pred.p_over_25,
            "over_1.5": pred.p_over_15,
            "over_3.5": pred.p_over_35,
            "btts": pred.p_btts,
        }


# ═══════════════════════════════════════════════════════════════════════
# Value Bet Scanner
# ═══════════════════════════════════════════════════════════════════════

def scan_event(
    ev: ParsedEvent,
    value_engine: ValueEngine,
    match_id: int = 0,
    enriched_stats: dict[str, dict[str, TeamStats]] | None = None,
) -> list[ValueBet]:
    """Scanne un événement pour trouver des value bets."""
    if not ev.has_odds:
        return []

    event_key = f"{ev.home_team}_{ev.away_team}"
    real_stats = enriched_stats.get(event_key) if enriched_stats else None

    try:
        preds = predict_football(ev, real_stats=real_stats)
    except Exception as exc:
        if real_stats:
            log.warning(
                f"Mode enrichi échoué pour {event_key} ({exc}), fallback dégradé."
            )
            preds = predict_football(ev, real_stats=None)
        else:
            log.error(f"Prédiction échouée pour {event_key}: {exc}")
            return []

    value_bets: list[ValueBet] = []

    # Scanner les cotes 1X2
    odds_map = {
        "home": ev.odds_home,
        "draw": ev.odds_draw,
        "away": ev.odds_away,
    }
    for sel, odds_val in odds_map.items():
        if odds_val <= 1.0:
            continue
        prob = preds.get(sel)
        if prob is None:
            continue
        vb = value_engine.evaluate(
            match_id=match_id,
            sport="football",
            home_team=ev.home_team,
            away_team=ev.away_team,
            market="h2h",
            selection=sel,
            model_probability=prob,
            bookmaker_odds=odds_val,
            bookmaker=ev.bookmaker,
            model_name="betx_football_espn_v2",
        )
        if vb:
            value_bets.append(vb)

    return value_bets


# ═══════════════════════════════════════════════════════════════════════
# Rich Display
# ═══════════════════════════════════════════════════════════════════════

def display_results(
    all_bets: list[tuple[ValueBet, StakeSuggestion]],
    bankroll: float,
    events_count: int,
):
    """Affiche les résultats avec Rich."""
    console.print()
    console.print(
        Panel(
            f"[bold cyan]betX Quick Scan[/bold cyan] — "
            f"{datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
            f"Bankroll: [green]{bankroll:.2f}€[/green] │ "
            f"Matchs scannés: [yellow]{events_count}[/yellow] │ "
            f"Value bets: [{'green' if all_bets else 'red'}]"
            f"{len(all_bets)}[/{'green' if all_bets else 'red'}]",
            title="🎯 Scan Rapide — 100% ESPN",
            border_style="cyan",
        )
    )

    if not all_bets:
        console.print(
            "[yellow]  Aucun value bet détecté avec les seuils actuels.[/yellow]"
        )
        console.print(
            f"  (edge min: {settings.value.min_edge:.0%}, "
            f"cotes: {settings.value.min_odds}–{settings.value.max_odds})\n"
        )
        return

    all_bets.sort(key=lambda x: x[0].model_probability, reverse=True)

    BACKTEST_SIGNAL = {
        "away": ("🟢", "+30%"),
        "draw": ("🟢", "+28%"),
        "home": ("🟡", "-10%"),
    }

    table = Table(
        title="📊 Value Bets du Jour (Stratégie Backtest-Optimisée)",
        show_header=True,
        header_style="bold magenta",
        show_lines=True,
        expand=False,
    )
    table.add_column("", width=3)
    table.add_column("Match", width=35)
    table.add_column("Sélection", style="green", width=14)
    table.add_column("BT", justify="center", width=4)
    table.add_column("P(modèle)", justify="right", width=10)
    table.add_column("Cote", justify="right", width=7)
    table.add_column("Edge", justify="right", width=8)
    table.add_column("Mise", justify="right", style="yellow", width=10)
    table.add_column("Gain est.", justify="right", style="green", width=12)
    table.add_column("Bookmaker", width=14)

    total_stake = 0.0
    for vb, stake in all_bets:
        conf = (
            "⚡" if vb.confidence == "high"
            else "✅" if vb.confidence == "medium"
            else "📊"
        )
        sel_display = vb.selection.replace("_", " ").title()
        bt_emoji, _ = BACKTEST_SIGNAL.get(vb.selection, ("", ""))
        gain_estime = stake.stake_amount * (vb.bookmaker_odds - 1)

        table.add_row(
            conf,
            f"{vb.home_team}\nvs {vb.away_team}",
            sel_display,
            bt_emoji,
            f"{vb.model_probability:.1%}",
            f"{vb.bookmaker_odds:.2f}",
            f"{vb.edge:.1%}",
            f"{stake.stake_amount:.2f}€",
            f"+{gain_estime:.2f}€",
            vb.bookmaker,
        )
        total_stake += stake.stake_amount

    console.print(table)

    total_ev = sum(vb.ev * s.stake_amount for vb, s in all_bets)
    total_gain_estime = sum(
        s.stake_amount * (vb.bookmaker_odds - 1) for vb, s in all_bets
    )
    avg_edge = (
        sum(vb.edge for vb, _ in all_bets) / len(all_bets)
        if all_bets else 0
    )
    avg_odds = (
        sum(vb.bookmaker_odds for vb, _ in all_bets) / len(all_bets)
        if all_bets else 0
    )

    sel_counts: dict[str, int] = {}
    for vb, _ in all_bets:
        sel_counts[vb.selection] = sel_counts.get(vb.selection, 0) + 1
    sel_text = " │ ".join(
        f"{s.title()}: {n}"
        for s, n in sorted(sel_counts.items(), key=lambda x: -x[1])
    )

    console.print(
        Panel(
            f"[bold]Récapitulatif – Stratégie Backtest-Optimisée[/bold]\n\n"
            f"  Nombre de paris : [cyan]{len(all_bets)}[/cyan] (1 par match, 1X2)\n"
            f"  Répartition     : {sel_text}\n"
            f"  Mise totale      : [yellow]{total_stake:.2f}€[/yellow] "
            f"({total_stake / bankroll:.1%} de la bankroll)\n"
            f"  Gain potentiel   : [bold green]+{total_gain_estime:.2f}€[/bold green] "
            f"(si tous gagnants)\n"
            f"  EV totale        : [green]+{total_ev:.2f}€[/green]\n"
            f"  Edge moyen       : [green]{avg_edge:.1%}[/green]\n"
            f"  Cote moyenne     : {avg_odds:.2f}\n\n"
            f"  [dim]🔬 Backtest 2024-25 : 1X2 edge≥8% → +4.76% yield, "
            f"Sharpe 1.48[/dim]\n"
            f"  [dim]   🟢 Away/Draw = high yield │ 🟡 Home = prudence[/dim]",
            title="💰 Récapitulatif",
            border_style="green",
        )
    )
    console.print()


# ═══════════════════════════════════════════════════════════════════════
# Main scan
# ═══════════════════════════════════════════════════════════════════════

def quick_scan(
    sports: list[str] | None = None,
    min_edge: float | None = None,
    bankroll: float | None = None,
) -> list[tuple[ValueBet, StakeSuggestion]]:
    """
    Exécute un scan rapide des value bets du jour.
    Source unique : ESPN API (gratuit, sans clé).

    Returns:
        Liste de (ValueBet, StakeSuggestion)
    """
    bankroll = bankroll or settings.bankroll.initial_bankroll

    value_engine = ValueEngine()
    if min_edge is not None:
        value_engine.min_edge = min_edge

    staking_engine = StakingEngine()

    console.print("\n[bold cyan]🔍 Récupération des matchs ESPN...[/bold cyan]\n")

    # 1. Récupérer les matchs du jour + cotes via ESPN
    all_events = _fixtures_to_events()

    n_with_odds = sum(1 for e in all_events if e.has_odds)
    n_without = len(all_events) - n_with_odds

    for ev in all_events:
        status = "✅" if ev.has_odds else "⚠️  (sans cotes)"
        console.print(
            f"  {ev.sport_label}: {ev.home_team} vs {ev.away_team} {status}"
        )

    console.print(
        f"\n[bold]Total: {len(all_events)} matchs "
        f"({n_with_odds} avec cotes)[/bold]\n"
    )

    global _last_scan_events_count
    _last_scan_events_count = len(all_events)

    if not all_events:
        display_results([], bankroll, 0)
        return []

    # 2. Enrichir avec les stats ESPN (standings + home/away)
    console.print("[bold cyan]🧠 Analyse des value bets...[/bold cyan]")

    try:
        enriched_stats = enrich_football_events(all_events)
    except Exception as exc:
        log.warning(f"Enrichissement ESPN échoué ({exc}). Mode dégradé.")
        console.print(f"  [yellow]⚠️  Enrichissement échoué : {exc}[/yellow]\n")
        enriched_stats = {}

    n_enriched = len(enriched_stats)
    if n_enriched == 0:
        console.print(
            f"  [yellow]⚠️  0/{len(all_events)} enrichis → mode dégradé[/yellow]\n"
        )
    elif n_enriched < len(all_events):
        console.print(
            f"  [dim]📊 {n_enriched} enrichis + "
            f"{len(all_events) - n_enriched} dégradés[/dim]\n"
        )

    # 2b. Récupérer le contexte avancé (H2H, forme, classement, pression)
    global _match_contexts
    _match_contexts = {}

    console.print(
        "[bold cyan]🔍 Contexte avancé (H2H, classement, forme)...[/bold cyan]\n"
    )

    from betx.data.espn_collector import fetch_match_context, ESPNFixture

    for ev in all_events:
        event_key = f"{ev.home_team}_{ev.away_team}"
        try:
            # Créer un ESPNFixture temporaire pour l'appel
            tmp_fix = ESPNFixture(
                espn_slug=ev.espn_slug,
                sport_key=ev.sport_key,
                league_label=ev.sport_label,
                home_team=ev.home_team,
                away_team=ev.away_team,
                home_espn_id=ev.home_espn_id,
                away_espn_id=ev.away_espn_id,
                commence_time=ev.commence_time.isoformat(),
                espn_event_id=ev.espn_event_id,
            )
            ctx = fetch_match_context(tmp_fix)
            if ctx:
                _match_contexts[event_key] = ctx
                # Afficher le contexte
                h = ctx.home
                a = ctx.away
                console.print(
                    f"  ⚽ [bold]{ev.home_team}[/bold] vs [bold]{ev.away_team}[/bold]"
                )
                # Classement (seulement si disponible)
                if h.rank > 0 and a.rank > 0:
                    console.print(
                        f"     🏅 #{h.rank} {h.zone} ({h.points}pts) "
                        f"vs #{a.rank} {a.zone} ({a.points}pts)"
                    )
                else:
                    comp_label = ev.sport_label or "Coupe"
                    console.print(
                        f"     🏅 {comp_label} (pas de classement dispo)"
                    )
                console.print(
                    f"     📈 Forme: {h.form_str or '?'} vs {a.form_str or '?'}"
                )
                if ctx.h2h_games:
                    console.print(
                        f"     🤝 H2H: {ctx.h2h_summary}"
                    )
                # Détail des 5 derniers matchs
                for side, tc in [("DOM", h), ("EXT", a)]:
                    if tc.form_events:
                        form_lines = []
                        for fe in tc.form_events[:5]:
                            icon = {
                                "W": "✅", "D": "🟡", "L": "❌"
                            }.get(fe.result, "❓")
                            ha = "H" if fe.is_home else "A"
                            form_lines.append(
                                f"{icon}{fe.score}({ha} vs {fe.opponent})"
                            )
                        console.print(
                            f"     [{side}] {' '.join(form_lines)}"
                        )
                console.print()
        except Exception as exc:
            log.debug(f"Contexte indisponible pour {event_key}: {exc}")

    # 3. Scanner chaque événement
    all_value_bets: list[ValueBet] = []
    for i, ev in enumerate(all_events):
        vbs = scan_event(
            ev, value_engine, match_id=i, enriched_stats=enriched_stats,
        )
        all_value_bets.extend(vbs)

    # 4. Déduplier : UN SEUL pari par match (meilleur edge)
    best_per_match: dict[str, ValueBet] = {}
    for vb in all_value_bets:
        match_key = f"{vb.home_team}_{vb.away_team}"
        if match_key not in best_per_match or vb.edge > best_per_match[match_key].edge:
            best_per_match[match_key] = vb

    unique_vbs = sorted(
        best_per_match.values(),
        key=lambda x: x.model_probability,
        reverse=True,
    )

    # 5. Calculer les mises
    stakes = staking_engine.calculate_stakes_batch(
        unique_vbs, bankroll, max_total_exposure=1.00,
    )
    results = [(s.value_bet, s) for s in stakes]

    # 6. Afficher
    display_results(results, bankroll, len(all_events))

    return results


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="betX Quick Scan (ESPN only)")
    parser.add_argument(
        "--min-edge",
        type=float,
        default=None,
        help="Edge minimum (ex: 0.05 pour 5%%)",
    )
    parser.add_argument(
        "--bankroll",
        type=float,
        default=None,
        help="Bankroll (défaut: config)",
    )
    args = parser.parse_args()

    quick_scan(
        min_edge=args.min_edge,
        bankroll=args.bankroll,
    )


if __name__ == "__main__":
    main()
