"""
betX – Suivi automatique des prédictions vs résultats réels.

Enregistre chaque prédiction émise (modèle + marché) et la résolution
(résultat réel) pour calculer les métriques de calibration.

Métriques calculées :
  - ROI par seuil d'edge (>5%, >10%, >15%, >20%)
  - Brier Score (calibration des probabilités)
  - Hit rate par marché (1X2, O/U, BTTS)
  - EV réalisée vs EV théorique

Stockage : data/prediction_log.json (simple, sans dépendance DB)
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Literal

LOG_FILE = Path("data/prediction_log.json")


# ─── Structures ───────────────────────────────────────────────────────────────

@dataclass
class PredictionRecord:
    """Une prédiction émise avant le match."""
    id: str                     # "{date}_{home}_{away}_{market}_{selection}"
    match_date: str             # "2026-06-11"
    home: str
    away: str
    market: Literal["1X2", "O/U", "BTTS"]
    selection: str              # "home"|"draw"|"away"|"over_25"|"under_25"|"btts_yes"|"btts_no"
    model_prob: float           # Probabilité modèle (0..1)
    market_odds: float          # Cote bookmaker
    market_implied: float       # Probabilité implicite = 1/cote
    edge: float                 # model_prob - market_implied
    ev: float                   # model_prob * (odds-1) - (1-model_prob)
    source: str                 # "API"|"FIFA"|"MIXED"
    lambda_home: float = 0.0
    lambda_away: float = 0.0
    predicted_score: str = ""   # Score le plus probable ex: "1-0"
    # Rempli après le match
    result: str | None = None   # "win"|"loss"
    actual_score: str | None = None
    actual_home_goals: int | None = None
    actual_away_goals: int | None = None
    resolved_at: str | None = None

    @property
    def is_resolved(self) -> bool:
        return self.result is not None

    @property
    def gain_loss(self) -> float | None:
        """Gain/perte pour une mise de 1€."""
        if not self.is_resolved or not self.market_odds:
            return None
        if self.result == "win":
            return round(self.market_odds - 1, 4)
        return -1.0

    def _selection_won(self, home_goals: int, away_goals: int) -> bool:
        sel = self.selection
        if sel == "home":
            return home_goals > away_goals
        if sel == "away":
            return away_goals > home_goals
        if sel == "draw":
            return home_goals == away_goals
        total = home_goals + away_goals
        if sel == "over_25":
            return total > 2
        if sel == "under_25":
            return total <= 2
        if sel == "over_15":
            return total > 1
        if sel == "under_15":
            return total <= 1
        if sel == "btts_yes":
            return home_goals > 0 and away_goals > 0
        if sel == "btts_no":
            return home_goals == 0 or away_goals == 0
        return False


# ─── Tracker ──────────────────────────────────────────────────────────────────

class PredictionTracker:
    """Gère le journal des prédictions et calcule les métriques."""

    def __init__(self, log_file: Path = LOG_FILE):
        self.log_file = log_file
        self._records: dict[str, PredictionRecord] = {}
        self._load()

    def _load(self) -> None:
        if self.log_file.exists():
            try:
                raw = json.loads(self.log_file.read_text())
                for r in raw:
                    rec = PredictionRecord(**r)
                    self._records[rec.id] = rec
            except Exception:
                pass

    def _save(self) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(r) for r in self._records.values()]
        self.log_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    # ── Enregistrement ────────────────────────────────────────────────────

    def record(self, rec: PredictionRecord) -> None:
        """Enregistre ou met à jour une prédiction."""
        self._records[rec.id] = rec
        self._save()

    def record_from_prediction(
        self,
        match_date: str,
        home: str,
        away: str,
        prediction: dict,
        match_odds: dict,
        source: str = "FIFA",
    ) -> list[PredictionRecord]:
        """
        Crée les enregistrements pour tous les marchés d'un match.

        Args:
            prediction : dict avec p_home, p_draw, p_away, p_over_25, p_btts, etc.
            match_odds : dict avec odds_home, odds_draw, odds_away
        """
        records = []
        lh = prediction.get("lambda_home", 0)
        la = prediction.get("lambda_away", 0)
        top1 = (prediction.get("top_scores") or [{}])[0]
        pred_score = top1.get("score", "") if isinstance(top1, dict) else ""

        # 1X2 (uniquement si cotes disponibles)
        for sel, prob_key, odds_key in [
            ("home",  "p_home",  "odds_home"),
            ("draw",  "p_draw",  "odds_draw"),
            ("away",  "p_away",  "odds_away"),
        ]:
            odds = match_odds.get(odds_key, 0)
            if not odds or odds <= 1.0:
                continue
            prob = prediction.get(prob_key, 0)
            impl = 1.0 / odds
            ev = prob * (odds - 1) - (1 - prob)
            rec = PredictionRecord(
                id=f"{match_date}_{home}_{away}_1X2_{sel}",
                match_date=match_date,
                home=home, away=away,
                market="1X2", selection=sel,
                model_prob=round(prob, 4),
                market_odds=round(odds, 2),
                market_implied=round(impl, 4),
                edge=round(prob - impl, 4),
                ev=round(ev, 4),
                source=source,
                lambda_home=round(lh, 3),
                lambda_away=round(la, 3),
                predicted_score=pred_score,
            )
            records.append(rec)
            self.record(rec)

        # O/U et BTTS (cote proxy 1.90 si ESPN n'expose pas ces marchés)
        STD_ODDS = 1.90
        for sel, prob_key in [
            ("over_25",  "p_over_25"),
            ("under_25", "p_under_25"),
            ("btts_yes", "p_btts"),
            ("btts_no",  "p_btts_no"),
        ]:
            prob = prediction.get(prob_key, 0)
            if prob <= 0:
                continue
            impl = 1.0 / STD_ODDS
            ev = prob * (STD_ODDS - 1) - (1 - prob)
            mkt = "O/U" if "25" in sel else "BTTS"
            rec = PredictionRecord(
                id=f"{match_date}_{home}_{away}_{mkt}_{sel}",
                match_date=match_date,
                home=home, away=away,
                market=mkt, selection=sel,  # type: ignore
                model_prob=round(prob, 4),
                market_odds=STD_ODDS,
                market_implied=round(impl, 4),
                edge=round(prob - impl, 4),
                ev=round(ev, 4),
                source=source,
                lambda_home=round(lh, 3),
                lambda_away=round(la, 3),
                predicted_score=pred_score,
            )
            records.append(rec)
            self.record(rec)

        return records

    # ── Résolution ────────────────────────────────────────────────────────

    def resolve_match(self, home: str, away: str, home_goals: int, away_goals: int) -> int:
        """
        Met à jour tous les enregistrements d'un match avec le résultat réel.
        Retourne le nombre d'enregistrements résolus.
        """
        resolved = 0
        actual_score = f"{home_goals}-{away_goals}"
        now = datetime.now().isoformat()[:16]

        for rec in self._records.values():
            if rec.home == home and rec.away == away and not rec.is_resolved:
                won = rec._selection_won(home_goals, away_goals)
                rec.result = "win" if won else "loss"
                rec.actual_score = actual_score
                rec.actual_home_goals = home_goals
                rec.actual_away_goals = away_goals
                rec.resolved_at = now
                resolved += 1

        if resolved:
            self._save()
        return resolved

    # ── Métriques ─────────────────────────────────────────────────────────

    def roi_by_edge_threshold(
        self,
        min_edge: float = 0.05,
        market: str | None = None,
    ) -> dict:
        """
        ROI pour les paris avec edge ≥ min_edge.
        Mise unitaire de 1€ sur chaque pari.
        """
        resolved = [
            r for r in self._records.values()
            if r.is_resolved and r.edge >= min_edge
            and (market is None or r.market == market)
        ]
        if not resolved:
            return {"count": 0, "roi": None, "win_rate": None, "avg_ev": None}

        total_staked = len(resolved)
        total_return = sum((r.market_odds if r.result == "win" else 0) for r in resolved)
        wins = sum(1 for r in resolved if r.result == "win")
        avg_ev = sum(r.ev for r in resolved) / len(resolved)

        return {
            "count": len(resolved),
            "wins": wins,
            "win_rate": round(wins / len(resolved), 3),
            "total_staked": total_staked,
            "total_return": round(total_return, 2),
            "roi": round((total_return - total_staked) / total_staked, 4),
            "avg_edge": round(sum(r.edge for r in resolved) / len(resolved), 4),
            "avg_ev_theoretical": round(avg_ev, 4),
        }

    def full_report(self) -> dict:
        """Rapport complet par seuil d'edge et par marché."""
        thresholds = [0.05, 0.10, 0.15, 0.20]
        markets = [None, "1X2", "O/U", "BTTS"]

        report: dict = {
            "total_predictions": len(self._records),
            "resolved": sum(1 for r in self._records.values() if r.is_resolved),
            "pending": sum(1 for r in self._records.values() if not r.is_resolved),
            "by_edge": {},
            "by_market": {},
            "brier_score": self._brier_score(),
            "score_accuracy": self._score_accuracy(),
        }

        for t in thresholds:
            key = f"edge_ge_{int(t*100)}pct"
            report["by_edge"][key] = self.roi_by_edge_threshold(t)

        for m in markets:
            label = m or "all"
            report["by_market"][label] = self.roi_by_edge_threshold(0.05, market=m)

        return report

    def _brier_score(self) -> float | None:
        """Brier Score sur toutes les prédictions résolues (plus bas = mieux, 0=parfait)."""
        resolved = [r for r in self._records.values() if r.is_resolved]
        if not resolved:
            return None
        total = sum((r.model_prob - (1 if r.result == "win" else 0)) ** 2 for r in resolved)
        return round(total / len(resolved), 4)

    def _score_accuracy(self) -> dict:
        """Précision des prédictions de score exact."""
        resolved_with_score = [
            r for r in self._records.values()
            if r.is_resolved and r.predicted_score and r.actual_score
            and r.selection == "home"  # un seul par match
        ]
        if not resolved_with_score:
            return {"exact": 0, "correct_result": 0, "total": 0}
        exact = sum(1 for r in resolved_with_score if r.predicted_score == r.actual_score)
        correct_result = sum(
            1 for r in resolved_with_score
            if r._selection_won(r.actual_home_goals or 0, r.actual_away_goals or 0)
        )
        return {
            "total": len(resolved_with_score),
            "exact": exact,
            "exact_rate": round(exact / len(resolved_with_score), 3),
            "correct_result": correct_result,
            "correct_result_rate": round(correct_result / len(resolved_with_score), 3),
        }

    def pending_matches(self) -> list[dict]:
        """Matchs avec prédictions non encore résolues (pour vérification auto)."""
        pending: dict[str, dict] = {}
        for r in self._records.values():
            if not r.is_resolved:
                key = f"{r.match_date}_{r.home}_{r.away}"
                pending[key] = {
                    "date": r.match_date,
                    "home": r.home,
                    "away": r.away,
                }
        return list(pending.values())

    def display_report(self) -> str:
        """Affichage console du rapport."""
        rep = self.full_report()
        lines = [
            "=" * 55,
            "betX – Suivi des prédictions",
            "=" * 55,
            f"Total prédictions : {rep['total_predictions']}",
            f"Résolues          : {rep['resolved']}",
            f"En attente        : {rep['pending']}",
        ]
        if rep["brier_score"] is not None:
            lines.append(f"Brier Score       : {rep['brier_score']:.4f}  (0=parfait, 0.25=aléatoire)")
        sc = rep["score_accuracy"]
        if sc["total"]:
            lines += [
                f"Score exact       : {sc['exact']}/{sc['total']} ({sc['exact_rate']:.0%})",
                f"Résultat correct  : {sc['correct_result']}/{sc['total']} ({sc['correct_result_rate']:.0%})",
            ]

        lines += ["", "ROI par seuil d'edge :"]
        for key, data in rep["by_edge"].items():
            threshold = key.split("_")[2]
            if data["count"] == 0:
                lines.append(f"  Edge ≥ {threshold}% : 0 paris")
            else:
                roi_str = f"{data['roi']*100:+.1f}%" if data["roi"] is not None else "N/A"
                lines.append(
                    f"  Edge ≥ {threshold}% : {data['count']} paris | "
                    f"Win rate {data['win_rate']:.0%} | ROI {roi_str}"
                )

        lines += ["", "ROI par marché (edge ≥ 5%) :"]
        for mkt, data in rep["by_market"].items():
            if mkt == "all":
                continue
            if data["count"] == 0:
                lines.append(f"  {mkt:6s} : 0 paris")
            else:
                roi_str = f"{data['roi']*100:+.1f}%" if data["roi"] is not None else "N/A"
                lines.append(f"  {mkt:6s} : {data['count']} paris | ROI {roi_str}")

        lines.append("=" * 55)
        return "\n".join(lines)
