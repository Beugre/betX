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


# Mapping position → ligne tactique
POSITION_LINE = {
    "GK": "GK",
    "CB": "DEF", "LB": "DEF", "RB": "DEF", "LWB": "DEF", "RWB": "DEF", "DF": "DEF",
    "CDM": "MID", "DM": "MID", "CM": "MID", "CAM": "MID", "LM": "MID", "RM": "MID", "AM": "MID", "MF": "MID",
    "ST": "ATT", "CF": "ATT", "SS": "ATT", "LW": "ATT", "RW": "ATT", "LF": "ATT", "RF": "ATT",
}


def _resolve_starter(starter: dict, team_ratings: dict) -> dict:
    """Résout un titulaire ESPN vers son rating EA FC 26."""
    DEFAULT_RATING = 60
    sname = starter["name"]
    pos_raw = starter.get("position", {})
    espn_pos = pos_raw.get("abbreviation", "?") if isinstance(pos_raw, dict) else str(pos_raw)
    for db_name, info in team_ratings.items():
        if db_name.lower() in sname.lower() or sname.lower() in db_name.lower():
            return {"name": db_name, "rating": info["rating"],
                    "position": info.get("position", espn_pos), "found": True}
    return {"name": sname, "rating": DEFAULT_RATING, "position": espn_pos, "found": False}


def calc_positional_lambda(home_impact: dict, away_impact: dict) -> tuple:
    """
    Multiplie les λ base en croisant les lignes tactiques :
    ATK_home vs DEF_away → λ_home | ATK_away vs DEF_home → λ_away
    MID dominance → ±5% | GK fort → réduit légèrement λ adverse.
    """
    REF = 80.0
    h = home_impact["avg_line"]
    a = away_impact["avg_line"]

    h_atk_adv = (h.get("ATT", REF) / REF) / (a.get("DEF", REF) / REF)
    a_atk_adv = (a.get("ATT", REF) / REF) / (h.get("DEF", REF) / REF)

    mid_total = h.get("MID", REF) + a.get("MID", REF)
    h_mid_bonus = ((h.get("MID", REF) / mid_total) - 0.5) * 0.10 if mid_total else 0

    h_gk_malus = (h.get("GK", REF) - REF) / REF * 0.05
    a_gk_malus = (a.get("GK", REF) - REF) / REF * 0.05

    lh = max(0.70, min(1.40, h_atk_adv * 0.70 + 0.30 + h_mid_bonus - a_gk_malus))
    la = max(0.70, min(1.40, a_atk_adv * 0.70 + 0.30 - h_mid_bonus - h_gk_malus))

    return round(lh, 3), round(la, 3)


def calc_lineup_impact(team_name: str, starters: list[dict], ratings: dict) -> dict:
    """
    Calcule l'impact d'une composition ligne par ligne (GK/DEF/MID/ATT).
    Retourne avg_line pour permettre le croisement ATK vs DEF inter-équipes.
    """
    team_ratings = {n: i for n, i in ratings.items() if i.get("team") == team_name}
    DEFAULT_RATING = 60

    resolved = [_resolve_starter(s, team_ratings) for s in starters]

    # Grouper par ligne tactique
    lines_r: dict[str, list] = {"GK": [], "DEF": [], "MID": [], "ATT": []}
    for p in resolved:
        pos = p["position"].upper()
        line = POSITION_LINE.get(pos)
        if line is None:
            if any(x in pos for x in ["KEEPER", "GOALKEEPER"]): line = "GK"
            elif any(x in pos for x in ["BACK", "DEFEND"]): line = "DEF"
            elif any(x in pos for x in ["FORWARD", "STRIKER", "WING"]): line = "ATT"
            else: line = "MID"
        lines_r[line].append(p["rating"])

    avg_line = {ln: round(sum(r)/len(r), 1) if r else DEFAULT_RATING for ln, r in lines_r.items()}

    # Absents notables (rating >= 80 non alignés)
    starters_lower = {s["name"].lower() for s in starters}
    absent_notable = [
        {"name": n, "rating": i["rating"], "position": i.get("position", "?")}
        for n, i in sorted(team_ratings.items(), key=lambda x: -x[1]["rating"])
        if i["rating"] >= 80 and not any(n.lower() in sl or sl in n.lower() for sl in starters_lower)
    ]

    # Résumé
    known_present = [p for p in resolved if p["found"] and p["rating"] >= 80]
    parts = [f"{p['name'].split()[-1]} ({p['rating']})" for p in known_present[:4]]
    if absent_notable:
        parts += [f"⚠{a['name'].split()[-1]} ({a['rating']})" for a in absent_notable[:2]]
    summary = ", ".join(parts) if parts else "—"

    return {
        "avg_line": avg_line,
        "resolved": resolved,
        "absent_notable": absent_notable,
        "summary": summary,
        "lambda_multiplier": 1.0,  # fallback — remplacé par calc_positional_lambda
    }


