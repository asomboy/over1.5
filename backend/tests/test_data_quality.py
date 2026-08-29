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
from models import League, Team, Fixture, HistoricalResult, MatchStatistics
from services.data_quality_service import DataQualityService


class TestDataQuality(unittest.TestCase):

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

    def test_global_coverage_calculations(self):
        """Validates exact mathematical coverage ratios (observed / max(1, eligible))."""
        league = League(name="EPL", country="England")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Liverpool", league_id=league.id)
        t2 = Team(name="Chelsea", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        # Create 4 finished fixtures
        f1 = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=datetime.now(timezone.utc), status="FINISHED")
        f2 = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=datetime.now(timezone.utc), status="FINISHED")
        f3 = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=datetime.now(timezone.utc), status="FINISHED")
        f4 = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=datetime.now(timezone.utc), status="FINISHED")
        self.db.add_all([f1, f2, f3, f4])
        self.db.commit()

        # 4 goals, 2 corners, 1 card, 1 referee
        self.db.add(HistoricalResult(fixture_id=f1.id, home_score=2, away_score=1, total_goals=3))
        self.db.add(HistoricalResult(fixture_id=f2.id, home_score=1, away_score=0, total_goals=1))
        self.db.add(HistoricalResult(fixture_id=f3.id, home_score=3, away_score=2, total_goals=5))
        self.db.add(HistoricalResult(fixture_id=f4.id, home_score=0, away_score=0, total_goals=0))

        self.db.add(MatchStatistics(fixture_id=f1.id, home_corners=5, away_corners=4, home_yellow_cards=2, away_yellow_cards=1, referee_name="Michael Oliver"))
        self.db.add(MatchStatistics(fixture_id=f2.id, home_corners=6, away_corners=3))
        self.db.commit()

        cov = DataQualityService.calculate_global_coverage(self.db, persist=True)

        self.assertEqual(cov["eligible_completed_matches"], 4)
        self.assertEqual(cov["goals_coverage"]["coverage_ratio"], 1.0) # 4/4
        self.assertEqual(cov["corners_coverage"]["coverage_ratio"], 0.5) # 2/4
        self.assertEqual(cov["cards_coverage"]["coverage_ratio"], 0.25) # 1/4
        self.assertEqual(cov["referee_coverage"]["coverage_ratio"], 0.25) # 1/4

    def test_competition_coverage(self):
        """Validates coverage breakdown per competition."""
        league = League(name="Serie A", country="Italy")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Milan", league_id=league.id)
        t2 = Team(name="Inter", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=datetime.now(timezone.utc), status="FINISHED")
        self.db.add(f)
        self.db.commit()

        self.db.add(HistoricalResult(fixture_id=f.id, home_score=1, away_score=1, total_goals=2))
        self.db.commit()

        comps = DataQualityService.calculate_competition_coverage(self.db)
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0]["competition"], "Serie A")
        self.assertEqual(comps[0]["goals_coverage"], 1.0)


if __name__ == "__main__":
    unittest.main()
