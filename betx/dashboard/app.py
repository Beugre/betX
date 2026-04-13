"""
betX – Dashboard Streamlit.

Interface visuelle pour :
- Vue d'ensemble des performances (ROI, bankroll, drawdown)
- Value bets du jour (shortlist)
- Historique des paris
- CLV tracking
- Analyse par sport / marché
- Backtesting interactif
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# Ajouter le root au path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from betx.database import (
    init_db,
    get_session,
    BankrollHistory,
    ExternalPrediction,
    Match,
    PredictionSite,
    SiteScore,
)
from betx.analytics.performance_metrics import PerformanceTracker
from betx.analytics.clv_tracker import CLVTracker
from betx.config import settings
from betx.external.service import ExternalBenchmarkService


# =============================================================================
# Config Streamlit
# =============================================================================
st.set_page_config(
    page_title="betX – Sports Betting Analytics",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)


def main():
    """Point d'entrée du dashboard."""

    # Initialiser la DB
    init_db()
    session = get_session()

    # ── Sidebar ──
    st.sidebar.title("🎯 betX")
    st.sidebar.markdown("*Radar pronostics externes*")

    page = st.sidebar.radio(
        "Navigation",
        [
            "🌐 Aggregation Sites",
            "🏆 Meilleurs Sites",
            "🎯 Paris Recommandes",
        ],
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**Bankroll actuelle**")

    # Bankroll actuelle
    last_entry = (
        session.query(BankrollHistory)
        .order_by(BankrollHistory.date.desc())
        .first()
    )
    current_bankroll = last_entry.bankroll if last_entry else settings.bankroll.initial_bankroll
    st.sidebar.metric(
        "💶 Bankroll",
        f"{current_bankroll:,.2f}€",
        delta=f"{last_entry.daily_pnl:+.2f}€" if last_entry else None,
    )

    # Dispatch (dashboard simplifie)
    if page == "🌐 Aggregation Sites":
        page_external_benchmark(session)
    elif page == "🏆 Meilleurs Sites":
        page_external_consensus(session)
    elif page == "🎯 Paris Recommandes":
        page_recommendations(session)

    session.close()


# =============================================================================
# Pages
# =============================================================================
def page_overview(session):
    """Vue d'ensemble des performances."""
    st.title("📊 Vue d'ensemble")

    tracker = PerformanceTracker(session)

    # Période
    col1, col2, col3 = st.columns(3)
    with col1:
        report_daily = tracker.generate_report("daily")
        st.metric("Aujourd'hui", f"{report_daily.total_pnl:+.2f}€", f"{report_daily.total_bets} bets")
    with col2:
        report_weekly = tracker.generate_report("weekly")
        st.metric("Cette semaine", f"{report_weekly.total_pnl:+.2f}€", f"ROI: {report_weekly.roi_pct:+.1f}%")
    with col3:
        report_monthly = tracker.generate_report("monthly")
        st.metric("Ce mois", f"{report_monthly.total_pnl:+.2f}€", f"ROI: {report_monthly.roi_pct:+.1f}%")

    st.markdown("---")

    # Courbe de bankroll
    st.subheader("📈 Évolution de la bankroll")
    df_bankroll = tracker.get_bankroll_curve()
    if not df_bankroll.empty:
        fig, ax = plt.subplots(figsize=(12, 5))
        ax.plot(df_bankroll["date"], df_bankroll["bankroll"], color="#2ecc71", linewidth=2)
        ax.fill_between(
            df_bankroll["date"],
            df_bankroll["bankroll"],
            alpha=0.15,
            color="#2ecc71",
        )
        ax.set_xlabel("Date")
        ax.set_ylabel("Bankroll (€)")
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
        plt.tight_layout()
        st.pyplot(fig)
    else:
        st.info("Aucune donnée de bankroll. Lancez le pipeline pour commencer.")

    # KPIs détaillés
    st.markdown("---")
    st.subheader("📊 KPIs globaux")
    report_all = tracker.generate_report("all")

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total Bets", report_all.total_bets)
    c2.metric("Winrate", f"{report_all.winrate_pct:.1f}%")
    c3.metric("Yield", f"{report_all.yield_pct:+.2f}%")
    c4.metric("Avg Odds", f"{report_all.avg_odds:.2f}")
    c5.metric("Avg Edge", f"{report_all.avg_edge:.2%}")
    c6.metric("Max DD", f"{report_all.max_drawdown_pct:.1f}%")


def page_value_bets(session):
    """Value bets identifiés aujourd'hui."""
    st.title("💰 Value Bets du jour")

    # Récupérer les bets pending
    pending = session.query(Bet).filter(Bet.status == "pending").all()

    if not pending:
        st.info("Aucun value bet identifié aujourd'hui. Lancez le pipeline quotidien.")
        return

    # Filtres
    col1, col2 = st.columns(2)
    with col1:
        sport_filter = st.selectbox(
            "Sport", ["Tous"] + list(set(b.sport for b in pending))
        )
    with col2:
        min_edge_filter = st.slider("Edge minimum", 0.0, 0.15, 0.03, 0.01)

    filtered = pending
    if sport_filter != "Tous":
        filtered = [b for b in filtered if b.sport == sport_filter]
    filtered = [b for b in filtered if b.edge >= min_edge_filter]

    # Affichage
    for bet in sorted(filtered, key=lambda x: x.ev, reverse=True):
        with st.container():
            cols = st.columns([3, 1, 1, 1, 1, 1])
            match = session.query(Match).filter(Match.id == bet.match_id).first()
            match_name = f"{match.home_name} vs {match.away_name}" if match else "N/A"

            emoji = "⚽" if bet.sport == "football" else "🎾" if bet.sport == "tennis" else "🏀"
            confidence = "🔴" if bet.edge >= 0.08 else "🟡" if bet.edge >= 0.05 else "🟢"

            cols[0].markdown(f"{emoji} **{match_name}**\n\n{bet.market} → {bet.selection}")
            cols[1].metric("Cote", f"{bet.bookmaker_odds:.2f}")
            cols[2].metric("P(modèle)", f"{bet.model_probability:.1%}")
            cols[3].metric("Edge", f"{bet.edge:.1%}")
            cols[4].metric("EV", f"{bet.ev:.1%}")
            cols[5].metric("Mise", f"{bet.stake:.2f}€")
            st.markdown("---")


def page_history(session):
    """Historique des paris."""
    st.title("📋 Historique des paris")

    # Filtres
    col1, col2, col3 = st.columns(3)
    with col1:
        days_back = st.selectbox("Période", [7, 14, 30, 90, 365], index=2)
    with col2:
        status_filter = st.selectbox("Statut", ["Tous", "won", "lost", "pending"])
    with col3:
        sport_filter = st.selectbox("Sport", ["Tous", "football", "tennis", "basketball"])

    since = date.today() - timedelta(days=days_back)
    q = session.query(Bet).filter(Bet.created_at >= str(since))
    if status_filter != "Tous":
        q = q.filter(Bet.status == status_filter)
    if sport_filter != "Tous":
        q = q.filter(Bet.sport == sport_filter)

    bets = q.order_by(Bet.created_at.desc()).all()

    if not bets:
        st.info("Aucun pari trouvé pour cette période.")
        return

    # DataFrame
    data = []
    for b in bets:
        match = session.query(Match).filter(Match.id == b.match_id).first()
        data.append({
            "Date": str(b.created_at)[:10] if b.created_at else "",
            "Sport": b.sport,
            "Match": f"{match.home_name} vs {match.away_name}" if match else "",
            "Marché": b.market,
            "Sélection": b.selection,
            "Cote": b.bookmaker_odds,
            "P(modèle)": f"{b.model_probability:.1%}",
            "Edge": f"{b.edge:.1%}",
            "Mise": f"{b.stake:.2f}€",
            "Statut": b.status,
            "PnL": f"{b.pnl:+.2f}€" if b.pnl is not None else "-",
        })

    df = pd.DataFrame(data)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Stats rapides
    st.markdown("---")
    total_pnl = sum(b.pnl or 0 for b in bets if b.status in ("won", "lost"))
    total_staked = sum(b.stake for b in bets if b.status in ("won", "lost"))
    st.metric("PnL Total", f"{total_pnl:+.2f}€")


def page_clv(session):
    """Suivi CLV."""
    st.title("📈 CLV Tracking")

    clv_tracker = CLVTracker(session)

    col1, col2 = st.columns(2)
    with col1:
        report = clv_tracker.generate_report("all")
        st.metric("CLV moyen", f"{report.avg_clv:+.2f}%")
        st.metric("CLV médian", f"{report.median_clv:+.2f}%")
    with col2:
        st.metric("Bets avec CLV", f"{report.bets_with_clv}/{report.total_bets}")
        st.metric("% CLV positif", f"{report.pct_positive_clv:.1f}%")

    # CLV par sport
    if report.clv_by_sport:
        st.subheader("CLV par sport")
        df_sport = pd.DataFrame([
            {"Sport": k, "CLV moyen (%)": v}
            for k, v in report.clv_by_sport.items()
        ])
        st.dataframe(df_sport, use_container_width=True, hide_index=True)

    # CLV par marché
    if report.clv_by_market:
        st.subheader("CLV par marché")
        df_market = pd.DataFrame([
            {"Marché": k, "CLV moyen (%)": v}
            for k, v in report.clv_by_market.items()
        ])
        st.dataframe(df_market, use_container_width=True, hide_index=True)


def page_sports(session):
    """Analyse par sport."""
    st.title("🏆 Analyse par sport")

    tracker = PerformanceTracker(session)

    for sport, emoji in [("football", "⚽"), ("tennis", "🎾"), ("basketball", "🏀")]:
        st.subheader(f"{emoji} {sport.capitalize()}")
        report = tracker.generate_report("all", sport=sport)

        if report.total_bets == 0:
            st.info(f"Aucun pari {sport}")
            continue

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Bets", report.total_bets)
        c2.metric("ROI", f"{report.roi_pct:+.1f}%")
        c3.metric("Winrate", f"{report.winrate_pct:.1f}%")
        c4.metric("PnL", f"{report.total_pnl:+.2f}€")

        if report.roi_by_market:
            st.markdown("**Par marché:**")
            for market, roi in report.roi_by_market.items():
                st.text(f"  {market}: ROI {roi:+.1f}%")

        st.markdown("---")


def page_backtest(session):
    """Interface de backtesting."""
    st.title("🧪 Backtesting")
    st.info(
        "Le backtesting est disponible via la ligne de commande :\n\n"
        "```python -m betx.backtest.backtester```\n\n"
        "Les résultats seront affichés ici après exécution."
    )


def page_external_benchmark(session):
    """Vue agrégée brute: toutes les sélections détectées par match."""
    st.title("🌐 Aggregation Sites")
    st.caption("Toutes les aggregations par match: votes, sites alignés, et confiance estimée.")

    col_a, col_b = st.columns(2)
    with col_a:
        history_days = st.selectbox("Historique à scraper", [3, 7, 15, 30, 60, 90], index=3)
    with col_b:
        score_window = st.selectbox("Fenêtre de score", [30, 60, 90], index=1)

    service = ExternalBenchmarkService(session=session)
    if st.button("🔄 Rafraîchir les agrégations", type="primary"):
        with st.spinner("Scraping + recalcul en cours..."):
            summary = service.run_full_refresh(history_days=history_days)
        st.success("Agrégations mises à jour")
        st.json(summary)

    health_rows = service.collect_source_health()
    if health_rows:
        st.subheader("Etat des sources")
        health_df = pd.DataFrame(health_rows).rename(
            columns={
                "site_name": "Site",
                "status": "Etat",
                "status_code": "HTTP",
                "parsed_count": "Extraits",
                "recent_predictions_7d": "Pronos 7j",
                "url": "URL testee",
            }
        )
        st.dataframe(health_df, use_container_width=True, hide_index=True)

    rows, _ = build_aggregated_predictions(session=session, window_days=score_window, top_only=False, top_n_sites=20)
    st.subheader("Aggregation brute par match")
    if not rows:
        st.warning("Aucune aggregation disponible. Lance un refresh pour alimenter les données.")
        return

    agg_df = pd.DataFrame(rows)
    st.dataframe(agg_df, use_container_width=True, hide_index=True)


def page_external_consensus(session):
    """Classement des meilleurs sites + leurs derniers choix détectés."""
    st.title("🏆 Meilleurs Sites")
    st.caption("Top sites classés et leurs derniers choix 1X2 détectés.")

    score_window = st.selectbox("Fenêtre scoring", [30, 60, 90], index=1)
    top_n_sites = st.selectbox("Nombre de sites à afficher", [3, 5, 8, 10], index=1)

    service = ExternalBenchmarkService(session=session)
    leaderboard = service.leaderboard_dataframe(window_days=score_window, min_graded=1)
    if not leaderboard:
        st.warning("Classement indisponible pour le moment. Lance un refresh dans Aggregation Sites.")
        return

    top_df = pd.DataFrame(leaderboard[:top_n_sites]).rename(
        columns={
            "site_slug": "Slug",
            "site_name": "Site",
            "graded_count": "Matchs évalués",
            "hit_rate": "Hit Rate",
            "roi_flat": "ROI Flat",
            "quality_score": "Score qualité",
        }
    )
    top_df["Hit Rate"] = top_df["Hit Rate"].map(lambda x: f"{x:.1%}")
    top_df["ROI Flat"] = top_df["ROI Flat"].map(lambda x: f"{x:+.1%}")
    top_df["Score qualité"] = top_df["Score qualité"].map(lambda x: f"{x:.2f}")
    st.dataframe(top_df, use_container_width=True, hide_index=True)

    top_slugs = set(top_df["Slug"].tolist())
    picks_rows, _ = build_aggregated_predictions(
        session=session,
        window_days=score_window,
        top_only=True,
        top_n_sites=top_n_sites,
    )
    picks_df = pd.DataFrame([r for r in picks_rows if r["Site leader"] in top_slugs])
    st.subheader("Choix détectés des meilleurs sites")
    if picks_df.empty:
        st.info("Aucun choix détecté sur la période actuelle pour ces sites.")
        return
    st.dataframe(picks_df, use_container_width=True, hide_index=True)


def page_recommendations(session):
    """Ecran actionnable: liste des paris à faire + choix + indice de confiance."""
    st.title("🎯 Paris Recommandes")
    st.caption("Liste des équipes à parier, choix 1/X/2, et indice de confiance.")

    col1, col2, col3 = st.columns(3)
    with col1:
        score_window = st.selectbox("Fenêtre scoring", [30, 60, 90], index=1, key="reco_window")
    with col2:
        top_n = st.selectbox("Top sites utilisés", [3, 5, 8, 10], index=1)
    with col3:
        min_votes = st.selectbox("Votes minimum", [1, 2, 3], index=0)

    service = ExternalBenchmarkService(session=session)
    recommendations = service.build_daily_recommendations(
        target_date=date.today(),
        top_n_sites=top_n,
        min_consensus_votes=min_votes,
        window_days=score_window,
    )

    # Fallback automatique pour toujours fournir une short-list exploitable.
    if not recommendations:
        recommendations = service.build_daily_recommendations(
            target_date=date.today(),
            top_n_sites=10,
            min_consensus_votes=1,
            window_days=score_window,
        )

    if not recommendations:
        _, best_rows = build_aggregated_predictions(
            session=session,
            window_days=score_window,
            top_only=True,
            top_n_sites=max(5, top_n),
        )
        recommendations = best_rows

    if not recommendations:
        st.warning("Aucune recommandation encore disponible. Lance un refresh dans Aggregation Sites.")
        return

    df = pd.DataFrame(recommendations)
    rename_map = {
        "match": "Match",
        "league": "Ligue",
        "selection": "Choix",
        "consensus_votes": "Votes",
        "confidence_score": "Indice confiance",
        "sites": "Sites alignés",
        "kickoff": "Coup d'envoi",
        "site_leader": "Site leader",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    if "Indice confiance" in df.columns:
        df["Indice confiance"] = df["Indice confiance"].map(lambda x: f"{float(x):.1f}")

    st.subheader("Equipes a parier maintenant")
    st.dataframe(df, use_container_width=True, hide_index=True)


def build_aggregated_predictions(
    session,
    window_days: int = 60,
    top_only: bool = False,
    top_n_sites: int = 5,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Aggregate raw external predictions into actionable match-level selections."""
    service = ExternalBenchmarkService(session=session)
    top_sites = service.get_top_sites(window_days=window_days, limit=top_n_sites, min_graded=1)
    top_slugs = [s["site_slug"] for s in top_sites]
    site_quality = {s["site_slug"]: float(s["quality_score"]) for s in top_sites}

    query = session.query(ExternalPrediction, PredictionSite).join(PredictionSite, PredictionSite.id == ExternalPrediction.site_id)
    if top_only and top_slugs:
        query = query.filter(PredictionSite.slug.in_(top_slugs))

    source_rows = query.order_by(ExternalPrediction.scraped_at.desc()).limit(800).all()

    grouped: dict[tuple[str, str], dict[str, dict[str, object]]] = defaultdict(lambda: defaultdict(dict))
    match_meta: dict[str, dict[str, str]] = {}
    for pred, site in source_rows:
        if pred.market != "1x2":
            continue
        if pred.result_status not in {"pending", "won", "lost"}:
            continue

        match_key = f"{pred.normalized_home}::{pred.normalized_away}"
        match_meta[match_key] = {
            "match": f"{pred.home_name} vs {pred.away_name}",
            "league": pred.league or "N/A",
            "kickoff": str(pred.kickoff_time) if pred.kickoff_time else "",
        }
        grouped[match_key][pred.predicted_selection][site.slug] = {
            "site_name": site.name,
            "quality": site_quality.get(site.slug, 1.0),
        }

    table_rows: list[dict[str, str]] = []
    best_rows: list[dict[str, str]] = []

    for match_key, by_selection in grouped.items():
        best = None
        for selection, by_site in by_selection.items():
            votes = len(by_site)
            weighted = sum(float(v["quality"]) for v in by_site.values())
            confidence = min(100.0, round(votes * 18.0 + weighted * 6.0, 1))
            sites = ", ".join(sorted(v["site_name"] for v in by_site.values()))
            leader_slug = sorted(by_site.items(), key=lambda kv: float(kv[1]["quality"]), reverse=True)[0][0]

            row = {
                "Match": match_meta[match_key]["match"],
                "Ligue": match_meta[match_key]["league"],
                "Choix": selection,
                "Votes": str(votes),
                "Indice confiance": f"{confidence:.1f}",
                "Sites alignés": sites,
                "Site leader": leader_slug,
                "Coup d'envoi": match_meta[match_key]["kickoff"],
            }
            table_rows.append(row)

            if best is None or (confidence > float(best["Indice confiance"])):
                best = row

        if best is not None:
            best_rows.append(
                {
                    "match": best["Match"],
                    "league": best["Ligue"],
                    "selection": best["Choix"],
                    "consensus_votes": int(best["Votes"]),
                    "confidence_score": float(best["Indice confiance"]),
                    "sites": best["Sites alignés"],
                    "site_leader": best["Site leader"],
                    "kickoff": best["Coup d'envoi"],
                }
            )

    table_rows.sort(key=lambda r: float(r["Indice confiance"]), reverse=True)
    best_rows.sort(key=lambda r: float(r["confidence_score"]), reverse=True)
    return table_rows, best_rows


def page_config():
    """Configuration du système."""
    st.title("⚙️ Configuration")

    st.subheader("💰 Bankroll & Staking")
    col1, col2 = st.columns(2)
    with col1:
        st.text(f"Bankroll initiale: {settings.bankroll.initial_bankroll}€")
        st.text(f"Kelly fraction: {settings.bankroll.kelly_fraction}")
        st.text(f"Méthode par défaut: {settings.bankroll.default_method}")
    with col2:
        st.text(f"Flat %: {settings.bankroll.flat_pct:.1%}")
        st.text(f"Max stake %: {settings.bankroll.max_stake_pct:.1%}")

    st.subheader("🎯 Value Betting")
    st.text(f"Edge minimum: {settings.value.min_edge:.1%}")
    st.text(f"Cote min: {settings.value.min_odds}")
    st.text(f"Cote max: {settings.value.max_odds}")

    st.subheader("⚽ Football")
    st.text(f"Ligues: {settings.football.leagues}")
    st.text(f"ELO K-factor: {settings.football.elo_k_factor}")
    st.text(f"Home advantage: {settings.football.home_advantage}")
    st.text(f"xG weight: {settings.football.xg_weight}")

    tennis_cfg = getattr(settings, "tennis", None)
    if tennis_cfg is not None:
        st.subheader("🎾 Tennis")
        st.text(f"ELO K-factor: {tennis_cfg.elo_k_factor}")
        st.text(f"Surface weight: {tennis_cfg.surface_weight}")

    basket_cfg = getattr(settings, "basket", None)
    if basket_cfg is not None:
        st.subheader("🏀 Basketball")
        st.text(f"Home advantage: {basket_cfg.home_advantage} pts")
        st.text(f"League avg pace: {basket_cfg.league_avg_pace}")


# =============================================================================
# Entry point
# =============================================================================
if __name__ == "__main__":
    main()
