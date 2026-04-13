"""
betX – Streamlit Dashboard 📊

Tableau de bord interactif des value bets du jour.
Pastilles visuelles de confiance + envoi email quotidien.

Usage local :
    streamlit run app.py

Usage VPS (headless) :
    python daily_scan.py   # Génère le JSON + envoie l'email
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st
import pandas as pd

# ─── Config page ──────────────────────────────────────────────────────────

st.set_page_config(
    page_title="betX – Value Bets",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── Chemins ──────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_FILE = PROJECT_ROOT / "data" / "daily_bets.json"


# ─── Fonctions utilitaires ───────────────────────────────────────────────

def confidence_badge(edge: float, selection: str, enriched: bool) -> str:
    """
    Retourne une pastille visuelle de confiance.
    
    Critères :
    - 🟢 Haute confiance  : edge ≥ 15% ET (Away/Draw OU enrichi)
    - 🟡 Confiance modérée : edge ≥ 8% (seuil min)
    - 🔴 Faible confiance  : edge < 8% (ne devrait pas apparaître)
    
    La confiance tient compte :
    1. De l'edge (force du signal)
    2. Du type de sélection (backtest: Away/Draw > Home)
    3. De l'enrichissement API-Football (données réelles vs consensus)
    """
    bt_boost = selection.lower() in ("away", "draw")
    
    if edge >= 0.20 and (bt_boost or enriched):
        return "🟢🟢🟢"  # Très haute
    elif edge >= 0.15 and (bt_boost or enriched):
        return "🟢🟢"    # Haute  
    elif edge >= 0.15:
        return "🟢"       # Bonne
    elif edge >= 0.10 and bt_boost:
        return "🟡🟢"    # Modérée+
    elif edge >= 0.10:
        return "🟡"       # Modérée
    else:
        return "🟡"       # Standard (edge 8-10%)


def confidence_score(edge: float, selection: str, enriched: bool) -> int:
    """Score numérique 1-5 pour le tri."""
    bt_boost = selection.lower() in ("away", "draw")
    score = 1
    if edge >= 0.20:
        score = 4
    elif edge >= 0.15:
        score = 3
    elif edge >= 0.10:
        score = 2
    if bt_boost:
        score += 1
    if enriched:
        score = min(score + 1, 5)
    return score


def load_bets_data() -> dict | None:
    """Charge les données du dernier scan."""
    if not DATA_FILE.exists():
        return None
    try:
        data = json.loads(DATA_FILE.read_text())
        return data
    except Exception as e:
        st.error(f"Erreur lecture données : {e}")
        return None


def run_fresh_scan() -> dict | None:
    """Lance un scan frais et retourne les données."""
    with st.spinner("🔍 Scan en cours... (30-60 secondes)"):
        try:
            # Importer et exécuter le scan
            sys.path.insert(0, str(PROJECT_ROOT))
            from daily_scan import run_and_export
            return run_and_export()
        except Exception as e:
            st.error(f"Erreur scan : {e}")
            return None


# ─── Sidebar ──────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("⚙️ Options")
    
    sort_by = st.selectbox("Trier par", [
        "Confiance ↓", "Edge ↓", "Cote ↓", "Gain estimé ↓"
    ])
    
    min_conf = st.slider("Confiance minimum", 1, 5, 1)
    
    show_enriched_only = st.checkbox("Enrichis API-Football uniquement", value=False)
    
    st.divider()
    st.caption("betX v1.0 – Stratégie Backtest-Optimisée")
    st.caption("🔬 Backtest 2024: +4.76% yield, Sharpe 1.48")


# ─── Header ───────────────────────────────────────────────────────────────

st.title("🎯 betX – Value Bets du Jour")

col1, col2, col3 = st.columns(3)

# ─── Charger ou scanner ──────────────────────────────────────────────────

data = load_bets_data()

# Bouton refresh
if st.button("🔄 Nouveau Scan", type="primary"):
    data = run_fresh_scan()

if not data:
    st.warning("⚠️ Aucune donnée disponible. Lance `python daily_scan.py` ou clique sur 🔄 Nouveau Scan.")
    st.stop()

# ─── KPIs ─────────────────────────────────────────────────────────────────

scan_time = data.get("scan_time", "N/A")
bets = data.get("bets", [])
summary = data.get("summary", {})

with col1:
    st.metric("📊 Value Bets", len(bets))
with col2:
    st.metric("💰 Mise totale", f"{summary.get('total_stake', 0):.0f}€")
with col3:
    st.metric("🎯 Gain potentiel", f"+{summary.get('total_potential_gain', 0):.0f}€")

st.caption(f"Dernier scan : {scan_time} │ {summary.get('events_scanned', 0)} matchs analysés │ "
           f"{summary.get('enriched_count', 0)}/{summary.get('events_scanned', 0)} enrichis API-Football")

st.divider()

# ─── Construire le DataFrame ─────────────────────────────────────────────

if not bets:
    st.info("Aucun value bet détecté avec les seuils actuels (edge ≥ 8%, 1X2 only).")
    st.stop()

rows = []
for b in bets:
    enriched = b.get("enriched", False)
    edge = b.get("edge", 0)
    selection = b.get("selection", "")
    
    badge = confidence_badge(edge, selection, enriched)
    score = confidence_score(edge, selection, enriched)
    gain = b.get("stake", 0) * (b.get("odds", 1) - 1)
    
    # Signal backtest
    bt_map = {"away": "🟢 +30%", "draw": "🟢 +28%", "home": "🟡 -10%"}
    bt_signal = bt_map.get(selection.lower(), "")
    
    rows.append({
        "Confiance": badge,
        "Score": score,
        "Match": f"{b.get('home_team', '')} vs {b.get('away_team', '')}",
        "Sélection": selection.replace("_", " ").title(),
        "BT Signal": bt_signal,
        "P(modèle)": f"{b.get('model_prob', 0):.1%}",
        "Cote": b.get("odds", 0),
        "Edge": edge,
        "Mise": f"{b.get('stake', 0):.0f}€",
        "Gain est.": f"+{gain:.0f}€",
        "Bookmaker": b.get("bookmaker", ""),
        "Enrichi": "✅" if enriched else "❌",
        "_edge_raw": edge,
        "_gain_raw": gain,
        "_odds_raw": b.get("odds", 0),
    })

df = pd.DataFrame(rows)

# ─── Filtres ──────────────────────────────────────────────────────────────

df = df[df["Score"] >= min_conf]
if show_enriched_only:
    df = df[df["Enrichi"] == "✅"]

# Tri
sort_map = {
    "Confiance ↓": ("Score", False),
    "Edge ↓": ("_edge_raw", False),
    "Cote ↓": ("_odds_raw", False),
    "Gain estimé ↓": ("_gain_raw", False),
}
sort_col, sort_asc = sort_map[sort_by]
df = df.sort_values(sort_col, ascending=sort_asc).reset_index(drop=True)
df.index = df.index + 1  # Numérotation 1-based

# ─── Affichage ────────────────────────────────────────────────────────────

st.subheader(f"📋 {len(df)} Paris Sélectionnés")

# Colonnes visibles
display_cols = ["Confiance", "Match", "Sélection", "BT Signal", "P(modèle)", 
                "Cote", "Edge", "Mise", "Gain est.", "Bookmaker", "Enrichi"]

# Style avec couleurs
def style_edge(val):
    """Colorie l'edge en vert foncé → vert clair."""
    if isinstance(val, float):
        if val >= 0.20:
            return "background-color: #1b5e20; color: white; font-weight: bold"
        elif val >= 0.15:
            return "background-color: #2e7d32; color: white"
        elif val >= 0.10:
            return "background-color: #4caf50; color: white"
        else:
            return "background-color: #81c784; color: black"
    return ""

