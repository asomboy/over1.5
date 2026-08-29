import os
import sys
import math
import unittest
from typing import cast
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base, get_db
from models import League, Team, Fixture, HistoricalResult, Prediction
from services.prediction_service import DixonColesPredictionEngine, HalfPredictionEngine, MODEL_VERSION
from main import app


class TestMatchIntelligenceCore(unittest.TestCase):

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

    def test_1x2_probabilities_sum_to_one(self):
        """Rule 1: Home Win + Draw + Away Win ≈ 1.0"""
        for lh, la in [(1.5, 1.2), (0.8, 2.5), (2.8, 0.4), (1.1, 1.1), (3.5, 3.2)]:
            matrix, grid_size = DixonColesPredictionEngine.calculate_score_matrix(lh, la)
            markets = DixonColesPredictionEngine.derive_markets_from_matrix(matrix, grid_size)
            result = markets["result"]
            total_prob = result["home_win"] + result["draw"] + result["away_win"]
            self.assertAlmostEqual(total_prob, 1.0, places=3, msg=f"Failed for lambda=({lh}, {la})")

    def test_over_under_lines_sum_to_one(self):
        """Rule 2: Over + Under for each line (0.5, 1.5, 2.5, 3.5) ≈ 1.0"""
        for lh, la in [(1.8, 1.3), (0.5, 0.5), (3.0, 2.0)]:
            matrix, grid_size = DixonColesPredictionEngine.calculate_score_matrix(lh, la)
            markets = DixonColesPredictionEngine.derive_markets_from_matrix(matrix, grid_size)
            goals = markets["goals"]

            self.assertAlmostEqual(goals["over_0_5"] + goals["under_0_5"], 1.0, places=3)
            self.assertAlmostEqual(goals["over_1_5"] + goals["under_1_5"], 1.0, places=3)
            self.assertAlmostEqual(goals["over_2_5"] + goals["under_2_5"], 1.0, places=3)
            self.assertAlmostEqual(goals["over_3_5"] + goals["under_3_5"], 1.0, places=3)

    def test_btts_probabilities_sum_to_one(self):
        """Rule 3: BTTS Yes + BTTS No ≈ 1.0"""
        for lh, la in [(1.6, 1.4), (0.6, 2.8), (1.0, 0.4)]:
            matrix, grid_size = DixonColesPredictionEngine.calculate_score_matrix(lh, la)
            markets = DixonColesPredictionEngine.derive_markets_from_matrix(matrix, grid_size)
            btts = markets["btts"]

            self.assertAlmostEqual(btts["yes"] + btts["no"], 1.0, places=3)

    def test_all_probabilities_in_valid_range(self):
        """Rule 4: All probabilities are strictly between 0.0 and 1.0"""
        matrix, grid_size = DixonColesPredictionEngine.calculate_score_matrix(1.7, 1.1)
        markets = DixonColesPredictionEngine.derive_markets_from_matrix(matrix, grid_size)

        for cat, vals in [("result", markets["result"]), ("goals", markets["goals"]), ("btts", markets["btts"]),
                         ("home_team_goals", markets["home_team_goals"]), ("away_team_goals", markets["away_team_goals"])]:
            for k, prob in vals.items():
                self.assertGreaterEqual(prob, 0.0, f"{cat}.{k} is negative: {prob}")
                self.assertLessEqual(prob, 1.0, f"{cat}.{k} exceeds 1.0: {prob}")

    def test_team_goal_probabilities_decrease_logically(self):
        """Rule 5: Team goal probabilities decrease logically: P(O0.5) >= P(O1.5) >= P(O2.5)"""
        for lh, la in [(2.0, 1.5), (0.7, 1.2), (3.1, 0.8)]:
            matrix, grid_size = DixonColesPredictionEngine.calculate_score_matrix(lh, la)
            markets = DixonColesPredictionEngine.derive_markets_from_matrix(matrix, grid_size)

            h = markets["home_team_goals"]
            self.assertGreaterEqual(h["over_0_5"], h["over_1_5"])
            self.assertGreaterEqual(h["over_1_5"], h["over_2_5"])

            a = markets["away_team_goals"]
            self.assertGreaterEqual(a["over_0_5"], a["over_1_5"])
            self.assertGreaterEqual(a["over_1_5"], a["over_2_5"])

    def test_total_goal_lines_decrease_logically(self):
        """Rule 6: Higher goal lines should not have higher probabilities: P(O0.5) >= P(O1.5) >= P(O2.5) >= P(O3.5)"""
        for lh, la in [(1.4, 1.1), (2.5, 2.0), (0.6, 0.5)]:
            matrix, grid_size = DixonColesPredictionEngine.calculate_score_matrix(lh, la)
            markets = DixonColesPredictionEngine.derive_markets_from_matrix(matrix, grid_size)
            g = markets["goals"]

            self.assertGreaterEqual(g["over_0_5"], g["over_1_5"])
            self.assertGreaterEqual(g["over_1_5"], g["over_2_5"])
            self.assertGreaterEqual(g["over_2_5"], g["over_3_5"])

    def test_invalid_and_extreme_lambda_values_handled_safely(self):
        """Rule 7: Invalid lambda values (0, negative, NaN, Inf, extreme values) are handled safely"""
        invalid_cases = [
            (0.0, 0.0),
            (-1.5, 2.0),
            (float("nan"), 1.5),
            (float("inf"), 1.2),
            (25.0, -10.0),
        ]
        for lh, la in invalid_cases:
            matrix, grid_size = DixonColesPredictionEngine.calculate_score_matrix(lh, la)
            self.assertGreaterEqual(grid_size, 10)
            self.assertEqual(len(matrix), grid_size)
            self.assertEqual(len(matrix[0]), grid_size)

            markets = DixonColesPredictionEngine.derive_markets_from_matrix(matrix, grid_size)
            self.assertIn("home_win", markets["result"])
            self.assertFalse(math.isnan(markets["result"]["home_win"]))
            self.assertFalse(math.isnan(markets["goals"]["over_1_5"]))

    def test_score_matrix_probability_mass_normalized(self):
        """Rule 8: Score matrix probability mass is checked and strictly normalized to 1.0"""
        for lh, la in [(1.2, 0.9), (3.0, 2.5), (0.4, 0.4)]:
            matrix, grid_size = DixonColesPredictionEngine.calculate_score_matrix(lh, la)
            total_sum = sum(matrix[i][j] for i in range(grid_size) for j in range(grid_size))
            self.assertAlmostEqual(total_sum, 1.0, places=6)

    def test_half_by_half_probabilities(self):
        """Rule 9: Half probabilities satisfy monotonicity and valid bounds"""
        half_props = HalfPredictionEngine.calculate_half_proportions(None, None, None, None)
        self.assertEqual(len(half_props), 4)

        halves = HalfPredictionEngine.calculate_half_probabilities(1.8, 1.2, half_props)
        self.assertIn("first_half_over_0_5", halves)
        self.assertIn("first_half_over_1_5", halves)
        self.assertIn("second_half_over_0_5", halves)
        self.assertIn("second_half_over_1_5", halves)

        self.assertGreaterEqual(halves["first_half_over_0_5"], halves["first_half_over_1_5"])
        self.assertGreaterEqual(halves["second_half_over_0_5"], halves["second_half_over_1_5"])

    def test_confidence_system_and_best_signal(self):
        """Rule 10: Multi-factor confidence system and Best Model Signal evaluate deterministically"""
        conf = DixonColesPredictionEngine.calculate_confidence_details(None, None, None)
        self.assertIn("overall", conf)
        self.assertIn("data_quality", conf)
        self.assertIn("model_stability", conf)
        self.assertIn("sample_quality", conf)
        self.assertTrue(0 <= conf["overall"] <= 100)
        self.assertTrue(0 <= conf["data_quality"] <= 100)
        self.assertTrue(0 <= conf["model_stability"] <= 100)

        matrix, grid_size = DixonColesPredictionEngine.calculate_score_matrix(1.9, 1.3)
        markets = DixonColesPredictionEngine.derive_markets_from_matrix(matrix, grid_size)
        signal = DixonColesPredictionEngine.determine_best_market_signal(markets, conf)

        self.assertIn("market", signal)
        self.assertIn("probability", signal)
        self.assertIn("signal_score", signal)
        self.assertIn("label", signal)
        self.assertIn(signal["label"], ["Strong", "Moderate", "Watch"])
        self.assertTrue(0 <= signal["signal_score"] <= 100)

    def test_full_fixture_details_api_schema(self):
        """Rule 11: /api/fixtures/{id}/details endpoint returns full Match Intelligence schema"""
        league = League(name="Premier League", country="England", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        team_a = Team(name="Arsenal", short_code="ARS", league_id=league.id)
        team_b = Team(name="Chelsea", short_code="CHE", league_id=league.id)
        self.db.add_all([team_a, team_b])
        self.db.commit()

        fixture = Fixture(
            league_id=league.id,
            home_team_id=team_a.id,
            away_team_id=team_b.id,
            match_date=datetime.now(timezone.utc),
            status="SCHEDULED",
            venue="Emirates Stadium"
        )
        self.db.add(fixture)
        self.db.commit()

        # Run prediction
        pred = DixonColesPredictionEngine.predict_fixture(self.db, cast(int, fixture.id))
        self.assertIsNotNone(pred)
        self.assertEqual(pred.model_version, MODEL_VERSION)
        self.assertIsNotNone(pred.raw_intelligence_json)

        # Call API endpoint
        resp = self.client.get(f"/api/fixtures/{fixture.id}/details")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["status"], "ok")
        self.assertIn("match_intelligence", data)
        intel = data["match_intelligence"]

        # Validate Match Intelligence Core schema structure
        self.assertEqual(intel["model"]["version"], MODEL_VERSION)
        self.assertIn("expected_goals", intel)
        self.assertIn("result", intel)
        self.assertIn("goals", intel)
        self.assertIn("btts", intel)
        self.assertIn("home_team_goals", intel)
        self.assertIn("away_team_goals", intel)
        self.assertIn("halves", intel)
        self.assertIn("exact_scores", intel)
        self.assertIn("confidence", intel)
        self.assertIn("best_signal", intel)

        # Validate Backward Compatibility in prediction object
        pred_dict = data["prediction"]
        self.assertIn("predicted_home_score", pred_dict)
        self.assertIn("predicted_away_score", pred_dict)
        self.assertIn("expected_goals_xg", pred_dict)
        self.assertIn("over_1_5_probability", pred_dict)
        self.assertIn("over_2_5_probability", pred_dict)
        self.assertIn("btts_probability", pred_dict)
        self.assertIn("confidence_score", pred_dict)
        self.assertIn("most_likely_score", pred_dict)


if __name__ == "__main__":
    unittest.main()
