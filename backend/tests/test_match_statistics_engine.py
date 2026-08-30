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
    Fixture, League, Team, MatchStatistics, MatchStatisticsPredictionSnapshot,
    Prediction
)
from services.match_statistics_prediction_service import MatchStatisticsPredictionEngine, MatchStatisticsFeatureService
from services.data_reconciliation_service import DataReconciliationService
from services.unified_match_intelligence_service import UnifiedMatchIntelligenceService


class TestMatchStatisticsEngine(unittest.TestCase):

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

    def test_possession_bounds_and_conservation(self):
        """Validates that possession forecasts are bounded and sum exactly to 100%."""
        league = League(name="Premier League", country="England")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Manchester City", league_id=league.id)
        t2 = Team(name="Brentford", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=datetime.now(timezone.utc), status="SCHEDULED"
        )
        self.db.add(f)
        self.db.commit()

        pred = MatchStatisticsPredictionEngine.predict_match_statistics(self.db, f.id)
        poss = pred["possession"]

        self.assertGreaterEqual(poss["expected_home_possession"], 0.0)
        self.assertLessEqual(poss["expected_home_possession"], 100.0)
        self.assertGreaterEqual(poss["expected_away_possession"], 0.0)
        self.assertLessEqual(poss["expected_away_possession"], 100.0)

        total_poss = round(poss["expected_home_possession"] + poss["expected_away_possession"], 1)
        self.assertEqual(total_poss, 100.0)

    def test_fouls_pmf_and_monotonicity(self):
        """Validates discrete Negative Binomial fouls PMF and strict Over/Under monotonicity."""
        league = League(name="La Liga", country="Spain")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Atletico Madrid", league_id=league.id)
        t2 = Team(name="Getafe", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=datetime.now(timezone.utc), status="SCHEDULED"
        )
        self.db.add(f)
        self.db.commit()

        pred = MatchStatisticsPredictionEngine.predict_match_statistics(self.db, f.id)
        fouls_probs = pred["fouls"]["probabilities"]

        o19 = fouls_probs["over_19_5"]
        o21 = fouls_probs["over_21_5"]
        o23 = fouls_probs["over_23_5"]
        o25 = fouls_probs["over_25_5"]
        o27 = fouls_probs["over_27_5"]

        self.assertGreaterEqual(o19, o21)
        self.assertGreaterEqual(o21, o23)
        self.assertGreaterEqual(o23, o25)
        self.assertGreaterEqual(o25, o27)

    def test_statistical_sanity_validation(self):
        """Verifies DataReconciliationService bounds checks for all match statistics."""
        # Negative fouls
        res1 = DataReconciliationService.validate_match_statistics_bounds(home_fouls=-3, away_fouls=12)
        self.assertFalse(res1["valid"])
        self.assertIn("Invalid negative home_fouls", res1["errors"][0])

        # Blocked shots exceeding total shots
        res2 = DataReconciliationService.validate_match_statistics_bounds(
            home_shots=8, home_blocked=10
        )
        self.assertFalse(res2["valid"])
        self.assertIn("blocked_shots (10) exceeds total_shots (8)", res2["errors"][0])

        # Shot location exceeding total shots
        res3 = DataReconciliationService.validate_match_statistics_bounds(
            home_shots=10, home_inside_box=7, home_outside_box=5
        )
        self.assertFalse(res3["valid"])
        self.assertIn("exceeds total_shots", res3["errors"][0])

    def test_attacking_pressure_model_derived(self):
        """Ensures Attacking Pressure Index is labeled MODEL_DERIVED and calculated transparently."""
        league = League(name="Serie A", country="Italy")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Inter", league_id=league.id)
        t2 = Team(name="Monza", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=datetime.now(timezone.utc), status="SCHEDULED"
        )
        self.db.add(f)
        self.db.commit()

        pred = MatchStatisticsPredictionEngine.predict_match_statistics(self.db, f.id)
        pressure = pred["attacking_pressure"]

        self.assertEqual(pressure["type"], "MODEL_DERIVED")
        self.assertGreaterEqual(pressure["home_pressure_index"], 0.0)
        self.assertLessEqual(pressure["home_pressure_index"], 100.0)
        self.assertIn("components_weighting", pressure)
        self.assertEqual(sum(pressure["components_weighting"].values()), 1.0)

    def test_live_match_statistics_and_early_resolution(self):
        """Resolves foul markets to 1.0 when observed live fouls reach threshold."""
        league = League(name="Bundesliga", country="Germany")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Bayern Munich", league_id=league.id)
        t2 = Team(name="Leverkusen", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=datetime.now(timezone.utc), status="LIVE"
        )
        self.db.add(f)
        self.db.commit()

        # Add match statistics showing 22 total fouls at minute 70
        self.db.add(MatchStatistics(
            fixture_id=f.id,
            home_possession=55.0,
            away_possession=45.0,
            home_fouls=12,
            away_fouls=10,
            home_offsides=3,
            away_offsides=1,
            home_saves=4,
            away_saves=2
        ))
        self.db.commit()

        live_pred = MatchStatisticsPredictionEngine.predict_live_match_statistics(self.db, f.id)

        self.assertEqual(live_pred["status"], "LIVE_ACTIVE")
        self.assertEqual(live_pred["fouls"]["observed_total"], 22)

        # Over 19.5 and Over 21.5 must be already resolved
        o19 = live_pred["fouls"]["markets"]["over_19_5"]
        self.assertEqual(o19["status"], "already_resolved")
        self.assertEqual(o19["probability"], 1.0)
        self.assertTrue(o19["resolved_result"])

        o21 = live_pred["fouls"]["markets"]["over_21_5"]
        self.assertEqual(o21["status"], "already_resolved")
        self.assertEqual(o21["probability"], 1.0)

        # Over 25.5 is still in play
        o25 = live_pred["fouls"]["markets"]["over_25_5"]
        self.assertEqual(o25["status"], "in_play")
        self.assertLess(o25["probability"], 1.0)

    def test_save_match_statistics_snapshot(self):
        """Creates and persists immutable MatchStatisticsPredictionSnapshot."""
        league = League(name="Ligue 1", country="France")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="PSG", league_id=league.id)
        t2 = Team(name="Marseille", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=datetime.now(timezone.utc), status="SCHEDULED"
        )
        self.db.add(f)
        self.db.commit()

        snap = MatchStatisticsPredictionEngine.save_match_statistics_snapshot(self.db, f.id)
        self.assertIsNotNone(snap.id)
        self.assertEqual(snap.fixture_id, f.id)
        self.assertEqual(snap.model_version, "v1_match_stats_nb")
        self.assertEqual(self.db.query(MatchStatisticsPredictionSnapshot).count(), 1)

    def test_unified_match_intelligence_integration(self):
        """Verifies Phase 11 match statistics are fully synthesized into Unified Match Intelligence."""
        league = League(name="Premier League", country="England")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Tottenham", league_id=league.id)
        t2 = Team(name="West Ham", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=datetime.now(timezone.utc), status="SCHEDULED"
        )
        self.db.add(f)
        self.db.commit()

        intel = UnifiedMatchIntelligenceService.get_unified_match_intelligence(self.db, f.id)
        self.assertIn("possession", intel["markets"])
        self.assertIn("fouls", intel["markets"])
        self.assertIn("offsides", intel["markets"])
        self.assertIn("saves", intel["markets"])
        self.assertIn("blocked_shots", intel["markets"])
        self.assertIn("shot_location", intel["markets"])
        self.assertIn("attacking_pressure", intel["markets"])


if __name__ == "__main__":
    unittest.main()
