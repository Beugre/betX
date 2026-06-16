"""
betX — Lineup Collector & Analyzer

Récupère les compositions d'équipes via ESPN (disponibles ~1h avant le match),
calcule l'impact sur les prédictions et envoie un Telegram avec recalcul.

Usage :
    python lineup_notifier.py              # analyser les matchs du jour
    python lineup_notifier.py --match 760432  # un match spécifique
    python lineup_notifier.py --force         # forcer même si déjà envoyé

Cron recommandé : */15 * * * * (toutes les 15min → capte les compos dès parution)
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from datetime import date, timedelta

import httpx
from dotenv import load_dotenv

load_dotenv()

ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
RATINGS_FILE = Path("data/player_ratings.json")
WC_PREDS_FILE = Path("data/wc_predictions.json")
LINEUP_LOCK_FILE = Path("data/cache/lineup_sent.json")
HEADERS = {"User-Agent": "Mozilla/5.0"}


def load_ratings() -> dict:
    if RATINGS_FILE.exists():
        return json.loads(RATINGS_FILE.read_text()).get("players", {})
    return {}


def get_today_matches() -> list[dict]:
    """Récupère les matchs d'aujourd'hui et demain."""
    matches = []
    for offset in [0, 1]:
        d = (date.today() + timedelta(days=offset)).strftime("%Y%m%d")
        try:
            r = httpx.get(ESPN_SCOREBOARD, params={"dates": d}, headers=HEADERS, timeout=10)
            for ev in r.json().get("events", []):
                comp = ev["competitions"][0]
                teams = comp["competitors"]
                h = next((t for t in teams if t.get("homeAway") == "home"), None)
                a = next((t for t in teams if t.get("homeAway") == "away"), None)
                if not h or not a:
                    continue
                hn, an = h["team"]["displayName"], a["team"]["displayName"]
                if any(w in hn for w in ("Place", "TBD", "Winner")):
                    continue
                status = comp.get("status", {}).get("type", {}).get("name", "")
                match_date = comp.get("date", "")
                matches.append({
                    "id": ev["id"],
                    "home": hn,
                    "away": an,
                    "date": match_date,
                    "status": status,
                })
        except Exception as e:
            print(f"ESPN scoreboard erreur: {e}")
    return matches


def fetch_lineup(event_id: str) -> dict | None:
    """Récupère la composition d'un match depuis ESPN."""
    try:
        r = httpx.get(ESPN_SUMMARY, params={"event": event_id}, headers=HEADERS, timeout=10)
        data = r.json()
        rosters = data.get("rosters", [])
        if not rosters:
            return None

        result = {}
        for team_data in rosters:
            team_name = team_data.get("team", {}).get("displayName", "?")
            players = team_data.get("roster", [])
            if not players:
                continue

            starters = []
            bench = []
            for p in players:
                ath = p.get("athlete", {})
                name = ath.get("displayName", "?")
                pos = p.get("position", {}).get("abbreviation", "?")
                starter = p.get("starter", False)
                jersey = ath.get("jersey", "?")
                entry = {"name": name, "pos": pos, "jersey": jersey}
                if starter:
                    starters.append(entry)
                else:
                    bench.append(entry)

            result[team_name] = {"starters": starters, "bench": bench}

        return result if result else None
    except Exception as e:
        print(f"ESPN summary erreur pour event {event_id}: {e}")
        return None


