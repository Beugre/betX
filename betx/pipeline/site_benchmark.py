"""Pipeline for scraping and benchmarking external football prediction sites."""

from __future__ import annotations

from betx.database import init_db
from betx.external.service import ExternalBenchmarkService
from betx.logger import get_logger

log = get_logger("pipeline.site_benchmark")


class SiteBenchmarkPipeline:
    def run(self, history_days: int = 30) -> dict:
        init_db()
        service = ExternalBenchmarkService()
        try:
            summary = service.run_full_refresh(history_days=history_days)
            log.info("External benchmark complete")
            return summary
        finally:
            service.close()


def main() -> None:
    pipeline = SiteBenchmarkPipeline()
    summary = pipeline.run(history_days=30)
    print("\n=== External Benchmark Summary ===")
    print(f"Scraped sites: {summary['scraped']}")
    print(f"Linked predictions: {summary['linked']}")
    print(f"Graded: {summary['graded']}")
    print(f"Top sites rows: {len(summary['top_sites'])}")
    print(f"Recommendations today: {summary['recommendations_count']}")


if __name__ == "__main__":
    main()
