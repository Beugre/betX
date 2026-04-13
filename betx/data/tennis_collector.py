"""
betX – Collecteur de données Tennis.

Utilise l'API-Tennis ou une source alternative pour récupérer
matchs, résultats, et stats joueurs.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from betx.config import settings
from betx.data.base_collector import BaseCollector
from betx.database import Match, Player, get_session
from betx.database.helpers import get_or_create_player


class TennisCollector(BaseCollector):
    """Collecte matchs et stats tennis."""

    def __init__(self) -> None:
        super().__init__(
            name="tennis",
            base_url="https://v1.tennis.api-sports.io",
            api_key=settings.api.tennis_key,
        )

    def fetch_matches(self, target_date: date) -> list[dict[str, Any]]:
        """Récupère les matchs tennis du jour."""
        try:
            data = self._get("/games", params={"date": target_date.isoformat()})
            return data.get("response", [])
        except Exception:
            self.log.warning("Échec récupération matchs tennis")
            return []

    def fetch_results(self, target_date: date) -> list[dict[str, Any]]:
        """Récupère les résultats tennis."""
        try:
            data = self._get(
                "/games",
                params={"date": target_date.isoformat(), "status": "finished"},
            )
            return data.get("response", [])
        except Exception:
            self.log.warning("Échec résultats tennis")
            return []

    def fetch_player_stats(self, player_id: int, season: int) -> dict[str, Any]:
        """Récupère les stats d'un joueur pour une saison."""
        try:
            data = self._get(
                "/players",
                params={"id": player_id, "season": season},
            )
            return data.get("response", {})
        except Exception:
            return {}

    def fetch_h2h(self, player1_id: int, player2_id: int) -> list[dict]:
        """Historique confrontations directes."""
        try:
            data = self._get(
                "/games/h2h",
                params={"h2h": f"{player1_id}-{player2_id}"},
            )
            return data.get("response", [])
        except Exception:
            return []

    def save_matches_to_db(self, matches_data: list[dict]) -> list[Match]:
        """Parse et sauvegarde les matchs tennis en DB."""
        session = get_session()
        saved = []
        try:
            for m in matches_data:
                game = m if "id" in m else m.get("game", m)
                ext_id = str(game.get("id", ""))

                existing = session.query(Match).filter_by(external_id=ext_id).first()
                if existing:
                    saved.append(existing)
                    continue

                players = game.get("players", {})
                p_home_data = players.get("home", {})
                p_away_data = players.get("away", {})

                p_home = get_or_create_player(
                    session,
                    name=p_home_data.get("name", "Unknown"),
                    sport="tennis",
                    external_id=str(p_home_data.get("id", "")),
                )
                p_away = get_or_create_player(
                    session,
                    name=p_away_data.get("name", "Unknown"),
                    sport="tennis",
                    external_id=str(p_away_data.get("id", "")),
                )

                scores = game.get("scores", {})
                home_sets = scores.get("home", 0)
                away_sets = scores.get("away", 0)

                status_raw = game.get("status", {}).get("short", "NS")
                status = "finished" if status_raw == "FT" else "scheduled"

                match = Match(
                    external_id=ext_id,
                    sport="tennis",
                    league=game.get("league", {}).get("name"),
                    match_date=date.fromisoformat(game.get("date", str(date.today()))[:10]),
                    home_player_id=p_home.id,
                    away_player_id=p_away.id,
                    home_name=p_home.name,
                    away_name=p_away.name,
                    status=status,
                    home_score=home_sets if isinstance(home_sets, int) else None,
                    away_score=away_sets if isinstance(away_sets, int) else None,
                    surface=game.get("surface"),
                    total_games=game.get("total_games"),
                )
                session.add(match)
                saved.append(match)

            session.commit()
            self.log.info(f"Sauvé {len(saved)} matchs tennis")
        except Exception as e:
            session.rollback()
            self.log.error(f"Erreur sauvegarde tennis: {e}")
            raise
        finally:
            session.close()

        return saved
