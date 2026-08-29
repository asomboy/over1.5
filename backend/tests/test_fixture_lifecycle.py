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
from models import (
    League, Team, Fixture, HistoricalResult, MatchStatistics,
    Prediction, ModelEvaluation
)
from services.fixture_lifecycle_service import FixtureLifecycleService


class TestFixtureLifecycle(unittest.TestCase):

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

    def test_lifecycle_states_and_transitions(self):
        """Validates progression: SCHEDULED -> PRE_MATCH_READY -> LIVE -> EVALUATED."""
        league = League(name="Bundesliga", country="Germany")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Bayern", league_id=league.id)
        t2 = Team(name="Dortmund", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        # 1. SCHEDULED
        f = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=datetime.now(timezone.utc) + timedelta(days=2), status="SCHEDULED")
        self.db.add(f)
        self.db.commit()

        st_1 = FixtureLifecycleService.get_fixture_lifecycle_state(self.db, f.id)
        self.assertEqual(st_1["lifecycle_state"], "SCHEDULED")

        # 2. PRE_MATCH_READY (when prediction exists)
        self.db.add(Prediction(
            fixture_id=f.id,
            predicted_home_score=2.0,
            predicted_away_score=1.0,
            home_win_probability=0.60,
            draw_probability=0.20,
            away_win_probability=0.20
        ))
        self.db.commit()

        st_2 = FixtureLifecycleService.get_fixture_lifecycle_state(self.db, f.id)
        self.assertEqual(st_2["lifecycle_state"], "PRE_MATCH_READY")

        # 3. LIVE
        f.status = "LIVE"
        self.db.commit()

        st_3 = FixtureLifecycleService.get_fixture_lifecycle_state(self.db, f.id)
        self.assertEqual(st_3["lifecycle_state"], "LIVE")

        # 4. FINISHED -> EVALUATED
        f.status = "FINISHED"
        f.home_score = 2
        f.away_score = 1
        self.db.add(HistoricalResult(fixture_id=f.id, home_score=2, away_score=1, total_goals=3))
        self.db.commit()

        # Trigger transition
        res = FixtureLifecycleService.process_lifecycle_transition(self.db, f.id)
        self.assertEqual(res["transition"], "EVALUATION_COMPLETED")

        st_4 = FixtureLifecycleService.get_fixture_lifecycle_state(self.db, f.id)
        self.assertEqual(st_4["lifecycle_state"], "EVALUATED")


if __name__ == "__main__":
    unittest.main()