def calc_lineup_impact(team_name: str, starters: list[dict], ratings: dict) -> dict:
    """
    Calcule l'impact d'une composition sur les prédictions.

    Logique :
    - Identifie les joueurs clés présents / absents
    - Calcule un "squad strength index" (0-100)
    - Dérive un facteur λ_multiplier

    Retourne:
        {
          "key_players": [{"name": ..., "rating": ..., "present": True}],
          "squad_strength": 82.5,
          "lambda_multiplier": 1.05,
          "summary": "Mané (93) ✓, Mbappé (98) ✓ ..."
        }
    """
    starter_names = {p["name"] for p in starters}

    # Joueurs connus de cette équipe dans notre DB
    team_ratings = {
        name: info for name, info in ratings.items()
        if info.get("team") == team_name
    }

    # Rating par défaut pour les joueurs non référencés dans EA FC 26
    DEFAULT_RATING = 60

    key_players = []

    # 1) Tous les starters ESPN — connus ou non
    for starter in starters:
        sname = starter["name"]
        # Cherche un match dans notre DB (approximatif, gère accents)
        matched = None
        for db_name, info in team_ratings.items():
            if db_name.lower() in sname.lower() or sname.lower() in db_name.lower():
                matched = (db_name, info)
                break
        if matched:
            key_players.append({
                "name": matched[0],
                "rating": matched[1]["rating"],
                "position": matched[1].get("position", "?"),
                "present": True,
            })
        else:
            key_players.append({
                "name": sname,
                "rating": DEFAULT_RATING,
                "position": "?",
                "present": True,
            })

    # 2) Joueurs connus de la DB NON présents dans la compo (absents notables)
    starter_lower = {s["name"].lower() for s in starters}
    for db_name, info in sorted(team_ratings.items(), key=lambda x: -x[1]["rating"]):
        already = any(
            db_name.lower() in s or s in db_name.lower()
            for s in starter_lower
        )
        if not already and info["rating"] >= 82:
            key_players.append({
                "name": db_name,
                "rating": info["rating"],
                "position": info.get("position", "?"),
                "present": False,
            })

    # Squad strength = moyenne des 11 titulaires
    present_ratings = [p["rating"] for p in key_players if p["present"]]

    if present_ratings:
        avg_present = sum(present_ratings) / len(present_ratings)
    else:
        avg_present = DEFAULT_RATING

    # Baseline = moyenne DB équipe (ou défaut si équipe inconnue)
    if team_ratings:
        avg_all = sum(i["rating"] for i in team_ratings.values()) / len(team_ratings)
    else:
        avg_all = DEFAULT_RATING

    if key_players:
        # Facteur λ basé sur l'écart compo réelle vs baseline DB
        lambda_mult = 1.0 + (avg_present - avg_all) / 500.0
        lambda_mult = max(0.85, min(1.20, lambda_mult))

        squad_strength = round(avg_present, 1)
    else:
        lambda_mult = 1.0
        squad_strength = DEFAULT_RATING

    # Résumé : joueurs connus présents (rating >= 82) + absents notables
    known_present = [p for p in key_players if p["present"] and p["rating"] >= 82]
    notable_absent = [p for p in key_players if not p["present"] and p["rating"] >= 82]
    parts = []
    for p in top5:
        flag = "✓" if p["present"] else "✗"
        parts.append(f"{p['name'].split()[-1]} ({p['rating']}) {flag}")
    summary = ", ".join(parts) if parts else "—"

    return {
        "squad_strength": squad_strength,
        "lambda_multiplier": round(lambda_mult, 3),
        "key_players": key_players,
        "summary": summary,
    }


def recalculate_with_lineup(home: str, away: str, home_mult: float, away_mult: float) -> dict | None:
    """Recalcule les probabilités en appliquant les multiplicateurs de lineup."""
    try:
        from betx.data.national_team_collector import NationalTeamCollector
        from betx.data.national_team_features import build_features, NationalMatchPredictor, _confidence_levels

        c = NationalTeamCollector()
        pred = NationalMatchPredictor()

        hp = c.get_profile(home, away)
        ap = c.get_profile(away, home)
        if not hp or not ap:
            return None

        feats = build_features(hp, ap)

        # Patcher les λ avant la prédiction analytique
        lh, la = pred.compute_lambdas(feats)
        lh_adj = round(lh * home_mult, 3)
        la_adj = round(la * away_mult, 3)

        probs = pred.predict_analytical(lh_adj, la_adj, home_team=home, away_team=away)

        return {
            "p_home": probs.p_home_win,
            "p_draw": probs.p_draw,
            "p_away": probs.p_away_win,
            "lambda_home": lh_adj,
            "lambda_away": la_adj,
            "top_score": max(probs.exact_scores.items(), key=lambda x: x[1]),
            "top_scores": sorted(probs.exact_scores.items(), key=lambda x: -x[1])[:4],
        }
    except Exception as e:
        print(f"Recalcul erreur: {e}")
        return None


