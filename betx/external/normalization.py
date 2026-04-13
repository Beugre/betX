"""Utilities to normalize team names and map predictions to 1X2 outcomes."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_STOP_WORDS = {
    "fc",
    "cf",
    "ac",
    "sc",
    "afc",
    "club",
    "football",
    "sporting",
    "the",
    "de",
    "cd",
    "fk",
    "if",
    "ii",
    "u19",
    "u20",
    "u21",
}


def normalize_team_name(name: str) -> str:
    """Return a compact normalized team key for cross-site matching."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [t for t in text.split() if t and t not in _STOP_WORDS]
    return " ".join(tokens)


def parse_selection_to_1x2(raw: str) -> str | None:
    """Map raw textual prediction to home/draw/away."""
    if not raw:
        return None
    txt = raw.strip().lower()

    exact = {
        "1": "home",
        "x": "draw",
        "2": "away",
        "home": "home",
        "away": "away",
        "draw": "draw",
        "domicile": "home",
        "exterieur": "away",
        "nul": "draw",
    }
    if txt in exact:
        return exact[txt]

    if "home" in txt or "domicile" in txt or "team 1" in txt:
        return "home"
    if "away" in txt or "exterieur" in txt or "team 2" in txt:
        return "away"
    if "draw" in txt or "nul" in txt or "tie" in txt:
        return "draw"

    if re.search(r"\b1\b", txt):
        return "home"
    if re.search(r"\bx\b", txt):
        return "draw"
    if re.search(r"\b2\b", txt):
        return "away"
    return None


def score_to_1x2(home_score: int | None, away_score: int | None) -> str | None:
    if home_score is None or away_score is None:
        return None
    if home_score > away_score:
        return "home"
    if home_score < away_score:
        return "away"
    return "draw"


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_team_name(a), normalize_team_name(b)).ratio()
