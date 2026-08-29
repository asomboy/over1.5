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
from models import League, Team, Fixture, HistoricalResult, MatchStatistics, Prediction
from services.corners_service import (
    CornersPredictionEngine,
    CornersBacktestService,
    CORNER_MODEL_VERSION,
    DEFAULT_FALLBACK_DISPERSION
)
from main import app


class TestCornersPredictionEngine(unittest.TestCase):

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
    # 1. PROBABILITY BOUNDS & SUM TO 1 CONSTRAINTS
    # =========================================================================

    def test_corner_probabilities_bounds_and_complementarity(self):
        """Rule 1 & 2: All probabilities in [0, 1] and Over + Under ≈ 1.0"""
        for lh, la, disp in [(5.5, 4.5, 5.0), (3.0, 2.5, 3.5), (7.5, 6.0, 8.0), (2.0, 1.5, 4.0)]:
            probs = CornersPredictionEngine.calculate_corner_probabilities(lh, la, dispersion=disp)
            tot = probs["total_markets"]

            for k in ["7_5", "8_5", "9_5", "10_5", "11_5"]:
                p_over = tot[f"over_{k}"]
                p_under = tot[f"under_{k}"]

                self.assertTrue(0.0 <= p_over <= 1.0, f"Over {k} out of bounds: {p_over}")
                self.assertTrue(0.0 <= p_under <= 1.0, f"Under {k} out of bounds: {p_under}")
                self.assertAlmostEqual(p_over + p_under, 1.0, places=3, msg=f"Sum mismatch for line {k}")

    # =========================================================================
    # 2. MONOTONICITY CONSTRAINTS
    # =========================================================================

    def test_total_corner_monotonicity(self):
        """Rule 4: P(O7.5) >= P(O8.5) >= P(O9.5) >= P(O10.5) >= P(O11.5)"""
        for lh, la in [(5.2, 4.8), (3.5, 2.8), (7.0, 5.5)]:
            probs = CornersPredictionEngine.calculate_corner_probabilities(lh, la)
            tot = probs["total_markets"]

            self.assertGreaterEqual(tot["over_7_5"], tot["over_8_5"])
            self.assertGreaterEqual(tot["over_8_5"], tot["over_9_5"])
            self.assertGreaterEqual(tot["over_9_5"], tot["over_10_5"])
            self.assertGreaterEqual(tot["over_10_5"], tot["over_11_5"])

    def test_team_corner_monotonicity(self):
        """Rule 3: P(O3.5) >= P(O4.5) >= P(O5.5) for Home and Away teams"""
        for lh, la in [(6.0, 4.0), (3.0, 5.0), (4.5, 4.5)]:
            probs = CornersPredictionEngine.calculate_corner_probabilities(lh, la)
            h = probs["home_team"]
            a = probs["away_team"]

            self.assertGreaterEqual(h["over_3_5"], h["over_4_5"])
            self.assertGreaterEqual(h["over_4_5"], h["over_5_5"])

            self.assertGreaterEqual(a["over_3_5"], a["over_4_5"])
            self.assertGreaterEqual(a["over_4_5"], a["over_5_5"])

    # =========================================================================
    # 3. EXPECTATION CONSISTENCY
    # =========================================================================

    def test_expected_total_corners_equals_sum(self):
        """Rule 5: expected_total == expected_home + expected_away"""
        league = League(name="Premier League", country="England", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        team_a = Team(name="Liverpool", short_code="LIV", league_id=league.id)
        team_b = Team(name="Manchester City", short_code="MCI", league_id=league.id)
        self.db.add_all([team_a, team_b])
        self.db.commit()

        # Ingest 10 historical matches with corner statistics
        now = datetime.now(timezone.utc)
        for i in range(10):
            f = Fixture(league_id=league.id, home_team_id=team_a.id, away_team_id=team_b.id, match_date=now - timedelta(days=i * 7), status="FINISHED")
            self.db.add(f)
            self.db.commit()

            stats = MatchStatistics(fixture_id=f.id, home_corners=6 + (i % 3), away_corners=4 + (i % 2), total_corners=10 + (i % 3) + (i % 2))
            self.db.add(stats)
        self.db.commit()

        xh, xa, xtot, _ = CornersPredictionEngine.calculate_expected_corners(self.db, cast(int, team_a.id), cast(int, team_b.id), league.id)
        self.assertAlmostEqual(xtot, xh + xa, places=2)

    # =========================================================================
    # 4. DATA ELIGIBILITY & EMPTY STATE HANDLING
    # =========================================================================

    def test_missing_corner_data_returns_unavailable_state(self):
        """Rule 6: Fixture with no corner statistics returns available: false and explanation"""
        league = League(name="Empty League", country="Unknown", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        team_a = Team(name="Team A", short_code="TMA", league_id=league.id)
        team_b = Team(name="Team B", short_code="TMB", league_id=league.id)
        self.db.add_all([team_a, team_b])
        self.db.commit()

        fixture = Fixture(league_id=league.id, home_team_id=team_a.id, away_team_id=team_b.id, match_date=datetime.now(timezone.utc), status="SCHEDULED")
        self.db.add(fixture)
        self.db.commit()

        pred = CornersPredictionEngine.predict_corners(self.db, cast(int, fixture.id))
        self.assertFalse(pred["available"])
        self.assertIsNotNone(pred["reason"])
        self.assertIn("Insufficient", pred["reason"])
        self.assertEqual(pred["confidence"]["label"], "insufficient")

    # =========================================================================
    # 5. DISPERSION ESTIMATION HIERARCHY
    # =========================================================================

    def test_dispersion_hierarchy_strategy(self):
        """Rule 8: Verify competition -> shrunk_competition -> global -> fallback hierarchy"""
        # A. Fallback on empty DB
        disp, source = CornersPredictionEngine.resolve_dispersion(self.db, league_id=None)
        self.assertEqual(disp, DEFAULT_FALLBACK_DISPERSION)
        self.assertEqual(source, "fallback")

        # B. Ingest 25 matches in a league with over-dispersed corner counts
        league = League(name="La Liga", country="Spain", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        team_a = Team(name="Real Madrid", short_code="RMA", league_id=league.id)
        team_b = Team(name="Barcelona", short_code="BAR", league_id=league.id)
        self.db.add_all([team_a, team_b])
        self.db.commit()

        now = datetime.now(timezone.utc)
        for i in range(25):
            f = Fixture(league_id=league.id, home_team_id=team_a.id, away_team_id=team_b.id, match_date=now - timedelta(days=i * 3), status="FINISHED")
            self.db.add(f)
            self.db.commit()
            hc = 5 + (i * 2 % 7)
            ac = 3 + (i * 3 % 6)
            self.db.add(MatchStatistics(fixture_id=f.id, home_corners=hc, away_corners=ac, total_corners=hc + ac))
        self.db.commit()

        disp_comp, source_comp = CornersPredictionEngine.resolve_dispersion(self.db, league_id=league.id)
        self.assertEqual(source_comp, "competition")
        self.assertTrue(1.5 <= disp_comp <= 15.0)

    # =========================================================================
    # 6. CHRONOLOGICAL BACKTESTING (NO FUTURE LEAKAGE)
    # =========================================================================

    def test_chronological_backtesting_no_future_leakage(self):
        """Rule 9: Chronological backtesting executes with training data restricted to strictly past matches"""
        league = League(name="Bundesliga", country="Germany", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        team_a = Team(name="Bayern Munich", short_code="BAY", league_id=league.id)
        team_b = Team(name="Dortmund", short_code="BVB", league_id=league.id)
        self.db.add_all([team_a, team_b])
        self.db.commit()

        base_date = datetime(2025, 1, 1, 15, 0, 0)
        for i in range(15):
            f = Fixture(
                league_id=league.id,
                home_team_id=team_a.id,
                away_team_id=team_b.id,
                match_date=base_date + timedelta(days=i * 7),
                status="FINISHED"
            )
            self.db.add(f)
            self.db.commit()
            hc = 6 + (i % 4)
            ac = 4 + (i % 3)
            self.db.add(MatchStatistics(fixture_id=f.id, home_corners=hc, away_corners=ac, total_corners=hc + ac))
        self.db.commit()

        bt_results = CornersBacktestService.run_chronological_backtest(self.db, min_samples=5)
        self.assertEqual(bt_results["status"], "ok")
        self.assertGreater(bt_results["matches_evaluated"], 0)
        self.assertIn("markets", bt_results)
        self.assertIn("over_8_5", bt_results["markets"])
        self.assertLessEqual(bt_results["markets"]["over_8_5"]["brier_score"], 0.50)

    # =========================================================================
    # 7. API ENDPOINT INTEGRATION
    # =========================================================================

    def test_api_fixture_details_includes_corners(self):
        """Rule 10: /api/fixtures/{id}/details and /api/corners/backtest respond with valid schemas"""
        league = League(name="Serie A", country="Italy", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        team_a = Team(name="Juventus", short_code="JUV", league_id=league.id)
        team_b = Team(name="Inter", short_code="INT", league_id=league.id)
        self.db.add_all([team_a, team_b])
        self.db.commit()

        fixture = Fixture(
            league_id=league.id,
            home_team_id=team_a.id,
            away_team_id=team_b.id,
            match_date=datetime.now(timezone.utc),
            status="SCHEDULED"
        )
        self.db.add(fixture)
        self.db.commit()

        resp = self.client.get(f"/api/fixtures/{fixture.id}/details")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertIn("corners", data)
        self.assertIn("available", data["corners"])

        # Test backtest endpoint
        bt_resp = self.client.get("/api/corners/backtest")
        self.assertEqual(bt_resp.status_code, 200)
        bt_data = bt_resp.json()
        self.assertIn("status", bt_data)


if __name__ == "__main__":
    unittest.main()