def format_telegram(match: dict, lineup: dict, home_impact: dict, away_impact: dict,
                    recalc: dict | None, original_pred: dict | None) -> str:
    """Formate le message Telegram pour une composition."""
    home, away = match["home"], match["away"]

    # Heure fr
    date_str = match.get("date", "")
    try:
        h_utc = int(date_str[11:13])
        h_fr = (h_utc + 2) % 24
        time_str = f"{h_fr:02d}h{date_str[14:16]}"
    except Exception:
        time_str = "?"

    lines = [
        f"⚽ <b>COMPOS — {home} vs {away}</b> ({time_str} heure fr)",
        "",
    ]

    # Compos
    for team_name, data in lineup.items():
        starters = data.get("starters", [])
        if starters:
            names = [p["name"].split()[-1] for p in starters]
            lines.append(f"<b>{team_name}</b>: {', '.join(names)}")

    lines.append("")

    # Impact joueurs clés
    for team_name, impact in [(home, home_impact), (away, away_impact)]:
        if impact.get("summary") and impact["summary"] != "—":
            lines.append(f"🔑 <b>{team_name}</b>: {impact['summary']}")

    lines.append("")

    # Prédictions
    if recalc:
        ph, px, pa = recalc["p_home"], recalc["p_draw"], recalc["p_away"]
        top_score, top_prob = recalc["top_score"]
            lines.append(f"📊 <b>Prédiction (avec compos)</b>")
            lines.append(f"   {home}: {ph:.0%} | Nul: {px:.0%} | {away}: {pa:.0%}")
            medals = ["🥇", "🥈", "🥉", "4️⃣"]
            for i, (sc, pr) in enumerate(recalc.get("top_scores", [(top_score, top_prob)])[:4]):
                lines.append(f"   {medals[i]} {sc} ({pr:.0%})")
            pa_old = original_pred.get("p_away", pa)
            dh = ph - ph_old
            da = pa - pa_old
            if abs(dh) >= 0.02 or abs(da) >= 0.02:
                lines.append(f"   Δ vs modèle de base: {home} {dh:+.0%} | {away} {da:+.0%}")
    elif original_pred:
        ph = original_pred.get("p_home", 0)
        px = original_pred.get("p_draw", 0)
        pa = original_pred.get("p_away", 0)
        top = original_pred.get("top_scores", [])
            lines.append(f"📊 <b>Prédiction (modèle de base)</b>")
            lines.append(f"   {home}: {ph:.0%} | Nul: {px:.0%} | {away}: {pa:.0%}")
            medals = ["🥇", "🥈", "🥉", "4️⃣"]
            for i, s in enumerate(top[:4]):
                lines.append(f"   {medals[i]} {s['score']} ({s['prob']:.0%})")
    """Envoie un message Telegram."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    channel = os.getenv("TELEGRAM_CHANNEL_ID", "")
    if not token or not channel:
        print("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHANNEL_ID manquant")
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": channel, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram erreur: {e}")
        return False


def run(force: bool = False, specific_event: str | None = None):
    """Point d'entrée principal."""
    ratings = load_ratings()
    print(f"Ratings chargés: {len(ratings)} joueurs")

    # Charger lock pour éviter les doublons
    sent: dict = {}
    if LINEUP_LOCK_FILE.exists():
        try:
            sent = json.loads(LINEUP_LOCK_FILE.read_text())
        except Exception:
            pass

    # Charger prédictions existantes
    existing_preds: dict = {}
    if WC_PREDS_FILE.exists():
        try:
            data = json.loads(WC_PREDS_FILE.read_text())
            for m in data.get("matches", []):
                key = f"{m['home']}|{m['away']}"
                existing_preds[key] = m.get("prediction", {})
        except Exception:
            pass

    # Récupérer les matchs
    if specific_event:
        matches_to_check = [{"id": specific_event, "home": "?", "away": "?", "date": "", "status": ""}]
        # Résoudre les noms depuis ESPN
        try:
            r = httpx.get(ESPN_SUMMARY, params={"event": specific_event}, headers=HEADERS, timeout=10)
            header = r.json().get("header", {})
            comps = header.get("competitions", [{}])
            if comps:
                teams = comps[0].get("competitors", [])
                h = next((t for t in teams if t.get("homeAway") == "home"), {})
                a = next((t for t in teams if t.get("homeAway") == "away"), {})
                matches_to_check[0]["home"] = h.get("team", {}).get("displayName", "?")
                matches_to_check[0]["away"] = a.get("team", {}).get("displayName", "?")
                matches_to_check[0]["date"] = comps[0].get("date", "")
        except Exception:
            pass
    else:
        matches_to_check = get_today_matches()

    print(f"Matchs à vérifier: {len(matches_to_check)}")
    notifications_sent = 0

    for match in matches_to_check:
        eid = match["id"]
        home, away = match["home"], match["away"]
        status = match.get("status", "")

        # Skip les matchs déjà joués sauf si --force
        if status in ("STATUS_FULL_TIME", "STATUS_FINAL") and not force:
            continue

        # Skip si compo déjà envoyée aujourd'hui
        lock_key = f"{eid}_{date.today().isoformat()}"
        if lock_key in sent and not force:
            print(f"  {home} vs {away}: compo déjà envoyée aujourd'hui")
            continue

        print(f"  Récupération compo: {home} vs {away} (id={eid})...")
        lineup = fetch_lineup(eid)

        if not lineup:
            print(f"    → Pas encore disponible")
            continue

        # Vérifier qu'on a des starters
        has_starters = any(len(data.get("starters", [])) > 0 for data in lineup.values())
        if not has_starters:
            print(f"    → Starters pas encore publiés")
            continue

        print(f"    → Compo disponible!")
        for team_name, data in lineup.items():
            n_s = len(data.get("starters", []))
            print(f"       {team_name}: {n_s} titulaires")

        # Calculer l'impact
        home_impact = calc_lineup_impact(home, lineup.get(home, {}), ratings) if home in lineup else {"squad_strength": 80, "lambda_multiplier": 1.0, "key_players": [], "summary": "—"}
        away_impact = calc_lineup_impact(away, lineup.get(away, {}), ratings) if away in lineup else {"squad_strength": 80, "lambda_multiplier": 1.0, "key_players": [], "summary": "—"}

        # Recalcul prédictions avec multiplicateurs
        recalc = recalculate_with_lineup(home, away, home_impact["lambda_multiplier"], away_impact["lambda_multiplier"])

        # Prédiction originale
        original_pred = existing_preds.get(f"{home}|{away}")

        # Formatter et envoyer
        msg = format_telegram(match, lineup, home_impact, away_impact, recalc, original_pred)
        print(f"    Message:\n{msg}\n")

        ok = send_telegram(msg)
        if ok:
            print(f"    ✅ Telegram envoyé")
            sent[lock_key] = time.time()
            notifications_sent += 1
        else:
            print(f"    ❌ Telegram échoué")

    # Sauvegarder le lock
    LINEUP_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LINEUP_LOCK_FILE.write_text(json.dumps(sent, indent=2))
    print(f"\nTotal notifications envoyées: {notifications_sent}")


if __name__ == "__main__":
    force = "--force" in sys.argv
    specific = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--match" and i + 1 < len(sys.argv)), None)
    run(force=force, specific_event=specific)
