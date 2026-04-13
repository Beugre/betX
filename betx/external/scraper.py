"""Scraping helpers for external prediction websites."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import httpx
from bs4 import BeautifulSoup

from betx.external.normalization import normalize_team_name, parse_selection_to_1x2
from betx.external.sites_registry import SiteDefinition
from betx.logger import get_logger

log = get_logger("external.scraper")

_MATCH_LINE_PATTERN = re.compile(
    r"([A-Za-z0-9\-\'().&/ ]{3,})\s+v(?:s)?\.?\s+([A-Za-z0-9\-\'().&/ ]{3,})",
    flags=re.IGNORECASE,
)


@dataclass
class ScrapedPrediction:
    source_url: str
    home_name: str
    away_name: str
    predicted_selection: str
    league: str | None = None
    kickoff_time: datetime | None = None
    confidence: float | None = None
    raw_prediction: str | None = None

    @property
    def normalized_home(self) -> str:
        return normalize_team_name(self.home_name)

    @property
    def normalized_away(self) -> str:
        return normalize_team_name(self.away_name)


class PredictionSitesScraper:
    """Generic scraper with site-specific parsers for 1X2 tips."""

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    def scrape_site(
        self,
        site: SiteDefinition,
        days_back: int = 0,
        include_today: bool = True,
    ) -> list[ScrapedPrediction]:
        urls: list[str] = []
        if include_today:
            urls.extend(site.today_urls)
        urls.extend(site.history_urls(days_back=days_back))

        out: list[ScrapedPrediction] = []
        for url in dict.fromkeys(urls):
            html = self._fetch(url)
            if not html:
                continue
            try:
                out.extend(self._parse_page(site.parse_mode, html, source_url=url))
            except Exception as exc:
                log.warning(f"Parse error on {site.slug} {url}: {exc}")
        return out

    def _fetch(self, url: str) -> str | None:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            )
        }
        try:
            resp = httpx.get(url, headers=headers, timeout=self.timeout, follow_redirects=True)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            log.warning(f"Fetch failed {url}: {exc}")
            return None

    def _parse_page(self, parse_mode: str, html: str, source_url: str) -> list[ScrapedPrediction]:
        if parse_mode == "predictz":
            return self._parse_predictz(html, source_url)
        if parse_mode == "forebet":
            return self._parse_forebet(html, source_url)
        if parse_mode == "generic_article":
            return self._parse_generic_article(html, source_url)
        return self._parse_generic_listing(html, source_url)

    def _parse_predictz(self, html: str, source_url: str) -> list[ScrapedPrediction]:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        out: list[ScrapedPrediction] = []
        current_league: str | None = None
        for line in lines:
            if " Tips" in line and " v " not in line and len(line) < 120:
                current_league = line.replace(" Tips", "").strip()
                continue

            if " v " not in line:
                continue

            match_obj = _MATCH_LINE_PATTERN.search(line)
            if not match_obj:
                continue

            home = match_obj.group(1).strip()
            away = match_obj.group(2).strip()

            raw_pick = ""
            if "Home" in line:
                raw_pick = "home"
            elif "Away" in line:
                raw_pick = "away"
            elif "Draw" in line:
                raw_pick = "draw"
            else:
                if re.search(r"\b1\b", line):
                    raw_pick = "1"
                elif re.search(r"\bX\b", line):
                    raw_pick = "X"
                elif re.search(r"\b2\b", line):
                    raw_pick = "2"

            selection = parse_selection_to_1x2(raw_pick)
            if not selection:
                continue

            out.append(
                ScrapedPrediction(
                    source_url=source_url,
                    home_name=home,
                    away_name=away,
                    predicted_selection=selection,
                    league=current_league,
                    raw_prediction=raw_pick,
                )
            )
        return self._dedupe(out)

    def _parse_forebet(self, html: str, source_url: str) -> list[ScrapedPrediction]:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        out: list[ScrapedPrediction] = []
        for idx, line in enumerate(lines):
            if " v " not in line and " vs " not in line:
                continue

            match_obj = _MATCH_LINE_PATTERN.search(line)
            if not match_obj:
                continue
            home = match_obj.group(1).strip()
            away = match_obj.group(2).strip()

            window = " ".join(lines[idx: idx + 4])
            raw_pick = ""
            if re.search(r"\bpred\b.*\b1\b", window, flags=re.IGNORECASE) or re.search(r"\b1\b", window):
                raw_pick = "1"
            if re.search(r"\bpred\b.*\bx\b", window, flags=re.IGNORECASE) or re.search(r"\bx\b", window):
                raw_pick = "X"
            if re.search(r"\bpred\b.*\b2\b", window, flags=re.IGNORECASE) or re.search(r"\b2\b", window):
                raw_pick = "2"

            selection = parse_selection_to_1x2(raw_pick)
            if not selection:
                continue

            out.append(
                ScrapedPrediction(
                    source_url=source_url,
                    home_name=home,
                    away_name=away,
                    predicted_selection=selection,
                    raw_prediction=raw_pick,
                )
            )
        return self._dedupe(out)

    def _parse_generic_article(self, html: str, source_url: str) -> list[ScrapedPrediction]:
        soup = BeautifulSoup(html, "html.parser")
        title = (soup.title.string if soup.title else "") or ""
        text = soup.get_text(" ", strip=True)

        match_obj = _MATCH_LINE_PATTERN.search(title) or _MATCH_LINE_PATTERN.search(text)
        if not match_obj:
            return []

        selection = None
        for token in ("home", "away", "draw", "1", "x", "2"):
            if re.search(rf"\b{token}\b", text, flags=re.IGNORECASE):
                selection = parse_selection_to_1x2(token)
                if selection:
                    break

        if not selection:
            return []

        return [
            ScrapedPrediction(
                source_url=source_url,
                home_name=match_obj.group(1).strip(),
                away_name=match_obj.group(2).strip(),
                predicted_selection=selection,
                raw_prediction=selection,
            )
        ]

    def _parse_generic_listing(self, html: str, source_url: str) -> list[ScrapedPrediction]:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        out: list[ScrapedPrediction] = []

        for i, line in enumerate(lines):
            if " v " not in line and " vs " not in line:
                continue
            match_obj = _MATCH_LINE_PATTERN.search(line)
            if not match_obj:
                continue

            context = " ".join(lines[max(0, i - 1): i + 2])
            selection = None
            for token in ("home", "away", "draw", "1", "x", "2"):
                if re.search(rf"\b{token}\b", context, flags=re.IGNORECASE):
                    selection = parse_selection_to_1x2(token)
                    if selection:
                        break
            if not selection:
                continue

            out.append(
                ScrapedPrediction(
                    source_url=source_url,
                    home_name=match_obj.group(1).strip(),
                    away_name=match_obj.group(2).strip(),
                    predicted_selection=selection,
                    raw_prediction=selection,
                )
            )
        return self._dedupe(out)

    @staticmethod
    def _dedupe(rows: Iterable[ScrapedPrediction]) -> list[ScrapedPrediction]:
        seen: set[tuple[str, str, str]] = set()
        out: list[ScrapedPrediction] = []
        for r in rows:
            key = (r.normalized_home, r.normalized_away, r.predicted_selection)
            if not all(key) or key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out
