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
from datetime import date, timedelta
from pathlib import Path

import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# Ajouter le root au path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from betx.database import init_db, get_session, Bet, BankrollHistory, Match, Prediction
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
    st.sidebar.markdown("*Analyse de paris sportifs*")

    page = st.sidebar.radio(
        "Navigation",
        [
            "📊 Vue d'ensemble",
            "💰 Value Bets du jour",
            "📋 Historique des paris",
            "📈 CLV Tracking",
            "🏆 Analyse par sport",
            "🌐 Benchmark Sites",
            "🤝 Consensus Sites",
            "🧪 Backtesting",
            "⚙️ Configuration",
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

    # Dispatch
    if page == "📊 Vue d'ensemble":
        page_overview(session)
    elif page == "💰 Value Bets du jour":
        page_value_bets(session)
    elif page == "📋 Historique des paris":
        page_history(session)
    elif page == "📈 CLV Tracking":
        page_clv(session)
    elif page == "🏆 Analyse par sport":
        page_sports(session)
    elif page == "🌐 Benchmark Sites":
        page_external_benchmark(session)
    elif page == "🤝 Consensus Sites":
        page_external_consensus(session)
    elif page == "🧪 Backtesting":
        page_backtest(session)
    elif page == "⚙️ Configuration":
        page_config()

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
    """Classement des sites de prediction externes."""
    st.title("🌐 Benchmark Sites de Pronostics")
    st.caption("Collecte multi-sites, comparaison aux résultats réels, et classement qualité.")

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        history_days = st.selectbox("Historique à scraper", [7, 15, 30, 60, 90], index=2)
    with col_b:
        score_window = st.selectbox("Fenêtre de score", [30, 60, 90], index=1)
    with col_c:
        min_graded = st.slider("Min matchs classés", 1, 100, 5, 1)

    service = ExternalBenchmarkService(session=session)

    if st.button("🔄 Refresh benchmark (scrape + scoring)", type="primary"):
        with st.spinner("Scraping des sites et recalcul des classements..."):
            summary = service.run_full_refresh(history_days=history_days)
        st.success("Benchmark mis à jour")
        st.json(summary)

    activity = service.latest_activity()
    c1, c2, c3 = st.columns(3)
    c1.metric("Sites suivis", activity.get("sites", 0))
    c2.metric("Pronos collectés", activity.get("predictions", 0))
    c3.metric("Pronos évalués", activity.get("graded", 0))

    st.subheader("Etat des sources")
    health_rows = service.collect_source_health()
    if health_rows:
        health_df = pd.DataFrame(health_rows).rename(
            columns={
                "site_name": "Site",
                "status": "Etat",
                "status_code": "HTTP",
                "parsed_count": "Extraits",
                "recent_predictions_7d": "Pronos 7j",
                "url": "URL testee",
                "error": "Erreur",
            }
        )
        st.dataframe(health_df, use_container_width=True, hide_index=True)

    leaderboard = service.leaderboard_dataframe(window_days=score_window, min_graded=min_graded)
    if not leaderboard:
        fallback = service.leaderboard_dataframe(window_days=score_window, min_graded=1)
        st.warning(
            "Pas encore assez de données évaluées pour classer les sites. "
            "Lance un refresh avec plus d'historique ou attends la fin de matchs."
        )
        if fallback:
            st.info(
                "Des données existent avec un seuil plus bas. "
                "Baissez 'Min matchs classés' pour afficher le classement."
            )
            fb = pd.DataFrame(fallback).rename(
                columns={
                    "site_name": "Site",
                    "graded_count": "Matchs évalués",
                    "hit_rate": "Hit Rate",
                    "roi_flat": "ROI Flat",
                    "quality_score": "Score qualité",
                }
            )
            if "Hit Rate" in fb:
                fb["Hit Rate"] = fb["Hit Rate"].map(lambda x: f"{x:.1%}")
            if "ROI Flat" in fb:
                fb["ROI Flat"] = fb["ROI Flat"].map(lambda x: f"{x:+.1%}")
            if "Score qualité" in fb:
                fb["Score qualité"] = fb["Score qualité"].map(lambda x: f"{x:.2f}")
            st.dataframe(fb, use_container_width=True, hide_index=True)
        return

    st.subheader(f"Top sites – fenêtre {score_window} jours")
    df = pd.DataFrame(leaderboard)
    df = df.rename(
        columns={
            "site_name": "Site",
            "graded_count": "Matchs évalués",
            "hit_rate": "Hit Rate",
            "roi_flat": "ROI Flat",
            "quality_score": "Score qualité",
        }
    )

    if "Hit Rate" in df:
        df["Hit Rate"] = df["Hit Rate"].map(lambda x: f"{x:.1%}")
    if "ROI Flat" in df:
        df["ROI Flat"] = df["ROI Flat"].map(lambda x: f"{x:+.1%}")
    if "Score qualité" in df:
        df["Score qualité"] = df["Score qualité"].map(lambda x: f"{x:.2f}")

    st.dataframe(df, use_container_width=True, hide_index=True)


def page_external_consensus(session):
    """Recommendations built from best-performing external sites."""
    st.title("🤝 Consensus des Meilleurs Sites")
    st.caption("Paris recommandés selon le consensus des sites les mieux classés.")

    service = ExternalBenchmarkService(session=session)

    col1, col2, col3 = st.columns(3)
    with col1:
        top_n = st.selectbox("Top sites retenus", [2, 3, 4, 5], index=1)
    with col2:
        min_votes = st.selectbox("Consensus minimum", [2, 3, 4], index=0)
    with col3:
        score_window = st.selectbox("Fenêtre scoring", [30, 60, 90], index=1, key="consensus_window")

    if st.button("🧠 Recalculer recommandations"):
        with st.spinner("Calcul en cours..."):
            service.compute_site_scores(windows=[30, 60, 90], min_graded=5)

    recommendations = service.build_daily_recommendations(
        target_date=date.today(),
        top_n_sites=top_n,
        min_consensus_votes=min_votes,
        window_days=score_window,
    )

    if not recommendations:
        st.info(
            "Aucun pari consensus trouvé aujourd'hui. Soit les sites n'ont pas encore été scrapés, "
            "soit le consensus est insuffisant."
        )
        return

    df = pd.DataFrame(recommendations)
    df = df.rename(
        columns={
            "match": "Match",
            "league": "Ligue",
            "selection": "Sélection",
            "consensus_votes": "Votes",
            "confidence_score": "Score confiance",
            "sites": "Sites alignés",
            "kickoff": "Coup d'envoi",
        }
    )
    st.subheader("Équipes à parier (selon top sites)")
    st.dataframe(df, use_container_width=True, hide_index=True)


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
