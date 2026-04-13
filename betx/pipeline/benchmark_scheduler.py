"""Scheduler for automated external benchmark refresh and grading."""

from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler

from betx.external.service import ExternalBenchmarkService
from betx.logger import get_logger

log = get_logger("pipeline.benchmark_scheduler")


class BenchmarkScheduler:
    def __init__(self) -> None:
        self.scheduler = BlockingScheduler(timezone="UTC")

    @staticmethod
    def job_refresh() -> None:
        service = ExternalBenchmarkService()
        try:
            summary = service.run_full_refresh(history_days=3)
            log.info(f"Benchmark refresh done: {summary}")
        finally:
            service.close()

    @staticmethod
    def job_grade_only() -> None:
        service = ExternalBenchmarkService()
        try:
            linked = service.link_predictions_to_matches(lookback_days=120)
            graded = service.grade_predictions()
            service.compute_site_scores(windows=[30, 60, 90], min_graded=10)
            log.info(f"Grade job done: linked={linked} graded={graded}")
        finally:
            service.close()

    def run(self) -> None:
        # Refresh sources and score every 6 hours.
        self.scheduler.add_job(self.job_refresh, "cron", hour="*/6", minute=5, id="ext_refresh")
        # Lightweight grading every hour to update rankings soon after match ends.
        self.scheduler.add_job(self.job_grade_only, "cron", minute=20, id="ext_grade")

        log.info("Benchmark scheduler started (UTC): refresh=*/6h, grade=hourly")
        self.scheduler.start()


def main() -> None:
    BenchmarkScheduler().run()


if __name__ == "__main__":
    main()