def recalculate_with_lineup(home: str, away: str,
                             home_impact: dict, away_impact: dict) -> dict | None:
    """Recalcule les probabilités en utilisant le croisement positionnel ATK/DEF/MID."""
    try:
        from betx.data.national_team_collector import NationalTeamCollector
        from betx.data.national_team_features import build_features, NationalMatchPredictor

        c = NationalTeamCollector()
        pred = NationalMatchPredictor()

        hp = c.get_profile(home, away)
        ap = c.get_profile(away, home)
        if not hp or not ap:
            return None

        feats = build_features(hp, ap)
        lh, la = pred.compute_lambdas(feats)

        # Croisement positionnel ATK vs DEF si les deux impacts sont disponibles
        if home_impact.get("avg_line") and away_impact.get("avg_line"):
            lh_mult, la_mult = calc_positional_lambda(home_impact, away_impact)
        else:
            lh_mult = home_impact.get("lambda_multiplier", 1.0)
            la_mult = away_impact.get("lambda_multiplier", 1.0)

        lh_adj = round(lh * lh_mult, 3)
        la_adj = round(la * la_mult, 3)

        probs = pred.predict_analytical(lh_adj, la_adj, home_team=home, away_team=away)

        return {
            "p_home": probs.p_home_win,
            "p_draw": probs.p_draw,
            "p_away": probs.p_away_win,
            "lambda_home": lh_adj,
            "lambda_away": la_adj,
            "lh_mult": lh_mult,
            "la_mult": la_mult,
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

    date_str = match.get("date", "")
    try:
        h_utc = int(date_str[11:13])
        h_fr = (h_utc + 2) % 24
        time_str = f"{h_fr:02d}h{date_str[14:16]}"
    except Exception:
        time_str = "?"

    lines = [f"⚽ <b>COMPOS — {home} vs {away}</b> ({time_str} heure fr)", ""]

    for team_name, data in lineup.items():
        starters = data.get("starters", [])
        if starters:
            names = [p["name"].split()[-1] for p in starters]
            lines.append(f"<b>{team_name}</b>: {', '.join(names)}")

    lines.append("")

    # Résumé positonnel
    for team_name, impact in [(home, home_impact), (away, away_impact)]:
        al = impact.get("avg_line", {})
        if al:
            parts = []
            if al.get("DEF"): parts.append(f"DEF {al['DEF']:.0f}")
            if al.get("MID"): parts.append(f"MID {al['MID']:.0f}")
            if al.get("ATT"): parts.append(f"ATT {al['ATT']:.0f}")
            if parts:
                lines.append(f"🎮 <b>{team_name}</b>: {' | '.join(parts)}")

    if recalc:
        lhm = recalc.get("lh_mult", 1.0)
        lam = recalc.get("la_mult", 1.0)
        lines.append(f"   λ mult: {home} ×{lhm:.2f} | {away} ×{lam:.2f}")

    lines.append("")

    for team_name, impact in [(home, home_impact), (away, away_impact)]:
        if impact.get("summary") and impact["summary"] != "—":
            lines.append(f"🔑 <b>{team_name}</b>: {impact['summary']}")

    lines.append("")

    if recalc:
        ph, px, pa = recalc["p_home"], recalc["p_draw"], recalc["p_away"]
        top_score, top_prob = recalc["top_score"]
        lines.append(f"📊 <b>Prédiction (avec compos)</b>")
        lines.append(f"   {home}: {ph:.0%} | Nul: {px:.0%} | {away}: {pa:.0%}")
        medals = ["🥇", "🥈", "🥉", "4️⃣"]
        for i, (sc, pr) in enumerate(recalc.get("top_scores", [(top_score, top_prob)])[:4]):
            lines.append(f"   {medals[i]} {sc} ({pr:.0%})")
        if original_pred:
            ph_old = original_pred.get("p_home", ph)
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

    return "\n".join(lines)


def send_telegram(message: str) -> bool:
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

    sent: dict = {}
    if LINEUP_LOCK_FILE.exists():
        try:
            sent = json.loads(LINEUP_LOCK_FILE.read_text())
        except Exception:
            pass

    existing_preds: dict = {}
    if WC_PREDS_FILE.exists():
        try:
            data = json.loads(WC_PREDS_FILE.read_text())
            for m in data.get("matches", []):
                key = f"{m['home']}|{m['away']}"
                existing_preds[key] = m.get("prediction", {})
        except Exception:
            pass

    if specific_event:
        matches_to_check = [{"id": specific_event, "home": "?", "away": "?", "date": "", "status": ""}]
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

        if status in ("STATUS_FULL_TIME", "STATUS_FINAL") and not force:
            continue

        lock_key = f"{eid}_{date.today().isoformat()}"
        if lock_key in sent and not force:
            print(f"  {home} vs {away}: compo déjà envoyée aujourd'hui")
            continue

        print(f"  Récupération compo: {home} vs {away} (id={eid})...")
        lineup = fetch_lineup(eid)

        if not lineup:
            print(f"    → Pas encore disponible")
            continue

        has_starters = any(len(data.get("starters", [])) > 0 for data in lineup.values())
        if not has_starters:
            print(f"    → Starters pas encore publiés")
            continue

        print(f"    → Compo disponible!")
        for team_name, data in lineup.items():
            print(f"       {team_name}: {len(data.get('starters', []))} titulaires")

        home_starters = lineup.get(home, {}).get("starters", [])
        away_starters = lineup.get(away, {}).get("starters", [])

        empty_impact = {"avg_line": {}, "summary": "—", "lambda_multiplier": 1.0, "resolved": [], "absent_notable": []}
        home_impact = calc_lineup_impact(home, home_starters, ratings) if home_starters else empty_impact
        away_impact = calc_lineup_impact(away, away_starters, ratings) if away_starters else empty_impact

        recalc = recalculate_with_lineup(home, away, home_impact, away_impact)
        original_pred = existing_preds.get(f"{home}|{away}")

        msg = format_telegram(match, lineup, home_impact, away_impact, recalc, original_pred)
        print(f"    Message:\n{msg}\n")

        ok = send_telegram(msg)
        if ok:
            print(f"    ✅ Telegram envoyé")
            sent[lock_key] = time.time()
            notifications_sent += 1
        else:
            print(f"    ❌ Telegram échoué")

    LINEUP_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LINEUP_LOCK_FILE.write_text(json.dumps(sent, indent=2))
    print(f"\nTotal notifications envoyées: {notifications_sent}")


if __name__ == "__main__":
    force = "--force" in sys.argv
    specific = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--match" and i + 1 < len(sys.argv)), None)
    run(force=force, specific_event=specific)

