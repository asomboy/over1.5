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
from services.ensemble_service import AdaptiveEnsembleService


class TestEnsembleActivation(unittest.TestCase):

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

    def test_ensemble_activation_gate_and_bounds(self):
        """When N < 100, ensemble remains gated/fallback with weights bounded in [0.05, 0.70] summing to 1.0."""
        res = AdaptiveEnsembleService.calculate_dynamic_ensemble_weights(self.db, "over_1_5_goals")

        # Bounds check
        total_weight = sum(res["model_weights"].values())
        self.assertAlmostEqual(total_weight, 1.0, places=3)

        for m, w in res["model_weights"].items():
            self.assertGreaterEqual(w, 0.049)
            self.assertLessEqual(w, 0.701)


if __name__ == "__main__":
    unittest.main()
