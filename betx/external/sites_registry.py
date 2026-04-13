"""Registry of tracked prediction websites and URL strategies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass
class SiteDefinition:
    slug: str
    name: str
    base_url: str
    parse_mode: str
    today_urls: list[str]
    history_templates: list[str] = field(default_factory=list)
    enabled: bool = True

    def history_urls(self, days_back: int) -> list[str]:
        urls: list[str] = []
        for d in range(1, max(days_back, 0) + 1):
            target = date.today() - timedelta(days=d)
            yyyyMMdd = target.strftime("%Y%m%d")
            for tpl in self.history_templates:
                urls.append(tpl.format(date_yyyymmdd=yyyyMMdd, date_obj=target))
        return urls


DEFAULT_SITES: list[SiteDefinition] = [
    SiteDefinition(
        slug="api_football",
        name="API-Football Predictions",
        base_url="https://v3.football.api-sports.io",
        parse_mode="api_football",
        today_urls=[],
        history_templates=[],
        enabled=False,
    ),
    SiteDefinition(
        slug="eaglepredict",
        name="EaglePredict",
        base_url="https://eaglepredict.com",
        parse_mode="eaglepredict_combo",
        today_urls=["https://eaglepredict.com/fr/pronostics/pronostics-combines/"],
        history_templates=[],
    ),
    SiteDefinition(
        slug="forebet",
        name="Forebet",
        base_url="https://www.forebet.com",
        parse_mode="forebet",
        today_urls=[
            "https://www.forebet.com/en/football-tips-and-predictions-for-today/predictions-1x2",
        ],
        history_templates=[
            "https://www.forebet.com/en/football-predictions-from-yesterday",
            "https://www.forebet.com/en/football-predictions",
        ],
    ),
    SiteDefinition(
        slug="predictz",
        name="PredictZ",
        base_url="https://www.predictz.com",
        parse_mode="predictz",
        today_urls=["https://www.predictz.com/predictions/today/"],
        history_templates=[
            "https://www.predictz.com/predictions/{date_yyyymmdd}/",
            "https://www.predictz.com/predictions/yesterday/",
        ],
    ),
    SiteDefinition(
        slug="windrawwin",
        name="WinDrawWin",
        base_url="https://www.windrawwin.com",
        parse_mode="generic_listing",
        today_urls=["https://www.windrawwin.com/predictions/"],
        history_templates=["https://www.windrawwin.com/predictions/"],
    ),
    SiteDefinition(
        slug="soccerpunter",
        name="SoccerPunter",
        base_url="https://www.soccerpunter.com",
        parse_mode="generic_listing",
        today_urls=["https://www.soccerpunter.com/"],
        history_templates=[],
        enabled=False,
    ),
    SiteDefinition(
        slug="soccervista",
        name="SoccerVista",
        base_url="https://www.soccervista.com",
        parse_mode="generic_listing",
        today_urls=["https://www.soccervista.com/predictions/"],
        history_templates=["https://www.soccervista.com/predictions/sh=1"],
    ),
    SiteDefinition(
        slug="sportytrader",
        name="SportyTrader",
        base_url="https://www.sportytrader.com",
        parse_mode="generic_listing",
        today_urls=["https://www.sportytrader.com/en/betting-tips/football/"],
        history_templates=["https://www.sportytrader.com/en/betting-tips/football/"],
    ),
    SiteDefinition(
        slug="bettingexpert",
        name="BettingExpert",
        base_url="https://www.bettingexpert.com",
        parse_mode="bettingexpert",
        today_urls=["https://www.bettingexpert.com/football"],
        history_templates=["https://www.bettingexpert.com/football"],
    ),
]
