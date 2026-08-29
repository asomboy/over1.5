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
from models import Fixture, League, Team, HistoricalResult, MatchStatistics
from services.validation_dataset_service import ValidationDatasetService


class TestValidationDataset(unittest.TestCase):

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

    def test_chronological_ordering_and_zero_future_leakage(self):
        """Constructs chronologically sorted dataset where earlier matches precede later matches."""
        league = League(name="Bundesliga", country="Germany")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Bayern", league_id=league.id)
        t2 = Team(name="Dortmund", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        base_time = datetime(2026, 3, 1, 15, 0, 0)

        # Match 1: March 1
        f1 = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=base_time, status="FINISHED", home_score=2, away_score=1)
        # Match 2: March 8
        f2 = Fixture(league_id=league.id, home_team_id=t2.id, away_team_id=t1.id, match_date=base_time + timedelta(days=7), status="FINISHED", home_score=0, away_score=2)
        # Match 3: March 15
        f3 = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=base_time + timedelta(days=14), status="FINISHED", home_score=3, away_score=3)
        self.db.add_all([f1, f2, f3])
        self.db.commit()

        self.db.add(HistoricalResult(fixture_id=f1.id, home_score=2, away_score=1, total_goals=3))
        self.db.add(HistoricalResult(fixture_id=f2.id, home_score=0, away_score=2, total_goals=2))
        self.db.add(HistoricalResult(fixture_id=f3.id, home_score=3, away_score=3, total_goals=6))
        self.db.commit()

        ds = ValidationDatasetService.build_chronological_dataset(self.db)
        self.assertEqual(len(ds), 3)

        # Verify chronological order
        self.assertEqual(ds[0]["fixture_id"], f1.id)
        self.assertEqual(ds[1]["fixture_id"], f2.id)
        self.assertEqual(ds[2]["fixture_id"], f3.id)
        self.assertEqual(ds[0]["data_quality"], "OBSERVED")


if __name__ == "__main__":
    unittest.main()
