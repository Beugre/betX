"""
betX – Pipeline quotidien.

Orchestration complète du workflow journalier :
1. Récupérer les matchs du jour
2. Récupérer les cotes actuelles
3. Charger les modèles
4. Générer les probabilités
5. Calculer les value bets
6. Générer la shortlist avec mises
7. Export CSV + dashboard
8. Résoudre les paris d'hier

Peut être lancé manuellement ou via cron job.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

from betx.config import settings
from betx.database import init_db, get_session, Match, Bet, Prediction, Odds
from betx.database.helpers import (
    get_current_bankroll,
    get_matches_by_date,
    get_pending_bets,
    record_bankroll,
    settle_bet,
)
from betx.data.football_collector import FootballCollector
from betx.data.tennis_collector import TennisCollector
from betx.data.basketball_collector import BasketballCollector
from betx.data.odds_collector import OddsCollector
from betx.models.football_model import FootballModel, TeamStats
from betx.models.tennis_model import TennisModel, TennisPlayerStats
from betx.models.basketball_model import BasketballModel, BasketTeamStats
from betx.engine.value_engine import ValueEngine, ValueBet
from betx.engine.staking_engine import StakingEngine
from betx.analytics.performance_metrics import PerformanceTracker
from betx.logger import get_logger

log = get_logger("pipeline")


class DailyPipeline:
    """Pipeline quotidien complet."""

    def __init__(self) -> None:
        self.session = get_session()
        # Collecteurs
        self.football_collector = FootballCollector()
        self.tennis_collector = TennisCollector()
        self.basketball_collector = BasketballCollector()
        self.odds_collector = OddsCollector()
        # Modèles
        self.football_model = FootballModel()
        self.tennis_model = TennisModel()
        self.basketball_model = BasketballModel()
        # Engines
        self.value_engine = ValueEngine()
        self.staking_engine = StakingEngine()

    def run(self, target_date: date | None = None) -> dict:
        """
        Exécute le pipeline complet.

        Args:
            target_date: Date cible (défaut : aujourd'hui)

        Returns:
            Résumé de l'exécution
        """
        target_date = target_date or date.today()
        log.info(f"{'='*60}")
        log.info(f"🚀 Pipeline quotidien – {target_date}")
        log.info(f"{'='*60}")

        summary = {
            "date": str(target_date),
            "matches_collected": 0,
            "odds_collected": 0,
            "predictions_generated": 0,
            "value_bets_found": 0,
            "bets_settled": 0,
            "total_pnl_today": 0.0,
        }

        try:
            # ── Étape 0 : Initialiser la DB ──
            init_db()

            # ── Étape 1 : Résoudre les paris d'hier ──
            log.info("📋 Étape 1: Résolution des paris en attente...")
            summary["bets_settled"] = self._settle_pending_bets(target_date)

            # ── Étape 2 : Collecter les matchs du jour ──
            log.info("📡 Étape 2: Collecte des matchs...")
            summary["matches_collected"] = self._collect_matches(target_date)

            # ── Étape 3 : Collecter les cotes ──
            log.info("💹 Étape 3: Collecte des cotes...")
            summary["odds_collected"] = self._collect_odds()

            # ── Étape 4 : Générer les prédictions ──
            log.info("🧠 Étape 4: Génération des prédictions...")
            all_value_bets = self._generate_predictions(target_date)
            summary["value_bets_found"] = len(all_value_bets)

            # ── Étape 5 : Calculer les mises ──
            log.info("💰 Étape 5: Calcul des mises...")
            bankroll = get_current_bankroll(self.session)
            stakes = self.staking_engine.calculate_stakes_batch(
                all_value_bets, bankroll
            )

            # ── Étape 6 : Sauvegarder les paris ──
            log.info("💾 Étape 6: Sauvegarde des paris...")
            for s in stakes:
                bet = Bet(
                    match_id=s.value_bet.match_id,
                    sport=s.value_bet.sport,
                    market=s.value_bet.market,
                    selection=s.value_bet.selection,
                    model_probability=s.value_bet.model_probability,
                    bookmaker_odds=s.value_bet.bookmaker_odds,
                    edge=s.value_bet.edge,
                    ev=s.value_bet.ev,
                    stake=s.stake_amount,
                    stake_pct=s.stake_pct,
                    kelly_raw=s.kelly_raw,
                    staking_method=s.method,
                    bookmaker=s.value_bet.bookmaker,
                    status="pending",
                )
                self.session.add(bet)

            self.session.commit()

            # ── Étape 7 : Export CSV ──
            log.info("📄 Étape 7: Export shortlist CSV...")
            self._export_shortlist(stakes, target_date)

            # ── Étape 8 : Mise à jour bankroll ──
            log.info("📊 Étape 8: Mise à jour bankroll...")
            self._update_bankroll(target_date)

            # ── Étape 9 : Alertes Telegram ──
            if settings.telegram.enabled and stakes:
                log.info("📱 Étape 9: Envoi alertes Telegram...")
                self._send_telegram_alert(stakes)

            # ── Résumé ──
            log.info(f"\n{'='*60}")
            log.info(f"✅ Pipeline terminé – {target_date}")
            log.info(f"   Matchs collectés: {summary['matches_collected']}")
            log.info(f"   Cotes collectées: {summary['odds_collected']}")
            log.info(f"   Value bets: {summary['value_bets_found']}")
            log.info(f"   Paris résolus: {summary['bets_settled']}")
            log.info(f"{'='*60}")

        except Exception as e:
            log.error(f"❌ Erreur pipeline: {e}", exc_info=True)
            self.session.rollback()
            raise
        finally:
            self.session.close()

        return summary

    # ─── Étapes internes ─────────────────────────────────────────

    def _collect_matches(self, target_date: date) -> int:
        """Collecte et sauvegarde les matchs du jour."""
        total = 0

        # Football
        try:
            fixtures = self.football_collector.fetch_matches(target_date)
            saved = self.football_collector.save_matches_to_db(fixtures)
            total += len(saved)
            log.info(f"  ⚽ {len(saved)} matchs football")
        except Exception as e:
            log.warning(f"  ⚽ Échec football: {e}")

        # Tennis
        try:
            matches = self.tennis_collector.fetch_matches(target_date)
            saved = self.tennis_collector.save_matches_to_db(matches)
            total += len(saved)
            log.info(f"  🎾 {len(saved)} matchs tennis")
        except Exception as e:
            log.warning(f"  🎾 Échec tennis: {e}")

        # Basketball
        try:
            matches = self.basketball_collector.fetch_matches(target_date)
            saved = self.basketball_collector.save_matches_to_db(matches)
            total += len(saved)
            log.info(f"  🏀 {len(saved)} matchs basket")
        except Exception as e:
            log.warning(f"  🏀 Échec basket: {e}")

        return total

    def _collect_odds(self) -> int:
        """Collecte les cotes pour tous les sports."""
        total = 0
        for sport in ["football", "tennis", "basketball"]:
            try:
                odds = self.odds_collector.fetch_odds(sport=sport)
                count = self.odds_collector.save_odds_to_db(odds, sport=sport)
                total += count
            except Exception as e:
                log.warning(f"  Échec cotes {sport}: {e}")
        return total

    def _generate_predictions(self, target_date: date) -> list[ValueBet]:
        """Génère les prédictions et identifie les value bets."""
        all_value_bets = []
        matches = get_matches_by_date(self.session, target_date)

        for match in matches:
            if match.status != "scheduled":
                continue

            try:
                if match.sport == "football":
                    vbs = self._predict_football(match)
                elif match.sport == "tennis":
                    vbs = self._predict_tennis(match)
                elif match.sport == "basketball":
                    vbs = self._predict_basketball(match)
                else:
                    continue
                all_value_bets.extend(vbs)
            except Exception as e:
                log.warning(f"Erreur prédiction {match.home_name} vs {match.away_name}: {e}")

        return all_value_bets

    def _predict_football(self, match: Match) -> list[ValueBet]:
        """Prédit un match de football et cherche les value bets."""
        # Construire les stats des équipes à partir de la DB
        home_stats = self._build_football_stats(match.home_team_id, match.home_name)
        away_stats = self._build_football_stats(match.away_team_id, match.away_name)

        # Prédiction
        pred = self.football_model.predict(home_stats, away_stats)

        # Sauvegarder les prédictions
        predictions = {
            "home": pred.p_home,
            "draw": pred.p_draw,
            "away": pred.p_away,
            "over_2.5": pred.p_over_25,
            "over_3.5": pred.p_over_35,
            "btts": pred.p_btts,
        }
        for sel, prob in predictions.items():
            market = "h2h" if sel in ("home", "draw", "away") else sel.split("_")[0]
            p = Prediction(
                match_id=match.id,
                model_name="poisson_dc_elo_xg_v1",
                market=market,
                selection=sel,
                probability=prob,
                model_details=pred.to_json(),
            )
            self.session.add(p)

        # Récupérer les cotes
        odds_entries = (
            self.session.query(Odds)
            .filter(Odds.match_id == match.id)
            .all()
        )
        odds_by_market = self._group_odds(odds_entries)

        # Chercher les value bets
        return self.value_engine.scan_match(
            match_id=match.id,
            sport="football",
            home_team=match.home_name,
            away_team=match.away_name,
            predictions=predictions,
            odds_by_market=odds_by_market,
            model_name="poisson_dc_elo_xg_v1",
        )

    def _predict_tennis(self, match: Match) -> list[ValueBet]:
        """Prédit un match de tennis."""
        pa = self._build_tennis_stats(match.home_player_id, match.home_name)
        pb = self._build_tennis_stats(match.away_player_id, match.away_name)

        pred = self.tennis_model.predict(
            pa, pb,
            surface=match.surface or "hard",
            best_of=3,
        )

        predictions = {
            "home": pred.p_win_a,
            "away": pred.p_win_b,
        }
        # Over/Under jeux
        for threshold, prob in pred.p_over_games.items():
            predictions[f"over_{threshold}"] = prob

        for sel, prob in predictions.items():
            p = Prediction(
                match_id=match.id,
                model_name="tennis_elo_service_v1",
                market="h2h" if sel in ("home", "away") else "totals",
                selection=sel,
                probability=prob,
            )
            self.session.add(p)

        odds_entries = self.session.query(Odds).filter(Odds.match_id == match.id).all()
        odds_by_market = self._group_odds(odds_entries)

        return self.value_engine.scan_match(
            match_id=match.id,
            sport="tennis",
            home_team=match.home_name,
            away_team=match.away_name,
            predictions=predictions,
            odds_by_market=odds_by_market,
            model_name="tennis_elo_service_v1",
        )

    def _predict_basketball(self, match: Match) -> list[ValueBet]:
        """Prédit un match de basket."""
        home = self._build_basket_stats(match.home_team_id, match.home_name)
        away = self._build_basket_stats(match.away_team_id, match.away_name)

        pred = self.basketball_model.predict(home, away)

        predictions = {
            "home": pred.p_home_win,
            "away": pred.p_away_win,
        }
        for threshold, prob in pred.p_over_total.items():
            predictions[f"over_{threshold}"] = prob

        for sel, prob in predictions.items():
            p = Prediction(
                match_id=match.id,
                model_name="basket_regression_pace_v1",
                market="h2h" if sel in ("home", "away") else "totals",
                selection=sel,
                probability=prob,
            )
            self.session.add(p)

        odds_entries = self.session.query(Odds).filter(Odds.match_id == match.id).all()
        odds_by_market = self._group_odds(odds_entries)

        return self.value_engine.scan_match(
            match_id=match.id,
            sport="basketball",
            home_team=match.home_name,
            away_team=match.away_name,
            predictions=predictions,
            odds_by_market=odds_by_market,
            model_name="basket_regression_pace_v1",
        )

    # ─── Helpers ─────────────────────────────────────────────────

    def _build_football_stats(self, team_id: int | None, name: str) -> TeamStats:
        """Construit les TeamStats à partir de la DB."""
        from betx.database import Team
        stats = TeamStats(name=name)
        if team_id:
            team = self.session.query(Team).filter(Team.id == team_id).first()
            if team:
                stats.elo = team.elo_rating
                stats.home_elo = team.elo_home
                stats.away_elo = team.elo_away
                if team.avg_goals_scored:
                    stats.avg_goals_scored = team.avg_goals_scored
                if team.avg_goals_conceded:
                    stats.avg_goals_conceded = team.avg_goals_conceded
                if team.avg_xg_for:
                    stats.xg_for = team.avg_xg_for
                if team.avg_xg_against:
                    stats.xg_against = team.avg_xg_against
        return stats

    def _build_tennis_stats(self, player_id: int | None, name: str) -> TennisPlayerStats:
        """Construit les TennisPlayerStats à partir de la DB."""
        from betx.database import Player
        stats = TennisPlayerStats(name=name)
        if player_id:
            player = self.session.query(Player).filter(Player.id == player_id).first()
            if player:
                stats.elo_global = player.elo_global
                stats.elo_hard = player.elo_hard
                stats.elo_clay = player.elo_clay
                stats.elo_grass = player.elo_grass
                stats.elo_indoor = player.elo_indoor
                if player.serve_win_pct:
                    stats.serve_win_pct = player.serve_win_pct
                if player.return_win_pct:
                    stats.return_win_pct = player.return_win_pct
                if player.break_point_convert_pct:
                    stats.break_point_convert_pct = player.break_point_convert_pct
        return stats

    def _build_basket_stats(self, team_id: int | None, name: str) -> BasketTeamStats:
        """Construit les BasketTeamStats à partir de la DB."""
        from betx.database import Team
        stats = BasketTeamStats(name=name)
        if team_id:
            team = self.session.query(Team).filter(Team.id == team_id).first()
            if team:
                stats.elo = team.elo_rating
                if team.offensive_rating:
                    stats.offensive_rating = team.offensive_rating
                if team.defensive_rating:
                    stats.defensive_rating = team.defensive_rating
                if team.pace:
                    stats.pace = team.pace
                if team.efg_pct:
                    stats.efg_pct = team.efg_pct
        return stats

    @staticmethod
    def _group_odds(odds_entries: list[Odds]) -> dict:
        """Groupe les cotes par marché et sélection."""
        result: dict[str, dict[str, list[tuple[float, str]]]] = {}
        for o in odds_entries:
            if o.market not in result:
                result[o.market] = {}
            if o.selection not in result[o.market]:
                result[o.market][o.selection] = []
            result[o.market][o.selection].append((o.odds_value, o.bookmaker))
        return result

    def _settle_pending_bets(self, today: date) -> int:
        """Résout les paris dont le match est terminé."""
        pending = get_pending_bets(self.session)
        settled = 0

        for bet in pending:
            match = self.session.query(Match).filter(Match.id == bet.match_id).first()
            if not match or match.status != "finished":
                continue

            result = self._determine_result(bet, match)
            if result:
                settle_bet(self.session, bet, result)
                settled += 1
                log.info(
                    f"  {'✅' if result == 'won' else '❌'} "
                    f"{bet.selection} @ {bet.bookmaker_odds:.2f} → {result} "
                    f"(PnL: {bet.pnl:+.2f}€)"
                )

        self.session.commit()
        return settled

    @staticmethod
    def _determine_result(bet: Bet, match: Match) -> str | None:
        """Détermine le résultat d'un pari."""
        if match.home_score is None or match.away_score is None:
            return None

        hs, aws = match.home_score, match.away_score

        if bet.market == "h2h":
            if bet.selection == "home":
                return "won" if hs > aws else "lost"
            elif bet.selection == "draw":
                return "won" if hs == aws else "lost"
            elif bet.selection == "away":
                return "won" if aws > hs else "lost"

        elif "over" in bet.selection:
            # over_2.5 → total > 2.5
            try:
                line = float(bet.selection.split("_")[-1])
                total = hs + aws
                return "won" if total > line else "lost"
            except ValueError:
                return None

        elif "under" in bet.selection:
            try:
                line = float(bet.selection.split("_")[-1])
                total = hs + aws
                return "won" if total < line else "lost"
            except ValueError:
                return None

        elif bet.selection in ("btts", "btts_yes"):
            return "won" if hs > 0 and aws > 0 else "lost"

        elif bet.selection == "btts_no":
            return "won" if hs == 0 or aws == 0 else "lost"

        return None

    def _update_bankroll(self, target_date: date) -> None:
        """Met à jour l'historique de bankroll."""
        bankroll = get_current_bankroll(self.session)

        # PnL du jour
        day_bets = (
            self.session.query(Bet)
            .filter(
                Bet.created_at >= str(target_date),
                Bet.status.in_(["won", "lost"]),
            )
            .all()
        )
        daily_pnl = sum(b.pnl or 0 for b in day_bets)
        new_bankroll = bankroll + daily_pnl

        # Stats
        all_bets = self.session.query(Bet).filter(Bet.status.in_(["won", "lost"])).all()
        total_pnl = sum(b.pnl or 0 for b in all_bets)
        n_bets = len(day_bets)
        n_wins = sum(1 for b in day_bets if b.status == "won")
        n_losses = sum(1 for b in day_bets if b.status == "lost")

        total_staked = sum(b.stake for b in all_bets)
        roi = (total_pnl / total_staked * 100) if total_staked > 0 else 0

        record_bankroll(
            self.session,
            bankroll=new_bankroll,
            daily_pnl=daily_pnl,
            total_pnl=total_pnl,
            n_bets=n_bets,
            n_wins=n_wins,
            n_losses=n_losses,
            roi_pct=roi,
            target_date=target_date,
        )
        self.session.commit()
        log.info(f"  Bankroll: {bankroll:.2f} → {new_bankroll:.2f} (PnL: {daily_pnl:+.2f})")

    def _export_shortlist(self, stakes, target_date: date) -> None:
        """Exporte la shortlist en CSV."""
        if not stakes:
            return

        export_dir = settings.paths.EXPORTS_DIR
        export_dir.mkdir(parents=True, exist_ok=True)
        filepath = export_dir / f"shortlist_{target_date}.csv"

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Sport", "Match", "Market", "Selection",
                "Model P", "Odds", "Edge", "EV",
                "Kelly", "Stake %", "Stake €", "Bookmaker",
            ])
            for s in stakes:
                vb = s.value_bet
                writer.writerow([
                    vb.sport,
                    f"{vb.home_team} vs {vb.away_team}",
                    vb.market,
                    vb.selection,
                    f"{vb.model_probability:.3f}",
                    f"{vb.bookmaker_odds:.2f}",
                    f"{vb.edge:.3f}",
                    f"{vb.ev:.3f}",
                    f"{s.kelly_raw:.4f}",
                    f"{s.stake_pct:.4f}",
                    f"{s.stake_amount:.2f}",
                    vb.bookmaker,
                ])

        log.info(f"  Shortlist exportée: {filepath}")

    def _send_telegram_alert(self, stakes) -> None:
        """Envoie un résumé via Telegram."""
        try:
            import httpx

            message = f"🎯 *betX – Shortlist du jour*\n\n"
            for s in stakes[:10]:
                vb = s.value_bet
                emoji = "⚽" if vb.sport == "football" else "🎾" if vb.sport == "tennis" else "🏀"
                message += (
                    f"{emoji} {vb.home_team} vs {vb.away_team}\n"
                    f"   {vb.selection} @ {vb.bookmaker_odds:.2f} "
                    f"(Edge: {vb.edge:.1%})\n"
                    f"   Mise: {s.stake_amount:.2f}€\n\n"
                )

            url = f"https://api.telegram.org/bot{settings.telegram.bot_token}/sendMessage"
            httpx.post(url, json={
                "chat_id": settings.telegram.chat_id,
                "text": message,
                "parse_mode": "Markdown",
            })
            log.info("  Alerte Telegram envoyée ✓")
        except Exception as e:
            log.warning(f"  Échec Telegram: {e}")


# =============================================================================
# Point d'entrée CLI
# =============================================================================
def run_daily():
    """Point d'entrée pour le pipeline quotidien."""
    pipeline = DailyPipeline()
    return pipeline.run()


if __name__ == "__main__":
    run_daily()
