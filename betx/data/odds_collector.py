"""
betX – Collecteur de cotes (The Odds API).

Source : https://the-odds-api.com/
Récupère les cotes de multiples bookmakers pour football, tennis, basket.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from betx.config import settings
from betx.data.base_collector import BaseCollector
from betx.database import Match, Odds, get_session


# Mapping sport → clé API
SPORT_KEYS = {
    "football": [
        "soccer_epl",
        "soccer_spain_la_liga",
        "soccer_italy_serie_a",
        "soccer_germany_bundesliga",
        "soccer_france_ligue_one",
        "soccer_uefa_champs_league",
    ],
    "tennis": ["tennis_atp_french_open", "tennis_atp_wimbledon", "tennis_atp_us_open"],
    "basketball": ["basketball_nba", "basketball_euroleague"],
}

# Mapping marchés
MARKET_KEYS = {
    "1x2": "h2h",
    "over_under": "totals",
    "handicap": "spreads",
}


class OddsCollector(BaseCollector):
    """Collecte les cotes multi-bookmakers via The Odds API."""

    def __init__(self) -> None:
        super().__init__(
            name="odds",
            base_url=settings.api.odds_api_base_url,
            api_key=settings.api.odds_api_key,
        )

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        """Override pour utiliser apiKey comme query param."""
        params = params or {}
        params["apiKey"] = self.api_key
        self.log.info(f"GET {endpoint}")
        resp = self.client.get(endpoint, params=params)
        resp.raise_for_status()
        return resp.json()

    def fetch_matches(self, target_date: date) -> list[dict[str, Any]]:
        """Non utilisé directement pour les cotes."""
        return []

    def fetch_results(self, target_date: date) -> list[dict[str, Any]]:
        """Non utilisé."""
        return []

    def fetch_odds(
        self,
        sport: str = "football",
        markets: list[str] | None = None,
        regions: str = "eu",
    ) -> list[dict[str, Any]]:
        """
        Récupère les cotes pour un sport donné.

        Args:
            sport: football, tennis, basketball
            markets: Liste de marchés (h2h, totals, spreads)
            regions: Région des bookmakers (eu, uk, us)

        Returns:
            Liste de matchs avec cotes
        """
        if markets is None:
            markets = ["h2h", "totals"]

        sport_keys = SPORT_KEYS.get(sport, [])
        all_odds = []

        for sport_key in sport_keys:
            try:
                data = self._get(
                    f"/sports/{sport_key}/odds",
                    params={
                        "regions": regions,
                        "markets": ",".join(markets),
                        "oddsFormat": "decimal",
                    },
                )
                if isinstance(data, list):
                    all_odds.extend(data)
                self.log.info(f"{sport_key}: {len(data) if isinstance(data, list) else 0} events")
            except Exception as e:
                self.log.warning(f"Échec cotes {sport_key}: {e}")

        return all_odds

    def save_odds_to_db(
        self,
        odds_data: list[dict],
        sport: str = "football",
        is_closing: bool = False,
    ) -> int:
        """Parse et sauvegarde les cotes en DB."""
        session = get_session()
        count = 0
        try:
            for event in odds_data:
                home_team = event.get("home_team", "")
                away_team = event.get("away_team", "")

                # Trouver le match correspondant en DB
                match = (
                    session.query(Match)
                    .filter(
                        Match.sport == sport,
                        Match.home_name == home_team,
                        Match.away_name == away_team,
                        Match.status == "scheduled",
                    )
                    .first()
                )
                if not match:
                    continue

                bookmakers = event.get("bookmakers", [])
                for bm in bookmakers:
                    bm_name = bm.get("title", "unknown")
                    for market in bm.get("markets", []):
                        market_key = market.get("key", "")
                        for outcome in market.get("outcomes", []):
                            selection = self._normalize_selection(
                                outcome.get("name", ""),
                                market_key,
                                home_team,
                                away_team,
                                outcome.get("point"),
                            )
                            odds_entry = Odds(
                                match_id=match.id,
                                bookmaker=bm_name,
                                market=market_key,
                                selection=selection,
                                odds_value=float(outcome.get("price", 0)),
                                is_closing=is_closing,
                                timestamp=datetime.utcnow(),
                            )
                            session.add(odds_entry)
                            count += 1

            session.commit()
            self.log.info(f"Sauvé {count} cotes ({sport})")
        except Exception as e:
            session.rollback()
            self.log.error(f"Erreur sauvegarde cotes: {e}")
            raise
        finally:
            session.close()

        return count

    @staticmethod
    def _normalize_selection(
        name: str,
        market: str,
        home: str,
        away: str,
        point: float | None = None,
    ) -> str:
        """Normalise le nom de la sélection."""
        if market == "h2h":
            if name == home:
                return "home"
            elif name == away:
                return "away"
            elif name.lower() == "draw":
                return "draw"
            return name.lower()
        elif market == "totals":
            prefix = "over" if name.lower() == "over" else "under"
            return f"{prefix}_{point}" if point else prefix
        elif market == "spreads":
            side = "home" if name == home else "away"
            return f"{side}_{point}" if point else side
        return name.lower()
