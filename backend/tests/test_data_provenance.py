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
from models import Fixture, League, Team, DataProvenance
from services.data_reconciliation_service import DataReconciliationService


class TestDataProvenance(unittest.TestCase):

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

    def test_record_and_retrieve_provenance(self):
        """Records field-level provenance and ensures audit trail retrieval."""
        league = League(name="Premier League", country="England")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Arsenal", league_id=league.id)
        t2 = Team(name="Chelsea", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=datetime.now(timezone.utc), status="FINISHED")
        self.db.add(f)
        self.db.commit()

        # Record field provenances
        DataReconciliationService.record_provenance(self.db, f.id, "home_score", 3, provider="espn", provider_record_id="ESPN-12345")
        DataReconciliationService.record_provenance(self.db, f.id, "home_corners", 7, provider="espn", provider_record_id="ESPN-12345")
        DataReconciliationService.record_provenance(self.db, f.id, "referee_name", "Michael Oliver", provider="espn")

        prov_list = DataReconciliationService.get_fixture_provenance(self.db, f.id)
        self.assertEqual(len(prov_list), 3)

        home_score_prov = next(p for p in prov_list if p["field_name"] == "home_score")
        self.assertEqual(home_score_prov["value"], "3")
        self.assertEqual(home_score_prov["provider"], "espn")
        self.assertEqual(home_score_prov["provider_record_id"], "ESPN-12345")
        self.assertTrue(home_score_prov["is_verified"])


if __name__ == "__main__":
    unittest.main()
