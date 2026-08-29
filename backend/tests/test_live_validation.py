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
from models import LivePredictionSnapshot, Fixture, League, Team
from services.model_evaluation_service import ModelEvaluationService


class TestLiveValidation(unittest.TestCase):

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

    def test_live_minute_bucket_performance(self):
        """Validates minute-bucket performance aggregation for in-play snapshots."""
        rep = ModelEvaluationService.get_live_performance_report(self.db)
        self.assertIn("minute_buckets", rep)
        self.assertEqual(len(rep["minute_buckets"]), 6) # 0-15, 16-30, 31-45, 46-60, 61-75, 76-90+


if __name__ == "__main__":
    unittest.main()
