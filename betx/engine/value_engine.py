"""
betX – Moteur de détection de Value Bets.

Identifie les paris à espérance positive en comparant
la probabilité estimée par le modèle à la cote du bookmaker.

Formules :
  q = 1 / odds         (probabilité implicite)
  edge = p - q          (avantage du modèle)
  EV = p*(odds-1) - (1-p)  (espérance de gain par unité misée)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from betx.config import settings
from betx.logger import get_logger

log = get_logger("engine.value")


@dataclass
class ValueBet:
    """Un pari à valeur positive identifié."""
    match_id: int
    sport: str
    home_team: str
    away_team: str
    market: str  # 1x2, over_under, btts, moneyline, handicap
    selection: str  # home, draw, away, over_2.5, etc.
    model_probability: float  # p
    bookmaker_odds: float  # O
    bookmaker: str
    # Calculés
    implied_probability: float  # q = 1/O
    edge: float  # p - q
    ev: float  # p*(O-1) - (1-p)
    # Optionnel
    confidence: str = ""  # "high", "medium", "low"
    model_name: str = ""

    def __post_init__(self):
        self.confidence = self._classify_confidence()

    def _classify_confidence(self) -> str:
        if self.edge >= 0.08:
            return "high"
        elif self.edge >= 0.05:
            return "medium"
        return "low"

    @property
    def display_line(self) -> str:
        return (
            f"{'⚡' if self.confidence == 'high' else '✅' if self.confidence == 'medium' else '📊'} "
            f"{self.sport.upper()} │ {self.home_team} vs {self.away_team} │ "
            f"{self.market} → {self.selection} │ "
            f"P={self.model_probability:.1%} vs Cote={self.bookmaker_odds:.2f} │ "
            f"Edge={self.edge:.1%} EV={self.ev:.1%} │ {self.bookmaker}"
        )


class ValueEngine:
    """
    Moteur de détection de value bets.

    Compare les probabilités modèle aux cotes bookmakers
    et filtre selon les seuils configurés.
    """

    def __init__(self) -> None:
        self.cfg = settings.value
        self.min_edge = self.cfg.min_edge
        self.min_ev = self.cfg.min_ev
        self.min_odds = self.cfg.min_odds
        self.max_odds = self.cfg.max_odds

    def evaluate(
        self,
        match_id: int,
        sport: str,
        home_team: str,
        away_team: str,
        market: str,
        selection: str,
        model_probability: float,
        bookmaker_odds: float,
        bookmaker: str = "unknown",
        model_name: str = "",
    ) -> Optional[ValueBet]:
        """
        Évalue un pari potentiel.

        Returns:
            ValueBet si les critères sont remplis, None sinon.
        """
        # Validation des inputs
        if model_probability <= 0 or model_probability >= 1:
            return None
        if bookmaker_odds <= 1.0:
            return None
        if bookmaker_odds < self.min_odds or bookmaker_odds > self.max_odds:
            return None

        # Calculs
        implied_prob = 1.0 / bookmaker_odds
        edge = model_probability - implied_prob
        ev = model_probability * (bookmaker_odds - 1) - (1 - model_probability)

        # Filtres
        if edge < self.min_edge:
            return None
        if ev < self.min_ev:
            return None

        value_bet = ValueBet(
            match_id=match_id,
            sport=sport,
            home_team=home_team,
            away_team=away_team,
            market=market,
            selection=selection,
            model_probability=model_probability,
            bookmaker_odds=bookmaker_odds,
            bookmaker=bookmaker,
            implied_probability=implied_prob,
            edge=edge,
            ev=ev,
            model_name=model_name,
        )

        log.info(f"💰 VALUE BET: {value_bet.display_line}")
        return value_bet

    def scan_match(
        self,
        match_id: int,
        sport: str,
        home_team: str,
        away_team: str,
        predictions: dict[str, float],
        odds_by_market: dict[str, dict[str, list[tuple[float, str]]]],
        model_name: str = "",
    ) -> list[ValueBet]:
        """
        Scanne tous les marchés d'un match pour trouver des value bets.

        Args:
            match_id: ID du match
            sport: Sport
            home_team, away_team: Noms des équipes
            predictions: Dict {selection: probability}
                Ex: {"home": 0.55, "draw": 0.25, "away": 0.20, "over_2.5": 0.60}
            odds_by_market: Dict {market: {selection: [(odds, bookmaker), ...]}}
                Ex: {"1x2": {"home": [(1.85, "Bet365"), (1.90, "Pinnacle")]}}

        Returns:
            Liste de ValueBet trouvés
        """
        value_bets = []

        for market, selections in odds_by_market.items():
            for selection, odds_list in selections.items():
                # Trouver la meilleure cote
                if not odds_list:
                    continue

                prob = predictions.get(selection)
                if prob is None:
                    # Essayer des alias
                    prob = self._resolve_prediction(selection, predictions)
                if prob is None:
                    continue

                # Tester chaque bookmaker
                for odds_val, bm_name in odds_list:
                    vb = self.evaluate(
                        match_id=match_id,
                        sport=sport,
                        home_team=home_team,
                        away_team=away_team,
                        market=market,
                        selection=selection,
                        model_probability=prob,
                        bookmaker_odds=odds_val,
                        bookmaker=bm_name,
                        model_name=model_name,
                    )
                    if vb:
                        value_bets.append(vb)

        # Trier par EV décroissant
        value_bets.sort(key=lambda x: x.ev, reverse=True)

        if value_bets:
            log.info(
                f"Match {home_team} vs {away_team}: "
                f"{len(value_bets)} value bets trouvés"
            )

        return value_bets

    @staticmethod
    def _resolve_prediction(selection: str, predictions: dict[str, float]) -> Optional[float]:
        """Résout les alias de sélections."""
        aliases = {
            "1": "home", "home_win": "home",
            "X": "draw",
            "2": "away", "away_win": "away",
            "over_2.5": "p_over_25", "over 2.5": "p_over_25",
            "over_3.5": "p_over_35", "over 3.5": "p_over_35",
            "under_2.5": None,  # Calculé comme 1 - over
            "btts_yes": "p_btts", "btts": "p_btts",
        }

        mapped = aliases.get(selection)
        if mapped and mapped in predictions:
            return predictions[mapped]

        # Under = 1 - Over
        if "under" in selection:
            over_key = selection.replace("under", "over")
            over_p = predictions.get(over_key) or predictions.get(aliases.get(over_key, ""))
            if over_p is not None:
                return 1 - over_p

        # BTTS No = 1 - BTTS
        if selection in ("btts_no", "btts_non"):
            btts_p = predictions.get("p_btts") or predictions.get("btts")
            if btts_p is not None:
                return 1 - btts_p

        return None

    def best_odds_for_selection(
        self,
        odds_list: list[tuple[float, str]],
    ) -> tuple[float, str]:
        """Retourne la meilleure cote et le bookmaker associé."""
        if not odds_list:
            return 0.0, "none"
        return max(odds_list, key=lambda x: x[0])
