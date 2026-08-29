import os
import sys
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
    League, Team, Fixture, HistoricalResult, MatchStatistics,
    Prediction, CornerPredictionSnapshot, CardPredictionSnapshot,
    LivePredictionSnapshot, ModelEvaluation
)
from services.market_resolution_service import MarketResolutionService
from services.model_evaluation_service import ModelEvaluationService
from main import app


class TestModelEvaluation(unittest.TestCase):

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
    # 1. UNIVERSAL MARKET RESOLUTION
    # =========================================================================

    def test_universal_market_resolution(self):
        """Validates exact binary resolution across Goals, 1X2, BTTS, Corners, Cards, and Red Cards."""
        # 2-1 Scoreline, 6 Corners (3-3), 5 Cards (2-2 yellows, 1-0 red)
        res = MarketResolutionService.resolve_market_outcome

        # Goals
        self.assertEqual(res("over_1_5_goals", 2, 1), 1.0)
        self.assertEqual(res("over_2_5_goals", 2, 1), 1.0)
        self.assertEqual(res("over_3_5_goals", 2, 1), 0.0)
        self.assertEqual(res("btts_yes", 2, 1), 1.0)
        self.assertEqual(res("btts_no", 2, 1), 0.0)
        self.assertEqual(res("1x2_home", 2, 1), 1.0)
        self.assertEqual(res("1x2_draw", 2, 1), 0.0)
        self.assertEqual(res("1x2_away", 2, 1), 0.0)

        # Corners
        self.assertEqual(res("over_7_5_corners", 2, 1, home_corners=5, away_corners=4), 1.0) # 9 corners
        self.assertEqual(res("over_9_5_corners", 2, 1, home_corners=5, away_corners=4), 0.0)

        # Cards
        self.assertEqual(res("over_3_5_cards", 2, 1, home_yellow=2, away_yellow=2, home_red=1, away_red=0), 1.0) # 5 cards
        self.assertEqual(res("any_red_card", 2, 1, home_yellow=2, away_yellow=2, home_red=1, away_red=0), 1.0)

    # =========================================================================
    # 2. AUTOMATED EVALUATION WORKFLOW & IDEMPOTENCY
    # =========================================================================

    def test_evaluate_finished_fixture_and_idempotency(self):
        """evaluate_finished_fixture links snapshots to outcomes and is strictly idempotent."""
        league = League(name="La Liga", country="Spain")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Real Madrid", league_id=league.id)
        t2 = Team(name="Barcelona", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        fixture = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=datetime.now(timezone.utc), status="FINISHED",
            home_score=2, away_score=1
        )
        self.db.add(fixture)
        self.db.commit()

        # Add pre-match prediction
        self.db.add(Prediction(
            fixture_id=fixture.id,
            predicted_home_score=1.8,
            predicted_away_score=1.2,
            over_1_5_probability=0.85,
            over_2_5_probability=0.62,
            btts_probability=0.58,
            home_win_probability=0.55,
            draw_probability=0.25,
            away_win_probability=0.20
        ))

        # Add pre-match corner snapshot
        self.db.add(CornerPredictionSnapshot(
            fixture_id=fixture.id,
            model_version="v1_corners_nb",
            expected_home_corners=5.5,
            expected_away_corners=4.2,
            expected_total_corners=9.7,
            over_7_5_prob=0.82,
            under_7_5_prob=0.18,
            over_8_5_prob=0.70,
            under_8_5_prob=0.30,
            over_9_5_prob=0.55,
            under_9_5_prob=0.45,
            over_10_5_prob=0.38,
            under_10_5_prob=0.62,
            over_11_5_prob=0.22,
            under_11_5_prob=0.78
        ))

        # Add pre-match card snapshot
        self.db.add(CardPredictionSnapshot(
            fixture_id=fixture.id,
            model_version="v1_cards_nb",
            expected_home_cards=2.1,
            expected_away_cards=2.4,
            expected_total_cards=4.5,
            over_1_5_prob=0.95,
            under_1_5_prob=0.05,
            over_2_5_prob=0.85,
            under_2_5_prob=0.15,
            over_3_5_prob=0.65,
            under_3_5_prob=0.35,
            over_4_5_prob=0.45,
            under_4_5_prob=0.55,
            over_5_5_prob=0.25,
            under_5_5_prob=0.75,
            over_6_5_prob=0.12,
            under_6_5_prob=0.88,
            any_red_card_prob=0.18
        ))

        self.db.add(HistoricalResult(fixture_id=fixture.id, home_score=2, away_score=1, total_goals=3))
        self.db.add(MatchStatistics(
            fixture_id=fixture.id, home_corners=5, away_corners=4,
            home_yellow_cards=2, away_yellow_cards=2, home_red_cards=0, away_red_cards=0
        ))
        self.db.commit()

        # 1. First evaluation run
        count_1 = ModelEvaluationService.evaluate_finished_fixture(self.db, cast(int, fixture.id))
        self.assertGreater(count_1, 0)
        total_evals_1 = self.db.query(ModelEvaluation).count()

        # 2. Second evaluation run (idempotency check)
        count_2 = ModelEvaluationService.evaluate_finished_fixture(self.db, cast(int, fixture.id))
        total_evals_2 = self.db.query(ModelEvaluation).count()

        self.assertEqual(total_evals_1, total_evals_2)

    # =========================================================================
    # 3. LEADERBOARD, DRIFT & API ENDPOINTS
    # =========================================================================

    def test_model_evaluation_api_endpoints(self):
        """API endpoints: /api/models/performance, /api/models/leaderboard, /api/models/calibration, /api/models/drift, /api/models/status."""
        league = League(name="Bundesliga", country="Germany")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Bayern", league_id=league.id)
        t2 = Team(name="Dortmund", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=datetime.now(timezone.utc), status="FINISHED")
        self.db.add(f)
        self.db.commit()

        # Add 1 verified evaluation record
        self.db.add(ModelEvaluation(
            fixture_id=f.id,
            prediction_type="goals",
            market="over_1_5_goals",
            model_version="v2_match_intelligence",
            prediction_timestamp=datetime.now(timezone.utc),
            predicted_probability=0.82,
            actual_outcome=1.0,
            brier_component=0.0324,
            log_loss_component=0.198,
            verified=True
        ))
        self.db.commit()

        # 1. Performance endpoint
        resp_perf = self.client.get("/api/models/performance")
        self.assertEqual(resp_perf.status_code, 200)
        data_perf = resp_perf.json()
        self.assertEqual(data_perf["sample_size"], 1)

        # 2. Leaderboard endpoint
        resp_lb = self.client.get("/api/models/leaderboard")
        self.assertEqual(resp_lb.status_code, 200)
        data_lb = resp_lb.json()
        self.assertIn("leaderboard", data_lb)
        self.assertEqual(len(data_lb["leaderboard"]), 1)

        # 3. Status endpoint
        resp_st = self.client.get("/api/models/status")
        self.assertEqual(resp_st.status_code, 200)
        data_st = resp_st.json()
        self.assertIn("goals_model", data_st)

        # 4. Drift endpoint
        resp_dr = self.client.get("/api/models/drift")
        self.assertEqual(resp_dr.status_code, 200)
        data_dr = resp_dr.json()
        self.assertEqual(data_dr["status"], "INSUFFICIENT_DATA")


if __name__ == "__main__":
    unittest.main()