styled_df = df[display_cols].style.format({
    "Edge": "{:.1%}",
    "Cote": "{:.2f}",
})

st.dataframe(
    styled_df,
    width="stretch",
    height=min(len(df) * 45 + 50, 800),
    column_config={
        "Confiance": st.column_config.TextColumn("🎯", width="small"),
        "Match": st.column_config.TextColumn("Match", width="large"),
        "Sélection": st.column_config.TextColumn("Sél.", width="small"),
        "BT Signal": st.column_config.TextColumn("BT", width="small"),
        "Edge": st.column_config.NumberColumn("Edge", format="%.1f%%"),
        "Cote": st.column_config.NumberColumn("Cote", format="%.2f"),
    },
)

# ─── Légende confiance ───────────────────────────────────────────────────

st.divider()

leg_col1, leg_col2 = st.columns(2)

with leg_col1:
    st.markdown("""
    ### 🎯 Légende Confiance
    | Pastille | Signification |
    |----------|--------------|
    | 🟢🟢🟢 | **Très haute** – Edge ≥ 20% + signal BT favorable |
    | 🟢🟢 | **Haute** – Edge ≥ 15% + signal BT ou enrichi |
    | 🟢 | **Bonne** – Edge ≥ 15% |
    | 🟡🟢 | **Modérée+** – Edge 10-15% + Away/Draw |
    | 🟡 | **Standard** – Edge 8-10% |
    """)

with leg_col2:
    st.markdown(f"""
    ### 📊 Stats du Scan
    - **Matchs analysés** : {summary.get('events_scanned', 0)}
    - **Enrichis API-Football** : {summary.get('enriched_count', 0)} ({summary.get('enriched_pct', 0):.0%})
    - **Edge moyen** : {summary.get('avg_edge', 0):.1%}
    - **Cote moyenne** : {summary.get('avg_odds', 0):.2f}
    - **Bankroll** : {summary.get('bankroll', 1000):.0f}€
    
    ### 🔬 Référence Backtest 2024
    - **Yield** : +4.76% │ **Sharpe** : 1.48
    - 🟢 Away/Draw = high yield (+28-30%)
    - 🟡 Home = prudence (yield négatif)
    """)

# ─── Footer ──────────────────────────────────────────────────────────────

st.divider()
st.caption("betX © 2026 – Système de détection de value bets | Données : The Odds API + API-Football")
