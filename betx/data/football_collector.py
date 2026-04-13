"""
betX – Collecteur de données Football (API-Football v3).

Source : https://www.api-football.com/documentation-v3
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from betx.config import settings
from betx.data.base_collector import BaseCollector
from betx.database import Match, Team, Odds, get_session
from betx.database.helpers import get_or_create_team


class FootballCollector(BaseCollector):
    """Collecte matchs, résultats et statistiques football."""

    def __init__(self) -> None:
        super().__init__(
            name="football",
            base_url=settings.api.football_base_url,
            api_key=settings.api.football_key,
        )
        self.leagues = settings.football.leagues

    # ─── Matchs du jour ─────────────────────────────────────────
    def fetch_matches(self, target_date: date) -> list[dict[str, Any]]:
        """Récupère les fixtures pour une date."""
        all_fixtures = []
        for league_id in self.leagues:
            try:
                data = self._get(
                    "/fixtures",
                    params={
                        "league": league_id,
                        "date": target_date.isoformat(),
                        "season": self._current_season(target_date),
                    },
                )
                fixtures = data.get("response", [])
                all_fixtures.extend(fixtures)
                self.log.info(f"League {league_id}: {len(fixtures)} matchs")
            except Exception:
                self.log.warning(f"Échec récupération league {league_id}")
        return all_fixtures

    def fetch_results(self, target_date: date) -> list[dict[str, Any]]:
        """Récupère les résultats (matchs terminés)."""
        all_results = []
        for league_id in self.leagues:
            try:
                data = self._get(
                    "/fixtures",
                    params={
                        "league": league_id,
                        "date": target_date.isoformat(),
                        "season": self._current_season(target_date),
                        "status": "FT",
                    },
                )
                results = data.get("response", [])
                all_results.extend(results)
            except Exception:
                self.log.warning(f"Échec résultats league {league_id}")
        return all_results

    # ─── Statistiques d'équipe ───────────────────────────────────
    def fetch_team_stats(self, team_id: int, league_id: int, season: int) -> dict[str, Any]:
        """Statistiques saisonnières d'une équipe."""
        data = self._get(
            "/teams/statistics",
            params={"team": team_id, "league": league_id, "season": season},
        )
        return data.get("response", {})

    def fetch_head_to_head(self, team1_id: int, team2_id: int, last: int = 10) -> list[dict]:
        """Historique confrontations directes."""
        data = self._get(
            "/fixtures/headtohead",
            params={"h2h": f"{team1_id}-{team2_id}", "last": last},
        )
        return data.get("response", [])

    # ─── Sauvegarde en DB ────────────────────────────────────────
    def save_matches_to_db(self, fixtures: list[dict]) -> list[Match]:
        """Parse les fixtures API et les enregistre."""
        session = get_session()
        saved = []
        try:
            for fx in fixtures:
                fixture = fx.get("fixture", {})
                teams_data = fx.get("teams", {})
                goals = fx.get("goals", {})
                league = fx.get("league", {})

                ext_id = str(fixture.get("id", ""))
                # Vérifie si déjà existant
                existing = session.query(Match).filter_by(external_id=ext_id).first()
                if existing:
                    saved.append(existing)
                    continue

                home_team = get_or_create_team(
                    session,
                    name=teams_data.get("home", {}).get("name", "Unknown"),
                    sport="football",
                    external_id=str(teams_data.get("home", {}).get("id", "")),
                    league=league.get("name"),
                    country=league.get("country"),
                )
                away_team = get_or_create_team(
                    session,
                    name=teams_data.get("away", {}).get("name", "Unknown"),
                    sport="football",
                    external_id=str(teams_data.get("away", {}).get("id", "")),
                    league=league.get("name"),
                    country=league.get("country"),
                )

                # Parse date
                dt_str = fixture.get("date", "")
                try:
                    kickoff = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                except Exception:
                    kickoff = None

                status_short = fixture.get("status", {}).get("short", "NS")
                status_map = {
                    "NS": "scheduled",
                    "1H": "live", "2H": "live", "HT": "live",
                    "FT": "finished", "AET": "finished", "PEN": "finished",
                    "PST": "postponed", "CANC": "postponed",
                }

                match = Match(
                    external_id=ext_id,
                    sport="football",
                    league=league.get("name"),
                    season=str(league.get("season", "")),
                    match_date=kickoff.date() if kickoff else date.today(),
                    kickoff_time=kickoff,
                    home_team_id=home_team.id,
                    away_team_id=away_team.id,
                    home_name=home_team.name,
                    away_name=away_team.name,
                    status=status_map.get(status_short, "scheduled"),
                    home_score=goals.get("home"),
                    away_score=goals.get("away"),
                )
                session.add(match)
                saved.append(match)

            session.commit()
            self.log.info(f"Sauvé {len(saved)} matchs football")
        except Exception as e:
            session.rollback()
            self.log.error(f"Erreur sauvegarde: {e}")
            raise
        finally:
            session.close()

        return saved

    # ─── Utilitaires ─────────────────────────────────────────────
    @staticmethod
    def _current_season(d: date) -> int:
        """Retourne la saison courante (ex: 2025 pour 2025-2026)."""
        return d.year if d.month >= 7 else d.year - 1
