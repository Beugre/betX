"""Free ESPN outcome lookup for grading external predictions (no API key)."""

from __future__ import annotations

from datetime import date

import httpx

from betx.external.normalization import normalize_team_name, similarity


class EspnOutcomeSource:
    """Finds 1X2 outcomes from ESPN scoreboard endpoints."""

    def __init__(self) -> None:
        self.base_url = "https://site.api.espn.com/apis/site/v2/sports/soccer"
        self._scoreboard_cache: dict[str, list[dict]] = {}

    def get_outcome_by_match(self, match_date: date, home_name: str, away_name: str) -> str | None:
        events = self._get_events_for_date(match_date)
        if not events:
            return None

        target_home = normalize_team_name(home_name)
        target_away = normalize_team_name(away_name)

        best_score = 0.0
        best_event = None
        for ev in events:
            comp = ((ev.get("competitions") or [{}])[0])
            competitors = comp.get("competitors") or []
            if len(competitors) < 2:
                continue

            home = next((c for c in competitors if str(c.get("homeAway", "")).lower() == "home"), None)
            away = next((c for c in competitors if str(c.get("homeAway", "")).lower() == "away"), None)
            if not home or not away:
                continue

            home_team = ((home.get("team") or {}).get("displayName") or "")
            away_team = ((away.get("team") or {}).get("displayName") or "")
            if not home_team or not away_team:
                continue

            s1 = similarity(target_home, normalize_team_name(home_team))
            s2 = similarity(target_away, normalize_team_name(away_team))
            direct = (s1 + s2) / 2.0

            s1_sw = similarity(target_home, normalize_team_name(away_team))
            s2_sw = similarity(target_away, normalize_team_name(home_team))
            swapped = (s1_sw + s2_sw) / 2.0

            score = max(direct, swapped)
            if score > best_score:
                best_score = score
                best_event = (home, away)

        if not best_event or best_score < 0.82:
            return None

        home, away = best_event
        try:
            home_score = int(home.get("score"))
            away_score = int(away.get("score"))
        except Exception:
            return None

        if home_score > away_score:
            return "home"
        if home_score < away_score:
            return "away"
        return "draw"

    def _get_events_for_date(self, dt: date) -> list[dict]:
        key = dt.isoformat()
        if key in self._scoreboard_cache:
            return self._scoreboard_cache[key]

        yyyymmdd = dt.strftime("%Y%m%d")
        urls = [
            f"{self.base_url}/all/scoreboard?dates={yyyymmdd}",
            f"{self.base_url}/eng.1/scoreboard?dates={yyyymmdd}",
            f"{self.base_url}/esp.1/scoreboard?dates={yyyymmdd}",
            f"{self.base_url}/ita.1/scoreboard?dates={yyyymmdd}",
            f"{self.base_url}/ger.1/scoreboard?dates={yyyymmdd}",
            f"{self.base_url}/fra.1/scoreboard?dates={yyyymmdd}",
        ]

        events: list[dict] = []
        seen_ids: set[str] = set()
        for url in urls:
            try:
                resp = httpx.get(url, timeout=20.0)
                if resp.status_code != 200:
                    continue
                payload = resp.json()
            except Exception:
                continue

            for ev in payload.get("events", []):
                ev_id = str(ev.get("id", ""))
                status = (((ev.get("status") or {}).get("type") or {}).get("state") or "").lower()
                if ev_id and ev_id in seen_ids:
                    continue
                if status != "post":
                    continue
                if ev_id:
                    seen_ids.add(ev_id)
                events.append(ev)

        self._scoreboard_cache[key] = events
        return events
