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
from models import League, Team, Fixture, HistoricalResult, Prediction, TeamStatistics, LeagueStatistics
from services.prediction_service import (
    DixonColesPredictionEngine,
    HalfPredictionEngine,
    MODEL_VERSION,
    MIN_RHO,
    MAX_RHO,
    DEFAULT_FALLBACK_RHO,
    TARGET_CAPTURED_MASS,
    MAX_SCORE_GRID
)
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

    # =========================================================================
    # AREA 1: DIXON-COLES RHO STRATEGY & TAU POSITIVITY TESTS
    # =========================================================================

    def test_rho_fallback_strategy_on_empty_db(self):
        """Verify fallback rho (-0.11) and source 'fallback' when no historical match data exists."""
        rho_val, rho_source = DixonColesPredictionEngine.resolve_rho_strategy(self.db, league_id=None)
        self.assertEqual(rho_val, DEFAULT_FALLBACK_RHO)
        self.assertEqual(rho_source, "fallback")

    def test_rho_competition_estimation(self):
        """Verify competition-specific rho estimation when >= 30 historical matches exist."""
        league = League(name="Premier League", country="England", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        team_a = Team(name="Arsenal", short_code="ARS", league_id=league.id)
        team_b = Team(name="Chelsea", short_code="CHE", league_id=league.id)
        self.db.add_all([team_a, team_b])
        self.db.commit()

        # Ingest 35 historical results with realistic score distribution
        fixtures = []
        results = []
        now = datetime.now(timezone.utc)
        for i in range(35):
            f = Fixture(league_id=league.id, home_team_id=team_a.id, away_team_id=team_b.id, match_date=now, status="FINISHED")
            fixtures.append(f)
        self.db.add_all(fixtures)
        self.db.commit()

        for idx, f in enumerate(fixtures):
            h_score = 1 if idx % 3 == 0 else (2 if idx % 3 == 1 else 0)
            a_score = 1 if idx % 3 == 0 else (0 if idx % 3 == 1 else 1)
            r = HistoricalResult(fixture_id=f.id, home_score=h_score, away_score=a_score, total_goals=h_score + a_score)
            results.append(r)
        self.db.add_all(results)
        self.db.commit()

        rho_val, rho_source = DixonColesPredictionEngine.resolve_rho_strategy(self.db, league_id=league.id)
        self.assertEqual(rho_source, "competition")
        self.assertGreaterEqual(rho_val, MIN_RHO)
        self.assertLessEqual(rho_val, MAX_RHO)

    def test_rho_shrunk_competition_estimation(self):
        """Verify shrunk competition rho when 10 <= matches < 30."""
        league = League(name="Serie A", country="Italy", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        team_a = Team(name="Inter", short_code="INT", league_id=league.id)
        team_b = Team(name="Milan", short_code="MIL", league_id=league.id)
        self.db.add_all([team_a, team_b])
        self.db.commit()

        fixtures = [Fixture(league_id=league.id, home_team_id=team_a.id, away_team_id=team_b.id, match_date=datetime.now(timezone.utc), status="FINISHED") for _ in range(15)]
        self.db.add_all(fixtures)
        self.db.commit()

        results = [HistoricalResult(fixture_id=f.id, home_score=1, away_score=1, total_goals=2) for f in fixtures]
        self.db.add_all(results)
        self.db.commit()

        rho_val, rho_source = DixonColesPredictionEngine.resolve_rho_strategy(self.db, league_id=league.id)
        self.assertEqual(rho_source, "shrunk_competition")
        self.assertTrue(MIN_RHO <= rho_val <= MAX_RHO)

    def test_dixon_coles_tau_positivity(self):
        """Verify all Dixon-Coles tau adjustments remain strictly positive under extreme inputs."""
        for rho in [-0.25, -0.15, -0.11, 0.0, 0.05, -0.30, 0.10]:
            for lh, la in [(0.05, 0.05), (1.5, 1.2), (3.5, 2.8), (6.0, 5.0)]:
                for x, y in [(0, 0), (1, 0), (0, 1), (1, 1), (2, 2)]:
                    tau = DixonColesPredictionEngine._dixon_coles_tau(x, y, lh, la, rho=rho)
                    self.assertGreater(tau, 0.0, f"Tau failed positivity: tau={tau} for ({x},{y}) with rho={rho}, lh={lh}, la={la}")

    # =========================================================================
    # AREA 2: ADAPTIVE SCORE MATRIX TAIL MASS TESTS
    # =========================================================================

    def test_adaptive_matrix_expansion_normal_lambda(self):
        """Verify score matrix captures >= 0.999 probability mass for standard expected goals."""
        matrix, grid_size, diag = DixonColesPredictionEngine.calculate_score_matrix(1.65, 1.25)
        self.assertGreaterEqual(diag["captured_mass"], TARGET_CAPTURED_MASS)
        self.assertTrue(diag["target_met"])
        self.assertLessEqual(grid_size, MAX_SCORE_GRID)

        # Exact normalization check
        total_prob = sum(matrix[i][j] for i in range(grid_size) for j in range(grid_size))
        self.assertAlmostEqual(total_prob, 1.0, places=6)

    def test_adaptive_matrix_expansion_high_lambda(self):
        """Verify score matrix adaptively expands for high expected goals (e.g. 4.2 vs 3.5)."""
        matrix, grid_size, diag = DixonColesPredictionEngine.calculate_score_matrix(4.2, 3.5)
        self.assertGreaterEqual(diag["captured_mass"], TARGET_CAPTURED_MASS)
        self.assertTrue(diag["target_met"])
        self.assertGreater(grid_size, 10)  # Must have expanded beyond min grid

        total_prob = sum(matrix[i][j] for i in range(grid_size) for j in range(grid_size))
        self.assertAlmostEqual(total_prob, 1.0, places=6)

    def test_adaptive_matrix_expansion_extreme_lambda_safety(self):
        """Verify extreme expected goals (e.g. 8.0 vs 7.0) expand safely within MAX_SCORE_GRID=25 without errors."""
        matrix, grid_size, diag = DixonColesPredictionEngine.calculate_score_matrix(8.0, 7.0)
        self.assertLessEqual(grid_size, MAX_SCORE_GRID)
        self.assertGreater(grid_size, 15)
        self.assertIsInstance(diag["tail_mass"], float)
        self.assertFalse(math.isnan(diag["captured_mass"]))

        total_prob = sum(matrix[i][j] for i in range(grid_size) for j in range(grid_size))
        self.assertAlmostEqual(total_prob, 1.0, places=6)

    # =========================================================================
    # AREA 3: BEST MODEL SIGNAL REWORK TESTS
    # =========================================================================

    def test_trivial_markets_never_selected_as_best_signal(self):
        """Verify trivial high-probability markets (Over 0.5, Team Over 0.5) are NEVER chosen as Best Signal."""
        matrix, grid_size, _ = DixonColesPredictionEngine.calculate_score_matrix(3.5, 2.5)
        markets = DixonColesPredictionEngine.derive_markets_from_matrix(matrix, grid_size)
        
        # Even though Over 0.5 is > 99%, it must NOT be selected
        conf = {"overall": 85, "data_quality": 85, "model_stability": 85}
        signal = DixonColesPredictionEngine.determine_best_market_signal(markets, conf)

        self.assertIsNotNone(signal["market"])
        self.assertNotEqual(signal["market"], "Over 0.5 Goals")
        self.assertNotEqual(signal["market"], "Home Team Over 0.5")
        self.assertNotEqual(signal["market"], "Away Team Over 0.5")
        self.assertIn(signal["market"], ["Over 1.5 Goals", "Over 2.5 Goals", "Over 3.5 Goals", "Both Teams To Score", "Home Win", "Away Win", "Home Team Over 1.5", "Away Team Over 1.5"])

    def test_low_confidence_results_in_explicit_no_signal(self):
        """Verify that weak confidence or poor data quality returns market=None and label='Watch'."""
        matrix, grid_size, _ = DixonColesPredictionEngine.calculate_score_matrix(2.0, 1.5)
        markets = DixonColesPredictionEngine.derive_markets_from_matrix(matrix, grid_size)

        # Inadequate data quality / confidence
        weak_conf = {"overall": 30, "data_quality": 25, "model_stability": 30}
        signal = DixonColesPredictionEngine.determine_best_market_signal(markets, weak_conf)

        self.assertIsNone(signal["market"])
        self.assertEqual(signal["probability"], 0.0)
        self.assertEqual(signal["signal_score"], 0)
        self.assertEqual(signal["label"], "Watch")

    def test_strong_eligible_candidate_selection(self):
        """Verify high-conviction eligible candidate achieves 'Strong' signal label."""
        matrix, grid_size, _ = DixonColesPredictionEngine.calculate_score_matrix(2.2, 1.4)
        markets = DixonColesPredictionEngine.derive_markets_from_matrix(matrix, grid_size)

        strong_conf = {"overall": 88, "data_quality": 85, "model_stability": 88}
        signal = DixonColesPredictionEngine.determine_best_market_signal(markets, strong_conf)

        self.assertIsNotNone(signal["market"])
        self.assertGreaterEqual(signal["signal_score"], 80)
        self.assertEqual(signal["label"], "Strong")

    # =========================================================================
    # AREA 4: HALF-BY-HALF MATHEMATICAL CONSISTENCY TESTS
    # =========================================================================

    def test_half_expectation_exact_sum_consistency(self):
        """Verify expected_first_half_goals + expected_second_half_goals == expected_full_match_goals."""
        for lh, la in [(1.8, 1.2), (2.5, 0.9), (0.7, 1.5), (3.2, 2.8), (1.1, 1.1)]:
            half_props = HalfPredictionEngine.calculate_half_proportions(None, None, None, None)
            halves = HalfPredictionEngine.calculate_half_probabilities(lh, la, half_props)
            
            full_match_xg = round(lh + la, 2)
            calculated_half_sum = round(halves["first_half_xg"] + halves["second_half_xg"], 2)
            self.assertAlmostEqual(
                calculated_half_sum, full_match_xg, delta=0.02,
                msg=f"Half sum mismatch for ({lh}, {la}): 1H={halves['first_half_xg']}, 2H={halves['second_half_xg']}, full={full_match_xg}"
            )

    def test_half_probabilities_monotonicity_and_bounds(self):
        """Verify 0 <= P <= 1 and P(Over 0.5) >= P(Over 1.5) for both halves."""
        for lh, la in [(1.4, 1.0), (3.0, 2.0), (0.5, 0.4)]:
            half_props = HalfPredictionEngine.calculate_half_proportions(None, None, None, None)
            halves = HalfPredictionEngine.calculate_half_probabilities(lh, la, half_props)

            for key in ["first_half_over_0_5", "first_half_over_1_5", "second_half_over_0_5", "second_half_over_1_5"]:
                self.assertTrue(0.0 <= halves[key] <= 1.0, f"Half prob {key} out of bounds: {halves[key]}")

            self.assertGreaterEqual(halves["first_half_over_0_5"], halves["first_half_over_1_5"])
            self.assertGreaterEqual(halves["second_half_over_0_5"], halves["second_half_over_1_5"])

    # =========================================================================
    # AREA 5: DERIVED MARKETS MATHEMATICAL TESTS
    # =========================================================================

    def test_1x2_probabilities_sum_to_one(self):
        """Rule 1: Home Win + Draw + Away Win ≈ 1.0"""
        for lh, la in [(1.5, 1.2), (0.8, 2.5), (2.8, 0.4), (1.1, 1.1), (3.5, 3.2)]:
            matrix, grid_size, _ = DixonColesPredictionEngine.calculate_score_matrix(lh, la)
            markets = DixonColesPredictionEngine.derive_markets_from_matrix(matrix, grid_size)
            result = markets["result"]
            total_prob = result["home_win"] + result["draw"] + result["away_win"]
            self.assertAlmostEqual(total_prob, 1.0, places=3)

    def test_over_under_lines_sum_to_one(self):
        """Rule 2: Over + Under for each line ≈ 1.0"""
        for lh, la in [(1.8, 1.3), (0.5, 0.5), (3.0, 2.0)]:
            matrix, grid_size, _ = DixonColesPredictionEngine.calculate_score_matrix(lh, la)
            markets = DixonColesPredictionEngine.derive_markets_from_matrix(matrix, grid_size)
            goals = markets["goals"]

            self.assertAlmostEqual(goals["over_0_5"] + goals["under_0_5"], 1.0, places=3)
            self.assertAlmostEqual(goals["over_1_5"] + goals["under_1_5"], 1.0, places=3)
            self.assertAlmostEqual(goals["over_2_5"] + goals["under_2_5"], 1.0, places=3)
            self.assertAlmostEqual(goals["over_3_5"] + goals["under_3_5"], 1.0, places=3)

    def test_btts_probabilities_sum_to_one(self):
        """Rule 3: BTTS Yes + BTTS No ≈ 1.0"""
        for lh, la in [(1.6, 1.4), (0.6, 2.8), (1.0, 0.4)]:
            matrix, grid_size, _ = DixonColesPredictionEngine.calculate_score_matrix(lh, la)
            markets = DixonColesPredictionEngine.derive_markets_from_matrix(matrix, grid_size)
            btts = markets["btts"]

            self.assertAlmostEqual(btts["yes"] + btts["no"], 1.0, places=3)

    def test_team_goal_probabilities_decrease_logically(self):
        """Rule 4: Team goal probabilities decrease logically: P(O0.5) >= P(O1.5) >= P(O2.5)"""
        for lh, la in [(2.0, 1.5), (0.7, 1.2), (3.1, 0.8)]:
            matrix, grid_size, _ = DixonColesPredictionEngine.calculate_score_matrix(lh, la)
            markets = DixonColesPredictionEngine.derive_markets_from_matrix(matrix, grid_size)

            h = markets["home_team_goals"]
            self.assertGreaterEqual(h["over_0_5"], h["over_1_5"])
            self.assertGreaterEqual(h["over_1_5"], h["over_2_5"])

            a = markets["away_team_goals"]
            self.assertGreaterEqual(a["over_0_5"], a["over_1_5"])
            self.assertGreaterEqual(a["over_1_5"], a["over_2_5"])

    def test_total_goal_lines_decrease_logically(self):
        """Rule 5: P(O0.5) >= P(O1.5) >= P(O2.5) >= P(O3.5) >= P(O4.5)"""
        for lh, la in [(1.4, 1.1), (2.5, 2.0), (0.6, 0.5)]:
            matrix, grid_size, _ = DixonColesPredictionEngine.calculate_score_matrix(lh, la)
            markets = DixonColesPredictionEngine.derive_markets_from_matrix(matrix, grid_size)
            g = markets["goals"]

            self.assertGreaterEqual(g["over_0_5"], g["over_1_5"])
            self.assertGreaterEqual(g["over_1_5"], g["over_2_5"])
            self.assertGreaterEqual(g["over_2_5"], g["over_3_5"])
            self.assertGreaterEqual(g["over_3_5"], g["over_4_5"])

    # =========================================================================
    # AREA 6: API ENDPOINTS & BACKWARD COMPATIBILITY TESTS
    # =========================================================================

    def test_api_fixture_details_schema_and_backward_compatibility(self):
        """Verify API response contains both new Match Intelligence Core and legacy backward compatible keys."""
        league = League(name="La Liga", country="Spain", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        team_a = Team(name="Real Madrid", short_code="RMA", league_id=league.id)
        team_b = Team(name="Barcelona", short_code="BAR", league_id=league.id)
        self.db.add_all([team_a, team_b])
        self.db.commit()

        fixture = Fixture(
            league_id=league.id,
            home_team_id=team_a.id,
            away_team_id=team_b.id,
            match_date=datetime.now(timezone.utc),
            status="SCHEDULED",
            venue="Santiago Bernabeu"
        )
        self.db.add(fixture)
        self.db.commit()

        pred = DixonColesPredictionEngine.predict_fixture(self.db, cast(int, fixture.id))
        self.assertIsNotNone(pred)

        resp = self.client.get(f"/api/fixtures/{fixture.id}/details")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["status"], "ok")
        self.assertIn("match_intelligence", data)
        intel = data["match_intelligence"]

        # Validate Model metadata with rho tracking
        self.assertEqual(intel["model"]["version"], MODEL_VERSION)
        self.assertIn("rho", intel["model"])
        self.assertIn("rho_source", intel["model"])

        # Validate Legacy backward compatibility keys
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
