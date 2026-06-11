"""
betX – Prédictions complètes phase de groupes Coupe du Monde 2026.

Utilise :
  1. Profils historiques API-Football (cache 24h, priorité)
  2. Classement FIFA 2026 comme ELO de fallback (aucune API requise)
  3. NationalMatchPredictor (Poisson + Dixon-Coles + Monte Carlo)

Usage :
    python predict_wc_groups.py              # toute la phase de groupes
    python predict_wc_groups.py --fetch      # enrichir le cache (max 80 req)
    python predict_wc_groups.py --date 2026-06-14   # un jour précis
    python predict_wc_groups.py --notify     # envoyer résultats sur Telegram
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

load_dotenv()
console = Console()

WC_JSON_FILE = Path("data/wc_predictions.json")

# ─── Classement FIFA juin 2026 (fallback ELO) ─────────────────────────────────
# Source : FIFA World Ranking juin 2026 (approximation par points FIFA → ELO)
# Formule : elo = 1200 + (rang_inverse / 48) * 600
# (équipe #1 mondiale → ~1800, équipe #48 → ~1200)
FIFA_RANKING_2026: dict[str, int] = {
    # Rang FIFA (approximatif juin 2026)
    "Argentina":        1,
    "France":           2,
    "England":          3,
    "Brazil":           4,
    "Spain":            5,
    "Belgium":          6,
    "Portugal":         7,
    "Netherlands":      8,
    "Germany":          9,
    "Morocco":         10,
    "Colombia":        11,
    "Uruguay":         12,
    "USA":             13,
    "Japan":           14,
    "Senegal":         15,
    "Mexico":          16,
    "Croatia":         17,
    "Denmark":         18,
    "Switzerland":     19,
    "Ecuador":         20,
    "Canada":          21,
    "Austria":         22,
    "Iran":            23,
    "South Korea":     24,
    "Turkey":          25,
    "Norway":          26,
    "Sweden":          27,
    "Australia":       28,
    "Ivory Coast":     29,
    "Scotland":        30,
    "Czech Republic":  31,
    "Czechia":         31,
    "Algeria":         32,
    "Saudi Arabia":    33,
    "Serbia":          34,
    "Ghana":           35,
    "Egypt":           36,
    "South Africa":    37,
    "Tunisia":         38,
    "Paraguay":        39,
    "Congo DR":        40,
    "Panama":          41,
    "Qatar":           42,
    "Jordan":          43,
    "Bosnia-Herz":     44,
    "Bosnia Herzegovina": 44,
    "Iraq":            45,
    "New Zealand":     46,
    "Uzbekistan":      47,
    "Cape Verde":      48,
    "Haiti":           49,
    "Curaçao":         50,
}


def fifa_elo(team_name: str, n_teams: int = 50) -> float:
    """Convertit le classement FIFA en ELO estimé."""
    rank = FIFA_RANKING_2026.get(team_name)
    if rank is None:
        # Chercher par similarité partielle
        for k, v in FIFA_RANKING_2026.items():
            if k.lower() in team_name.lower() or team_name.lower() in k.lower():
                rank = v
                break
    if rank is None:
        return 1450.0  # équipe inconnue → milieu de tableau
    # #1 → 1800, #50 → 1200
    return 1800.0 - (rank - 1) * (600.0 / (n_teams - 1))


# ─── Récupération du calendrier ESPN ──────────────────────────────────────────

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"


def fetch_group_matches(
    start: date = date(2026, 6, 11),
    end: date = date(2026, 6, 28),
) -> list[dict]:
    """Récupère tous les matchs de poule depuis ESPN."""
    all_matches = []
    d = start
    while d <= end:
        try:
            r = httpx.get(ESPN_BASE, params={"dates": d.strftime("%Y%m%d")}, timeout=15)
            events = r.json().get("events", [])
            for e in events:
                comp = e.get("competitions", [{}])[0]
                teams = comp.get("competitors", [])
                status = comp.get("status", {}).get("type", {})
                home = next((t for t in teams if t.get("homeAway") == "home"), {})
                away = next((t for t in teams if t.get("homeAway") == "away"), {})
                h_name = home.get("team", {}).get("displayName", "?")
                a_name = away.get("team", {}).get("displayName", "?")
                h_short = home.get("team", {}).get("shortDisplayName", h_name)
                a_short = away.get("team", {}).get("shortDisplayName", a_name)
                h_score = home.get("score")
                a_score = away.get("score")
                state = status.get("name", "")
                note = comp.get("notes", [{}])
                group = note[0].get("headline", "") if note else ""
                espn_id = e.get("id", "")

                # Exclure les matchs à élimination directe (équipes indéfinies)
                if "1A" in h_name or "2B" in h_name or "3RD" in h_name:
                    continue

                # Cotes ESPN (moneyline US → décimal)
                def _ml_to_dec(ml) -> float | None:
                    if ml is None:
                        return None
                    try:
                        v = float(str(ml).replace("+", ""))
                        if str(ml).upper() == "EVEN":
                            return 2.0
                        return round(1 + v / 100, 2) if v > 0 else round(1 + 100 / abs(v), 2)
                    except Exception:
                        return None

                odds_home = odds_draw = odds_away = None
                odds_raw = [x for x in comp.get("odds", []) if x]
                if odds_raw:
                    o = odds_raw[0]
                    ml = o.get("moneyline", {})
                    odds_home = _ml_to_dec(ml.get("home", {}).get("close", {}).get("odds"))
                    odds_away = _ml_to_dec(ml.get("away", {}).get("close", {}).get("odds"))
                    odds_draw = _ml_to_dec(o.get("drawOdds", {}).get("moneyLine"))

                all_matches.append({
                    "date": e.get("date", "")[:16],
                    "home": h_name,
                    "away": a_name,
                    "home_short": h_short,
                    "away_short": a_short,
                    "home_score": int(h_score) if h_score is not None else None,
                    "away_score": int(a_score) if a_score is not None else None,
                    "status": state,
                    "group": group,
                    "espn_id": espn_id,
                    "odds_home": odds_home,
                    "odds_draw": odds_draw,
                    "odds_away": odds_away,
                })
        except Exception as e:
            console.print(f"[yellow]⚠️  ESPN {d}: {e}[/yellow]")
        d += timedelta(days=1)
    return all_matches


# ─── Profil minimal basé sur FIFA ranking (sans API) ──────────────────────────

@dataclass
class MinimalProfile:
    """Profil minimal quand l'historique API n'est pas disponible."""
    team_name: str
    elo: float
    avg_scored: float = 1.20
    avg_conceded: float = 1.20
    form: list[str] = None  # type: ignore

    def __post_init__(self):
        if self.form is None:
            self.form = []
        # Calibrer les buts selon le niveau ELO
        # top team (elo≥1700): 1.6/0.7 | mid (1500): 1.2/1.2 | weak (1300): 0.85/1.65
        ratio = max(0.0, min(1.0, (self.elo - 1300) / 500))
        self.avg_scored = round(0.85 + ratio * 0.75, 3)
        self.avg_conceded = round(1.65 - ratio * 0.95, 3)


# ─── Prédiction par match ──────────────────────────────────────────────────────

def predict_match(
    home_name: str,
    away_name: str,
    national_profiles: dict,
) -> dict:
    """
    Prédit un match avec le meilleur profil disponible.
    Priorité : profil API complet > profil FIFA ranking minimal.
    """
    from betx.data.national_team_features import (
        NationalTeamFeatureSet,
        NationalMatchPredictor,
        INTL_AVG_GOALS_PER_TEAM,
    )

    home_api = national_profiles.get(home_name)
    away_api = national_profiles.get(away_name)

    predictor = NationalMatchPredictor()

    # Construire les features selon disponibilité
    if home_api and away_api:
        from betx.data.national_team_features import build_features
        feats = build_features(home_api, away_api, neutral=True, match_importance=1.8)
        source = "API"
    else:
        # Fallback FIFA ranking
        h_elo = fifa_elo(home_name) if not home_api else home_api.elo_estimate
        a_elo = fifa_elo(away_name) if not away_api else away_api.elo_estimate
        h_min = MinimalProfile(home_name, h_elo) if not home_api else None
        a_min = MinimalProfile(away_name, a_elo) if not away_api else None

        h_scored = home_api.weighted_lambda_scored(10) if home_api else (h_min.avg_scored if h_min else 1.2)
        h_conceded = home_api.weighted_lambda_conceded(10) if home_api else (h_min.avg_conceded if h_min else 1.2)
        a_scored = away_api.weighted_lambda_scored(10) if away_api else (a_min.avg_scored if a_min else 1.2)
        a_conceded = away_api.weighted_lambda_conceded(10) if away_api else (a_min.avg_conceded if a_min else 1.2)

        h_form = home_api.form_score(5) if home_api else 0.0
        a_form = away_api.form_score(5) if away_api else 0.0

        feats = NationalTeamFeatureSet(
            home_team=home_name,
            away_team=away_name,
            home_elo=h_elo,
            away_elo=a_elo,
            home_form_5=h_form,
            away_form_5=a_form,
            home_form_10=home_api.form_score(10) if home_api else 0.0,
            away_form_10=away_api.form_score(10) if away_api else 0.0,
            home_official_form=home_api.form_score(10, official_only=True) if home_api else 0.0,
            away_official_form=away_api.form_score(10, official_only=True) if away_api else 0.0,
            home_friendly_form=0.0,
            away_friendly_form=0.0,
            home_goals_for_10=h_scored,
            away_goals_for_10=a_scored,
            home_goals_against_10=h_conceded,
            away_goals_against_10=a_conceded,
            home_goals_for_5=h_scored,
            away_goals_for_5=a_scored,
            h2h_count=0,
            h2h_bias=0.0,
            neutral_ground=True,
            match_importance=1.8,
            home_sample_size=len(home_api.recent_matches) if home_api else 0,
            away_sample_size=len(away_api.recent_matches) if away_api else 0,
        )
        source = "FIFA" if not home_api and not away_api else "MIXED"

    probs = predictor.predict(feats, use_monte_carlo=True)
    top3 = sorted(probs.exact_scores.items(), key=lambda x: x[1], reverse=True)[:3]

    # P(Clean Sheet) = P(équipe adverse marque 0 buts)
    p_cs_home = sum(v for k, v in probs.exact_scores.items() if k.split("-")[1] == "0")
    p_cs_away = sum(v for k, v in probs.exact_scores.items() if k.split("-")[0] == "0")

    return {
        "p_home": probs.p_home_win,
        "p_draw": probs.p_draw,
        "p_away": probs.p_away_win,
        "lambda_home": probs.lambda_home,
        "lambda_away": probs.lambda_away,
        "top_scores": top3,
        "p_over_15": probs.p_over_15,
        "p_over_25": probs.p_over_25,
        "p_over_35": probs.p_over_35,
        "p_under_25": probs.p_under_25,
        "p_btts": probs.p_btts,
        "p_btts_no": probs.p_btts_no,
        "p_cs_home": round(p_cs_home, 4),
        "p_cs_away": round(p_cs_away, 4),
        "source": source,
    }


# ─── Chargement des profils (cache d'abord, API si nécessaire) ────────────────

def load_profiles(teams: list[str], fetch: bool = False) -> dict:
    """
    Charge les profils depuis le cache, optionnellement enrichit via API.

    Sans --fetch : 100% offline, n'utilise que le cache existant.
    Avec --fetch : appels API pour les équipes manquantes (max ~75 req).
    """
    from betx.data.national_team_collector import NationalTeamCollector

    collector = NationalTeamCollector()
    profiles = {}

    for team in sorted(set(teams)):
        key = team.lower().strip()

        # Vérifier si l'équipe est en cache (team_id + fixtures)
        team_id_entry = collector._cache.get("team_ids", {}).get(key, {})
        team_id = team_id_entry.get("id")
        in_fixture_cache = team_id and str(team_id) in collector._cache.get("fixtures", {})

        if in_fixture_cache:
            # Charger depuis le cache (0 requête API)
            profile = collector.get_profile(team)
            if profile and profile.recent_matches:
                profiles[team] = profile
        elif fetch:
            # Enrichissement API
            console.print(f"  📡 Chargement API : {team}...")
            profile = collector.get_profile(team)
            if profile and profile.recent_matches:
                profiles[team] = profile
                console.print(
                    f"     ✅ {len(profile.recent_matches)} matchs | ELO~{profile.elo_estimate:.0f}"
                )
            else:
                console.print(f"     ⚠️  Introuvable → fallback FIFA ranking")
        # Si pas en cache et pas --fetch : silencieux, sera géré via FIFA ranking

    n_api = len(profiles)
    n_total = len(set(teams))
    n_fifa = n_total - n_api
    console.print(
        f"\n  📊 [green]{n_api}[/green] équipes avec historique API | "
        f"[yellow]{n_fifa}[/yellow] via classement FIFA\n"
    )
    return profiles


# ─── Affichage ────────────────────────────────────────────────────────────────

def display_predictions(matches: list[dict], profiles: dict, filter_date: str | None = None):
    """Affiche les prédictions pour tous les matchs de poule."""
    # Filtrer si demandé
    if filter_date:
        matches = [m for m in matches if m["date"].startswith(filter_date)]

    # Grouper par jour
    by_day: dict[str, list] = {}
    for m in matches:
        day = m["date"][:10]
        by_day.setdefault(day, []).append(m)

    total_matches = len(matches)
    console.print(Panel(
        f"[bold cyan]betX – Prédictions Coupe du Monde 2026[/bold cyan]\n"
        f"{total_matches} matchs de poule | "
        f"Modèle : Poisson + Dixon-Coles + Monte Carlo 10k\n"
        f"Sources : {'API-Football (historique 2022-2024) + FIFA Ranking (fallback)'}",
        title="🌍 World Cup 2026 – Phase de groupes",
        border_style="cyan",
    ))

    for day, day_matches in sorted(by_day.items()):
        console.print(f"\n[bold yellow]📅 {day}[/bold yellow]")

        table = Table(
            show_header=True, header_style="bold magenta",
            show_lines=True, expand=True,
        )
        table.add_column("Match", width=32)
        table.add_column("Score favori", justify="center", width=12)
        table.add_column("Top 3 scores", width=30)
        table.add_column("P(1)", justify="right", width=7)
        table.add_column("P(X)", justify="right", width=7)
        table.add_column("P(2)", justify="right", width=7)
        table.add_column("O2.5", justify="right", width=7)
        table.add_column("BTTS", justify="right", width=7)
        table.add_column("λh/λa", justify="center", width=9)
        table.add_column("Src", justify="center", width=5)

        for m in sorted(day_matches, key=lambda x: x["date"]):
            home = m["home"]
            away = m["away"]
            status = m["status"]

            # Résultat réel si disponible
            if status == "STATUS_FINAL" and m["home_score"] is not None:
                result_str = f"[bold green]{m['home_score']}-{m['away_score']}[/bold green] ✅"
                table.add_row(
                    f"[bold]{m['home_short']}[/bold] vs {m['away_short']}",
                    result_str,
                    "[dim]match terminé[/dim]", "", "", "", "", "", "", "real",
                )
                continue

            try:
                pred = predict_match(home, away, profiles)
            except Exception as e:
                table.add_row(
                    f"{m['home_short']} vs {m['away_short']}",
                    "[red]erreur[/red]", str(e)[:28], "", "", "", "", "", "", "ERR",
                )
                continue

            # Score le plus probable
            best_score, best_prob = pred["top_scores"][0]
            h_goals, a_goals = best_score.split("-")
            if int(h_goals) > int(a_goals):
                winner_col = "green"
            elif int(h_goals) < int(a_goals):
                winner_col = "red"
            else:
                winner_col = "yellow"
            score_cell = f"[bold {winner_col}]{best_score}[/bold {winner_col}] ({best_prob*100:.0f}%)"

            # Top 3 scores
            top3_str = "  ".join(f"{sc} {p*100:.0f}%" for sc, p in pred["top_scores"][:3])

            # Favori
            ph, px, pa = pred["p_home"], pred["p_draw"], pred["p_away"]
            if ph > pa:
                ph_str = f"[bold green]{ph:.0%}[/bold green]"
                pa_str = f"{pa:.0%}"
            elif pa > ph:
                ph_str = f"{ph:.0%}"
                pa_str = f"[bold red]{pa:.0%}[/bold red]"
            else:
                ph_str = f"{ph:.0%}"
                pa_str = f"{pa:.0%}"

            src_color = {"API": "green", "MIXED": "yellow", "FIFA": "dim"}.get(pred["source"], "white")
            time_str = m["date"][11:16] + "Z"

            table.add_row(
                f"[bold]{m['home_short']}[/bold] vs {m['away_short']}\n[dim]{time_str}[/dim]",
                score_cell,
                top3_str,
                ph_str,
                f"{px:.0%}",
                pa_str,
                f"{pred['p_over_25']:.0%}",
                f"{pred['p_btts']:.0%}",
                f"{pred['lambda_home']:.2f}/{pred['lambda_away']:.2f}",
                f"[{src_color}]{pred['source']}[/{src_color}]",
            )

        console.print(table)

    console.print(
        "\n[dim]Légende : Src = source des données | "
        "[green]API[/green] = historique réel 2022-2024 | "
        "[yellow]MIXED[/yellow] = une équipe historique | "
        "FIFA = classement FIFA uniquement[/dim]\n"
    )


# ─── Export JSON ──────────────────────────────────────────────────────────────

def export_predictions(matches: list[dict], profiles: dict, filter_date: str | None = None) -> dict:
    """
    Calcule toutes les prédictions et les exporte en JSON.
    Retourne le dictionnaire exporté.
    """
    if filter_date:
        matches = [m for m in matches if m["date"].startswith(filter_date)]

    records = []
    for m in sorted(matches, key=lambda x: x["date"]):
        try:
            pred = predict_match(m["home"], m["away"], profiles)
        except Exception:
            pred = {}

        top3 = pred.get("top_scores", [])
        records.append({
            "date": m["date"],
            "home": m["home"],
            "away": m["away"],
            "home_short": m["home_short"],
            "away_short": m["away_short"],
            "status": m["status"],
            "home_score": m["home_score"],
            "away_score": m["away_score"],
            "prediction": {
                "p_home": round(pred.get("p_home", 0), 4),
                "p_draw": round(pred.get("p_draw", 0), 4),
                "p_away": round(pred.get("p_away", 0), 4),
                "lambda_home": round(pred.get("lambda_home", 0), 3),
                "lambda_away": round(pred.get("lambda_away", 0), 3),
                "p_over_15": round(pred.get("p_over_15", 0), 4),
                "p_over_25": round(pred.get("p_over_25", 0), 4),
                "p_over_35": round(pred.get("p_over_35", 0), 4),
                "p_under_25": round(pred.get("p_under_25", 0), 4),
                "p_btts": round(pred.get("p_btts", 0), 4),
                "p_btts_no": round(pred.get("p_btts_no", 0), 4),
                "p_cs_home": round(pred.get("p_cs_home", 0), 4),
                "p_cs_away": round(pred.get("p_cs_away", 0), 4),
                "top_scores": [{"score": sc, "prob": round(p, 4)} for sc, p in top3],
                "most_likely": top3[0][0] if top3 else "1-0",
                "source": pred.get("source", "FIFA"),
            } if pred else {},
        })

    data = {
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "total_matches": len(records),
        "matches": records,
    }
    WC_JSON_FILE.parent.mkdir(parents=True, exist_ok=True)
    WC_JSON_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    console.print(f"\n💾 Prédictions exportées → [cyan]{WC_JSON_FILE}[/cyan]")
    return data


# ─── Telegram ─────────────────────────────────────────────────────────────────

_TG_API = "https://api.telegram.org/bot{token}/{method}"
_TG_MAX = 4096


def _tg_send(token: str, chat_id: str, text: str) -> bool:
    """Envoie un message Telegram (découpe si nécessaire)."""
    chunks = []
    if len(text) <= _TG_MAX:
        chunks = [text]
    else:
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > _TG_MAX - 100:
                chunks.append(current)
                current = line
            else:
                current += ("\n" if current else "") + line
        if current:
            chunks.append(current)

    ok = True
    for chunk in chunks:
        try:
            r = httpx.post(
                _TG_API.format(token=token, method="sendMessage"),
                json={"chat_id": chat_id, "text": chunk,
                      "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=30,
            )
            if r.status_code != 200:
                # Fallback : réessayer sans parse_mode si erreur HTML
                err = r.json().get("description", "")
                if "parse" in err.lower() or "entities" in err.lower():
                    import re as _re
                    clean = _re.sub(r'<[^>]+>', '', chunk)
                    r2 = httpx.post(
                        _TG_API.format(token=token, method="sendMessage"),
                        json={"chat_id": chat_id, "text": clean,
                              "disable_web_page_preview": True},
                        timeout=30,
                    )
                    if r2.status_code != 200:
                        console.print(f"  [red]❌ Telegram: {r2.json().get('description', r2.text)}[/red]")
                        ok = False
                else:
                    console.print(f"  [red]❌ Telegram: {err}[/red]")
                    ok = False
        except Exception as e:
            console.print(f"  [red]❌ Telegram: {e}[/red]")
            ok = False
        time.sleep(0.3)
    return ok


def build_wc_telegram(data: dict, filter_date: str | None = None) -> list[str]:
    """
    Construit 3 messages Telegram distincts pour le canal betX CdM :

    MSG 1 – 🎯 Value Bets (bets avec edge > 5% vs bookmaker)
    MSG 2 – 📊 Prédictions concises (tous matchs du jour + demain)
    MSG 3 – 💎 Combiné du jour (si P(combiné) > 15% ET cote > 4.00)
    """
    today_str = filter_date or date.today().isoformat()
    tomorrow_str = (date.fromisoformat(today_str) + timedelta(days=1)).isoformat()
    day_after_str = (date.fromisoformat(today_str) + timedelta(days=2)).isoformat()

    def _is_today(m: dict) -> bool:
        return (
            m["date"].startswith(today_str)
            or (m["date"].startswith(tomorrow_str) and m["date"][11:13] < "06")
        )

    def _is_tomorrow(m: dict) -> bool:
        if m["date"].startswith(tomorrow_str) and m["date"][11:13] >= "06":
            return True
        if m["date"].startswith(day_after_str) and m["date"][11:13] < "06":
            return True
        return False

    today_matches = [m for m in data["matches"] if m.get("prediction") and _is_today(m)]
    tomorrow_matches = [m for m in data["matches"] if m.get("prediction") and _is_tomorrow(m)]
    all_day_matches = sorted(today_matches, key=lambda x: x["date"]) + \
                      sorted(tomorrow_matches, key=lambda x: x["date"])

    if not all_day_matches:
        return []

    # ── Helpers ──────────────────────────────────────────────────────────

    def _implied(odds: float | None) -> float | None:
        """Probabilité implicite sans marge."""
        return (1.0 / odds) if odds and odds > 1.0 else None

    def _edge(model_p: float, market_odds: float | None) -> float | None:
        """Edge = modèle - marché."""
        imp = _implied(market_odds)
        return round(model_p - imp, 4) if imp else None

    def _conf_badge(edge: float) -> str:
        if edge >= 0.20: return "🟢🟢🟢"
        if edge >= 0.15: return "🟢🟢"
        if edge >= 0.10: return "🟢"
        return "🟡"

    COUNTRY_FLAGS = {
        "mexico": "🇲🇽", "south africa": "🇿🇦", "south korea": "🇰🇷",
        "czechia": "🇨🇿", "czech republic": "🇨🇿", "canada": "🇨🇦",
        "bosnia-herzegovina": "🇧🇦", "usa": "🇺🇸", "united states": "🇺🇸",
        "paraguay": "🇵🇾", "qatar": "🇶🇦", "switzerland": "🇨🇭",
        "brazil": "🇧🇷", "morocco": "🇲🇦", "haiti": "🇭🇹",
        "scotland": "🏴🇬🇧", "australia": "🇦🇺", "türkiye": "🇹🇷",
        "germany": "🇩🇪", "curaçao": "🇨🇼", "netherlands": "🇳🇱",
        "japan": "🇯🇵", "ivory coast": "🇨🇮", "ecuador": "🇪🇨",
        "sweden": "🇸🇪", "tunisia": "🇹🇳", "spain": "🇪🇸",
        "cape verde": "🇨🇻", "belgium": "🇧🇪", "egypt": "🇪🇬",
        "saudi arabia": "🇸🇦", "uruguay": "🇺🇾", "iran": "🇮🇷",
        "new zealand": "🇳🇿", "france": "🇫🇷", "senegal": "🇸🇳",
        "iraq": "🇮🇶", "norway": "🇳🇴", "argentina": "🇦🇷",
        "algeria": "🇩🇿", "austria": "🇦🇹", "jordan": "🇯🇴",
        "portugal": "🇵🇹", "congo dr": "🇨🇩", "england": "🏴🇬🇧",
        "croatia": "🇭🇷", "ghana": "🇬🇭", "panama": "🇵🇦",
        "uzbekistan": "🇺🇿", "colombia": "🇨🇴",
    }

    def _flag(name: str) -> str:
        flag = COUNTRY_FLAGS.get(name.lower(), "")
        return flag  # Les drapeaux emoji sont OK dans Telegram HTML mode

    # ── MSG 1 : Value Bets ────────────────────────────────────────────────

    value_bets = []
    for m in all_day_matches:
        pred = m.get("prediction", {})
        ph = pred.get("p_home", 0)
        px = pred.get("p_draw", 0)
        pa = pred.get("p_away", 0)
        p_o25 = pred.get("p_over_25", 0)
        p_u25 = pred.get("p_under_25", 0)
        p_btts = pred.get("p_btts", 0)
        p_btts_no = pred.get("p_btts_no", 0)
        oh = m.get("odds_home")
        ox = m.get("odds_draw")
        oa = m.get("odds_away")
        time_str = m["date"][11:16] + "Z"
        is_tmrw = _is_tomorrow(m)
        day_tag = " [demain]" if is_tmrw else ""
        match_label = f"{m['home_short']}-{m['away_short']}"

        # Marchés 1X2
        for sel, prob, odds, label in [
            ("home", ph, oh, m["home_short"]),
            ("draw", px, ox, f"Nul {match_label}"),
            ("away", pa, oa, m["away_short"]),
        ]:
            if not odds or odds <= 1.0:
                continue
            e = _edge(prob, odds)
            if e and e >= 0.05:
                value_bets.append({
                    "label": label, "sel": sel, "odds": odds,
                    "model_p": prob, "implied_p": _implied(odds),
                    "edge": e, "time": time_str, "day_tag": day_tag,
                    "home": m["home"], "away": m["away"],
                    "home_short": m["home_short"], "away_short": m["away_short"],
                    "market": "1X2",
                })

        # Marchés Over/Under (cotes ESPN si disponibles, sinon cote standard ~1.90)
        # ESPN expose peu les O/U sur CdM → on utilise 1.90 comme proxy marché
        STD_OU = 1.90
        for sel, prob, label in [
            ("over_25", p_o25, f"Over 2.5 {match_label}"),
            ("under_25", p_u25, f"Under 2.5 {match_label}"),
        ]:
            e = _edge(prob, STD_OU)
            if e and e >= 0.07:  # seuil légèrement plus haut (proxy cote)
                value_bets.append({
                    "label": label, "sel": sel, "odds": STD_OU,
                    "model_p": prob, "implied_p": _implied(STD_OU),
                    "edge": e, "time": time_str, "day_tag": day_tag,
                    "home": m["home"], "away": m["away"],
                    "home_short": m["home_short"], "away_short": m["away_short"],
                    "market": "O/U",
                })

        # Marché BTTS
        for sel, prob, label in [
            ("btts_yes", p_btts, f"BTTS Oui {match_label}"),
            ("btts_no", p_btts_no, f"BTTS Non {match_label}"),
        ]:
            e = _edge(prob, STD_OU)
            if e and e >= 0.07:
                value_bets.append({
                    "label": label, "sel": sel, "odds": STD_OU,
                    "model_p": prob, "implied_p": _implied(STD_OU),
                    "edge": e, "time": time_str, "day_tag": day_tag,
                    "home": m["home"], "away": m["away"],
                    "home_short": m["home_short"], "away_short": m["away_short"],
                    "market": "BTTS",
                })

    value_bets.sort(key=lambda x: x["edge"], reverse=True)

    if value_bets:
        lines1 = [f"🎯 <b>betX CdM – Value Bets</b>", ""]
        for vb in value_bets:
            badge = _conf_badge(vb["edge"])
            mkt_tag = f" [{vb['market']}]" if vb.get("market", "1X2") != "1X2" else ""
            odds_tag = "@~" if vb.get("market") in ("O/U", "BTTS") else "@"
            lines1 += [
                f"{badge} {_flag(vb['label'].split()[0] if vb['label'] else '')} <b>{vb['label']}</b>{mkt_tag} {odds_tag}{vb['odds']:.2f}{vb['day_tag']}",
                f"   📈 Modèle: <b>{vb['model_p']:.0%}</b>  │  📊 Marché: {vb['implied_p']:.0%}",
                f"   🔥 Edge: <b>+{vb['edge']*100:.0f} pts</b>",
                f"   🕐 {vb['time']}",
                "",
            ]
        lines1 += [
            "━" * 28,
            f'📊 <a href="http://213.199.41.168">Dashboard complet</a>',
        ]
        msg1 = "\n".join(lines1)
    else:
        msg1 = (
            "🎯 <b>betX CdM – Value Bets</b>\n\n"
            "⚪ Aucun value bet détecté aujourd'hui.\n"
            "<i>(edge < 5% sur toutes les sélections)</i>\n\n"
            f'📊 <a href="http://213.199.41.168">Dashboard complet</a>'
        )

    # ── MSG 2 : Prédictions avec comparaison modèle vs marché ────────────

    # Charger les cotes ESPN du scan du jour (disponibles pour matchs d'aujourd'hui)
    _bets_odds: dict[str, dict] = {}
    try:
        import json as _json
        _daily = _json.loads(Path("data/daily_bets.json").read_text())
        for b in _daily.get("bets", []):
            k = f"{b['home_team']}_{b['away_team']}"
            if k not in _bets_odds:
                _bets_odds[k] = {}
            _bets_odds[k][b["selection"]] = b.get("odds", 0)
        # Récupérer aussi toutes les cotes depuis l'analyse
        for b in _daily.get("bets", []):
            k = f"{b['home_team']}_{b['away_team']}"
            a = b.get("analysis", {})
            _bets_odds[k]["_odds_home"] = a.get("odds_home", 0)
            _bets_odds[k]["_odds_draw"] = a.get("odds_draw", 0)
            _bets_odds[k]["_odds_away"] = a.get("odds_away", 0)
    except Exception:
        pass

    lines2 = ["📊 <b>Prédictions betX – Modèle vs Marché</b>", ""]

    def _ev(model_p: float, odds: float) -> float:
        """EV = modèle × (cote - 1) - (1 - modèle)"""
        return model_p * (odds - 1) - (1 - model_p)

    def _edge_display(model_p: float, odds: float) -> str:
        if not odds or odds <= 1.0:
            return ""
        impl = 1.0 / odds
        e = model_p - impl
        ev = _ev(model_p, odds)
        sign = "🟢" if e >= 0.05 else ("🟡" if e > 0 else "🔴")
        return f"{sign} Edge: <b>{e*100:+.1f}%</b>  │  EV: <b>{ev*100:+.1f}%</b>  @{odds:.2f}"

    def _section(matches: list[dict], label: str):
        nonlocal lines2
        if not matches:
            return
        lines2.append(f"<b>{label}</b>")
        for m in matches:
            pred = m.get("prediction", {})
            ph = pred.get("p_home", 0)
            px = pred.get("p_draw", 0)
            pa = pred.get("p_away", 0)
            top1 = pred.get("top_scores", [{}])[0] if pred.get("top_scores") else {}
            score = top1.get("score", "?")
            prob_score = top1.get("prob", 0)
            state = m.get("status", "")
            time_str = m["date"][11:16] + "Z"
            lh = pred.get("lambda_home", 0)
            la = pred.get("lambda_away", 0)

            # Résultat si disponible
            if state == "STATUS_FINAL" and m.get("home_score") is not None:
                actual = f"{m['home_score']}-{m['away_score']}"
                correct = " 🎯" if actual == score else ""
                result_line = f"✅ Résultat : <b>{actual}</b>{correct}"
            else:
                result_line = f"🕐 {time_str}"

            # Cotes ESPN (match du jour si disponible)
            k = f"{m['home']}_{m['away']}"
            bk = _bets_odds.get(k, {})
            oh = m.get("odds_home") or bk.get("_odds_home") or 0
            ox = m.get("odds_draw") or bk.get("_odds_draw") or 0
            oa = m.get("odds_away") or bk.get("_odds_away") or 0

            # Probabilités implicites bookmaker (normalisées si les 3 cotes dispo)
            has_odds = oh > 1 and ox > 1 and oa > 1
            if has_odds:
                total_impl = 1/oh + 1/ox + 1/oa
                bk_h = round(1/oh/total_impl, 3)
                bk_x = round(1/ox/total_impl, 3)
                bk_a = round(1/oa/total_impl, 3)
                bk_line = f"  📊 Marché : {bk_h:.0%} / {bk_x:.0%} / {bk_a:.0%}  <i>(DraftKings, marge déduite)</i>"
                # Edge 1X2
                edges = [
                    _edge_display(ph, oh),
                    _edge_display(px, ox),
                    _edge_display(pa, oa),
                ]
                best_edge = max(
                    [(ph, oh, m["home_short"]), (px, ox, "Nul"), (pa, oa, m["away_short"])],
                    key=lambda x: (x[0] - 1/x[1]) if x[1] > 1 else -99
                )
                e_best = best_edge[0] - 1/best_edge[1] if best_edge[1] > 1 else 0
                rec_line = ""
                if e_best >= 0.05:
                    rec_line = f"  💎 Recommandation : <b>{best_edge[2]}</b> — {_edge_display(best_edge[0], best_edge[1])}"
                elif e_best > 0:
                    rec_line = f"  ⚪ Pas de value significatif (edge max {e_best*100:+.1f}%)"
                else:
                    rec_line = f"  🔴 Bookmaker plus optimiste que le modèle"
            else:
                bk_line = "  📊 Cotes ESPN non disponibles <i>(matchs à venir)</i>"
                rec_line = ""

            lines2 += [
                f"{_flag(m['home'])} <b>{m['home_short']}</b> vs {_flag(m['away'])} <b>{m['away_short']}</b>  │  {result_line}",
                f"  🔢 Modèle : 1️⃣ {ph:.0%}  🤝 {px:.0%}  2️⃣ {pa:.0%}",
                bk_line,
                rec_line if rec_line else None,
                f"  ⚽ Score prédit : <b>{score}</b> ({prob_score*100:.0f}%)"
                f"  │  λ {lh:.2f}–{la:.2f}",
                f"  📈 O1.5 {pred.get('p_over_15',0):.0%}"
                f"  │ <b>O2.5 {pred.get('p_over_25',0):.0%}</b>"
                f"  │ O3.5 {pred.get('p_over_35',0):.0%}",
                f"  🔀 BTTS <b>{pred.get('p_btts',0):.0%}</b>"
                f"  │ CS dom {pred.get('p_cs_home',0):.0%}"
                f"  │ CS ext {pred.get('p_cs_away',0):.0%}",
                "",
            ]
            # Filtrer les None
            lines2 = [l for l in lines2 if l is not None]

    _section(sorted(today_matches, key=lambda x: x["date"]), "📅 Aujourd'hui")
    _section(sorted(tomorrow_matches, key=lambda x: x["date"]), "📅 Demain")

    lines2.append(f'<i>Modèle : Poisson + Dixon-Coles + MC 10k | Edge = modèle − marché</i>')
    msg2 = "\n".join(lines2)

    # ── MSG 3 : Combiné conditionnel ─────────────────────────────────────

    msg3 = None
    if len(value_bets) >= 2:
        # Prendre les 2 meilleurs value bets indépendants (matchs différents)
        seen_matches: set[str] = set()
        combo = []
        for vb in value_bets:
            key = f"{vb['home']}_{vb['away']}"
            if key not in seen_matches:
                combo.append(vb)
                seen_matches.add(key)
            if len(combo) == 2:
                break

        if len(combo) == 2:
            p_combined = combo[0]["model_p"] * combo[1]["model_p"]
            o_combined = combo[0]["odds"] * combo[1]["odds"]
            if p_combined >= 0.15 and o_combined >= 4.00:
                gain_10 = round(10 * o_combined, 2)
                lines3 = [
                    "💎 <b>betX CdM – Combiné du jour</b>",
                    "",
                    f"✅ {_flag(combo[0]['label'])} <b>{combo[0]['label']}</b> @{combo[0]['odds']:.2f}",
                    f"✅ {_flag(combo[1]['label'])} <b>{combo[1]['label']}</b> @{combo[1]['odds']:.2f}",
                    "",
                    f"🎰 Cote combinée : <b>{o_combined:.2f}</b>",
                    f"📈 Proba modèle : <b>{p_combined:.0%}</b>",
                    f"💰 10€ → <b>{gain_10:.0f}€</b>",
                    "",
                    "<i>⚠️ Combiné à faible mise. Ne jamais dépasser 2% bankroll.</i>",
                ]
                msg3 = "\n".join(lines3)

    messages = [msg1, msg2]
    if msg3:
        messages.append(msg3)
    return messages


def send_wc_telegram(data: dict, filter_date: str | None = None) -> bool:
    """Envoie les prédictions CdM via Telegram — un message par match.

    Anti-doublon : skip si déjà envoyé dans les 4 dernières heures
    (évite les doublons entre cron 08h et 15h UTC).
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    dm_id = os.getenv("TELEGRAM_CHAT_ID", "")
    channel_id = os.getenv("TELEGRAM_CHANNEL_ID", "")

    if not token:
        console.print("[yellow]⚠️  TELEGRAM_BOT_TOKEN manquant — envoi ignoré[/yellow]")
        return False

    # Anti-doublon : lock file avec timestamp
    lock_file = Path("data/cache/wc_tg_sent.txt")
    today_str = filter_date or date.today().isoformat()
    if lock_file.exists():
        try:
            last_sent = lock_file.read_text().strip()
            # Skip si même date ET moins de 4h d'écart
            if last_sent.startswith(today_str):
                last_ts = float(last_sent.split("|")[1]) if "|" in last_sent else 0
                if time.time() - last_ts < 4 * 3600:
                    console.print(
                        f"[yellow]⏭️  CdM Telegram déjà envoyé récemment (skip)[/yellow]"
                    )
                    return True
        except Exception:
            pass

    messages = build_wc_telegram(data, filter_date)
    if not messages:
        console.print("[yellow]⚠️  Aucun match à envoyer[/yellow]")
        return False

    # Envoyer uniquement au channel (pas de DM en doublon)
    # Le channel est le point de diffusion principal
    # Le DM reçoit quand même si channel_id est vide
    if channel_id:
        targets = [(channel_id, "Channel")]
    elif dm_id:
        targets = [(dm_id, "DM")]
    else:
        console.print("[yellow]⚠️  Aucun destinataire Telegram.[/yellow]")
        return False

    ok = True
    for cid, label in targets:
        sent = 0
        for msg in messages:
            if msg.strip():
                if _tg_send(token, cid, msg):
                    sent += 1
        console.print(f"  ✅ Telegram {label} : {sent} messages envoyés")
        if sent == 0:
            ok = False

    # Marquer comme envoyé
    if ok:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text(f"{today_str}|{time.time():.0f}")
    return ok


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Prédictions CdM 2026 – phase de groupes")
    parser.add_argument("--fetch", action="store_true",
                        help="Enrichir le cache API (max ~75 req)")
    parser.add_argument("--date", type=str, default=None,
                        help="Filtrer sur une date YYYY-MM-DD")
    parser.add_argument("--notify", action="store_true",
                        help="Envoyer les prédictions du jour sur Telegram")
    args = parser.parse_args()

    today = args.date or date.today().isoformat()

    console.print("\n[bold cyan]📡 Chargement du calendrier ESPN...[/bold cyan]")

    if args.date:
        d = date.fromisoformat(args.date)
        matches = fetch_group_matches(d, d)
    else:
        matches = fetch_group_matches(date(2026, 6, 11), date(2026, 6, 28))

    console.print(f"  {len(matches)} matchs de poule trouvés\n")

    teams = list({m["home"] for m in matches} | {m["away"] for m in matches})

    console.print("[bold cyan]📊 Chargement des profils équipes...[/bold cyan]")
    profiles = load_profiles(teams, fetch=args.fetch)

    # Export JSON (toujours)
    data = export_predictions(matches, profiles, filter_date=args.date)

    # Affichage console
    display_predictions(matches, profiles, filter_date=args.date)

    # Telegram si demandé
    if args.notify:
        console.print("\n[bold cyan]📨 Envoi Telegram...[/bold cyan]")
        send_wc_telegram(data, filter_date=today)


if __name__ == "__main__":
    main()
