import os
import sys
import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base
from models import League, Team, Fixture, ModelEvaluation
from services.ensemble_service import (
    AdaptiveEnsembleService, MIN_ENSEMBLE_SAMPLE_THRESHOLD,
    MAX_MODEL_WEIGHT, MIN_ACTIVE_MODEL_WEIGHT
)


class TestAdaptiveEnsemble(unittest.TestCase):

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

    def test_ensemble_insufficient_sample_fallback(self):
        """When N < 100, returns uniform baseline weights and INSUFFICIENT_DATA status."""
        res = AdaptiveEnsembleService.calculate_ensemble_weights(
            self.db, "over_1_5_goals", ["dixon_coles_v2", "poisson_v1", "negbin_v1"]
        )
        self.assertEqual(res["status"], "INSUFFICIENT_DATA")
        self.assertEqual(len(res["weights"]), 3)
        self.assertAlmostEqual(sum(res["weights"].values()), 1.0, places=3)

    def test_ensemble_weighting_and_bounds_with_sample(self):
        """When N >= 100, computes adaptive weights bounded by [0.05, 0.70] summing to 1.0."""
        from datetime import datetime, timezone

        league = League(name="EPL", country="England")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Arsenal", league_id=league.id)
        t2 = Team(name="Chelsea", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        # Add 120 evaluations for model A (accurate, Brier=0.04) and 120 for model B (less accurate, Brier=0.36)
        for i in range(120):
            f = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=datetime.now(timezone.utc), status="FINISHED")
            self.db.add(f)
            self.db.commit()

            # Model A: pred=0.8, act=1.0 -> Brier=0.04
            self.db.add(ModelEvaluation(
                fixture_id=f.id,
                prediction_type="goals",
                market="over_1_5_goals",
                model_version="model_a",
                prediction_timestamp=datetime.now(timezone.utc),
                predicted_probability=0.8,
                actual_outcome=1.0,
                brier_component=0.04,
                verified=True
            ))

            # Model B: pred=0.4, act=1.0 -> Brier=0.36
            self.db.add(ModelEvaluation(
                fixture_id=f.id,
                prediction_type="goals",
                market="over_1_5_goals",
                model_version="model_b",
                prediction_timestamp=datetime.now(timezone.utc),
                predicted_probability=0.4,
                actual_outcome=1.0,
                brier_component=0.36,
                verified=True
            ))
        self.db.commit()

        res = AdaptiveEnsembleService.calculate_ensemble_weights(
            self.db, "over_1_5_goals", ["model_a", "model_b"]
        )
        self.assertEqual(res["status"], "ADAPTIVE_ENSEMBLE_ACTIVE")
        weights = res["weights"]

        # Sum == 1.0
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=3)
        # Model A should have higher weight than Model B
        self.assertGreater(weights["model_a"], weights["model_b"])
        # Bounded by MAX_MODEL_WEIGHT (0.70) and MIN_ACTIVE_MODEL_WEIGHT (0.05)
        self.assertLessEqual(weights["model_a"], MAX_MODEL_WEIGHT)
        self.assertGreaterEqual(weights["model_b"], MIN_ACTIVE_MODEL_WEIGHT)


if __name__ == "__main__":
    unittest.main()
