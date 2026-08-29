import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base
from models import ProviderHealth
from services.provider_health_service import ProviderHealthService


class TestProviderHealth(unittest.TestCase):

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

    def test_provider_event_logging_and_status(self):
        """Logs provider requests, average latency, and calculates status."""
        # 10 successful requests with 120ms latency
        for _ in range(10):
            ProviderHealthService.record_provider_event(self.db, "espn", success=True, latency_ms=120.0)

        status_list = ProviderHealthService.get_providers_status(self.db)
        espn = next(p for p in status_list if p["provider"] == "espn")

        self.assertEqual(espn["status"], "HEALTHY")
        self.assertEqual(espn["request_count"], 10)
        self.assertEqual(espn["success_count"], 10)
        self.assertAlmostEqual(espn["average_latency_ms"], 120.0, places=1)

    def test_live_feed_freshness_evaluation(self):
        """Assesses feed latency and confidence penalty."""
        now = datetime.now(timezone.utc)

        # Fresh (< 60s)
        f_fresh = ProviderHealthService.evaluate_live_feed_freshness(now - timedelta(seconds=20))
        self.assertEqual(f_fresh["freshness_state"], "FRESH")
        self.assertEqual(f_fresh["confidence_penalty"], 0.0)

        # Delayed (60-180s)
        f_delayed = ProviderHealthService.evaluate_live_feed_freshness(now - timedelta(seconds=90))
        self.assertEqual(f_delayed["freshness_state"], "DELAYED")
        self.assertGreater(f_delayed["confidence_penalty"], 0.0)

        # Stale (> 180s)
        f_stale = ProviderHealthService.evaluate_live_feed_freshness(now - timedelta(seconds=250))
        self.assertEqual(f_stale["freshness_state"], "STALE")
        self.assertGreaterEqual(f_stale["confidence_penalty"], 0.25)


if __name__ == "__main__":
    unittest.main()
