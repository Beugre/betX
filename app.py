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
TRACKER_FILE = PROJECT_ROOT / "data" / "prediction_log.json"


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
    st.subheader("🌍 Coupe du Monde 2026")

    # ── Charger les données ──────────────────────────────────────────────
    wc_data = None
    if WC_FILE.exists():
        try:
            wc_data = json.loads(WC_FILE.read_text())
        except Exception:
            pass

    tracker_records = []
    if TRACKER_FILE.exists():
        try:
            tracker_records = json.loads(TRACKER_FILE.read_text())
        except Exception:
            pass

    col_wc1, col_wc2 = st.columns([3, 1])
    with col_wc1:
        if wc_data:
            st.caption(f"Prédictions : {wc_data.get('generated_at', 'N/A')} │ {wc_data.get('total_matches', 0)} matchs")
        else:
            st.warning("⚠️ Lance `python predict_wc_groups.py` pour générer les prédictions.")
    with col_wc2:
        if st.button("🔄 Actualiser", type="secondary"):
            with st.spinner("Calcul..."):
                import subprocess as _sp
                _sp.run([sys.executable, "predict_wc_groups.py"], capture_output=True)
            if WC_FILE.exists():
                wc_data = json.loads(WC_FILE.read_text())
                st.rerun()

    # ── KPIs historique ──────────────────────────────────────────────────
    resolved = [r for r in tracker_records if r.get("result")]
    if resolved:
        st.subheader("📊 Historique de réussite")

        # Calculer les stats par marché
        def _market_stats(records, market=None, min_edge=0.0):
            r = [x for x in records
                 if x.get("result")
                 and (market is None or x.get("market") == market)
                 and x.get("edge", 0) >= min_edge]
            if not r:
                return None
            wins = sum(1 for x in r if x["result"] == "win")
            return {
                "n": len(r),
                "wins": wins,
                "win_rate": wins / len(r),
                "roi": sum((x["market_odds"] if x["result"] == "win" else 0) for x in r) / len(r) - 1,
                "avg_edge": sum(x.get("edge", 0) for x in r) / len(r),
            }

        all_stats = _market_stats(resolved)
        ou_stats  = _market_stats(resolved, "O/U")
        btts_stats = _market_stats(resolved, "BTTS")
        x12_stats  = _market_stats(resolved, "1X2")

        # Brier score
        brier = sum(
            (r["model_prob"] - (1 if r["result"] == "win" else 0)) ** 2
            for r in resolved
        ) / len(resolved)

        # KPI row global
        kpi_cols = st.columns(4)
        with kpi_cols[0]:
            wr = all_stats["win_rate"] if all_stats else 0
            st.metric("🎯 Win rate global", f"{wr:.0%}",
                      help=f"{all_stats['wins']}/{all_stats['n']} paris gagnés" if all_stats else "")
        with kpi_cols[1]:
            roi = all_stats["roi"] if all_stats else 0
            st.metric("💰 ROI simulé", f"{roi*100:+.1f}%",
                      delta=f"{roi*100:+.1f}%",
                      delta_color="normal")
        with kpi_cols[2]:
            st.metric("📐 Brier Score", f"{brier:.3f}",
                      help="0 = parfait | 0.25 = aléatoire")
        with kpi_cols[3]:
            st.metric("📋 Paris résolus", f"{len(resolved)}")

        st.divider()

        # Tableau par marché
        mkt_rows = []
        for mkt_name, stats in [("Tous", all_stats), ("O/U 2.5", ou_stats),
                                  ("BTTS", btts_stats), ("1X2", x12_stats)]:
            if not stats:
                continue
            roi_color = "🟢" if stats["roi"] > 0 else "🔴"
            mkt_rows.append({
                "Marché": mkt_name,
                "Paris": stats["n"],
                "Gagnés": stats["wins"],
                "Win rate": f"{stats['win_rate']:.0%}",
                "ROI simulé": f"{roi_color} {stats['roi']*100:+.1f}%",
                "Edge moyen": f"{stats['avg_edge']*100:.1f} pts",
            })
        if mkt_rows:
            st.dataframe(pd.DataFrame(mkt_rows), use_container_width=True, hide_index=True)

        # ── Détail des paris résolus ──────────────────────────────────
        st.subheader("📋 Détail des prédictions passées")

        hist_rows = []
        for r in sorted(resolved, key=lambda x: x.get("match_date", ""), reverse=True):
            won = r["result"] == "win"
            edge = r.get("edge", 0)
            ev = r.get("ev", 0)
            hist_rows.append({
                "📅": r.get("match_date", "?"),
                "Match": f"{r.get('home','?')} vs {r.get('away','?')}",
                "Marché": r.get("market", "?"),
                "Sélection": r.get("selection", "?"),
                "P(modèle)": f"{r.get('model_prob',0):.0%}",
                "Cote": f"{r.get('market_odds',0):.2f}",
                "Edge": f"{edge*100:+.1f} pts",
                "EV": f"{ev*100:+.1f}%",
                "Résultat": r.get("actual_score", "?"),
                "✓": "✅ Win" if won else "❌ Loss",
                "_won": won,
                "_edge": edge,
            })

        hist_df = pd.DataFrame(hist_rows)
        cols_show = ["📅", "Match", "Marché", "Sélection", "P(modèle)",
                     "Cote", "Edge", "EV", "Résultat", "✓"]

        def _color_result(val):
            if "Win" in str(val):
                return "background-color: #1b5e20; color: white"
            if "Loss" in str(val):
                return "background-color: #b71c1c; color: white"
            return ""

        styled_hist = hist_df[cols_show].style.map(_color_result, subset=["✓"])
        st.dataframe(styled_hist, use_container_width=True,
                     height=min(len(hist_rows) * 40 + 50, 600), hide_index=True)

        st.divider()
    else:
        st.info("Aucun résultat résolu pour l'instant. Les prédictions se résolvent automatiquement après chaque match.")

    # ── Prédictions à venir ──────────────────────────────────────────────
    if not wc_data:
        st.stop()

    st.subheader("🔮 Prédictions")
    matches_all = wc_data.get("matches", [])

    all_dates = sorted({m["date"][:10] for m in matches_all})
    today_str = datetime.now().strftime("%Y-%m-%d")
    default_idx = next((i for i, d in enumerate(all_dates) if d >= today_str), 0)
    selected_date = st.selectbox("📅 Journée", ["Toutes"] + all_dates,
                                  index=0)  # "Toutes" par défaut

    matches = matches_all if selected_date == "Toutes" else [
        m for m in matches_all if m["date"].startswith(selected_date)
    ]

    # Index tracker par match — toutes les prédictions historiques (pas seulement 1X2)
    tracker_by_match: dict[str, dict] = {}
    tracker_scores: dict[str, str] = {}  # prédiction de score au moment du pari
    for r in tracker_records:
        k = f"{r.get('home','')}_{r.get('away','')}_{r.get('match_date','')}"
        if r.get("market") == "1X2" and r.get("result"):
            tracker_by_match[k] = r
            # Score prédit stocké dans le tracker au moment de l'enregistrement
            if r.get("predicted_score"):
                tracker_scores[k] = r["predicted_score"]

    wc_rows = []
    for m in matches:
        pred = m.get("prediction", {})
        if not pred:
            continue
        top3 = pred.get("top_scores", [])
        best = top3[0] if top3 else {}
        status = m.get("status", "")
        is_done = status in ("STATUS_FINAL", "STATUS_FULL_TIME")
        h_s = m["date"][11:13]
        h_fr = (int(h_s) + 2) % 24 if h_s.isdigit() else 0
        heure = f"{h_fr:02d}h" if not is_done else "FT"
        ph = pred.get("p_home", 0)

        # Score prédit : utiliser la valeur historique du tracker si dispo
        tk_key = f"{m['home']}_{m['away']}_{m['date'][:10]}"
        historical_score = tracker_scores.get(tk_key)
        display_score = historical_score if (historical_score and is_done) else (best.get("score", "?") if top3 else "?")
        px = pred.get("p_draw", 0)
        pa = pred.get("p_away", 0)

        # Vérifier la réussite de la prédiction
        # Priorité : données du tracker (prédiction historique) > recalcul actuel
        perf = ""
        if is_done and m.get("home_score") is not None:
            actual = f"{m['home_score']}-{m['away_score']}"
            tk = tracker_by_match.get(f"{m['home']}_{m['away']}_{m['date'][:10]}")
            if tk:
                # Utiliser la prédiction stockée dans le tracker (pas le recalcul)
                tracked_sel = tk.get("selection", "")
                hg, ag = int(m["home_score"]), int(m["away_score"])
                correct_sel = "home" if hg > ag else ("away" if ag > hg else "draw")
                if tracked_sel == correct_sel:
                    perf = "✅ Bon sens"
                else:
                    perf = "❌ Raté"
            else:
                # Fallback : utiliser les probabilités actuelles
                if best.get("score") == actual:
                    perf = "🎯 Score exact"
                else:
                    hg, ag = int(m["home_score"]), int(m["away_score"])
                    likely = max([("home", ph), ("draw", px), ("away", pa)], key=lambda x: x[1])[0]
                    ok = (likely == "home" and hg > ag) or (likely == "away" and ag > hg) or (likely == "draw" and hg == ag)
                    perf = "✅ Bon sens" if ok else "❌ Raté"

        # Over/Under réel
        ou_real = ""
        if is_done and m.get("home_score") is not None:
            total = int(m["home_score"]) + int(m["away_score"])
            pred_over = pred.get("p_over_25", 0) >= 0.5
            real_over = total > 2
            ou_real = ("✅" if pred_over == real_over else "❌") + f" {'O' if real_over else 'U'}2.5 ({total})"

        # ── Calcul Edge depuis les cotes disponibles (auto ou saisie manuelle) ──
        oh = m.get("odds_home") or 0
        ox = m.get("odds_draw") or 0
        oa = m.get("odds_away") or 0
        o_over = m.get("odds_over_25") or 0
        o_under = m.get("odds_under_25") or 0
        bkm = m.get("odds_bookmaker", "")

        def _edge_str(model_p, odds):
            if not odds or odds <= 1.0:
                return "—"
            impl = 1.0 / odds
            e = model_p - impl
            icon = "🟢" if e >= 0.05 else ("🟡" if e > 0 else "🔴")
            return f"{icon} {e*100:+.0f}pts @{odds:.2f}"

        # Edge 1X2 : meilleur edge parmi les 3 issues
        edge_1x2 = "—"
        if oh > 1 and ox > 1 and oa > 1:
            best_e = max(
                [(ph, oh, "1"), (px, ox, "X"), (pa, oa, "2")],
                key=lambda x: x[0] - 1/x[1]
            )
            e_val = best_e[0] - 1/best_e[1]
            icon = "🟢" if e_val >= 0.05 else ("🟡" if e_val > 0 else "🔴")
            edge_1x2 = f"{icon} {best_e[2]} {e_val*100:+.0f}pts @{best_e[1]:.2f}"

        # Edge O/U
        p_o25 = pred.get("p_over_25", 0)
        p_u25 = pred.get("p_under_25", p_o25 and (1 - p_o25))
        if p_o25 >= 0.5:
            raw = _edge_str(p_o25, o_over) if o_over > 1 else _edge_str(p_o25, 1.90)
            edge_ou = raw.replace("@1.90", "@~1.90").replace("pts ", "pts O2.5 ") if raw != "—" else "—"
        else:
            raw = _edge_str(p_u25, o_under) if o_under > 1 else _edge_str(p_u25, 1.90)
            edge_ou = raw.replace("@1.90", "@~1.90").replace("pts ", "pts U2.5 ") if raw != "—" else "—"

        def _conf_bar(v: float) -> str:
            if v >= 0.65: return f"🟢 {v:.0%}"
            if v >= 0.35: return f"🟡 {v:.0%}"
            return f"⚪ {v:.0%}"

        wc_rows.append({
            "📅": m["date"][:10],
            "🕐": heure,
            "Match": f"{m['home_short']} vs {m['away_short']}",
            "Score réel": f"{m['home_score']}-{m['away_score']}" if is_done else "—",
            "Score prédit": display_score,
            "P(1/X/2)": f"{ph:.0%}/{px:.0%}/{pa:.0%}",
            "Conf 1X2": _conf_bar(pred.get("conf_1x2", 0)),
            "λ": f"{pred.get('lambda_home',0):.2f}–{pred.get('lambda_away',0):.2f}",
            "O2.5": f"{pred.get('p_over_25',0):.0%}",
            "Conf O/U": _conf_bar(pred.get("conf_ou25", 0)),
            "BTTS": f"{pred.get('p_btts',0):.0%}",
            "Conf BTTS": _conf_bar(pred.get("conf_btts", 0)),
            "Conf Score": _conf_bar(pred.get("conf_score", 0)),
            "Edge 1X2": edge_1x2,
            "Edge O/U": edge_ou,
            "Bkm": bkm[:8] if bkm else "—",
            "1X2 ✓": perf,
            "O/U ✓": ou_real,
            "Src": pred.get("source", "?"),
        })

    if wc_rows:
        wc_df = pd.DataFrame(wc_rows)
        display_cols = ["📅", "🕐", "Match", "Score réel", "Score prédit", "Conf Score",
                        "P(1/X/2)", "Conf 1X2", "λ",
                        "O2.5", "Conf O/U", "BTTS", "Conf BTTS",
                        "Edge 1X2", "Edge O/U", "Bkm", "1X2 ✓", "O/U ✓", "Src"]
        st.dataframe(
            wc_df[display_cols],
            use_container_width=True,
            height=min(len(wc_df) * 42 + 60, 900),
            hide_index=True,
            column_config={
                "Match": st.column_config.TextColumn(width="medium"),
                "Score prédit": st.column_config.TextColumn(width="small"),
                "P(1/X/2)": st.column_config.TextColumn(width="medium"),
                "O2.5": st.column_config.TextColumn(width="small"),
                "BTTS": st.column_config.TextColumn(width="small"),
            },
        )
        done_rows = [r for r in wc_rows if r["Score réel"] != "—"]
        if done_rows:
            n_done = len(done_rows)
            n_exact = sum(1 for r in done_rows if "🎯" in r["1X2 ✓"])
            n_bon_sens = sum(1 for r in done_rows if "✅" in r["1X2 ✓"])
            n_ou_ok = sum(1 for r in done_rows if "✅" in r["O/U ✓"])
            st.caption(
                f"Matchs joués : {n_done} │ "
                f"Score exact : {n_exact}/{n_done} ({n_exact/n_done:.0%}) │ "
                f"Bon sens 1X2 : {n_bon_sens}/{n_done} ({n_bon_sens/n_done:.0%}) │ "
                f"O/U correct : {n_ou_ok}/{n_done} ({n_ou_ok/n_done:.0%})"
            )
    else:
        st.info("Aucun match pour cette journée.")

    # ── Bloc B : Saisie manuelle des cotes ───────────────────────────────
    st.divider()
    with st.expander("✏️ Saisir des cotes manuellement (Betclic / autre)", expanded=False):
        st.caption("Pour corriger ou enrichir les cotes d'un match spécifique.")
        matches_sans_cotes = [
            m for m in (wc_data.get("matches", []) if wc_data else [])
            if not m.get("odds_home") and m.get("status") not in ("STATUS_FINAL", "STATUS_FULL_TIME")
        ]
        match_labels = [f"{m['home_short']} vs {m['away_short']} ({m['date'][:10]})" for m in matches_sans_cotes[:20]]
        if match_labels:
            sel_match = st.selectbox("Match", match_labels, key="manual_match")
            idx = match_labels.index(sel_match)
            m_sel = matches_sans_cotes[idx]
            mc1, mc2, mc3 = st.columns(3)
            with mc1:
                o_home = st.number_input(f"Cote {m_sel['home_short']} (1)", min_value=1.0, value=2.0, step=0.01, key="oh")
            with mc2:
                o_draw = st.number_input("Cote Nul (X)", min_value=1.0, value=3.0, step=0.01, key="ox")
            with mc3:
                o_away = st.number_input(f"Cote {m_sel['away_short']} (2)", min_value=1.0, value=3.5, step=0.01, key="oa")
            oc1, oc2 = st.columns(2)
            with oc1:
                o_over = st.number_input("Cote Over 2.5", min_value=1.0, value=1.90, step=0.01, key="oo")
            with oc2:
                o_under = st.number_input("Cote Under 2.5", min_value=1.0, value=1.90, step=0.01, key="ou")
            bookmaker_name = st.text_input("Bookmaker", value="Betclic", key="bkm")

            if st.button("💾 Appliquer ces cotes", type="primary"):
                # Mettre à jour wc_predictions.json
                if wc_data:
                    for m in wc_data["matches"]:
                        if m["home"] == m_sel["home"] and m["away"] == m_sel["away"]:
                            m["odds_home"] = o_home
                            m["odds_draw"] = o_draw
                            m["odds_away"] = o_away
                            m["odds_over_25"] = o_over
                            m["odds_under_25"] = o_under
                            m["odds_bookmaker"] = bookmaker_name
                    WC_FILE.write_text(json.dumps(wc_data, ensure_ascii=False, indent=2))
                    st.success(f"✅ Cotes enregistrées pour {m_sel['home_short']} vs {m_sel['away_short']}")
                    st.rerun()
        else:
            st.success("✅ Tous les matchs à venir ont des cotes disponibles.")

    # ── Bloc C : Graphe ROI ───────────────────────────────────────────────
    if resolved:
        st.divider()
        st.subheader("📈 Évolution du ROI dans le temps")

        # Trier par date et calculer le ROI cumulé
        sorted_resolved = sorted(resolved, key=lambda x: x.get("match_date", ""))
        roi_data = {"date": [], "ROI cumulé (%)": [], "Marché": []}
        cumul_by_mkt: dict[str, dict] = {"Tous": {"staked": 0, "returned": 0}}
        for mkt in ("O/U", "BTTS", "1X2"):
            cumul_by_mkt[mkt] = {"staked": 0, "returned": 0}

        for r in sorted_resolved:
            mkt = r.get("market", "?")
            odds = r.get("market_odds", 0)
            won = r["result"] == "win"
            ret = odds if won else 0

            for label in ("Tous", mkt):
                if label not in cumul_by_mkt:
                    cumul_by_mkt[label] = {"staked": 0, "returned": 0}
                cumul_by_mkt[label]["staked"] += 1
                cumul_by_mkt[label]["returned"] += ret

            roi_pct = (cumul_by_mkt["Tous"]["returned"] - cumul_by_mkt["Tous"]["staked"]) / cumul_by_mkt["Tous"]["staked"] * 100
            roi_data["date"].append(r.get("match_date", "?"))
            roi_data["ROI cumulé (%)"].append(round(roi_pct, 1))
            roi_data["Marché"].append(mkt)

        roi_df = pd.DataFrame(roi_data)
        if len(roi_df) >= 2:
            import numpy as np
            # Ligne zéro de référence
            chart_df = roi_df[["date", "ROI cumulé (%)"]].copy()
            chart_df = chart_df.drop_duplicates(subset="date", keep="last")
            st.line_chart(chart_df.set_index("date"), color="#00cc44")
            st.caption("ROI cumulé simulé (mise 1€ par pari, cotes bookmaker réelles)")

        # Stats détaillées par marché
        st.subheader("📊 ROI par marché")
        roi_mkt_rows = []
        for mkt_label, d in cumul_by_mkt.items():
            if d["staked"] == 0:
                continue
            roi_v = (d["returned"] - d["staked"]) / d["staked"] * 100
            roi_mkt_rows.append({
                "Marché": mkt_label,
                "Paris": d["staked"],
                "Retour total": f"{d['returned']:.2f}€",
                "ROI": f"{'🟢' if roi_v > 0 else '🔴'} {roi_v:+.1f}%",
            })
        st.dataframe(pd.DataFrame(roi_mkt_rows), use_container_width=True, hide_index=True)

