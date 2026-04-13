"""End-to-end service: scrape external sites, grade predictions, score and recommend."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_
from sqlalchemy.orm import Session

from betx.database import (
    ExternalPrediction,
    Match,
    PredictionSite,
    SiteScore,
    get_session,
)
from betx.external.normalization import normalize_team_name, score_to_1x2, similarity
from betx.external.api_source import ApiFootballPredictionSource
from betx.external.scoring import SiteScoreRow, compute_quality_score, flat_roi
from betx.external.scraper import PredictionSitesScraper
from betx.external.sites_registry import DEFAULT_SITES, SiteDefinition
from betx.logger import get_logger

log = get_logger("external.service")


class ExternalBenchmarkService:
    """Main entry point used by pipeline scripts and Streamlit dashboard."""

    def __init__(self, session: Session | None = None) -> None:
        self.session = session or get_session()
        self.scraper = PredictionSitesScraper()
        self.api_source = ApiFootballPredictionSource()

    def bootstrap_sites(self) -> list[PredictionSite]:
        out: list[PredictionSite] = []
        for site in DEFAULT_SITES:
            row = self.session.query(PredictionSite).filter_by(slug=site.slug).first()
            if not row:
                row = PredictionSite(
                    slug=site.slug,
                    name=site.name,
                    base_url=site.base_url,
                    sport="football",
                    is_active=site.enabled,
                )
                self.session.add(row)
            else:
                row.name = site.name
                row.base_url = site.base_url
                row.is_active = site.enabled
            out.append(row)
        self.session.commit()
        return out

    def scrape_predictions(
        self,
        days_back: int = 0,
        include_today: bool = True,
        only_slugs: list[str] | None = None,
    ) -> dict[str, int]:
        self.bootstrap_sites()
        summary: dict[str, int] = {}
        active_sites = [s for s in DEFAULT_SITES if s.enabled]
        if only_slugs:
            active_sites = [s for s in active_sites if s.slug in set(only_slugs)]

        for site in active_sites:
            db_site = self.session.query(PredictionSite).filter_by(slug=site.slug).first()
            if not db_site:
                continue

            if site.parse_mode == "api_football":
                scraped = self.api_source.fetch_from_known_matches(self.session, days_back=days_back)
                if not scraped:
                    scraped = self.api_source.fetch_from_fixtures_window(days_back=days_back)
            else:
                scraped = self.scraper.scrape_site(site, days_back=days_back, include_today=include_today)
            inserted = 0
            for pred in scraped:
                source_prediction_id = (
                    f"{pred.normalized_home}::{pred.normalized_away}::{pred.predicted_selection}"
                )
                exists = (
                    self.session.query(ExternalPrediction)
                    .filter_by(
                        site_id=db_site.id,
                        source_url=pred.source_url,
                        source_prediction_id=source_prediction_id,
                    )
                    .first()
                )
                if exists:
                    continue

                row = ExternalPrediction(
                    site_id=db_site.id,
                    source_url=pred.source_url,
                    source_prediction_id=source_prediction_id,
                    sport="football",
                    league=pred.league,
                    kickoff_time=pred.kickoff_time,
                    home_name=pred.home_name,
                    away_name=pred.away_name,
                    normalized_home=pred.normalized_home,
                    normalized_away=pred.normalized_away,
                    market="1x2",
                    predicted_selection=pred.predicted_selection,
                    confidence=pred.confidence,
                    raw_prediction=pred.raw_prediction,
                )
                self.session.add(row)
                inserted += 1
            self.session.commit()
            summary[site.slug] = inserted
            log.info(f"{site.slug}: {inserted} new predictions")

        return summary

    def link_predictions_to_matches(self, lookback_days: int = 120) -> int:
        """Attach external predictions to internal matches using fuzzy name matching."""
        since = date.today() - timedelta(days=lookback_days)
        preds = (
            self.session.query(ExternalPrediction)
            .filter(ExternalPrediction.match_id.is_(None))
            .all()
        )

        # Preload candidate matches once for speed.
        matches = (
            self.session.query(Match)
            .filter(
                and_(
                    Match.sport == "football",
                    Match.match_date >= since,
                )
            )
            .all()
        )

        linked = 0
        for pred in preds:
            best_match = None
            best_score = 0.0
            for match in matches:
                s1 = similarity(pred.normalized_home, normalize_team_name(match.home_name))
                s2 = similarity(pred.normalized_away, normalize_team_name(match.away_name))
                score = (s1 + s2) / 2.0
                if score > best_score:
                    best_score = score
                    best_match = match

            if best_match and best_score >= 0.78:
                pred.match_id = best_match.id
                linked += 1

        self.session.commit()
        return linked

    def materialize_matches_from_external(self) -> int:
        """Create minimal internal Match rows from external predictions when missing."""
        rows = (
            self.session.query(ExternalPrediction)
            .filter(ExternalPrediction.match_id.is_(None))
            .all()
        )
        created = 0
        for row in rows:
            match_date = (row.kickoff_time.date() if row.kickoff_time else date.today())
            existing = (
                self.session.query(Match)
                .filter(
                    Match.sport == "football",
                    Match.match_date == match_date,
                    Match.home_name == row.home_name,
                    Match.away_name == row.away_name,
                )
                .first()
            )
            if existing:
                row.match_id = existing.id
                continue

            status = "finished" if row.result_status in {"won", "lost"} else "scheduled"
            m = Match(
                external_id=None,
                sport="football",
                league=row.league,
                season=str(match_date.year),
                match_date=match_date,
                kickoff_time=row.kickoff_time,
                home_name=row.home_name,
                away_name=row.away_name,
                status=status,
            )
            self.session.add(m)
            self.session.flush()
            row.match_id = m.id
            created += 1

        self.session.commit()
        return created

    def grade_predictions(self) -> dict[str, int]:
        """Grade linked predictions where the match is finished."""
        api_graded, api_won, api_lost = self._grade_api_football_predictions()

        rows = (
            self.session.query(ExternalPrediction)
            .join(Match, Match.id == ExternalPrediction.match_id)
            .filter(
                ExternalPrediction.result_status == "pending",
                Match.status == "finished",
            )
            .all()
        )

        graded = 0
        won = 0
        lost = 0
        for row in rows:
            outcome = score_to_1x2(row.match.home_score, row.match.away_score)
            if not outcome:
                continue
            row.result_status = "won" if row.predicted_selection == outcome else "lost"
            row.grade_points = 1.0 if row.result_status == "won" else 0.0
            graded += 1
            if row.result_status == "won":
                won += 1
            else:
                lost += 1

        self.session.commit()
        return {
            "graded": graded + api_graded,
            "won": won + api_won,
            "lost": lost + api_lost,
        }

    def _grade_api_football_predictions(self) -> tuple[int, int, int]:
        """Grade API-Football rows even when no internal match is linked."""
        site = self.session.query(PredictionSite).filter_by(slug="api_football").first()
        if not site:
            return (0, 0, 0)

        rows = (
            self.session.query(ExternalPrediction)
            .filter(
                ExternalPrediction.site_id == site.id,
                ExternalPrediction.result_status == "pending",
                ExternalPrediction.source_url.like("api-football://predictions/%"),
            )
            .all()
        )

        graded = 0
        won = 0
        lost = 0
        for row in rows:
            fixture_id = row.source_url.rsplit("/", 1)[-1].strip()
            outcome = self.api_source.get_fixture_outcome(fixture_id)
            if not outcome:
                continue
            row.result_status = "won" if row.predicted_selection == outcome else "lost"
            row.grade_points = 1.0 if row.result_status == "won" else 0.0
            graded += 1
            if row.result_status == "won":
                won += 1
            else:
                lost += 1

        self.session.commit()
        return (graded, won, lost)

    def compute_site_scores(
        self,
        windows: list[int] | None = None,
        min_graded: int = 20,
    ) -> list[SiteScoreRow]:
        windows = windows or [30, 60, 90]
        today = date.today()

        created: list[SiteScoreRow] = []
        sites = self.session.query(PredictionSite).filter_by(is_active=True).all()

        for window_days in windows:
            start_dt = datetime.combine(today - timedelta(days=window_days), datetime.min.time())

            for site in sites:
                rows = (
                    self.session.query(ExternalPrediction)
                    .filter(
                        ExternalPrediction.site_id == site.id,
                        ExternalPrediction.scraped_at >= start_dt,
                    )
                    .all()
                )
                by_league: dict[str, list[ExternalPrediction]] = defaultdict(list)
                for row in rows:
                    by_league[(row.league or "all")].append(row)
                by_league["all"] = rows

                for league, subset in by_league.items():
                    graded_rows = [r for r in subset if r.result_status in {"won", "lost"}]
                    wins = sum(1 for r in graded_rows if r.result_status == "won")
                    losses = sum(1 for r in graded_rows if r.result_status == "lost")
                    graded_count = len(graded_rows)
                    predictions_count = len(subset)
                    hit_rate = (wins / graded_count) if graded_count else 0.0
                    roi = flat_roi(wins, losses)
                    quality = compute_quality_score(hit_rate, roi, graded_count)

                    score_row = (
                        self.session.query(SiteScore)
                        .filter_by(
                            site_id=site.id,
                            score_date=today,
                            league=league,
                            window_days=window_days,
                        )
                        .first()
                    )
                    if not score_row:
                        score_row = SiteScore(
                            site_id=site.id,
                            score_date=today,
                            league=league,
                            window_days=window_days,
                        )
                        self.session.add(score_row)

                    score_row.predictions_count = predictions_count
                    score_row.graded_count = graded_count
                    score_row.wins = wins
                    score_row.losses = losses
                    score_row.hit_rate = hit_rate
                    score_row.roi_flat = roi
                    score_row.quality_score = quality

                    if graded_count >= min_graded and league == "all":
                        created.append(
                            SiteScoreRow(
                                site_slug=site.slug,
                                site_name=site.name,
                                league=league,
                                predictions_count=predictions_count,
                                graded_count=graded_count,
                                wins=wins,
                                losses=losses,
                                hit_rate=hit_rate,
                                roi_flat=roi,
                                quality_score=quality,
                            )
                        )

        self.session.commit()
        created.sort(key=lambda r: r.quality_score, reverse=True)
        return created

    def get_top_sites(self, window_days: int = 60, limit: int = 5, min_graded: int = 5) -> list[dict[str, Any]]:
        today = date.today()
        rows = (
            self.session.query(SiteScore, PredictionSite)
            .join(PredictionSite, PredictionSite.id == SiteScore.site_id)
            .filter(
                SiteScore.score_date == today,
                SiteScore.window_days == window_days,
                SiteScore.league == "all",
                SiteScore.graded_count >= min_graded,
            )
            .order_by(SiteScore.quality_score.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "site_slug": site.slug,
                "site_name": site.name,
                "graded_count": score.graded_count,
                "hit_rate": score.hit_rate,
                "roi_flat": score.roi_flat,
                "quality_score": score.quality_score,
            }
            for score, site in rows
        ]

    def build_daily_recommendations(
        self,
        target_date: date | None = None,
        top_n_sites: int = 3,
        min_consensus_votes: int = 2,
        window_days: int = 60,
    ) -> list[dict[str, Any]]:
        """Build recommended bets from consensus of top-ranked sites."""
        target_date = target_date or date.today()
        top_sites = self.get_top_sites(window_days=window_days, limit=top_n_sites)
        if not top_sites:
            return []

        effective_min_votes = max(1, min(min_consensus_votes, len(top_sites)))

        top_ids = {
            site.id
            for site in self.session.query(PredictionSite)
            .filter(PredictionSite.slug.in_([r["site_slug"] for r in top_sites]))
            .all()
        }

        all_rows = (
            self.session.query(ExternalPrediction)
            .filter(ExternalPrediction.site_id.in_(top_ids))
            .all()
        )

        rows: list[ExternalPrediction] = []
        future_rows: list[ExternalPrediction] = []
        for row in all_rows:
            pred_date = None
            if row.match_id and row.match:
                pred_date = row.match.match_date
            elif row.kickoff_time:
                pred_date = row.kickoff_time.date()

            if pred_date == target_date:
                if row.match_id and row.match and row.match.status != "scheduled":
                    continue
                rows.append(row)
                continue

            if pred_date and target_date < pred_date <= (target_date + timedelta(days=1)):
                if row.result_status == "pending":
                    future_rows.append(row)

        if not rows:
            rows = future_rows

        grouped: dict[tuple[str, str], list[ExternalPrediction]] = defaultdict(list)
        for row in rows:
            key = f"{row.normalized_home}::{row.normalized_away}"
            grouped[(key, row.predicted_selection)].append(row)

        by_match: dict[str, dict[str, Any]] = {}
        for (match_key, selection), preds in grouped.items():
            votes = len(preds)
            if votes < effective_min_votes:
                continue
            score = 0.0
            for p in preds:
                site_score = next((x["quality_score"] for x in top_sites if x["site_slug"] == p.site.slug), 0.0)
                score += site_score

            first = preds[0]
            match_label = f"{first.home_name} vs {first.away_name}"
            league = first.league or (first.match.league if first.match_id and first.match else "N/A")
            kickoff = ""
            if first.match_id and first.match and first.match.kickoff_time:
                kickoff = str(first.match.kickoff_time)
            elif first.kickoff_time:
                kickoff = str(first.kickoff_time)

            cur = by_match.get(match_key)
            row = {
                "match_id": first.match_id,
                "match": match_label,
                "league": league,
                "kickoff": kickoff,
                "selection": selection,
                "consensus_votes": votes,
                "confidence_score": round(score, 2),
                "sites": ", ".join(sorted({p.site.name for p in preds})),
            }
            if cur is None or row["confidence_score"] > cur["confidence_score"]:
                by_match[match_key] = row

        return sorted(by_match.values(), key=lambda x: x["confidence_score"], reverse=True)

    def run_full_refresh(self, history_days: int = 30) -> dict[str, Any]:
        scraped = self.scrape_predictions(days_back=history_days, include_today=True)
        materialized = self.materialize_matches_from_external()
        linked = self.link_predictions_to_matches(lookback_days=max(120, history_days + 30))
        graded = self.grade_predictions()
        scores = self.compute_site_scores(windows=[30, 60, 90], min_graded=5)
        top_sites = self.get_top_sites(window_days=60, limit=10, min_graded=5)
        recos = self.build_daily_recommendations(window_days=60)
        health = self.collect_source_health()

        return {
            "scraped": scraped,
            "materialized_matches": materialized,
            "linked": linked,
            "graded": graded,
            "top_sites": top_sites,
            "recommendations_count": len(recos),
            "scores_generated": len(scores),
            "source_health": health,
        }

    def collect_source_health(self) -> list[dict[str, Any]]:
        """Provide transparent status for each source: ok/blocked/url_invalid/parsed_0."""
        self.bootstrap_sites()
        out: list[dict[str, Any]] = []

        for site in [s for s in DEFAULT_SITES if s.enabled]:
            db_site = self.session.query(PredictionSite).filter_by(slug=site.slug).first()
            if not db_site:
                continue

            recent_count = (
                self.session.query(ExternalPrediction)
                .filter(
                    ExternalPrediction.site_id == db_site.id,
                    ExternalPrediction.scraped_at >= datetime.utcnow() - timedelta(days=7),
                )
                .count()
            )

            if site.parse_mode == "api_football":
                out.append(
                    {
                        "site_slug": site.slug,
                        "site_name": site.name,
                        "status": "ok" if recent_count > 0 else "api_no_data",
                        "status_code": None,
                        "parsed_count": recent_count,
                        "url": "api-football://predictions",
                        "recent_predictions_7d": recent_count,
                    }
                )
                continue

            urls = site.today_urls or [site.base_url]
            status = "fetch_error"
            status_code = None
            parsed_count = 0
            url_used = urls[0]
            error = None

            for url in urls:
                url_used = url
                html, code, err = self.scraper._fetch_with_status(url)
                status_code = code
                error = err
                if html:
                    parsed = self.scraper._parse_page(site.parse_mode, html, source_url=url)
                    parsed_count = len(parsed)
                    if parsed_count > 0:
                        status = "ok"
                        break
                    status = "parsed_0"
                elif code == 403:
                    status = "http_403"
                elif code == 404:
                    status = "url_invalid"

            out.append(
                {
                    "site_slug": site.slug,
                    "site_name": site.name,
                    "status": status,
                    "status_code": status_code,
                    "parsed_count": parsed_count,
                    "url": url_used,
                    "recent_predictions_7d": recent_count,
                    "error": error,
                }
            )

        return out

    def leaderboard_dataframe(self, window_days: int = 60, min_graded: int = 5) -> list[dict[str, Any]]:
        rows = self.get_top_sites(window_days=window_days, limit=50, min_graded=min_graded)
        return rows

    def recommendations_dataframe(self, target_date: date | None = None) -> list[dict[str, Any]]:
        return self.build_daily_recommendations(target_date=target_date)

    def latest_activity(self) -> dict[str, Any]:
        counts = Counter()
        counts["sites"] = self.session.query(PredictionSite).count()
        counts["predictions"] = self.session.query(ExternalPrediction).count()
        counts["graded"] = (
            self.session.query(ExternalPrediction)
            .filter(ExternalPrediction.result_status.in_(["won", "lost"]))
            .count()
        )
        return dict(counts)

    def close(self) -> None:
        self.session.close()
