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
        self.last_fetch_meta: dict[str, dict[str, str | int | None]] = {}

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
            html, status_code, error = self._fetch_with_status(url)
            self.last_fetch_meta[url] = {
                "status_code": status_code,
                "error": error,
                "parsed_count": 0,
            }
            if not html:
                continue
            try:
                parsed = self._parse_page(site.parse_mode, html, source_url=url)
                self.last_fetch_meta[url]["parsed_count"] = len(parsed)
                out.extend(parsed)
            except Exception as exc:
                self.last_fetch_meta[url]["error"] = f"parse_error: {exc}"
                log.warning(f"Parse error on {site.slug} {url}: {exc}")
        return out

    def _fetch(self, url: str) -> str | None:
        html, _, _ = self._fetch_with_status(url)
        return html

    def _fetch_with_status(self, url: str) -> tuple[str | None, int | None, str | None]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
        }
        try:
            resp = httpx.get(url, headers=headers, timeout=self.timeout, follow_redirects=True)
            resp.raise_for_status()
            return resp.text, resp.status_code, None
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response else None
            msg = f"http_{status_code}" if status_code else "http_error"
            log.warning(f"Fetch failed {url}: {msg}")
            return None, status_code, msg
        except Exception as exc:
            log.warning(f"Fetch failed {url}: {exc}")
            return None, None, str(exc)

    def _parse_page(self, parse_mode: str, html: str, source_url: str) -> list[ScrapedPrediction]:
        if parse_mode == "predictz":
            return self._parse_predictz(html, source_url)
        if parse_mode == "forebet":
            return self._parse_forebet(html, source_url)
        if parse_mode == "bettingexpert":
            return self._parse_bettingexpert(html, source_url)
        if parse_mode == "eaglepredict_combo":
            return self._parse_eaglepredict_combo(html, source_url)
        if parse_mode == "generic_article":
            return self._parse_generic_article(html, source_url)
        return self._parse_generic_listing(html, source_url)

    def _parse_bettingexpert(self, html: str, source_url: str) -> list[ScrapedPrediction]:
        soup = BeautifulSoup(html, "html.parser")
        lines = [ln.strip() for ln in soup.get_text("\n", strip=True).splitlines() if ln.strip()]
        out: list[ScrapedPrediction] = []

        for i, line in enumerate(lines):
            if not re.fullmatch(r"[A-Z0-9 .&'()/-]{4,}-[A-Z0-9 .&'()/-]{4,}", line):
                continue

            home, away = [p.strip().title() for p in line.split("-", 1)]
            window = " ".join(lines[i + 1: i + 5])
            selection = self._selection_from_tip_text(window, home, away)
            if not selection:
                continue

            out.append(
                ScrapedPrediction(
                    source_url=source_url,
                    home_name=home,
                    away_name=away,
                    predicted_selection=selection,
                    raw_prediction=window[:120],
                )
            )

        return self._dedupe(out)

    def _parse_eaglepredict_combo(self, html: str, source_url: str) -> list[ScrapedPrediction]:
        soup = BeautifulSoup(html, "html.parser")
        links = {
            a.get("href", "")
            for a in soup.find_all("a")
            if "/fr/pronostics/match/" in a.get("href", "")
        }

        out: list[ScrapedPrediction] = []
        for link in sorted(links)[:25]:
            if not link.startswith("http"):
                continue

            article_html = self._fetch(link)
            if not article_html:
                continue

            article_soup = BeautifulSoup(article_html, "html.parser")
            title = (article_soup.title.string if article_soup.title else "") or ""
            text = article_soup.get_text(" ", strip=True)
            match_obj = _MATCH_LINE_PATTERN.search(title) or _MATCH_LINE_PATTERN.search(text)
            if not match_obj:
                continue

            home = match_obj.group(1).strip()
            away = match_obj.group(2).strip()
            selection = self._selection_from_tip_text(text, home, away)
            if not selection:
                continue

            out.append(
                ScrapedPrediction(
                    source_url=link,
                    home_name=home,
                    away_name=away,
                    predicted_selection=selection,
                    raw_prediction=selection,
                )
            )

        return self._dedupe(out)

    @staticmethod
    def _selection_from_tip_text(text: str, home: str, away: str) -> str | None:
        lower = text.lower()
        home_l = home.lower()
        away_l = away.lower()

        if re.search(r"\b(draw|match nul|nul)\b", lower) and "draw no bet" not in lower:
            return "draw"

        win_tokens = r"(to win|wins?|vainqueur|gagne|gagnant|victoire)"
        if home_l in lower and re.search(win_tokens, lower):
            return "home"
        if away_l in lower and re.search(win_tokens, lower):
            return "away"

        if re.search(r"\bpronostic\s*[:\-]?\s*1\b", lower) or re.search(r"\bpick\s*[:\-]?\s*1\b", lower):
            return "home"
        if re.search(r"\bpronostic\s*[:\-]?\s*x\b", lower) or re.search(r"\bpick\s*[:\-]?\s*x\b", lower):
            return "draw"
        if re.search(r"\bpronostic\s*[:\-]?\s*2\b", lower) or re.search(r"\bpick\s*[:\-]?\s*2\b", lower):
            return "away"

        return None

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
