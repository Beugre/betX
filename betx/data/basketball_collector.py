"""
betX – Collecteur de données Basketball.

Source : API-Basketball ou autre source pour NBA / Euroleague.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from betx.config import settings
from betx.data.base_collector import BaseCollector
from betx.database import Match, Team, get_session
from betx.database.helpers import get_or_create_team


class BasketballCollector(BaseCollector):
    """Collecte matchs et stats basketball."""

    def __init__(self) -> None:
        super().__init__(
            name="basketball",
            base_url="https://v1.baseball.api-sports.io",
            api_key=settings.api.basketball_key,
        )

    def fetch_matches(self, target_date: date) -> list[dict[str, Any]]:
        """Récupère les matchs basket du jour."""
        try:
            data = self._get("/games", params={"date": target_date.isoformat()})
            return data.get("response", [])
        except Exception:
            self.log.warning("Échec récupération matchs basket")
            return []

    def fetch_results(self, target_date: date) -> list[dict[str, Any]]:
        """Résultats du jour."""
        try:
            data = self._get(
                "/games",
                params={"date": target_date.isoformat(), "status": "finished"},
            )
            return data.get("response", [])
        except Exception:
            return []

    def fetch_team_stats(self, team_id: int, league_id: int, season: str) -> dict[str, Any]:
        """Stats d'une équipe pour une saison."""
        try:
            data = self._get(
                "/statistics",
                params={"team": team_id, "league": league_id, "season": season},
            )
            return data.get("response", {})
        except Exception:
            return {}

    def save_matches_to_db(self, matches_data: list[dict]) -> list[Match]:
        """Parse et sauvegarde les matchs basket."""
        session = get_session()
        saved = []
        try:
            for m in matches_data:
                ext_id = str(m.get("id", ""))
                existing = session.query(Match).filter_by(external_id=ext_id).first()
                if existing:
                    saved.append(existing)
                    continue

                teams = m.get("teams", {})
                scores = m.get("scores", {})
                league = m.get("league", {})

                home_team = get_or_create_team(
                    session,
                    name=teams.get("home", {}).get("name", "Unknown"),
                    sport="basketball",
                    external_id=str(teams.get("home", {}).get("id", "")),
                    league=league.get("name"),
                )
                away_team = get_or_create_team(
                    session,
                    name=teams.get("away", {}).get("name", "Unknown"),
                    sport="basketball",
                    external_id=str(teams.get("away", {}).get("id", "")),
                    league=league.get("name"),
                )

                home_total = scores.get("home", {}).get("total")
                away_total = scores.get("away", {}).get("total")

                status_raw = m.get("status", {}).get("short", "NS")
                status = "finished" if status_raw == "FT" else "scheduled"

                match = Match(
                    external_id=ext_id,
                    sport="basketball",
                    league=league.get("name"),
                    season=str(league.get("season", "")),
                    match_date=date.fromisoformat(m.get("date", str(date.today()))[:10]),
                    home_team_id=home_team.id,
                    away_team_id=away_team.id,
                    home_name=home_team.name,
                    away_name=away_team.name,
                    status=status,
                    home_score=home_total,
                    away_score=away_total,
                    # Quarter scores
                    home_q1=scores.get("home", {}).get("quarter_1"),
                    home_q2=scores.get("home", {}).get("quarter_2"),
                    home_q3=scores.get("home", {}).get("quarter_3"),
                    home_q4=scores.get("home", {}).get("quarter_4"),
                    away_q1=scores.get("away", {}).get("quarter_1"),
                    away_q2=scores.get("away", {}).get("quarter_2"),
                    away_q3=scores.get("away", {}).get("quarter_3"),
                    away_q4=scores.get("away", {}).get("quarter_4"),
                )
                session.add(match)
                saved.append(match)

            session.commit()
            self.log.info(f"Sauvé {len(saved)} matchs basket")
        except Exception as e:
            session.rollback()
            self.log.error(f"Erreur sauvegarde basket: {e}")
            raise
        finally:
            session.close()

        return saved