st.divider()
st.caption("betX © 2026 – Données : ESPN + API-Football + FIFA Ranking")

# ── Bloc MPP : Mon Petit Prono ────────────────────────────────────────────────
with tab_wc:
    st.divider()
    st.subheader("🏆 Mon Petit Prono — Scores à jouer")
    st.caption("Score le plus probable selon le modèle Poisson+ELO. 🔥🔥🔥 = >18% | 🔥🔥 = >14% | 🔥 = >11%")

    if wc_data:
        mpp_rows = []
        for m in sorted(wc_data.get("matches", []), key=lambda x: x["date"]):
            if m.get("status") == "STATUS_FULL_TIME":
                continue
            p = m.get("prediction", {})
            top = p.get("top_scores", [])
            if not top:
                continue
            best_score = top[0]["score"]
            best_prob = top[0]["prob"]
            s2 = top[1]["score"] if len(top) > 1 else "—"
            s2p = top[1]["prob"] if len(top) > 1 else 0
            s3 = top[2]["score"] if len(top) > 2 else "—"
            s3p = top[2]["prob"] if len(top) > 2 else 0

            if best_prob >= 0.18: conf = "🔥🔥🔥"
            elif best_prob >= 0.14: conf = "🔥🔥"
            elif best_prob >= 0.11: conf = "🔥"
            else: conf = "·"

            h_s = m["date"][11:13]
            h_fr = (int(h_s) + 2) % 24 if h_s.isdigit() else 0
            heure = f"{h_fr:02d}h"

            mpp_rows.append({
                "📅": m["date"][:10],
                "🕐": heure,
                "Domicile": m["home_short"],
                "Extérieur": m["away_short"],
                "✍️ Prono": best_score,
                "Conf": conf,
                "P%": f"{best_prob:.0%}",
                "2e choix": f"{s2} ({s2p:.0%})" if s2 != "—" else "—",
                "3e choix": f"{s3} ({s3p:.0%})" if s3 != "—" else "—",
            })

        if mpp_rows:
            mpp_df = pd.DataFrame(mpp_rows)
            st.dataframe(
                mpp_df,
                use_container_width=True,
                height=min(len(mpp_df) * 42 + 60, 900),
                hide_index=True,
                column_config={
                    "✍️ Prono": st.column_config.TextColumn("✍️ Prono", width="small"),
                    "Conf": st.column_config.TextColumn("Conf", width="small"),
                    "P%": st.column_config.TextColumn("P%", width="small"),
                    "Domicile": st.column_config.TextColumn(width="medium"),
                    "Extérieur": st.column_config.TextColumn(width="medium"),
                },
            )
            st.caption(f"{len(mpp_rows)} matchs restants · Copie le score colonne '✍️ Prono' dans Mon Petit Prono")

    # ── Bloc Analyse Compos ───────────────────────────────────────────────
    st.divider()
    st.subheader("🎮 Analyse des compositions — Simulation positionnelle")
    st.caption("Sélectionne un match, récupère les compos ESPN ou saisis-les manuellement, puis simule le match ligne par ligne (GK / DEF / MID / ATT).")

    # ── Helper : dessin du terrain ────────────────────────────────────────
    def draw_pitch(home_players, away_players, home_name, away_name):
        """Terrain football avec les deux équipes positionnées par ligne."""
        import matplotlib.patches as mpatches
        W, H = 68.0, 105.0
        fig, ax = plt.subplots(figsize=(9, 13))
        ax.set_facecolor("#2e7d32")
        fig.patch.set_facecolor("#121212")
        ax.set_xlim(-3, W + 3)
        ax.set_ylim(-6, H + 6)
        ax.axis("off")

        lw, wc = 1.5, "white"
        # Contour
        ax.add_patch(mpatches.Rectangle((0, 0), W, H, fill=False, edgecolor=wc, linewidth=lw))
        # Ligne médiane
        ax.plot([0, W], [H/2, H/2], color=wc, linewidth=lw)
        # Cercle central
        ax.add_patch(mpatches.Circle((W/2, H/2), 9.15, fill=False, edgecolor=wc, linewidth=lw))
        ax.add_patch(mpatches.Circle((W/2, H/2), 0.5, fill=True, facecolor=wc))
        # Surfaces de réparation
        for yb, yt in [(0, 16.5), (H-16.5, H)]:
            ax.add_patch(mpatches.Rectangle(((W-40.32)/2, yb), 40.32, 16.5, fill=False, edgecolor=wc, linewidth=lw))
        for yb, yt in [(0, 5.5), (H-5.5, H)]:
            ax.add_patch(mpatches.Rectangle(((W-18.32)/2, yb), 18.32, 5.5, fill=False, edgecolor=wc, linewidth=lw))
        # Points de penalty
        ax.add_patch(mpatches.Circle((W/2, 11), 0.5, fill=True, facecolor=wc))
        ax.add_patch(mpatches.Circle((W/2, H-11), 0.5, fill=True, facecolor=wc))
        # Buts
        gx = (W - 7.32) / 2
        for gy in [(-2, 0), (H, H+2)]:
            ax.add_patch(mpatches.Rectangle((gx, gy[0]), 7.32, 2, fill=True, facecolor="#555", edgecolor=wc, linewidth=lw))
        # Bandes vertes alternées
        band_h = H / 10
        for i in range(10):
            if i % 2 == 0:
                ax.add_patch(mpatches.Rectangle((0, i*band_h), W, band_h,
                             fill=True, facecolor="#256029", alpha=0.3, zorder=0))

        _POS_LINE = {
            "GK": "GK",
            "CB": "DEF", "LB": "DEF", "RB": "DEF", "LWB": "DEF", "RWB": "DEF", "DF": "DEF",
            "CDM": "MID", "DM": "MID", "CM": "MID", "CAM": "MID",
            "LM": "MID", "RM": "MID", "AM": "MID", "MF": "MID",
            "ST": "ATT", "CF": "ATT", "SS": "ATT",
            "LW": "ATT", "RW": "ATT", "LF": "ATT", "RF": "ATT",
        }

        def _group(players):
            g = {"GK": [], "DEF": [], "MID": [], "ATT": []}
            for p in players:
                pos = p.get("position", "MID").upper()
                line = _POS_LINE.get(pos, "MID")
                g[line].append(p)
            return g

        def _place(players, y_map, color, shadow_color):
            groups = _group(players)
            for line, y in y_map.items():
                grp = groups.get(line, [])
                n = len(grp)
                if n == 0:
                    continue
                xs = [W * (i+1)/(n+1) for i in range(n)]
                for p, x in zip(grp, xs):
                    # Ombre
                    ax.add_patch(mpatches.Circle((x+0.4, y-0.4), 3.4, color=shadow_color, zorder=4, alpha=0.5))
                    # Cercle joueur
                    ax.add_patch(mpatches.Circle((x, y), 3.4, color=color, zorder=5,
                                                 ec="white", linewidth=1.2))
                    name = p["name"].split()[-1][:10]
                    rating = p.get("rating", 60)
                    ax.text(x, y+0.5, name, ha="center", va="center",
                            fontsize=5.5, color="white", fontweight="bold", zorder=6)
                    ax.text(x, y-1.8, str(rating), ha="center", va="center",
                            fontsize=5, color="#FFD700", zorder=6, fontweight="bold")

        # Home (bleu) joue en bas, Away (rouge) joue en haut
        _place(home_players,
               {"GK": 6, "DEF": 22, "MID": 39, "ATT": 54},
               "#1565c0", "#0a2a5e")
        _place(away_players,
               {"GK": H-6, "DEF": H-22, "MID": H-39, "ATT": H-54},
               "#b71c1c", "#4a0000")

        # Noms des équipes
        ax.text(W/2, -4.5, f"🏠 {home_name}", ha="center", va="center",
                fontsize=11, color="#90caf9", fontweight="bold")
        ax.text(W/2, H+4.5, f"✈️ {away_name}", ha="center", va="center",
                fontsize=11, color="#ef9a9a", fontweight="bold")

        # Légende ligne médiane
        ax.text(-2.5, H/2, "⬅ AWAY", ha="center", va="center",
                fontsize=7, color="#ef9a9a", rotation=90)
        ax.text(W+2.5, H/2, "HOME ➡", ha="center", va="center",
                fontsize=7, color="#90caf9", rotation=90)

        plt.tight_layout(pad=0.5)
        return fig

    if wc_data:
        upcoming = [m for m in wc_data.get("matches", []) if m.get("status") not in ("STATUS_FULL_TIME", "STATUS_FINAL")]
        if upcoming:
            match_labels_compo = [
                f"{m['home_short']} vs {m['away_short']} — {m['date'][:10]} {(int(m['date'][11:13])+2)%24:02d}h"
                for m in upcoming[:20]
            ]
            sel_compo = st.selectbox("🏟️ Match à analyser", match_labels_compo, key="compo_match")
            idx_compo = match_labels_compo.index(sel_compo)
            m_compo = upcoming[idx_compo]
            home_c, away_c = m_compo["home"], m_compo["away"]

            from lineup_notifier import load_ratings, calc_lineup_impact, calc_positional_lambda, get_today_matches, fetch_lineup

            ratings_cache = load_ratings()

            def get_team_players(team_name: str, max_players: int = 30):
                return sorted(
                    [(n, i["rating"], i.get("position", "?"))
                     for n, i in ratings_cache.items() if i.get("team") == team_name],
                    key=lambda x: -x[1]
                )[:max_players]

            home_pool = get_team_players(home_c)
            away_pool = get_team_players(away_c)

            # ── Auto-fetch ESPN ───────────────────────────────────────────
            espn_status = ""
            espn_home_default = None
            espn_away_default = None

            btn_col, status_col = st.columns([1, 4])
            with btn_col:
                fetch_clicked = st.button("📡 Compos ESPN", key="fetch_espn",
                                          help="Récupère la vraie compo depuis ESPN (~1h avant le match)")
            with status_col:
                espn_placeholder = st.empty()

            if fetch_clicked:
                with st.spinner("Récupération ESPN..."):
                    try:
                        today_ms = get_today_matches()
                        eid = None
                        for tm in today_ms:
                            if any(t in tm.get("home", "") for t in [home_c, home_c[:5]]) or \
                               any(t in tm.get("away", "") for t in [away_c, away_c[:5]]):
                                eid = tm["id"]
                                break
                        if eid:
                            lineup_espn = fetch_lineup(eid)
                            if lineup_espn:
                                # Mapper les noms ESPN → noms DB
                                home_names_set = {p[0].lower() for p in home_pool}
                                away_names_set = {p[0].lower() for p in away_pool}
                                espn_home_default = []
                                espn_away_default = []
                                home_names_fmt = [f"{p[0]} ({p[1]} – {p[2]})" for p in home_pool]
                                away_names_fmt = [f"{p[0]} ({p[1]} – {p[2]})" for p in away_pool]
                                for starters_list, fmt_list, result_list in [
                                    (lineup_espn.get(list(lineup_espn.keys())[0], {}).get("starters", []),
                                     home_names_fmt, espn_home_default),
                                    (lineup_espn.get(list(lineup_espn.keys())[-1], {}).get("starters", []),
                                     away_names_fmt, espn_away_default),
                                ]:
                                    for s in starters_list:
                                        sn = s["name"].lower()
                                        for fmt in fmt_list:
                                            db_name = fmt.split(" (")[0].lower()
                                            if db_name in sn or sn in db_name or \
                                               db_name.split()[-1] in sn:
                                                result_list.append(fmt)
                                                break
                                st.session_state["espn_home"] = espn_home_default or home_names_fmt[:11]
                                st.session_state["espn_away"] = espn_away_default or away_names_fmt[:11]
                                espn_placeholder.success(f"✅ Compos ESPN récupérées pour event {eid} ! ({len(espn_home_default)} / {len(espn_away_default)} joueurs matchés)")
                            else:
                                espn_placeholder.warning("⏳ Compos pas encore disponibles sur ESPN (publiées ~1h avant le coup d'envoi).")
                        else:
                            espn_placeholder.info("ℹ️ Match introuvable parmi les matchs ESPN du jour. Vérifie la date.")
                    except Exception as e:
                        espn_placeholder.error(f"Erreur ESPN : {e}")

            # Defaults : ESPN si dispo, sinon top-11 DB
            home_names_fmt = [f"{p[0]} ({p[1]} – {p[2]})" for p in home_pool]
            away_names_fmt = [f"{p[0]} ({p[1]} – {p[2]})" for p in away_pool]
            default_h = st.session_state.get("espn_home", home_names_fmt[:11])
            default_a = st.session_state.get("espn_away", away_names_fmt[:11])

            col_h, col_a = st.columns(2)
            with col_h:
                st.markdown(f"**🏠 {home_c}**")
                home_sel = st.multiselect("Titulaires", home_names_fmt,
                                          default=[x for x in default_h if x in home_names_fmt][:11],
                                          max_selections=11, key="home_sel")
            with col_a:
                st.markdown(f"**✈️ {away_c}**")
                away_sel = st.multiselect("Titulaires", away_names_fmt,
                                          default=[x for x in default_a if x in away_names_fmt][:11],
                                          max_selections=11, key="away_sel")

            if st.button("⚡ Simuler le match", type="primary", key="sim_btn"):

                def parse_sel(sel_list):
                    return [{"name": s.split(" (")[0],
                             "position": s.split("– ")[-1].rstrip(")")} for s in sel_list]

                h_starters = parse_sel(home_sel)
                a_starters = parse_sel(away_sel)

                if len(h_starters) < 11 or len(a_starters) < 11:
                    st.warning("⚠️ Sélectionne exactement 11 joueurs par équipe.")
                else:
                    h_impact = calc_lineup_impact(home_c, h_starters, ratings_cache)
                    a_impact = calc_lineup_impact(away_c, a_starters, ratings_cache)
                    lh_mult, la_mult = calc_positional_lambda(h_impact, a_impact)

                    from lineup_notifier import recalculate_with_lineup
                    recalc = recalculate_with_lineup(home_c, away_c, h_impact, a_impact)

                    st.markdown("---")

                    # ── Terrain ──────────────────────────────────────────
                    h_players_for_pitch = [{"name": p["name"], "position": p["position"],
                                            "rating": next((x[1] for x in home_pool if x[0] == p["name"]), 60)}
                                           for p in h_starters]
                    a_players_for_pitch = [{"name": p["name"], "position": p["position"],
                                            "rating": next((x[1] for x in away_pool if x[0] == p["name"]), 60)}
                                           for p in a_starters]

                    fig = draw_pitch(h_players_for_pitch, a_players_for_pitch, home_c, away_c)
                    col_pitch, col_stats = st.columns([1, 1])
                    with col_pitch:
                        st.pyplot(fig, use_container_width=True)
                        plt.close(fig)

                    with col_stats:
                        st.markdown("### 📊 Simulation")

                        # Tableau lignes
                        cmp_rows = []
                        for line in ["GK", "DEF", "MID", "ATT"]:
                            hv = h_impact["avg_line"].get(line, 60)
                            av = a_impact["avg_line"].get(line, 60)
                            adv = "🏠" if hv > av + 1 else ("✈️" if av > hv + 1 else "＝")
                            cmp_rows.append({"Ligne": line, home_c: f"{hv:.0f}", "": adv, away_c: f"{av:.0f}"})
                        st.dataframe(pd.DataFrame(cmp_rows), hide_index=True, use_container_width=True)

                        # Multiplicateurs λ
                        mc1, mc2 = st.columns(2)
                        with mc1:
                            st.metric(f"λ {home_c}", f"×{lh_mult:.3f}",
                                      delta=f"{(lh_mult-1)*100:+.1f}%",
                                      delta_color="normal" if lh_mult >= 1 else "inverse")
                        with mc2:
                            st.metric(f"λ {away_c}", f"×{la_mult:.3f}",
                                      delta=f"{(la_mult-1)*100:+.1f}%",
                                      delta_color="normal" if la_mult >= 1 else "inverse")

                        if recalc:
                            ph, px, pa = recalc["p_home"], recalc["p_draw"], recalc["p_away"]
                            st.markdown(f"**Résultat :** {home_c} **{ph:.0%}** | Nul **{px:.0%}** | {away_c} **{pa:.0%}**")
                            medals = ["🥇", "🥈", "🥉", "4️⃣"]
                            for i, (sc, pr) in enumerate(recalc.get("top_scores", [])[:4]):
                                st.markdown(f"{medals[i]} **{sc}** — {pr:.0%}")

                        # Absents notables
                        for team, impact in [(home_c, h_impact), (away_c, a_impact)]:
                            if impact.get("absent_notable"):
                                absents = [f"{a['name'].split()[-1]} ({a['rating']})"
                                           for a in impact["absent_notable"][:4]]
                                st.warning(f"⚠️ **{team}** absents : {', '.join(absents)}")
        else:
            st.info("Aucun match à venir.")


