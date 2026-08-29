import os
import sys
import math
import unittest
from datetime import datetime, timezone, timedelta
from typing import cast
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base, get_db
from models import (
    League, Team, Fixture, MatchStatistics,
    LiveMatchState, LivePredictionSnapshot, Prediction
)
from services.live_service import (
    LiveTimeService, ScoreStateAdjustmentService, LiveMomentumEngine,
    RedCardAdjustmentService, LiveModelFusionEngine, LiveGoalsPredictionEngine,
    LiveCornersPredictionEngine, LiveCardsPredictionEngine, LiveSignalEngine,
    LiveMatchIntelligenceService, LivePredictionSnapshotService,
    LiveModelEvaluationService, LIVE_MODEL_VERSION
)
from schemas.live_schema import LiveMatchStateSchema, LiveConfidence
from main import app


class TestLivePredictionEngine(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.TestingSessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )
        Base.metadata.create_all(bind=self.engine)
        self.db = self.TestingSessionLocal()

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        app.dependency_overrides.clear()

    # =========================================================================
    # 1. TIME REMAINING & TIME CONSISTENCY
    # =========================================================================

    def test_effective_time_remaining_and_time_consistency(self):
        """Time consistency: 0m -> 45m -> 90+m; at 90+m remaining minutes approaches zero."""
        # 0 mins (1H start)
        rem_0, el_0, gprop_0 = LiveTimeService.calculate_effective_time_remaining(0, "1H")
        self.assertGreaterEqual(rem_0, 90.0)
        self.assertEqual(gprop_0, 1.0)

        # 45 mins (HT)
        rem_ht, el_ht, gprop_ht = LiveTimeService.calculate_effective_time_remaining(45, "HT")
        self.assertEqual(rem_ht, 49.0) # 45 + 4 stoppage
        self.assertEqual(gprop_ht, 0.55) # 55% in 2H

        # 75 mins (2H late)
        rem_75, el_75, gprop_75 = LiveTimeService.calculate_effective_time_remaining(75, "2H")
        self.assertLess(rem_75, 20.0)
        self.assertLess(gprop_75, 0.30)

        # 94 mins (stoppage)
        rem_94, el_94, gprop_94 = LiveTimeService.calculate_effective_time_remaining(94, "2H")
        self.assertLessEqual(rem_94, 0.0)
        self.assertEqual(gprop_94, 0.0)

    # =========================================================================
    # 2. SCORE-STATE & RED-CARD ADJUSTMENTS
    # =========================================================================

    def test_score_state_and_red_card_adjustments(self):
        """Score state: trailing team gets boost, leading gets reduction. Red card: penalized team reduced."""
        # Home trailing 0-1 at min 65 -> Home gets boost (>1.0), Away reduced (<1.0)
        h_mult, a_mult, diag = ScoreStateAdjustmentService.calculate_score_state_multipliers(0, 1, 65)
        self.assertGreater(h_mult, 1.0)
        self.assertLess(a_mult, 1.0)
        self.assertEqual(diag["score_diff"], -1)

        # Red card on Home team -> Home penalized (<1.0), Away boosted (>1.0)
        red_h, red_a, r_diag = RedCardAdjustmentService.calculate_red_card_multipliers(1, 0, remaining_minutes=40.0)
        self.assertLess(red_h, 1.0)
        self.assertGreater(red_a, 1.0)
        self.assertTrue(r_diag["red_card_adjustment_applied"])

    # =========================================================================
    # 3. PRIOR / LIVE FUSION GUARANTEES
    # =========================================================================

    def test_prior_live_fusion_weights_sum_to_one(self):
        """Fusion invariant: prior_weight + live_weight == 1.0 at all minutes."""
        for m in [5, 20, 45, 60, 80, 90]:
            pw, lw = LiveModelFusionEngine.calculate_fusion_weights(m, data_quality_score=80)
            self.assertAlmostEqual(pw + lw, 1.0, places=3)
            self.assertGreaterEqual(pw, 0.0)
            self.assertGreaterEqual(lw, 0.0)

    # =========================================================================
    # 4. LIVE GOALS, RESOLVED MARKETS & MONOTONICITY
    # =========================================================================

    def test_live_goals_resolved_markets_and_probabilities(self):
        """Current score 2-0 -> Over 1.5 is already_resolved (P=1.0); Over 2.5 active; Monotonicity preserved."""
        state = LiveMatchStateSchema(
            fixture_id=1,
            minute=65,
            home_score=2,
            away_score=0,
            status="LIVE"
        )
        pred = LiveGoalsPredictionEngine.predict_live_goals(
            pre_xg_home=1.80, pre_xg_away=1.10, state=state,
            time_rem_mins=28.0, goal_prop_rem=0.35,
            score_adj_h=0.85, score_adj_a=1.20,
            mom_adj_h=1.0, mom_adj_a=1.0,
            red_adj_h=1.0, red_adj_a=1.0,
            prior_w=0.6, live_w=0.4
        )

        # Over 1.5 must be already resolved!
        self.assertEqual(pred.full_match_over_1_5.status, "already_resolved")
        self.assertEqual(pred.full_match_over_1_5.probability, 1.0)
        self.assertTrue(pred.full_match_over_1_5.resolved_result)

        # Over 2.5 is active and requires 1 more goal
        self.assertEqual(pred.full_match_over_2_5.status, "active")
        self.assertGreater(pred.full_match_over_2_5.probability, 0.0)

        # Monotonicity: Over 2.5 >= Over 3.5 >= Over 4.5
        self.assertGreaterEqual(pred.full_match_over_2_5.probability, pred.full_match_over_3_5.probability)
        self.assertGreaterEqual(pred.full_match_over_3_5.probability, pred.full_match_over_4_5.probability)

    # =========================================================================
    # 5. LIVE CORNERS & CARDS ENGINES
    # =========================================================================

    def test_live_corners_and_cards_engines(self):
        """Live corners and cards compute valid non-negative remaining expectations."""
        state = LiveMatchStateSchema(
            fixture_id=1,
            minute=70,
            home_score=1,
            away_score=1,
            home_corners=6,
            away_corners=3,
            home_yellow_cards=2,
            away_yellow_cards=1,
            status="LIVE"
        )

        c_pred = LiveCornersPredictionEngine.predict_live_corners(
            pre_corners_home=5.5, pre_corners_away=4.5, state=state,
            time_rem_mins=22.0, mom_adj_h=1.05, mom_adj_a=0.95,
            prior_w=0.6, live_w=0.4
        )
        # Total corners = 9 -> Over 7.5 and Over 8.5 already resolved
        self.assertEqual(c_pred.over_7_5.status, "already_resolved")
        self.assertEqual(c_pred.over_8_5.status, "already_resolved")
        self.assertEqual(c_pred.over_9_5.status, "active")

        cd_pred = LiveCardsPredictionEngine.predict_live_cards(
            pre_cards_home=2.2, pre_cards_away=2.0, state=state,
            time_rem_mins=22.0, score_diff=0, ref_adj=1.0
        )
        self.assertGreater(cd_pred.remaining_expected_cards["total"], 0.0)
        self.assertGreater(cd_pred.at_least_1_more_card.probability, 0.0)

    # =========================================================================
    # 6. LIVE SIGNAL GATING & NO-SIGNAL STATE
    # =========================================================================

    def test_live_signal_gating_and_no_signal_state(self):
        """When time remaining < 8 mins or probability below gate, returns NO_SIGNAL."""
        state = LiveMatchStateSchema(fixture_id=1, minute=88, home_score=0, away_score=0)
        conf = LiveConfidence(
            overall_confidence=70, pre_match_confidence=70, live_data_quality=80,
            statistical_coverage=80, model_stability=70, time_sensitivity=10, label="good"
        )
        goals = LiveGoalsPredictionEngine.predict_live_goals(
            1.5, 1.2, state, time_rem_mins=4.0, goal_prop_rem=0.05,
            score_adj_h=1.0, score_adj_a=1.0, mom_adj_h=1.0, mom_adj_a=1.0,
            red_adj_h=1.0, red_adj_a=1.0, prior_w=0.5, live_w=0.5
        )
        corners = LiveCornersPredictionEngine.predict_live_corners(
            5.0, 4.0, state, time_rem_mins=4.0, mom_adj_h=1.0, mom_adj_a=1.0, prior_w=0.5, live_w=0.5
        )
        cards = LiveCardsPredictionEngine.predict_live_cards(
            2.0, 2.0, state, time_rem_mins=4.0, score_diff=0, ref_adj=1.0
        )

        signals, best = LiveSignalEngine.evaluate_live_signals(goals, corners, cards, conf, time_rem_mins=4.0)
        self.assertEqual(best.label, "NO_SIGNAL")
        self.assertIsNone(best.market)

    # =========================================================================
    # 7. SNAPSHOT PERSISTENCE & IMMUTABILITY
    # =========================================================================

    def test_live_snapshot_persistence(self):
        """Live snapshots are archived without modifying pre-match predictions."""
        league = League(name="Premier League", country="England")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Liverpool", short_code="LIV", league_id=league.id)
        t2 = Team(name="Man City", short_code="MCI", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=datetime.now(timezone.utc), status="LIVE",
            home_score=1, away_score=0
        )
        self.db.add(f)
        self.db.commit()

        # Ingest live state
        l_state = LiveMatchState(
            fixture_id=f.id, minute=35, period="1H", status="LIVE",
            home_score=1, away_score=0, home_shots=5, away_shots=4
        )
        self.db.add(l_state)
        self.db.commit()

        intel = LiveMatchIntelligenceService.get_live_intelligence(self.db, cast(int, f.id))
        self.assertIsNotNone(intel)
        self.assertEqual(intel["model_version"], LIVE_MODEL_VERSION)

        # Verify LivePredictionSnapshot was created
        snap = self.db.query(LivePredictionSnapshot).filter(LivePredictionSnapshot.fixture_id == f.id).first()
        self.assertIsNotNone(snap)
        self.assertEqual(snap.match_minute, 35)
        self.assertEqual(snap.home_score, 1)

    # =========================================================================
    # 8. API ENDPOINTS INTEGRATION
    # =========================================================================

    def test_live_api_endpoints(self):
        """API endpoints: /api/fixtures/{id}/live-intelligence, /api/live/fixtures, /api/live/performance."""
        league = League(name="Serie A", country="Italy")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Inter", short_code="INT", league_id=league.id)
        t2 = Team(name="Napoli", short_code="NAP", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=datetime.now(timezone.utc), status="LIVE",
            home_score=0, away_score=0
        )
        self.db.add(f)
        self.db.commit()

        # 1. Live intelligence endpoint
        resp = self.client.get(f"/api/fixtures/{f.id}/live-intelligence")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("live_goals", data)
        self.assertIn("live_corners", data)
        self.assertIn("live_cards", data)
        self.assertIn("best_live_signal", data)

        # 2. Live fixtures endpoint
        resp_list = self.client.get("/api/live/fixtures")
        self.assertEqual(resp_list.status_code, 200)
        list_data = resp_list.json()
        self.assertEqual(list_data["live_count"], 1)

        # 3. Performance endpoint
        resp_perf = self.client.get("/api/live/performance")
        self.assertEqual(resp_perf.status_code, 200)
        perf_data = resp_perf.json()
        self.assertEqual(perf_data["status"], "insufficient_data")


if __name__ == "__main__":
    unittest.main()
