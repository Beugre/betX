"""Scoring and recommendation helpers for prediction sites."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SiteScoreRow:
    site_slug: str
    site_name: str
    league: str
    predictions_count: int
    graded_count: int
    wins: int
    losses: int
    hit_rate: float
    roi_flat: float
    quality_score: float


def compute_quality_score(hit_rate: float, roi_flat: float, graded_count: int) -> float:
    """Weighted score with sample-size penalty."""
    # Hit rate is main signal, ROI is secondary, and sample-size tempers noisy winners.
    volume_penalty = min(1.0, graded_count / 100.0)
    return ((hit_rate * 100.0) * 0.7 + (roi_flat * 100.0) * 0.3) * volume_penalty


def flat_roi(wins: int, losses: int, avg_odds: float = 2.0) -> float:
    """Simple flat-stake ROI approximation if no exact odds are available."""
    total = wins + losses
    if total <= 0:
        return 0.0
    stake = float(total)
    pnl = (wins * (avg_odds - 1.0)) - losses
    return pnl / stake
