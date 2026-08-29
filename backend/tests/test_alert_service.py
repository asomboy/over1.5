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
from models import SystemAlert
from services.alert_service import AlertService


class TestAlertService(unittest.TestCase):

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

    def test_alert_emission_deduplication_and_cooldown(self):
        """Emitting identical alert during cooldown suppresses duplicate alerts."""
        alt1 = AlertService.emit_alert(
            self.db, category="PROVIDER_FAILURE",
            message="ESPN connection timeout", severity="WARNING", cooldown_minutes=30
        )
        self.assertIsNotNone(alt1)
        self.assertEqual(self.db.query(SystemAlert).count(), 1)

        # Immediate repeated call -> Suppressed
        alt2 = AlertService.emit_alert(
            self.db, category="PROVIDER_FAILURE",
            message="ESPN connection timeout", severity="WARNING", cooldown_minutes=30
        )
        self.assertEqual(alt1.id, alt2.id)
        self.assertEqual(self.db.query(SystemAlert).count(), 1)

    def test_alert_resolution(self):
        """Resolving alert sets is_active=False."""
        alt = AlertService.emit_alert(
            self.db, category="DATABASE_FAILURE",
            message="Slow query detected", severity="CRITICAL"
        )
        self.assertEqual(len(AlertService.get_active_alerts(self.db)), 1)

        success = AlertService.resolve_alert(self.db, alt.alert_id)
        self.assertTrue(success)
        self.assertEqual(len(AlertService.get_active_alerts(self.db)), 0)


if __name__ == "__main__":
    unittest.main()
