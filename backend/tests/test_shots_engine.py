import os
import sys
import unittest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base
from models import (
    Fixture, League, Team, MatchStatistics, ShotPredictionSnapshot
)
from services.shots_prediction_service import ShotsPredictionEngine, ShotsFeatureService
from services.data_reconciliation_service import DataReconciliationService
from services.unified_match_intelligence_service import UnifiedMatchIntelligenceService


class TestShotsEngine(unittest.TestCase):

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

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_shots_pmf_and_monotonicity(self):
        """Validates discrete Negative Binomial PMF, captured mass, and strict market monotonicity."""
        league = League(name="Premier League", country="England")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Arsenal", league_id=league.id)
        t2 = Team(name="Chelsea", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=datetime.now(timezone.utc), status="SCHEDULED"
        )
        self.db.add(f)
        self.db.commit()

        pred = ShotsPredictionEngine.predict_shots(self.db, f.id)

        # Assert structure
        self.assertEqual(pred["status"], "AVAILABLE")
        self.assertEqual(pred["model_version"], "v1_shots_nb")
        self.assertGreater(pred["shots"]["expected_total_shots"], 0.0)
        self.assertGreater(pred["shots_on_target"]["expected_total_sot"], 0.0)

        # Captured mass
        self.assertGreaterEqual(pred["diagnostics"]["captured_probability_mass"], 0.98)

        # Monotonicity test for Total Shots
        shots_probs = pred["shots"]["probabilities"]
        o15 = shots_probs["over_15_5"]
        o17 = shots_probs["over_17_5"]
        o19 = shots_probs["over_19_5"]
        o21 = shots_probs["over_21_5"]
        o23 = shots_probs["over_23_5"]
        o25 = shots_probs["over_25_5"]
        o27 = shots_probs["over_27_5"]

        self.assertGreaterEqual(o15, o17)
        self.assertGreaterEqual(o17, o19)
        self.assertGreaterEqual(o19, o21)
        self.assertGreaterEqual(o21, o23)
        self.assertGreaterEqual(o23, o25)
        self.assertGreaterEqual(o25, o27)

        # Monotonicity test for Shots on Target
        sot_probs = pred["shots_on_target"]["probabilities"]
        so2 = sot_probs["over_2_5"]
        so3 = sot_probs["over_3_5"]
        so4 = sot_probs["over_4_5"]
        so5 = sot_probs["over_5_5"]
        so6 = sot_probs["over_6_5"]

        self.assertGreaterEqual(so2, so3)
        self.assertGreaterEqual(so3, so4)
        self.assertGreaterEqual(so4, so5)
        self.assertGreaterEqual(so5, so6)

    def test_statistical_sanity_validation_for_shots(self):
        """Rejects negative shots and invalid SoT > shots observations."""
        # Negative shots
        res1 = DataReconciliationService.validate_match_statistics_bounds(
            home_shots=-2, away_shots=10
        )
        self.assertFalse(res1["valid"])
        self.assertIn("Negative home_shots", res1["errors"][0])

        # SoT > Total Shots
        res2 = DataReconciliationService.validate_match_statistics_bounds(
            home_shots=8, home_shots_on_target=12
        )
        self.assertFalse(res2["valid"])
        self.assertIn("home_shots_on_target (12) exceeds home_shots (8)", res2["errors"][0])

    def test_live_shots_already_resolved(self):
        """Resolves market to 1.0 when observed live shots exceed threshold."""
        league = League(name="Bundesliga", country="Germany")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Bayern", league_id=league.id)
        t2 = Team(name="Dortmund", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=datetime.now(timezone.utc), status="LIVE"
        )
        self.db.add(f)
        self.db.commit()

        # Add match statistics showing 18 total shots at minute 65
        self.db.add(MatchStatistics(
            fixture_id=f.id,
            home_shots=11,
            away_shots=7,
            home_shots_on_target=5,
            away_shots_on_target=3
        ))
        self.db.commit()

        live_pred = ShotsPredictionEngine.predict_live_shots(self.db, f.id)
        self.assertEqual(live_pred["observed"]["total_shots"], 18)

        # Over 15.5 and Over 17.5 must be already resolved
        o15_status = live_pred["markets"]["over_15_5"]
        self.assertEqual(o15_status["status"], "already_resolved")
        self.assertEqual(o15_status["probability"], 1.0)
        self.assertTrue(o15_status["resolved_result"])

        o17_status = live_pred["markets"]["over_17_5"]
        self.assertEqual(o17_status["status"], "already_resolved")
        self.assertEqual(o17_status["probability"], 1.0)

        # Over 21.5 is still in play
        o21_status = live_pred["markets"]["over_21_5"]
        self.assertEqual(o21_status["status"], "in_play")
        self.assertLess(o21_status["probability"], 1.0)

    def test_save_shot_snapshot(self):
        """Creates and persists immutable ShotPredictionSnapshot."""
        league = League(name="La Liga", country="Spain")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Barcelona", league_id=league.id)
        t2 = Team(name="Sevilla", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=datetime.now(timezone.utc), status="SCHEDULED"
        )
        self.db.add(f)
        self.db.commit()

        snap = ShotsPredictionEngine.save_shot_snapshot(self.db, f.id)
        self.assertIsNotNone(snap.id)
        self.assertEqual(snap.fixture_id, f.id)
        self.assertEqual(snap.model_version, "v1_shots_nb")
        self.assertEqual(self.db.query(ShotPredictionSnapshot).count(), 1)

    def test_unified_match_intelligence_shots_integration(self):
        """Verifies Phase 9 Unified Match Intelligence receives actual Phase 10 shots data."""
        league = League(name="Serie A", country="Italy")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Juventus", league_id=league.id)
        t2 = Team(name="Roma", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=datetime.now(timezone.utc), status="SCHEDULED"
        )
        self.db.add(f)
        self.db.commit()

        intel = UnifiedMatchIntelligenceService.get_unified_match_intelligence(self.db, f.id)
        self.assertIn("shots", intel["markets"])
        self.assertIn("shots_on_target", intel["markets"])
        self.assertIn("expected_total_shots", intel["markets"]["shots"])
        self.assertIn("expected_total_sot", intel["markets"]["shots_on_target"])
        self.assertNotEqual(intel["markets"]["shots"].get("status"), "PLANNED_PHASE_10_EXPANSION")


if __name__ == "__main__":
    unittest.main()
