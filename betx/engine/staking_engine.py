"""
betX – Moteur de Staking (gestion des mises).

Méthodes de staking :
1. Kelly fractionné (principal) : k = (p*O - 1) / (O - 1), stake = bankroll × fraction × k
2. Flat betting : stake = bankroll × flat_pct
3. Cap max par bet

Le Kelly fractionné (0.25) est recommandé pour limiter la variance
tout en exploitant l'edge.
"""

from __future__ import annotations

from dataclasses import dataclass

from betx.config import settings
from betx.engine.value_engine import ValueBet
from betx.logger import get_logger

log = get_logger("engine.staking")


@dataclass
class StakeSuggestion:
    """Suggestion de mise pour un value bet."""
    value_bet: ValueBet
    method: str  # "kelly", "flat"
    kelly_raw: float  # Critère de Kelly brut
    kelly_fraction: float  # Fraction utilisée
    stake_pct: float  # % de la bankroll
    stake_amount: float  # Montant en unités monétaires
    bankroll: float  # Bankroll au moment du calcul

    @property
    def display_line(self) -> str:
        return (
            f"💶 MISE: {self.value_bet.selection} @ {self.value_bet.bookmaker_odds:.2f} │ "
            f"Kelly={self.kelly_raw:.2%} │ "
            f"Stake={self.stake_pct:.2%} ({self.stake_amount:.2f}€) │ "
            f"Méthode={self.method}"
        )


class StakingEngine:
    """
    Moteur de calcul des mises optimales.

    Implémente le Kelly fractionné avec cap et floor.
    """

    def __init__(self) -> None:
        self.cfg = settings.bankroll
        self.kelly_fraction = self.cfg.kelly_fraction
        self.flat_pct = self.cfg.flat_pct
        self.max_stake_pct = self.cfg.max_stake_pct
        self.default_method = self.cfg.default_method

    def kelly_criterion(self, probability: float, odds: float) -> float:
        """
        Calcule le critère de Kelly brut.

        k = (p * O - 1) / (O - 1)

        Args:
            probability: Probabilité estimée par le modèle
            odds: Cote décimale du bookmaker

        Returns:
            Fraction de bankroll à miser (Kelly brut)
        """
        if odds <= 1.0 or probability <= 0 or probability >= 1:
            return 0.0

        k = (probability * odds - 1) / (odds - 1)
        return max(0.0, k)

    def calculate_stake(
        self,
        value_bet: ValueBet,
        bankroll: float,
        method: str | None = None,
    ) -> StakeSuggestion:
        """
        Calcule la mise optimale pour un value bet.

        Args:
            value_bet: Le pari identifié
            bankroll: Bankroll actuelle
            method: "kelly" ou "flat" (override)

        Returns:
            StakeSuggestion
        """
        method = method or self.default_method

        # Kelly brut
        kelly_raw = self.kelly_criterion(
            value_bet.model_probability,
            value_bet.bookmaker_odds,
        )

        if method == "kelly":
            # Kelly fractionné
            stake_pct = kelly_raw * self.kelly_fraction
        else:
            # Flat betting
            stake_pct = self.flat_pct

        # Cap max
        stake_pct = min(stake_pct, self.max_stake_pct)

        # Floor : pas de mise si trop faible
        if stake_pct < 0.002:  # < 0.2%
            stake_pct = 0.0

        stake_amount = bankroll * stake_pct

        suggestion = StakeSuggestion(
            value_bet=value_bet,
            method=method,
            kelly_raw=kelly_raw,
            kelly_fraction=self.kelly_fraction,
            stake_pct=stake_pct,
            stake_amount=round(stake_amount, 2),
            bankroll=bankroll,
        )

        if stake_pct > 0:
            log.info(suggestion.display_line)

        return suggestion

    def calculate_stakes_batch(
        self,
        value_bets: list[ValueBet],
        bankroll: float,
        method: str | None = None,
        max_total_exposure: float = 0.15,
        max_bets: int = 30,
    ) -> list[StakeSuggestion]:
        """
        Calcule les mises pour une liste de value bets.

        Calcule d'abord les stakes Kelly pour tous les bets (top max_bets),
        puis applique un scaling proportionnel si l'exposition totale
        dépasse le cap. Cela permet d'afficher jusqu'à max_bets paris
        au lieu de s'arrêter dès que le cap est atteint.

        Args:
            value_bets: Liste de value bets
            bankroll: Bankroll actuelle
            method: Méthode de staking
            max_total_exposure: Exposition max totale (% bankroll)
            max_bets: Nombre max de paris à retourner

        Returns:
            Liste de StakeSuggestion triée par EV décroissant
        """
        # Trier par EV décroissant et limiter à max_bets
        sorted_bets = sorted(value_bets, key=lambda x: x.ev, reverse=True)[:max_bets]

        # Phase 1 : calculer les stakes bruts pour tous les bets
        suggestions = []
        for vb in sorted_bets:
            suggestion = self.calculate_stake(vb, bankroll, method)
            if suggestion.stake_pct > 0:
                suggestions.append(suggestion)

        # Phase 2 : si l'exposition totale dépasse le cap, scaler proportionnellement
        total_exposure = sum(s.stake_pct for s in suggestions)

        if total_exposure > max_total_exposure and total_exposure > 0:
            scale_factor = max_total_exposure / total_exposure
            log.info(
                f"Scaling stakes ×{scale_factor:.2f} pour respecter le cap "
                f"({total_exposure:.1%} → {max_total_exposure:.0%})"
            )
            for s in suggestions:
                s.stake_pct = s.stake_pct * scale_factor
                s.stake_amount = round(bankroll * s.stake_pct, 2)
                # Floor : supprimer si la mise scalée est trop faible
                if s.stake_pct < 0.001:
                    s.stake_pct = 0.0
                    s.stake_amount = 0.0

            # Retirer les mises nulles après scaling
            suggestions = [s for s in suggestions if s.stake_pct > 0]
            total_exposure = sum(s.stake_pct for s in suggestions)

        log.info(
            f"Batch: {len(suggestions)} mises, "
            f"exposition totale: {total_exposure:.2%} "
            f"({total_exposure * bankroll:.2f}€)"
        )

        return suggestions

    def simulate_kelly_growth(
        self,
        probability: float,
        odds: float,
        n_bets: int = 100,
        initial_bankroll: float = 1000.0,
    ) -> dict:
        """
        Simule la croissance de la bankroll avec Kelly.
        Utile pour le backtesting.
        """
        import numpy as np

        k = self.kelly_criterion(probability, odds)
        stake_pct = k * self.kelly_fraction

        bankroll = initial_bankroll
        history = [bankroll]
        wins = 0

        rng = np.random.default_rng(42)
        for _ in range(n_bets):
            stake = bankroll * stake_pct
            if rng.random() < probability:
                bankroll += stake * (odds - 1)
                wins += 1
            else:
                bankroll -= stake
            history.append(bankroll)

        return {
            "final_bankroll": round(bankroll, 2),
            "roi": round((bankroll - initial_bankroll) / initial_bankroll * 100, 2),
            "win_rate": round(wins / n_bets * 100, 2),
            "kelly_raw": round(k, 4),
            "stake_pct": round(stake_pct, 4),
            "max_drawdown": round(
                min(
                    (history[i] - max(history[:i+1])) / max(history[:i+1]) * 100
                    for i in range(1, len(history))
                ),
                2,
            ),
            "history": history,
        }
