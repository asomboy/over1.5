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
from models import Fixture, League, Team, MatchStatistics, HistoricalEnrichmentStatus
from services.historical_data_service import HistoricalDataService


class TestHistoricalBackfill(unittest.TestCase):

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

    def test_backfill_pause_and_resume(self):
        """Validates pause and resume backfill controls."""
        HistoricalDataService.pause_backfill()
        self.assertTrue(HistoricalDataService.is_paused())

        res = HistoricalDataService.discover_and_enrich_batch(self.db, batch_size=10)
        self.assertEqual(res["status"], "PAUSED")

        HistoricalDataService.resume_backfill()
        self.assertFalse(HistoricalDataService.is_paused())

    def test_resumable_and_idempotent_backfill(self):
        """Enriching batch does not duplicate records on subsequent runs."""
        league = League(name="La Liga", country="Spain")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Real Madrid", league_id=league.id)
        t2 = Team(name="Barcelona", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=datetime.now(timezone.utc), status="FINISHED", home_score=2, away_score=1)
        self.db.add(f)
        self.db.commit()

        # Add complete match stats
        self.db.add(MatchStatistics(fixture_id=f.id, home_corners=6, away_corners=4, home_yellow_cards=2, away_yellow_cards=3, referee_name="Mateu Lahoz"))
        self.db.commit()

        # Run 1
        res1 = HistoricalDataService.discover_and_enrich_batch(self.db, batch_size=10)
        self.assertEqual(res1["fixtures_inspected"], 1)
        self.assertEqual(res1["fixtures_completed"], 1)

        # Run 2 (Idempotent - already completed fixture should be skipped)
        res2 = HistoricalDataService.discover_and_enrich_batch(self.db, batch_size=10)
        self.assertEqual(res2["fixtures_inspected"], 0)


if __name__ == "__main__":
    unittest.main()
