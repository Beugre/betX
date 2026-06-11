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
WC_FILE = PROJECT_ROOT / "data" / "wc_predictions.json"


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

# ─── Onglets principaux ───────────────────────────────────────────────────
tab_vb, tab_wc = st.tabs(["🎯 Value Bets", "🌍 Coupe du Monde 2026"])

with tab_vb:
 col1, col2, col3 = st.columns(3)

with tab_vb:
 col1, col2, col3 = st.columns(3)

 # ─── Charger ou scanner ─────────────────────────────────────────────────
 data = load_bets_data()
 if st.button("🔄 Nouveau Scan", type="primary"):
     data = run_fresh_scan()

 if not data:
     st.warning("⚠️ Aucune donnée disponible. Lance `python daily_scan.py` ou clique sur 🔄 Nouveau Scan.")
     st.stop()

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

 if not bets:
     st.info("Aucun value bet détecté avec les seuils actuels (edge ≥ 8%, 1X2 only).")
 else:
  rows = []
  for b in bets:
      enriched = b.get("enriched", False)
      edge = b.get("edge", 0)
      selection = b.get("selection", "")
      badge = confidence_badge(edge, selection, enriched)
      score = confidence_score(edge, selection, enriched)
      gain = b.get("stake", 0) * (b.get("odds", 1) - 1)
      bt_map = {"away": "🟢 +30%", "draw": "🟢 +28%", "home": "🟡 -10%"}
      rows.append({
          "Confiance": badge, "Score": score,
          "Match": f"{b.get('home_team', '')} vs {b.get('away_team', '')}",
          "Sélection": selection.replace("_", " ").title(),
          "BT Signal": bt_map.get(selection.lower(), ""),
          "P(modèle)": f"{b.get('model_prob', 0):.1%}",
          "Cote": b.get("odds", 0), "Edge": edge,
          "Mise": f"{b.get('stake', 0):.0f}€", "Gain est.": f"+{gain:.0f}€",
          "Bookmaker": b.get("bookmaker", ""),
          "Enrichi": "✅" if enriched else "❌",
          "_edge_raw": edge, "_gain_raw": gain, "_odds_raw": b.get("odds", 0),
      })
  df = pd.DataFrame(rows)
  df = df[df["Score"] >= min_conf]
  if show_enriched_only:
      df = df[df["Enrichi"] == "✅"]
  sort_map = {"Confiance ↓": ("Score", False), "Edge ↓": ("_edge_raw", False),
              "Cote ↓": ("_odds_raw", False), "Gain estimé ↓": ("_gain_raw", False)}
  sort_col, sort_asc = sort_map[sort_by]
  df = df.sort_values(sort_col, ascending=sort_asc).reset_index(drop=True)
  df.index = df.index + 1
  display_cols = ["Confiance", "Match", "Sélection", "BT Signal", "P(modèle)",
                  "Cote", "Edge", "Mise", "Gain est.", "Bookmaker", "Enrichi"]
  styled_df = df[display_cols].style.format({"Edge": "{:.1%}", "Cote": "{:.2f}"})
  st.subheader(f"📋 {len(df)} Paris Sélectionnés")
  st.dataframe(styled_df, width="stretch", height=min(len(df) * 45 + 50, 800),
      column_config={
          "Confiance": st.column_config.TextColumn("🎯", width="small"),
          "Match": st.column_config.TextColumn("Match", width="large"),
          "Edge": st.column_config.NumberColumn("Edge", format="%.1f%%"),
          "Cote": st.column_config.NumberColumn("Cote", format="%.2f"),
      })
  st.divider()
  leg_col1, leg_col2 = st.columns(2)
  with leg_col1:
      st.markdown("""### 🎯 Légende\n| Pastille | Signification |\n|---|---|\n| 🟢🟢🟢 | Edge ≥ 20% + BT |\n| 🟢🟢 | Edge ≥ 15% + BT |\n| 🟢 | Edge ≥ 15% |\n| 🟡🟢 | Edge 10-15% Away/Draw |\n| 🟡 | Edge 8-10% |""")
  with leg_col2:
      st.markdown(f"""### 📊 Stats\n- Matchs : {summary.get('events_scanned',0)}\n- Edge moy : {summary.get('avg_edge',0):.1%}\n- Cote moy : {summary.get('avg_odds',0):.2f}\n- Bankroll : {summary.get('bankroll',1000):.0f}€""")

# ─── Onglet Coupe du Monde ────────────────────────────────────────────────

