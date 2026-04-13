"""Stable external source using API-Football predictions endpoint."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from betx.database import Match
from betx.external.normalization import parse_selection_to_1x2
from betx.external.scraper import ScrapedPrediction
from betx.logger import get_logger

log = get_logger("external.api_source")


class ApiFootballPredictionSource:
    """Fetches third-party predictions through API-Football instead of website scraping."""

    def __init__(self) -> None:
        self.base_url = os.getenv("API_FOOTBALL_BASE_URL", "https://v3.football.api-sports.io")
        self.api_key = os.getenv("API_FOOTBALL_KEY", "")
        leagues_env = os.getenv("API_FOOTBALL_LEAGUES", "")
        self.leagues = [int(x.strip()) for x in leagues_env.split(",") if x.strip().isdigit()]

    def fetch_from_known_matches(self, session: Session, days_back: int = 60) -> list[ScrapedPrediction]:
        if not self.api_key:
            log.warning("API_FOOTBALL_KEY missing: API-Football source skipped")
            return []

        since = date.today() - timedelta(days=max(days_back, 1))
        matches = (
            session.query(Match)
            .filter(
                Match.sport == "football",
                Match.external_id.is_not(None),
                Match.match_date >= since,
            )
            .all()
        )

        headers = {"x-apisports-key": self.api_key}
        out: list[ScrapedPrediction] = []

        with httpx.Client(base_url=self.base_url, headers=headers, timeout=20.0) as client:
            for m in matches:
                fixture_id = str(m.external_id or "").strip()
                if not fixture_id.isdigit():
                    continue
                try:
                    response = client.get("/predictions", params={"fixture": fixture_id})
                    response.raise_for_status()
                    payload = response.json().get("response", [])
                except Exception as exc:
                    log.debug(f"Prediction API failed for fixture {fixture_id}: {exc}")
                    continue

                if not payload:
                    continue

                pred_block = payload[0].get("predictions", {})
                winner = pred_block.get("winner", {}) if isinstance(pred_block, dict) else {}
                raw_pick = ""
                if winner.get("id") is not None:
                    if str(winner.get("id")) == str(m.home_team_id) or winner.get("name") == m.home_name:
                        raw_pick = "home"
                    elif str(winner.get("id")) == str(m.away_team_id) or winner.get("name") == m.away_name:
                        raw_pick = "away"
                if not raw_pick and winner.get("comment"):
                    raw_pick = str(winner.get("comment", ""))
                if not raw_pick and pred_block.get("advice"):
                    raw_pick = str(pred_block.get("advice", ""))

                selection = parse_selection_to_1x2(raw_pick)
                if not selection:
                    continue

                confidence = None
                percentages = pred_block.get("percent", {}) if isinstance(pred_block, dict) else {}
                if isinstance(percentages, dict):
                    key = {"home": "home", "draw": "draw", "away": "away"}.get(selection)
                    if key and percentages.get(key):
                        txt = str(percentages.get(key, "")).replace("%", "").strip()
                        try:
                            confidence = float(txt) / 100.0
                        except Exception:
                            confidence = None

                out.append(
                    ScrapedPrediction(
                        source_url=f"api-football://predictions/{fixture_id}",
                        home_name=m.home_name,
                        away_name=m.away_name,
                        predicted_selection=selection,
                        league=m.league,
                        kickoff_time=m.kickoff_time,
                        confidence=confidence,
                        raw_prediction=raw_pick,
                    )
                )

        return out

    def fetch_from_fixtures_window(self, days_back: int = 30, max_fixtures: int = 500) -> list[ScrapedPrediction]:
        """Backfill predictions directly from API fixtures, independent of local DB."""
        if not self.api_key:
            return []

        headers = {"x-apisports-key": self.api_key}
        out: list[ScrapedPrediction] = []
        seen: set[str] = set()
        dates = [date.today() - timedelta(days=d) for d in range(max(days_back, 1), -1, -1)]

        with httpx.Client(base_url=self.base_url, headers=headers, timeout=20.0) as client:
            fixtures_processed = 0
            for dt in dates:
                if fixtures_processed >= max_fixtures:
                    return out
                try:
                    resp = client.get("/fixtures", params={"date": dt.isoformat()})
                    resp.raise_for_status()
                    fixtures = resp.json().get("response", [])
                except Exception:
                    continue

                for fx in fixtures:
                    if fixtures_processed >= max_fixtures:
                        break
                    fixture = fx.get("fixture", {})
                    teams = fx.get("teams", {})
                    league = fx.get("league", {})

                    league_id = league.get("id")
                    if self.leagues and league_id not in self.leagues:
                        continue

                    fixture_id = str(fixture.get("id", "")).strip()
                    if not fixture_id or fixture_id in seen:
                        continue
                    seen.add(fixture_id)

                    home = (teams.get("home", {}) or {}).get("name")
                    away = (teams.get("away", {}) or {}).get("name")
                    if not home or not away:
                        continue

                    raw_pick = ""
                    confidence = None
                    try:
                        pred_resp = client.get("/predictions", params={"fixture": fixture_id})
                        pred_resp.raise_for_status()
                        payload = pred_resp.json().get("response", [])
                        pred_block = payload[0].get("predictions", {}) if payload else {}

                        winner = pred_block.get("winner", {}) if isinstance(pred_block, dict) else {}
                        if winner.get("name") == home:
                            raw_pick = "home"
                        elif winner.get("name") == away:
                            raw_pick = "away"
                        elif winner.get("comment"):
                            raw_pick = str(winner.get("comment", ""))
                        elif pred_block.get("advice"):
                            raw_pick = str(pred_block.get("advice", ""))

                        percentages = pred_block.get("percent", {}) if isinstance(pred_block, dict) else {}
                        selection_tmp = parse_selection_to_1x2(raw_pick)
                        if selection_tmp and isinstance(percentages, dict):
                            key = {"home": "home", "draw": "draw", "away": "away"}.get(selection_tmp)
                            if key and percentages.get(key):
                                txt = str(percentages.get(key, "")).replace("%", "").strip()
                                confidence = float(txt) / 100.0
                    except Exception:
                        pass

                    selection = parse_selection_to_1x2(raw_pick)
                    if not selection:
                        continue

                    kickoff = None
                    if fixture.get("date"):
                        try:
                            kickoff = datetime.fromisoformat(str(fixture.get("date")).replace("Z", "+00:00"))
                        except Exception:
                            kickoff = None

                    out.append(
                        ScrapedPrediction(
                            source_url=f"api-football://predictions/{fixture_id}",
                            home_name=home,
                            away_name=away,
                            predicted_selection=selection,
                            league=league.get("name"),
                            kickoff_time=kickoff,
                            confidence=confidence,
                            raw_prediction=raw_pick,
                        )
                    )
                    fixtures_processed += 1

        return out

    def get_fixture_outcome(self, fixture_id: str) -> str | None:
        """Return 1X2 outcome for a finished fixture from API-Football."""
        if not self.api_key or not fixture_id.isdigit():
            return None

        headers = {"x-apisports-key": self.api_key}
        try:
            with httpx.Client(base_url=self.base_url, headers=headers, timeout=20.0) as client:
                resp = client.get("/fixtures", params={"id": fixture_id})
                resp.raise_for_status()
                payload = resp.json().get("response", [])
        except Exception:
            return None

        if not payload:
            return None
        fx = payload[0]
        status_short = ((fx.get("fixture", {}) or {}).get("status", {}) or {}).get("short", "")
        if status_short not in {"FT", "AET", "PEN"}:
            return None
        goals = fx.get("goals", {}) or {}
        home_score = goals.get("home")
        away_score = goals.get("away")
        if home_score is None or away_score is None:
            return None
        if home_score > away_score:
            return "home"
        if home_score < away_score:
            return "away"
        return "draw"
