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
from models import Fixture, League, Team, DataConflict
from services.data_reconciliation_service import DataReconciliationService


class TestDataReconciliation(unittest.TestCase):

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

    def test_statistical_sanity_validation(self):
        """Validates statistical bounds on goals, corners, cards, and possession."""
        # Valid cases
        self.assertTrue(DataReconciliationService.validate_goals(2, 1)[0])
        self.assertTrue(DataReconciliationService.validate_corners(5, 4)[0])
        self.assertTrue(DataReconciliationService.validate_cards(2, 3, 0, 0)[0])
        self.assertTrue(DataReconciliationService.validate_possession(52.0, 48.0)[0])

        # Invalid cases
        self.assertFalse(DataReconciliationService.validate_goals(-1, 2)[0])
        self.assertFalse(DataReconciliationService.validate_corners(4, -2)[0])
        self.assertFalse(DataReconciliationService.validate_cards(-1, 0)[0])
        self.assertFalse(DataReconciliationService.validate_possession(60.0, 60.0)[0]) # > 100% total
        self.assertFalse(DataReconciliationService.validate_shots(4, 6)[0]) # shots on target > total shots

    def test_detect_and_resolve_conflict(self):
        """Detects conflicting provider values and resolves with operator audit."""
        league = League(name="Serie A", country="Italy")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Juventus", league_id=league.id)
        t2 = Team(name="Napoli", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=datetime.now(timezone.utc), status="FINISHED")
        self.db.add(f)
        self.db.commit()

        # Conflicting corners between ESPN (7) and Football-Data (9)
        conf = DataReconciliationService.detect_and_record_conflict(
            self.db, f.id, "total_corners", 7, "espn", 9, "football_data"
        )
        self.assertIsNotNone(conf)
        self.assertEqual(len(DataReconciliationService.get_all_conflicts(self.db)), 1)

        # Resolve conflict
        success = DataReconciliationService.resolve_conflict(self.db, conf.id, "7", notes="Verified via official league video")
        self.assertTrue(success)
        self.assertEqual(len(DataReconciliationService.get_all_conflicts(self.db, status="CONFLICT")), 0)


if __name__ == "__main__":
    unittest.main()
