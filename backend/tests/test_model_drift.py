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
from models import ModelEvaluation, Fixture, League, Team
from services.model_evaluation_service import ModelEvaluationService


class TestModelDrift(unittest.TestCase):

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

    def test_drift_detection_stability(self):
        """Under baseline conditions with small sample, returns STABLE or INSUFFICIENT_DATA."""
        drift = ModelEvaluationService.get_model_drift_analysis(self.db)
        self.assertIn(drift["status"], ["STABLE", "INSUFFICIENT_DATA"])


if __name__ == "__main__":
    unittest.main()
