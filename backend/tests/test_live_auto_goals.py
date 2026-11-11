import asyncio
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base, get_db
from main import (
    _LIVE_ALERT_SENT_CACHE,
    _evaluate_live_goal_alert_candidate,
    _mark_live_goal_alert_sent,
    _should_send_live_goal_alert,
    app,
)
from models import (
    Fixture,
    League,
    LiveMatchState,
    LivePredictionSnapshot,
    Prediction,
    Team,
)
from schemas.live_schema import (
    LiveCardsPrediction,
    LiveConfidence,
    LiveCornersPrediction,
    LiveGoalsPrediction,
    LiveMarketProbability,
)
from services.live_provider_service import LiveProviderAdapterService
from services.live_service import LiveSignalEngine
from services.telegram_service import TelegramNotificationService


class TestLiveAutoGoals(unittest.TestCase):
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
            external_id="ESPN-T-1", name="Arsenal & Co", short_code="ARS"
        )
        self.away_team = Team(
            external_id="ESPN-T-2", name="Liverpool FC", short_code="LIV"
        )
        self.db.add_all([self.league, self.home_team, self.away_team])
        self.db.commit()
        self.db.refresh(self.league)
        self.db.refresh(self.home_team)
        self.db.refresh(self.away_team)

    def tearDown(self):
        _LIVE_ALERT_SENT_CACHE.clear()
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        app.dependency_overrides.clear()

    def _create_fixture(
        self,
        ext_id: str,
        status: str = "SCHEDULED",
        minutes_offset: int = -45,
        home_score=None,
        away_score=None,
        live_clock=None,
    ) -> Fixture:
        kickoff = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
            minutes=minutes_offset
        )
        fix = Fixture(
            external_id=ext_id,
            league_id=self.league.id,
            home_team_id=self.home_team.id,
            away_team_id=self.away_team.id,
            match_date=kickoff,
            status=status,
            home_score=home_score,
            away_score=away_score,
            live_clock=live_clock,
        )
        self.db.add(fix)
        self.db.commit()
        self.db.refresh(fix)
        return fix

    def test_sync_scoreboard_live_fixtures_transitions_and_postponed_safety(self):
        """Global ESPN scoreboard scan promotes live games, closes finished, and preserves POSTPONED."""
        fix_live = self._create_fixture("ESPN-FIX-901", status="SCHEDULED")
        fix_done = self._create_fixture("ESPN-FIX-902", status="LIVE")
        fix_postponed = self._create_fixture(
            "ESPN-FIX-903", status="SCHEDULED", live_clock="TBD"
        )

        mock_payload = {
            "events": [
                {
                    "id": "901",
                    "status": {
                        "displayClock": "64'",
                        "type": {"state": "in", "name": "STATUS_SECOND_HALF"},
                    },
                    "competitions": [
                        {
                            "competitors": [
                                {"homeAway": "home", "score": "1"},
                                {"homeAway": "away", "score": "1"},
                            ]
                        }
                    ],
                },
                {
                    "id": "902",
                    "status": {
                        "displayClock": "FT",
                        "type": {
                            "state": "post",
                            "completed": True,
                            "name": "STATUS_FULL_TIME",
                        },
                    },
                    "competitions": [
                        {
                            "competitors": [
                                {"homeAway": "home", "score": "2"},
                                {"homeAway": "away", "score": "0"},
                            ]
                        }
                    ],
                },
                {
                    "id": "903",
                    "status": {
                        "type": {"state": "post", "name": "STATUS_POSTPONED"},
                    },
                    "competitions": [],
                },
            ]
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_payload
        mock_http = MagicMock()
        mock_http.get.return_value = mock_resp

        fake_circuit = MagicMock()
        fake_circuit.can_execute.return_value = True

        with patch(
            "services.live_provider_service.CircuitBreakerService.get_circuit",
            return_value=fake_circuit,
        ):
            summary = LiveProviderAdapterService.sync_scoreboard_live_fixtures(
                self.db, client=mock_http
            )

        self.db.refresh(fix_live)
        self.db.refresh(fix_done)
        self.db.refresh(fix_postponed)

        self.assertEqual(summary["transitioned_live"], 1)
        self.assertEqual(summary["transitioned_finished"], 1)
        self.assertIn(fix_live.id, summary["discovered_live_ids"])

        self.assertEqual(fix_live.status, "LIVE")
        self.assertEqual(fix_live.live_clock, "64'")
        self.assertEqual(fix_live.home_score, 1)
        self.assertEqual(fix_live.away_score, 1)

        self.assertEqual(fix_done.status, "FINISHED")
        self.assertEqual(fix_done.live_clock, "FT")
        self.assertEqual(fix_done.home_score, 2)
        self.assertEqual(fix_done.away_score, 0)

        self.assertEqual(fix_postponed.status, "POSTPONED")
        self.assertIsNone(fix_postponed.live_clock)

    def test_fetch_live_summary_promotes_fixture_to_live(self):
        """Per-fixture ESPN summary sync updates Fixture.status, live_clock, and scores."""
        fix = self._create_fixture("ESPN-FIX-950", status="SCHEDULED")

        summary_json = {
            "header": {
                "id": "950",
                "league": {
                    "name": "Premier League",
                    "country": {"name": "England"},
                },
                "competitions": [
                    {
                        "id": "950",
                        "date": fix.match_date.strftime("%Y-%m-%dT%H:%MZ"),
                        "status": {
                            "clock": 52,
                            "displayClock": "52'",
                            "period": 2,
                            "type": {
                                "state": "in",
                                "completed": False,
                                "name": "STATUS_SECOND_HALF",
                            },
                        },
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "1",
                                "team": {"id": "ESPN-T-1", "displayName": "Arsenal & Co"},
                            },
                            {
                                "homeAway": "away",
                                "score": "0",
                                "team": {"id": "ESPN-T-2", "displayName": "Liverpool FC"},
                            },
                        ],
                    }
                ],
            },
            "boxscore": {"teams": []},
            "keyEvents": [],
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = summary_json
        mock_http = MagicMock()
        mock_http.get.return_value = mock_resp

        fake_circuit = MagicMock()
        fake_circuit.can_execute.return_value = True

        with patch(
            "services.live_provider_service.CircuitBreakerService.get_circuit",
            return_value=fake_circuit,
        ):
            res = LiveProviderAdapterService.fetch_live_summary(
                self.db, fix.id, client=mock_http
            )

        self.db.refresh(fix)
        self.assertEqual(res["status"], "LIVE")
        self.assertEqual(fix.status, "LIVE")
        self.assertEqual(fix.live_clock, "52'")
        self.assertEqual(fix.home_score, 1)
        self.assertEqual(fix.away_score, 0)

    def test_live_signal_engine_detects_active_over_lines_and_skips_resolved(self):
        """LiveSignalEngine promotes unresolved Full Match Over 1.5/2.5 goals while skipping hit lines."""
        goals = LiveGoalsPrediction(
            pre_match_xg={"home": 1.6, "away": 1.2, "total": 2.8},
            remaining_xg={"home": 0.9, "away": 0.8, "total": 1.7},
            current_score={"home": 1, "away": 0},
            at_least_1_more_goal=LiveMarketProbability(probability=0.81, status="active"),
            at_least_2_more_goals=LiveMarketProbability(probability=0.52, status="active"),
            full_match_over_1_5=LiveMarketProbability(probability=0.81, status="active"),
            full_match_over_2_5=LiveMarketProbability(probability=0.52, status="active"),
            full_match_over_3_5=LiveMarketProbability(probability=0.25, status="active"),
            full_match_over_4_5=LiveMarketProbability(probability=0.10, status="active"),
            btts_yes=LiveMarketProbability(probability=0.64, status="active"),
            btts_no=LiveMarketProbability(probability=0.36, status="active"),
            home_to_score_again=LiveMarketProbability(probability=0.55, status="active"),
            away_to_score_again=LiveMarketProbability(probability=0.64, status="active"),
            next_goal={"home": 0.43, "away": 0.38, "none": 0.19},
        )
        conf = LiveConfidence(
            overall_confidence=80,
            pre_match_confidence=78,
            live_data_quality=85,
            statistical_coverage=80,
            model_stability=82,
            time_sensitivity=84,
            label="High",
        )
        low_prob = LiveMarketProbability(probability=0.35, status="active")
        empty_corners = LiveCornersPrediction(
            pre_match_expected_corners=9.0,
            current_corners={"home": 2, "away": 2, "total": 4},
            remaining_expected_corners={"home": 2.5, "away": 2.0, "total": 4.5},
            over_7_5=low_prob,
            over_8_5=low_prob,
            over_9_5=low_prob,
            over_10_5=low_prob,
            over_11_5=low_prob,
            next_corner={"home": 0.45, "away": 0.40, "none": 0.15},
        )
        empty_cards = LiveCardsPrediction(
            current_cards={"home": 1, "away": 1, "total": 2},
            remaining_expected_cards={"home": 1.0, "away": 1.0, "total": 2.0},
            at_least_1_more_card=low_prob,
            over_current_plus_1_5=low_prob,
            over_current_plus_2_5=low_prob,
            next_card={"home": 0.4, "away": 0.4, "none": 0.2},
            any_red_card=LiveMarketProbability(probability=0.10, status="active"),
            referee_tendency=None,
        )

        items, best = LiveSignalEngine.evaluate_live_signals(
            goals=goals,
            corners=empty_corners,
            cards=empty_cards,
            confidence=conf,
            time_rem_mins=38.0,
            fresh_status="FRESH",
            identity_status="IDENTITY_VALID",
            fixture_status="LIVE",
        )

        markets = [i.market for i in items]
        self.assertIn("Full Match Over 1.5 Goals", markets)
        self.assertEqual(best.label, "STRONG")
        self.assertTrue(any(i.signal_strength == "STRONG" for i in items))

        # Already resolved lines must never emit live betting signals
        goals_resolved = goals.model_copy(
            update={
                "at_least_1_more_goal": LiveMarketProbability(probability=0.50, status="active"),
                "full_match_over_1_5": LiveMarketProbability(probability=1.0, status="already_resolved"),
            }
        )
        items2, _ = LiveSignalEngine.evaluate_live_signals(
            goals=goals_resolved,
            corners=empty_corners,
            cards=empty_cards,
            confidence=conf,
            time_rem_mins=25.0,
            fresh_status="FRESH",
            identity_status="IDENTITY_VALID",
            fixture_status="LIVE",
        )
        markets2 = [i.market for i in items2]
        self.assertNotIn("Full Match Over 1.5 Goals", markets2)

    def test_cached_live_endpoints_and_upcoming_live_overlay(self):
        """Cached snapshots power /api/live/fixtures & /api/fixtures/upcoming with 0 external HTTP calls."""
        fix = self._create_fixture(
            "ESPN-FIX-990",
            status="LIVE",
            minutes_offset=-62,
            home_score=1,
            away_score=1,
            live_clock="62'",
        )
        pred = Prediction(
            fixture_id=fix.id,
            predicted_home_score=1.7,
            predicted_away_score=1.2,
            expected_goals_xg=2.9,
            home_win_probability=0.44,
            draw_probability=0.28,
            away_win_probability=0.28,
            over_1_5_probability=0.84,
            over_2_5_probability=0.61,
            btts_probability=0.63,
            confidence_score=78.0,
        )
        st = LiveMatchState(
            fixture_id=fix.id,
            minute=62,
            period="2H",
            home_score=1,
            away_score=1,
            status="LIVE",
        )
        live_goals_dict = {
            "pre_match_xg": {"home": 1.7, "away": 1.2, "total": 2.9},
            "remaining_xg": {"home": 0.65, "away": 0.55, "total": 1.20},
            "current_score": {"home": 1, "away": 1},
            "at_least_1_more_goal": {"probability": 0.78, "status": "active"},
            "at_least_2_more_goals": {"probability": 0.36, "status": "active"},
            "full_match_over_1_5": {"probability": 1.0, "status": "already_resolved"},
            "full_match_over_2_5": {"probability": 0.78, "status": "active"},
            "full_match_over_3_5": {"probability": 0.36, "status": "active"},
            "btts_yes": {"probability": 1.0, "status": "already_resolved"},
            "next_goal": {"home": 0.42, "away": 0.36, "none": 0.22},
        }
        best_sig_dict = {
            "market": "Full Match Over 2.5 Goals",
            "category": "goals",
            "probability": 0.78,
            "time_remaining_minutes": 32.0,
            "signal_strength": "STRONG",
        }
        snap = LivePredictionSnapshot(
            fixture_id=fix.id,
            match_minute=62,
            period="2H",
            home_score=1,
            away_score=1,
            goals_prediction_json=json.dumps(live_goals_dict),
            corners_prediction_json=json.dumps({}),
            cards_prediction_json=json.dumps({}),
            best_live_signal_json=json.dumps(best_sig_dict),
            confidence=82,
            data_quality=90,
            prediction_timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        self.db.add_all([pred, st, snap])
        self.db.commit()

        with patch(
            "services.live_provider_service.LiveProviderAdapterService.fetch_live_summary",
            side_effect=AssertionError("Should serve from DB snapshot cache without provider HTTP"),
        ):
            live_resp = self.client.get("/api/live/fixtures")
            self.assertEqual(live_resp.status_code, 200)
            body = live_resp.json()
            self.assertEqual(body["live_count"], 1)
            item = body["fixtures"][0]
            self.assertEqual(item["fixture_id"], fix.id)
            self.assertEqual(item["live_clock"], "62'")
            self.assertAlmostEqual(item["pre_match_over_1_5"], 0.84, places=2)
            self.assertEqual(item["best_signal"]["market"], "Full Match Over 2.5 Goals")
            self.assertAlmostEqual(
                item["live_goals"]["at_least_1_more_goal"]["probability"], 0.78, places=2
            )

            upcoming_resp = self.client.get("/api/fixtures/upcoming")
            self.assertEqual(upcoming_resp.status_code, 200)
            u_items = upcoming_resp.json()["data"]
            matched = next((x for x in u_items if x["id"] == fix.id), None)
            self.assertIsNotNone(matched)
            self.assertEqual(matched["status"], "LIVE")
            self.assertIsNotNone(matched["live"])
            self.assertTrue(matched["live"]["is_live"])
            self.assertTrue(matched["live"]["available"])
            self.assertAlmostEqual(
                matched["live"]["live_goals"]["full_match_over_2_5"]["probability"],
                0.78,
                places=2,
            )

    def test_telegram_live_goal_alert_gating_dedup_and_formatting(self):
        """High-confidence live goal signals pass gating, deduplicate per fixture+market, and escape HTML."""
        fix = self._create_fixture(
            "ESPN-FIX-995",
            status="LIVE",
            minutes_offset=-58,
            home_score=1,
            away_score=1,
            live_clock="58'",
        )
        intel = {
            "identity_status": "IDENTITY_VALID",
            "freshness_status": "FRESH",
            "match_state": {
                "minute": 58,
                "period": "2H",
                "home_score": 1,
                "away_score": 1,
            },
            "confidence": {"overall_confidence": 84},
            "best_live_signal": {
                "market": "At Least 1 More Goal",
                "category": "goals",
                "probability": 0.79,
                "confidence_score": 84,
                "time_remaining_minutes": 36.0,
                "signal_strength": "STRONG",
            },
            "live_signals": [
                {
                    "market": "At Least 1 More Goal",
                    "category": "goals",
                    "probability": 0.79,
                    "confidence_score": 84,
                    "time_remaining_minutes": 36.0,
                    "signal_strength": "STRONG",
                }
            ],
            "live_goals": {
                "pre_match_xg": {"total": 2.95},
                "remaining_xg": {"total": 1.38},
                "at_least_1_more_goal": {"probability": 0.79, "status": "active"},
                "full_match_over_2_5": {"probability": 0.79, "status": "active"},
            },
        }

        cand = _evaluate_live_goal_alert_candidate(fix, intel)
        self.assertIsNotNone(cand)
        fid, market, kwargs = cand
        self.assertEqual(fid, fix.id)
        self.assertEqual(market, "At Least 1 More Goal")
        self.assertTrue(_should_send_live_goal_alert(fid, market))

        # Stale feed or low time remaining must be blocked
        stale_intel = dict(intel, freshness_status="STALE")
        self.assertIsNone(_evaluate_live_goal_alert_candidate(fix, stale_intel))

        # Test HTML escaping and async dispatch
        with patch.object(
            TelegramNotificationService,
            "send_message",
            new=AsyncMock(return_value=True),
        ) as mocked_send:
            ok = asyncio.run(
                TelegramNotificationService.broadcast_live_goal_signal(**kwargs)
            )
            self.assertTrue(ok)
            mocked_send.assert_awaited_once()
            sent_html = mocked_send.call_args.args[0]
            self.assertIn("Arsenal &amp; Co 1 - 1 Liverpool FC", sent_html)
            self.assertIn("LIVE GOAL ALERT", sent_html)
            self.assertIn("At Least 1 More Goal", sent_html)

        # Marking sent deduplicates future ticks for the same fixture & market
        _mark_live_goal_alert_sent(fid, market)
        self.assertFalse(_should_send_live_goal_alert(fid, market))
        self.assertIsNone(_evaluate_live_goal_alert_candidate(fix, intel))


if __name__ == "__main__":
    unittest.main()
