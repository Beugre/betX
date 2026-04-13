#!/usr/bin/env python3
"""
betX – Daily Scan & Export + Telegram

Lance le scan quotidien, exporte les résultats en JSON
(pour Streamlit) et envoie un récapitulatif via Telegram Bot.

Usage :
    python daily_scan.py                 # Scan + export JSON
    python daily_scan.py --notify        # Scan + export + Telegram
    python daily_scan.py --get-chat-id   # Récupère ton chat_id

Configuration Telegram dans .env :
    TELEGRAM_BOT_TOKEN=ton_token
    TELEGRAM_CHAT_ID=ton_chat_id
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx

from dotenv import load_dotenv

# ─── Setup ────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

logging.disable(logging.WARNING)

DATA_FILE = PROJECT_ROOT / "data" / "daily_bets.json"


# ─── Scan & Export ────────────────────────────────────────────────────────

def run_and_export() -> dict:
    """Lance le scan et exporte les résultats en JSON."""
    from betx.pipeline import quick_scan as _qs

    print(f"\n🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')} – Lancement du scan quotidien...\n")

    results = _qs.quick_scan()

    # Récupérer les metadata du scan (via le module, pas une copie)
    _last_scan_events_count = _qs._last_scan_events_count

    # Trier par edge décroissant
    results.sort(key=lambda x: x[0].edge, reverse=True)

    # Construire les données exportables
    bets_data = []
    enriched_count = 0

    # Récupérer les analyses détaillées du modèle
    from betx.pipeline import quick_scan as _qs_mod
    match_analyses = getattr(_qs_mod, '_match_analysis', {})

    for vb, stake in results:
        # Déterminer si le match a été enrichi
        event_key = f"{vb.home_team}_{vb.away_team}"
        analysis = match_analyses.get(event_key, {})
        enriched = analysis.get("enriched", False)
        if enriched:
            enriched_count += 1

        gain = stake.stake_amount * (vb.bookmaker_odds - 1)

        # Récupérer le contexte du match (H2H, forme, classement)
        event_key = f"{vb.home_team}_{vb.away_team}"
        ctx = _qs._match_contexts.get(event_key)
        context_data = {}
        if ctx:
            context_data = {
                "home_rank": ctx.home.rank,
                "away_rank": ctx.away.rank,
                "home_points": ctx.home.points,
                "away_points": ctx.away.points,
                "home_zone": ctx.home.zone,
                "away_zone": ctx.away.zone,
                "home_form": ctx.home.form_str,
                "away_form": ctx.away.form_str,
                "home_pressure": ctx.home.pressure,
                "away_pressure": ctx.away.pressure,
                "h2h_summary": ctx.h2h_summary,
                "h2h_count": len(ctx.h2h_games),
                "home_form_detail": [
                    {"date": fe.date, "result": fe.result, "score": fe.score,
                     "opponent": fe.opponent, "is_home": fe.is_home}
                    for fe in ctx.home.form_events[:5]
                ],
                "away_form_detail": [
                    {"date": fe.date, "result": fe.result, "score": fe.score,
                     "opponent": fe.opponent, "is_home": fe.is_home}
                    for fe in ctx.away.form_events[:5]
                ],
            }

        bets_data.append({
            "home_team": vb.home_team,
            "away_team": vb.away_team,
            "selection": vb.selection,
            "model_prob": float(round(vb.model_probability, 4)),
            "odds": float(round(vb.bookmaker_odds, 2)),
            "edge": float(round(vb.edge, 4)),
            "ev": float(round(vb.ev, 4)),
            "stake": float(round(stake.stake_amount, 2)),
            "gain_potential": float(round(gain, 2)),
            "bookmaker": vb.bookmaker,
            "confidence": vb.confidence,
            "enriched": bool(enriched),
            "sport": vb.sport,
            "market": vb.market,
            "context": context_data,
            "analysis": {
                "home_scored": round(analysis.get("home_scored", 0), 3),
                "home_conceded": round(analysis.get("home_conceded", 0), 3),
                "away_scored": round(analysis.get("away_scored", 0), 3),
                "away_conceded": round(analysis.get("away_conceded", 0), 3),
                "home_elo": round(analysis.get("home_elo", 1500), 1),
                "away_elo": round(analysis.get("away_elo", 1500), 1),
                "home_form": analysis.get("home_form", []),
                "away_form": analysis.get("away_form", []),
                "avg_home": round(analysis.get("avg_home", 1.5), 3),
                "avg_away": round(analysis.get("avg_away", 1.2), 3),
                "lambda_home": round(analysis.get("lambda_home", 0), 3),
                "lambda_away": round(analysis.get("lambda_away", 0), 3),
                "p_home": round(analysis.get("p_home", 0), 4),
                "p_draw": round(analysis.get("p_draw", 0), 4),
                "p_away": round(analysis.get("p_away", 0), 4),
                "odds_home": round(analysis.get("odds_home", 0), 2),
                "odds_draw": round(analysis.get("odds_draw", 0), 2),
                "odds_away": round(analysis.get("odds_away", 0), 2),
                "is_euro": analysis.get("is_euro", False),
                "enriched": analysis.get("enriched", False),
            } if analysis else {},
        })

    total_stake = sum(b["stake"] for b in bets_data)
    total_gain = sum(b["gain_potential"] for b in bets_data)
    avg_edge = sum(b["edge"] for b in bets_data) / len(bets_data) if bets_data else 0
    avg_odds = sum(b["odds"] for b in bets_data) / len(bets_data) if bets_data else 0

    export_data = {
        "scan_time": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "scan_date": datetime.now().strftime("%Y-%m-%d"),
        "bets": bets_data,
        "summary": {
            "total_bets": len(bets_data),
            "total_stake": round(total_stake, 2),
            "total_potential_gain": round(total_gain, 2),
            "total_return": round(total_stake + total_gain, 2),
            "avg_edge": round(avg_edge, 4),
            "avg_odds": round(avg_odds, 2),
            "enriched_count": enriched_count,
            "enriched_pct": round(enriched_count / len(bets_data), 2) if bets_data else 0,
            "events_scanned": _last_scan_events_count,
            "source": "ESPN",
            "bankroll": 1000,
        },
    }

    # Sauvegarder
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(export_data, ensure_ascii=False, indent=2))
    print(f"\n💾 Données exportées → {DATA_FILE}")
    print(f"   {len(bets_data)} paris │ Mise: {total_stake:.0f}€ │ Gain potentiel: +{total_gain:.0f}€")

    return export_data


# ─── Telegram Message Builder ─────────────────────────────────────────────

def build_match_analysis_message(bet: dict, analysis: dict, index: int) -> str:
    """
    Construit un message Telegram d'analyse détaillée pour UN match.
    Format riche avec décomposition du modèle Poisson + Dixon-Coles.
    """
    home = bet.get("home_team", "?")
    away = bet.get("away_team", "?")
    sel = bet.get("selection", "")
    edge = bet.get("edge", 0)
    odds = bet.get("odds", 0)
    model_prob = bet.get("model_prob", 0)
    stake = bet.get("stake", 0)
    gain = stake * (odds - 1)
    ctx = bet.get("context", {})

    # Icônes
    sel_icon = {"away": "🟢", "draw": "🟢", "home": "🟡"}.get(sel.lower(), "⚪")
    if edge >= 0.20:
        conf = "🔥🔥🔥"
    elif edge >= 0.15:
        conf = "🔥🔥"
    elif edge >= 0.10:
        conf = "🔥"
    else:
        conf = "✅"

    is_euro = analysis.get("is_euro", False)
    comp_tag = " 🏆" if is_euro else ""

    lines = [
        f"{conf} <b>{index}. {home} vs {away}</b>{comp_tag}",
        "",
    ]

    # ── Stats brutes ──
    h_scored = analysis.get("home_scored", 0)
    h_conced = analysis.get("home_conceded", 0)
    a_scored = analysis.get("away_scored", 0)
    a_conced = analysis.get("away_conceded", 0)
    lines.append("📊 <b>Stats saison</b>")
    lines.append(
        f"  {home}: {h_scored:.2f} buts/m │ {h_conced:.2f} enc/m"
    )
    lines.append(
        f"  {away}: {a_scored:.2f} buts/m │ {a_conced:.2f} enc/m"
    )

    # ── ELO ──
    h_elo = analysis.get("home_elo", 1500)
    a_elo = analysis.get("away_elo", 1500)
    elo_diff = h_elo - a_elo
    elo_arrow = "➡️" if abs(elo_diff) < 30 else ("⬆️" if elo_diff > 0 else "⬇️")
    lines.append(f"  ELO: {h_elo:.0f} vs {a_elo:.0f} ({elo_arrow} diff={elo_diff:+.0f})")

    # ── Forme ──
    def form_icons(form_list):
        if not form_list:
            return "?"
        return "".join(
            {"W": "✅", "D": "🟡", "L": "❌"}.get(str(x).upper(), "?")
            for x in form_list[:5]
        )
    h_form = analysis.get("home_form", [])
    a_form = analysis.get("away_form", [])
    lines.append(f"  Forme: {form_icons(h_form)} vs {form_icons(a_form)}")

    # ── Classement (si dispo) ──
    hr = ctx.get("home_rank", 0)
    ar = ctx.get("away_rank", 0)
    if hr and ar:
        hz = ctx.get("home_zone", "")
        az = ctx.get("away_zone", "")
        hp = ctx.get("home_points", 0)
        ap = ctx.get("away_points", 0)
        lines.append(f"  🏅 #{hr} {hz} ({hp}pts) vs #{ar} {az} ({ap}pts)")

    # ── H2H ──
    h2h = ctx.get("h2h_summary", "")
    if h2h and h2h != "pas de H2H":
        lines.append(f"  🤝 H2H: {h2h}")

    lines.append("")

    # ── Modèle Poisson ──
    lam_h = analysis.get("lambda_home", 0)
    lam_a = analysis.get("lambda_away", 0)
    avg_h = analysis.get("avg_home", 1.5)
    avg_a = analysis.get("avg_away", 1.2)
    lines.append("🧮 <b>Modèle Poisson + Dixon-Coles</b>")
    lines.append(f"  μ ligue: {avg_h:.2f} dom / {avg_a:.2f} ext")
    lines.append(f"  λ {home}: <b>{lam_h:.2f}</b> buts attendus")
    lines.append(f"  λ {away}: <b>{lam_a:.2f}</b> buts attendus")
    lines.append("")

    # ── Probabilités modèle vs marché ──
    p_h = analysis.get("p_home", 0)
    p_d = analysis.get("p_draw", 0)
    p_a = analysis.get("p_away", 0)
    o_h = analysis.get("odds_home", 0)
    o_d = analysis.get("odds_draw", 0)
    o_a = analysis.get("odds_away", 0)

    # Consensus bookmaker (sans marge)
    total_impl = 0
    if o_h > 1 and o_d > 1 and o_a > 1:
        total_impl = 1 / o_h + 1 / o_d + 1 / o_a
    c_h = (1 / o_h / total_impl) if total_impl > 0 else 0
    c_d = (1 / o_d / total_impl) if total_impl > 0 else 0
    c_a = (1 / o_a / total_impl) if total_impl > 0 else 0

    e_h = p_h * o_h - 1 if o_h > 1 else -1
    e_d = p_d * o_d - 1 if o_d > 1 else -1
    e_a = p_a * o_a - 1 if o_a > 1 else -1

    def edge_icon(e):
        if e >= 0.08:
            return "✅"
        return "❌"

    lines.append("📈 <b>Probabilités modèle vs marché</b>")
    lines.append(
        f"  Home: modèle {p_h:.0%} vs marché {c_h:.0%} "
        f"(cote {o_h:.2f}) → edge {e_h:+.0%} {edge_icon(e_h)}"
    )
    lines.append(
        f"  Draw: modèle {p_d:.0%} vs marché {c_d:.0%} "
        f"(cote {o_d:.2f}) → edge {e_d:+.0%} {edge_icon(e_d)}"
    )
    lines.append(
        f"  Away: modèle {p_a:.0%} vs marché {c_a:.0%} "
        f"(cote {o_a:.2f}) → edge {e_a:+.0%} {edge_icon(e_a)}"
    )
    lines.append("")

    # ── Verdict ──
    sel_label = {"home": home, "away": away, "draw": "Nul"}.get(sel.lower(), sel)
    lines.append(
        f"💎 <b>VERDICT : {sel_icon} {sel.upper()} ({sel_label})</b>"
    )
    lines.append(
        f"  Cote <b>{odds:.2f}</b> │ Modèle <b>{model_prob:.0%}</b> │ "
        f"Edge <b>{edge:.0%}</b>"
    )
    lines.append(
        f"  💵 Mise: {stake:.0f}€ → Gain: <b>+{gain:.0f}€</b>"
    )

    # ── Explication de la value ──
    if sel.lower() == "away" or sel.lower() == "draw":
        lines.append("")
        if sel.lower() == "away":
            lines.append(
                f"  💡 <i>Pourquoi ? Le marché donne {c_a:.0%} à {away}, "
                f"le modèle dit {p_a:.0%}. "
            )
            if a_conced < avg_h * 0.5:
                lines.append(
                    f"  Défense ext. exceptionnelle ({a_conced:.2f} enc/m "
                    f"vs {avg_h:.2f} moy. ligue).</i>"
                )
            elif a_scored > avg_a * 1.3:
                lines.append(
                    f"  Attaque ext. supérieure ({a_scored:.2f} buts/m "
                    f"vs {avg_a:.2f} moy. ligue).</i>"
                )
            else:
                lines.append(
                    f"  Écart significatif entre le modèle et le marché.</i>"
                )
        else:  # draw
            lines.append(
                f"  💡 <i>Pourquoi ? Le marché donne {c_d:.0%} au nul, "
                f"le modèle dit {p_d:.0%}. "
                f"Équipes proches (λ similaires).</i>"
            )
    elif sel.lower() == "home" and edge >= 0.08:
        lines.append("")
        lines.append(
            f"  💡 <i>Favori sous-coté : modèle {p_h:.0%} vs marché {c_h:.0%}.</i>"
        )

    return "\n".join(lines)


def build_telegram_message(data: dict, is_resend: bool = False) -> str:
    """
    Construit le message Telegram formaté en HTML.
    Telegram supporte : <b>, <i>, <u>, <s>, <a>, <code>, <pre>
    """
    bets = data.get("bets", [])
    summary = data.get("summary", {})
    scan_time = data.get("scan_time", "N/A")

    # ─── Header ───
    resend_tag = " 🔁 <i>(rappel)</i>" if is_resend else ""
    lines = [
        f"🎯 <b>betX – Value Bets du Jour</b>{resend_tag}",
        f"📅 {scan_time} │ 100% ESPN │ Stratégie BT-Optimisée",
        "",
        f"📊 <b>{len(bets)}</b> paris │ "
        f"💰 Mise: <b>{summary.get('total_stake', 0):.0f}€</b> │ "
        f"🎯 Gain: <b>+{summary.get('total_potential_gain', 0):.0f}€</b>",
        f"📈 Edge moy: <b>{summary.get('avg_edge', 0):.1%}</b> │ "
        f"Cote moy: <b>{summary.get('avg_odds', 0):.2f}</b>",
        "",
        "━" * 28,
    ]

    # ─── Pastille confiance ───
    def conf_badge(edge: float, sel: str) -> str:
        bt_boost = sel.lower() in ("away", "draw")
        if edge >= 0.20 and bt_boost:
            return "🟢🟢🟢"
        elif edge >= 0.15 and bt_boost:
            return "🟢🟢"
        elif edge >= 0.15:
            return "🟢"
        elif edge >= 0.10 and bt_boost:
            return "🟡🟢"
        return "🟡"

    def bt_icon(sel: str) -> str:
        return {"away": "🟢", "draw": "🟢", "home": "🟡"}.get(sel.lower(), "⚪")

    # ─── Liste des bets ───
    for i, b in enumerate(bets, 1):
        edge = b.get("edge", 0)
        sel = b.get("selection", "")
        gain = b.get("stake", 0) * (b.get("odds", 1) - 1)
        badge = conf_badge(edge, sel)
        ctx = b.get("context", {})

        lines.append("")
        lines.append(
            f"{badge} <b>{i}. {b.get('home_team', '')} vs {b.get('away_team', '')}</b>"
        )
        lines.append(
            f"   {bt_icon(sel)} {sel.title()} │ "
            f"P: <b>{b.get('model_prob', 0):.0%}</b> │ "
            f"Cote: <b>{b.get('odds', 0):.2f}</b> │ "
            f"Edge: <b>{edge:.0%}</b>"
        )
        lines.append(
            f"   💵 {b.get('stake', 0):.0f}€ → <b>+{gain:.0f}€</b> │ "
            f"📚 {b.get('bookmaker', '')}"
        )

        # Contexte avancé
        if ctx:
            # Classement + zone
            hr = ctx.get('home_rank', 0)
            ar = ctx.get('away_rank', 0)
            hz = ctx.get('home_zone', '')
            az = ctx.get('away_zone', '')
            hp = ctx.get('home_points', 0)
            ap = ctx.get('away_points', 0)
            if hr and ar:
                lines.append(
                    f"   🏅 #{hr} {hz} ({hp}pts) vs #{ar} {az} ({ap}pts)"
                )

            # Forme
            hf = ctx.get('home_form', '')
            af = ctx.get('away_form', '')
            if hf or af:
                def form_icons(f_str: str) -> str:
                    return ''.join(
                        {"W": "✅", "D": "🟡", "L": "❌"}.get(c, c)
                        for c in f_str
                    )
                lines.append(
                    f"   📈 Forme: {form_icons(hf)} vs {form_icons(af)}"
                )

            # H2H
            h2h = ctx.get('h2h_summary', '')
            if h2h and h2h != 'pas de H2H':
                lines.append(f"   🤝 H2H: {h2h}")

            # Pression
            hp_p = ctx.get('home_pressure', 1.0)
            ap_p = ctx.get('away_pressure', 1.0)
            if hp_p > 1.0 or ap_p > 1.0:
                pressure_text = []
                if hp_p > 1.0:
                    pressure_text.append(f"{b.get('home_team', '')} x{hp_p:.2f}")
                if ap_p > 1.0:
                    pressure_text.append(f"{b.get('away_team', '')} x{ap_p:.2f}")
                lines.append(f"   ⚡ Pression: {' / '.join(pressure_text)}")

    # ─── Footer ───
    lines.extend([
        "",
        "━" * 28,
        "",
        f"💰 <b>Retour total estimé : {summary.get('total_return', 0):.0f}€</b>",
        "",
        "🔬 <i>Backtest 2024: +4.76% yield │ Sharpe 1.48</i>",
        "🟢 Away/Draw = high yield │ 🟡 Home = prudence",
        "",
        "<i>🟢🟢🟢 Très haute │ 🟢🟢 Haute │ 🟢 Bonne</i>",
        "<i>🟡🟢 Modérée+ │ 🟡 Standard</i>",
        "",
        '📊 <a href="http://213.199.41.168">Dashboard Live</a>',
    ])

    return "\n".join(lines)


# ─── Telegram Sender ──────────────────────────────────────────────────────

_TG_API = "https://api.telegram.org/bot{token}/{method}"
_TG_MAX_MSG_LEN = 4096  # Limite Telegram


def send_telegram(data: dict, chat_id: str | None = None, is_resend: bool = False) -> bool:
    """
    Envoie via Telegram :
    1) Un message d'analyse détaillée par match (si analyse dispo)
    2) Un résumé global (comme avant)

    Envoie en DM + channel.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    dm_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
    channel_id = os.getenv("TELEGRAM_CHANNEL_ID", "")

    if not token:
        print("\n⚠️  TELEGRAM_BOT_TOKEN manquant dans .env")
        return False

    targets: list[tuple[str, str]] = []
    if dm_id:
        targets.append((dm_id, "DM"))
    if channel_id:
        targets.append((channel_id, "Channel"))

    if not targets:
        print("\n⚠️  Aucun destinataire Telegram.")
        return False

    import time as _time

    def _send_msg(cid: str, text: str) -> bool:
        """Envoie un message, découpe si > 4096 chars."""
        chunks: list[str] = []
        if len(text) <= _TG_MAX_MSG_LEN:
            chunks = [text]
        else:
            current = ""
            for line in text.split("\n"):
                if len(current) + len(line) + 1 > _TG_MAX_MSG_LEN - 100:
                    chunks.append(current)
                    current = line
                else:
                    current += ("\n" if current else "") + line
            if current:
                chunks.append(current)

        ok = True
        for chunk in chunks:
            url = _TG_API.format(token=token, method="sendMessage")
            payload = {
                "chat_id": cid,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            try:
                resp = httpx.post(url, json=payload, timeout=30)
                if resp.status_code != 200:
                    err = resp.json().get("description", resp.text)
                    print(f"  ❌ Telegram erreur : {err}")
                    ok = False
            except Exception as e:
                print(f"  ❌ Erreur Telegram : {e}")
                ok = False
            _time.sleep(0.3)  # Rate limit Telegram
        return ok

    bets = data.get("bets", [])
    success = True
    sent_to = []

    for cid, label in targets:
        # ── 1) Analyses détaillées par match ──
        analyses_sent = 0
        for i, b in enumerate(bets, 1):
            analysis = b.get("analysis", {})
            if not analysis or not analysis.get("enriched"):
                continue
            try:
                msg = build_match_analysis_message(b, analysis, i)
                if _send_msg(cid, msg):
                    analyses_sent += 1
            except Exception as e:
                print(f"  ⚠️  Analyse match {i} échouée : {e}")

        if analyses_sent > 0:
            print(f"  📊 {analyses_sent} analyses détaillées envoyées [{label}]")

        # ── 2) Résumé global ──
        summary_msg = build_telegram_message(data, is_resend=is_resend)
        if _send_msg(cid, summary_msg):
            sent_to.append(label)
        else:
            success = False

    if sent_to:
        print(f"\n📬 Telegram envoyé ! ({len(bets)} bets → {', '.join(sent_to)})")

    return success


# ─── Helper : récupérer le chat_id ───────────────────────────────────────

def get_chat_id() -> str | None:
    """Récupère le chat_id en lisant les derniers messages reçus par le bot."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN manquant dans .env")
        return None

    url = _TG_API.format(token=token, method="getUpdates")
    resp = httpx.get(url, timeout=15)
    data = resp.json()

    if not data.get("ok") or not data.get("result"):
        print("\n⚠️  Aucun message reçu par le bot.")
        print("   → Ouvre Telegram, cherche @BetX_goat_bot")
        print("   → Envoie /start")
        print("   → Puis relance cette commande")
        return None

    # Prendre le dernier message
    last = data["result"][-1]
    chat = last.get("message", {}).get("chat", {})
    cid = str(chat.get("id", ""))
    name = (chat.get("first_name", "") + " " + chat.get("last_name", "")).strip()
    username = chat.get("username", "")

    print(f"\n✅ Chat ID trouvé : {cid}")
    print(f"   Utilisateur : {name} (@{username})")
    print(f"\n   Ajoute dans .env :")
    print(f"   TELEGRAM_CHAT_ID={cid}")

    return cid


# ─── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="betX Daily Scan")
    parser.add_argument("--notify", action="store_true",
                        help="Envoyer le récap via Telegram")
    parser.add_argument("--resend", action="store_true",
                        help="Renvoyer le dernier scan (pas de nouvel appel API)")
    parser.add_argument("--chat-id", type=str, default=None,
                        help="Chat ID Telegram (override .env)")
    parser.add_argument("--get-chat-id", action="store_true",
                        help="Récupère ton chat_id Telegram")
    args = parser.parse_args()

    # Mode : récupérer le chat_id
    if args.get_chat_id:
        get_chat_id()
        return

    # Mode : renvoyer le dernier scan sans consommer de quota API
    if args.resend:
        if not DATA_FILE.exists():
            print("\n❌ Aucun scan précédent trouvé.")
            return
        data = json.loads(DATA_FILE.read_text())
        scan_date = data.get("scan_date", "?")
        nb = len(data.get("bets", []))
        events = data.get("summary", {}).get("events_scanned", 0)
        if events == 0:
            print(f"\n⏭️  Scan du {scan_date} vide (0 événements). Pas de resend.")
            return
        if nb == 0:
            print(f"\n⏭️  Scan du {scan_date} : 0 value bets. Pas de resend.")
            return
        print(f"\n📋 Renvoi du scan du {scan_date} ({nb} bets)")
        send_telegram(data, chat_id=args.chat_id, is_resend=True)
        print("\n✅ Resend terminé.")
        return

    # Mode normal : nouveau scan
    data = run_and_export()

    if args.notify:
        events = data.get("summary", {}).get("events_scanned", 0)
        if events == 0:
            print("\n⏭️  0 événements scannés. Telegram non envoyé.")
        else:
            send_telegram(data, chat_id=args.chat_id)

    print("\n✅ Scan quotidien terminé.")


if __name__ == "__main__":
    main()