with tab_wc:
    st.subheader("🌍 Coupe du Monde 2026 – Prédictions phase de groupes")

    # Charger les données
    wc_data = None
    if WC_FILE.exists():
        try:
            wc_data = json.loads(WC_FILE.read_text())
        except Exception:
            pass

    col_wc1, col_wc2 = st.columns([3, 1])
    with col_wc1:
        if wc_data:
            st.caption(f"Dernière mise à jour : {wc_data.get('generated_at', 'N/A')} │ {wc_data.get('total_matches', 0)} matchs")
        else:
            st.warning("⚠️ Pas encore de prédictions CdM. Lance `python predict_wc_groups.py`")
    with col_wc2:
        if st.button("🔄 Actualiser CdM", type="secondary"):
            with st.spinner("Calcul des prédictions..."):
                import subprocess, sys
                subprocess.run([sys.executable, "predict_wc_groups.py"], capture_output=True)
            if WC_FILE.exists():
                wc_data = json.loads(WC_FILE.read_text())
                st.success("✅ Prédictions mises à jour")
                st.rerun()

    if not wc_data:
        st.stop()

    matches = wc_data.get("matches", [])

    # Filtre date
    all_dates = sorted({m["date"][:10] for m in matches})
    today_str = datetime.now().strftime("%Y-%m-%d")
    default_idx = next((i for i, d in enumerate(all_dates) if d >= today_str), 0)
    selected_date = st.selectbox("📅 Journée", ["Toutes"] + all_dates, index=default_idx + 1 if all_dates else 0)

    if selected_date != "Toutes":
        matches = [m for m in matches if m["date"].startswith(selected_date)]

    # Construire le tableau
    wc_rows = []
    for m in matches:
        pred = m.get("prediction", {})
        if not pred:
            continue
        top3 = pred.get("top_scores", [])
        best = top3[0] if top3 else {}
        status = m.get("status", "")
        is_done = status == "STATUS_FINAL"

        h_goals = int(m.get("home_score") or 0)
        a_goals = int(m.get("away_score") or 0)

        # Vérifier si la prédiction était correcte
        correct = ""
        if is_done and best:
            actual = f"{h_goals}-{a_goals}"
            if best.get("score") == actual:
                correct = "🎯 exact"
            elif (h_goals > a_goals and pred.get("p_home", 0) == max(pred["p_home"], pred["p_draw"], pred["p_away"])):
                correct = "✅ bon sens"
            elif (a_goals > h_goals and pred.get("p_away", 0) == max(pred["p_home"], pred["p_draw"], pred["p_away"])):
                correct = "✅ bon sens"
            elif h_goals == a_goals and pred.get("p_draw", 0) == max(pred["p_home"], pred["p_draw"], pred["p_away"]):
                correct = "✅ bon sens"

        # Favori
        ph, px, pa = pred.get("p_home", 0), pred.get("p_draw", 0), pred.get("p_away", 0)
        if ph > pa + 0.05:
            fav = f"⬆️ {m['home_short']}"
        elif pa > ph + 0.05:
            fav = f"⬇️ {m['away_short']}"
        else:
            fav = "↔️ Équilibré"

        scores_str = " / ".join(
            f"{s['score']} ({s['prob']*100:.0f}%)" for s in top3[:3]
        ) if top3 else "-"

        wc_rows.append({
            "📅 Date": m["date"][:10],
            "🕐": m["date"][11:16] + "Z",
            "Match": f"{m['home_short']} vs {m['away_short']}",
            "Résultat": f"{m['home_score']}-{m['away_score']}" if is_done else "—",
            "Score prédit": best.get("score", "?") if top3 else "?",
            "Top 3 scores": scores_str,
            "P(1) / P(X) / P(2)": f"{ph:.0%} / {px:.0%} / {pa:.0%}",
            "λ dom / ext": f"{pred.get('lambda_home',0):.2f} / {pred.get('lambda_away',0):.2f}",
            "O2.5": f"{pred.get('p_over_25',0):.0%}",
            "BTTS": f"{pred.get('p_btts',0):.0%}",
            "Favori": fav,
            "✓": correct,
            "Src": pred.get("source", "?"),
            "_ph": ph, "_pa": pa,
        })

    if not wc_rows:
        st.info("Aucun match pour cette journée.")
    else:
        wc_df = pd.DataFrame(wc_rows)
        display_wc_cols = ["📅 Date", "🕐", "Match", "Résultat", "Score prédit",
                           "Top 3 scores", "P(1) / P(X) / P(2)", "λ dom / ext",
                           "O2.5", "BTTS", "Favori", "✓", "Src"]
        st.dataframe(
            wc_df[display_wc_cols],
            use_container_width=True,
            height=min(len(wc_df) * 45 + 60, 900),
            column_config={
                "📅 Date": st.column_config.TextColumn(width="small"),
                "Match": st.column_config.TextColumn(width="medium"),
                "Top 3 scores": st.column_config.TextColumn(width="large"),
                "P(1) / P(X) / P(2)": st.column_config.TextColumn(width="medium"),
            },
        )
        # Stats rapides
        n_api = sum(1 for r in wc_rows if r["Src"] == "API")
        n_mixed = sum(1 for r in wc_rows if r["Src"] == "MIXED")
        n_fifa = sum(1 for r in wc_rows if r["Src"] == "FIFA")
        st.caption(
            f"Sources : 📡 API historique ({n_api}) │ 🔀 Mixte ({n_mixed}) │ 📊 FIFA ranking ({n_fifa})"
        )

st.divider()
st.caption("betX © 2026 – Données : ESPN + API-Football + FIFA Ranking")
