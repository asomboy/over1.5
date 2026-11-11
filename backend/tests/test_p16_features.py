import json
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base, get_db
from main import _LIVE_ALERT_SENT_CACHE, _evaluate_live_goal_alert_candidate, app
from models import (
    AlertPreference,
    BetSlip,
    Fixture,
    HistoricalResult,
    League,
    LivePredictionSnapshot,
    MatchStatistics,
    ModelEvaluation,
    Team,
)
from services.alert_preferences_service import AlertPreferencesService
from services.bet_slip_service import BetSlipService
from services.live_service import LiveModelEvaluationService
from services.model_evaluation_service import ModelEvaluationService


class TestPhase16Features(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )
        Base.metadata.create_all(bind=self.engine)
        self.db = self.SessionLocal()

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        _LIVE_ALERT_SENT_CACHE.clear()

        self.league = League(
            external_id="ESPN-ENG.1",
            name="Premier League",
            country="England",
            season="2026",
        )
        self.home_team = Team(
            external_id="ESPN-T-H1", name="Arsenal", short_code="ARS"
        )
        self.away_team = Team(
            external_id="ESPN-T-A1", name="Chelsea", short_code="CHE"
        )
        self.db.add_all([self.league, self.home_team, self.away_team])
        self.db.commit()
        self.db.refresh(self.league)
        self.db.refresh(self.home_team)
        self.db.refresh(self.away_team)

    def tearDown(self):
        app.dependency_overrides.clear()
        _LIVE_ALERT_SENT_CACHE.clear()
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_ev_math_and_slip_auto_settlement_roi(self):
        ev_with_odds = BetSlipService.calculate_leg_ev(0.80, 1.50)
        self.assertEqual(ev_with_odds["fair_odds"], 1.25)
        self.assertEqual(ev_with_odds["effective_odds"], 1.50)
        self.assertEqual(ev_with_odds["ev_pct"], 20.0)
        self.assertGreater(ev_with_odds["kelly_fraction"], 0.0)

        ev_fair = BetSlipService.calculate_leg_ev(0.75, None)
        self.assertEqual(ev_fair["effective_odds"], ev_fair["fair_odds"])
        self.assertEqual(ev_fair["ev_pct"], 0.0)

        fix_done = Fixture(
            external_id="ESPN-DONE-1",
            league_id=self.league.id,
            home_team_id=self.home_team.id,
            away_team_id=self.away_team.id,
            match_date=datetime(2026, 11, 10, 19, 45),
            status="FINISHED",
            home_score=2,
            away_score=1,
        )
        fix_post = Fixture(
            external_id="ESPN-POST-1",
            league_id=self.league.id,
            home_team_id=self.home_team.id,
            away_team_id=self.away_team.id,
            match_date=datetime(2026, 11, 11, 19, 45),
            status="POSTPONED",
        )
        self.db.add_all([fix_done, fix_post])
        self.db.commit()
        self.db.refresh(fix_done)
        self.db.refresh(fix_post)

        # Winning slip (+50% return at 1.50 odds on $100)
        win_slip = BetSlipService.create_slip(
            self.db,
            title="Arsenal Over 1.5",
            source="manual",
            stake=100.0,
            legs=[{
                "fixture_id": fix_done.id,
                "match_label": "Arsenal vs Chelsea",
                "league_name": "Premier League",
                "market": "Over 1.5 Goals",
                "model_prob": 0.80,
                "bookmaker_odds": 1.50,
            }],
        )
        self.assertEqual(win_slip["status"], "WON")
        self.assertEqual(win_slip["payout"], 150.0)
        self.assertEqual(win_slip["profit_loss"], 50.0)

        # Losing slip (Under 2.5 when final is 2-1)
        lose_slip = BetSlipService.create_slip(
            self.db,
            title="Arsenal Under 2.5",
            source="manual",
            stake=20.0,
            legs=[{
                "fixture_id": fix_done.id,
                "match_label": "Arsenal vs Chelsea",
                "league_name": "Premier League",
                "market": "Under 2.5 Goals",
                "model_prob": 0.60,
                "bookmaker_odds": 2.00,
            }],
        )
        self.assertEqual(lose_slip["status"], "LOST")
        self.assertEqual(lose_slip["profit_loss"], -20.0)

        # Postponed VOID refund slip
        void_slip = BetSlipService.create_slip(
            self.db,
            title="Postponed Leg",
            source="manual",
            stake=30.0,
            legs=[{
                "fixture_id": fix_post.id,
                "match_label": "Arsenal vs Chelsea",
                "league_name": "Premier League",
                "market": "Over 1.5 Goals",
                "model_prob": 0.78,
                "bookmaker_odds": 1.35,
            }],
        )
        self.assertEqual(void_slip["status"], "VOID")
        self.assertEqual(void_slip["payout"], 30.0)
        self.assertEqual(void_slip["profit_loss"], 0.0)

        # Live +1 More Goal baseline check (snapshot taken at 1-1 => 2 goals, final 2-1 => 3 goals => WON)
        live_win = BetSlipService.create_slip(
            self.db,
            title="Live +1 Goal",
            source="live_radar",
            stake=10.0,
            legs=[{
                "fixture_id": fix_done.id,
                "match_label": "Arsenal vs Chelsea",
                "league_name": "Premier League",
                "market": "At Least 1 More Goal",
                "model_prob": 0.75,
                "bookmaker_odds": 1.60,
                "baseline_goals": 2,
            }],
        )
        self.assertEqual(live_win["status"], "WON")
        self.assertEqual(live_win["profit_loss"], 6.0)

        report = BetSlipService.get_slips_and_summary(self.db)
        summary = report["summary"]
        self.assertEqual(summary["won_slips"], 2)
        self.assertEqual(summary["lost_slips"], 1)
        self.assertEqual(summary["void_slips"], 1)
        self.assertAlmostEqual(summary["net_profit"], 36.0, places=2)
        self.assertEqual(summary["max_drawdown"], 20.0)

    def test_alert_preferences_bounds_quiet_hours_and_gating(self):
        prefs = AlertPreferencesService.get_or_create_preferences(self.db)
        self.assertTrue(prefs["telegram_live_enabled"])
        self.assertEqual(prefs["min_live_confidence"], 70)

        updated = AlertPreferencesService.update_preferences(
            self.db,
            {
                "min_live_probability": 0.99,  # clamps to 0.95
                "min_live_confidence": 30,     # clamps to 50
                "min_time_remaining": 120.0,   # clamps to 60.0
                "quiet_hours_start": 22,
                "quiet_hours_end": 6,
                "allowed_leagues": ["Premier League"],
                "allowed_markets": ["At Least 1 More Goal"],
            },
        )
        self.assertEqual(updated["min_live_probability"], 0.95)
        self.assertEqual(updated["min_live_confidence"], 50)
        self.assertEqual(updated["min_time_remaining"], 60.0)

        self.assertTrue(AlertPreferencesService.is_within_quiet_hours(23, 22, 6))
        self.assertTrue(AlertPreferencesService.is_within_quiet_hours(2, 22, 6))
        self.assertFalse(AlertPreferencesService.is_within_quiet_hours(14, 22, 6))

        fix = Fixture(
            external_id="ESPN-LIVE-GATE",
            league_id=self.league.id,
            home_team_id=self.home_team.id,
            away_team_id=self.away_team.id,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None),
            status="LIVE",
            home_score=1,
            away_score=1,
        )
        self.db.add(fix)
        self.db.commit()
        self.db.refresh(fix)
        fix.league = self.league
        fix.home_team = self.home_team
        fix.away_team = self.away_team

        sample_intel = {
            "identity_status": "IDENTITY_VALID",
            "freshness_status": "FRESH",
            "confidence": {"overall_confidence": 82},
            "match_state": {"minute": 64, "period": "2H", "home_score": 1, "away_score": 1},
            "best_live_signal": {
                "category": "goals",
                "market": "At Least 1 More Goal",
                "signal_strength": "STRONG",
                "probability": 0.81,
                "time_remaining_minutes": 26.0,
                "confidence_score": 82,
            },
            "live_signals": [],
            "live_goals": {"remaining_xg": {"total": 1.35}, "pre_match_xg": {"total": 2.9}},
        }

        open_prefs = {
            "telegram_live_enabled": True,
            "quiet_hours_start": None,
            "quiet_hours_end": None,
            "min_live_probability": 0.75,
            "min_live_confidence": 75,
            "min_time_remaining": 15.0,
            "allowed_leagues": ["Premier League"],
            "allowed_markets": ["At Least 1 More Goal"],
        }
        cand = _evaluate_live_goal_alert_candidate(fix, sample_intel, prefs=open_prefs)
        self.assertIsNotNone(cand)

        # League mismatch blocks signal
        blocked_league = {**open_prefs, "allowed_leagues": ["La Liga"]}
        self.assertIsNone(_evaluate_live_goal_alert_candidate(fix, sample_intel, prefs=blocked_league))

        # Market mismatch blocks signal
        blocked_mkt = {**open_prefs, "allowed_markets": ["Full Match Over 3.5 Goals"]}
        self.assertIsNone(_evaluate_live_goal_alert_candidate(fix, sample_intel, prefs=blocked_mkt))

        # Telegram toggle off blocks signal
        blocked_tg = {**open_prefs, "telegram_live_enabled": False}
        self.assertIsNone(_evaluate_live_goal_alert_candidate(fix, sample_intel, prefs=blocked_tg))

    def test_live_snapshot_settlement_across_markets_and_buckets(self):
        fix = Fixture(
            external_id="ESPN-SNAP-DONE",
            league_id=self.league.id,
            home_team_id=self.home_team.id,
            away_team_id=self.away_team.id,
            match_date=datetime(2026, 11, 10, 15, 0),
            status="FINISHED",
            home_score=2,
            away_score=1,
        )
        self.db.add(fix)
        self.db.commit()
        self.db.refresh(fix)

        snap = LivePredictionSnapshot(
            fixture_id=fix.id,
            model_version="v1_live_intelligence",
            prediction_timestamp=datetime(2026, 11, 10, 15, 35),
            match_minute=34,
            period="1H",
            home_score=1,
            away_score=0,
            goals_prediction_json=json.dumps({
                "at_least_1_more_goal": {"probability": 0.84},
                "full_match_over_1_5": {"probability": 0.86},
                "full_match_over_2_5": {"probability": 0.64},
                "btts_yes": {"probability": 0.61},
            }),
            best_live_signal_json=json.dumps({
                "category": "goals",
                "market": "Full Match Over 2.5 Goals",
                "signal_strength": "STRONG",
                "probability": 0.64,
            }),
            confidence=82,
            data_quality=90,
        )
        self.db.add(snap)
        self.db.commit()

        summary = ModelEvaluationService.settle_pending_live_snapshots(self.db, limit=25)
        self.assertEqual(summary["fixtures_checked"], 1)
        self.assertEqual(summary["evaluations_created"], 5)

        # Idempotent re-run creates 0 duplicates
        summary2 = ModelEvaluationService.settle_pending_live_snapshots(self.db, limit=25)
        self.assertEqual(summary2["fixtures_checked"], 0)
        self.assertEqual(summary2["evaluations_created"], 0)

        perf = LiveModelEvaluationService.evaluate_live_performance(self.db)
        self.assertEqual(perf["settled_evaluations"], 5)
        self.assertEqual(perf["settled_fixtures_count"], 1)
        self.assertEqual(perf["overall_accuracy"], 1.0)
        mkts = {m["market"] for m in perf["market_breakdown"]}
        self.assertIn("live_at_least_1_more_goal", mkts)
        self.assertIn("live_over_1_5", mkts)
        self.assertIn("live_over_2_5", mkts)
        self.assertIn("live_btts_yes", mkts)
        self.assertIn("live_strong_signal", mkts)

    def test_betslips_and_alert_preferences_endpoints(self):
        fix = Fixture(
            external_id="ESPN-API-1",
            league_id=self.league.id,
            home_team_id=self.home_team.id,
            away_team_id=self.away_team.id,
            match_date=datetime(2026, 11, 12, 18, 0),
            status="SCHEDULED",
        )
        self.db.add(fix)
        self.db.commit()
        self.db.refresh(fix)

        create_res = self.client.post(
            "/api/betslips",
            json={
                "title": "API Tracked Single",
                "source": "manual",
                "stake": 25.0,
                "legs": [{
                    "fixture_id": fix.id,
                    "match_label": "Arsenal vs Chelsea",
                    "league_name": "Premier League",
                    "market": "Over 1.5 Goals",
                    "model_prob": 0.78,
                    "bookmaker_odds": 1.45,
                }],
            },
        )
        self.assertEqual(create_res.status_code, 200)
        slip_id = create_res.json()["slip"]["id"]

        list_res = self.client.get("/api/betslips")
        self.assertEqual(list_res.status_code, 200)
        self.assertEqual(len(list_res.json()["slips"]), 1)
        self.assertEqual(list_res.json()["slips"][0]["status"], "PENDING")

        # Update fixture to finished & explicitly trigger settlement
        fix.status = "FINISHED"
        fix.home_score = 3
        fix.away_score = 0
        self.db.commit()

        settle_res = self.client.post("/api/betslips/settle")
        self.assertEqual(settle_res.status_code, 200)
        self.assertEqual(settle_res.json()["settled_count"], 1)
        self.assertEqual(settle_res.json()["slips"][0]["status"], "WON")

        del_res = self.client.delete(f"/api/betslips/{slip_id}")
        self.assertEqual(del_res.status_code, 200)
        self.assertEqual(self.client.delete(f"/api/betslips/{slip_id}").status_code, 404)

        get_prefs = self.client.get("/api/alerts/preferences")
        self.assertEqual(get_prefs.status_code, 200)
        self.assertIn("min_live_probability", get_prefs.json()["preferences"])

        with patch("main.settings.ENVIRONMENT", "production"), patch("main.settings.ADMIN_API_KEY", "secret-key"):
            unauth = self.client.put("/api/alerts/preferences", json={"min_live_probability": 0.80})
            self.assertIn(unauth.status_code, (401, 403))

            auth_ok = self.client.put(
                "/api/alerts/preferences",
                json={"min_live_probability": 0.80, "quiet_hours_start": 1, "quiet_hours_end": 5},
                headers={"X-Admin-Key": "secret-key"},
            )
            self.assertEqual(auth_ok.status_code, 200)
            self.assertEqual(auth_ok.json()["preferences"]["min_live_probability"], 0.80)
            self.assertEqual(auth_ok.json()["preferences"]["quiet_hours_start"], 1)


if __name__ == "__main__":
    unittest.main()
