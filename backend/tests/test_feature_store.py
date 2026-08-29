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
from models import League, Team, Fixture, HistoricalResult, MatchStatistics, FeatureSnapshot
from services.feature_store_service import FeatureStoreService


class TestFeatureStore(unittest.TestCase):

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

    def test_strict_temporal_integrity_no_future_leakage(self):
        """Features extracted as of target kickoff must never include matches played afterwards."""
        league = League(name="La Liga", country="Spain")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Real Madrid", league_id=league.id)
        t2 = Team(name="Sevilla", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        base_time = datetime(2026, 5, 10, 15, 0, 0)

        # Match 1: 5 days prior (Finished, 3-1)
        f_past = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=base_time - timedelta(days=5), status="FINISHED",
            home_score=3, away_score=1
        )
        # Match 2: Target match
        f_target = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=base_time, status="SCHEDULED"
        )
        # Match 3: 5 days AFTER target match (Finished, 5-0) -> MUST NOT BE LEAKED!
        f_future = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=base_time + timedelta(days=5), status="FINISHED",
            home_score=5, away_score=0
        )
        self.db.add_all([f_past, f_target, f_future])
        self.db.commit()

        # Extract features for target match kickoff
        feats = FeatureStoreService.get_team_rolling_features(self.db, t1.id, base_time, window_size=5)

        # Only f_past should be counted (sample_size = 1, avg_goals_for = 3.0)
        self.assertEqual(feats["sample_size"], 1)
        self.assertEqual(feats["avg_goals_for"], 3.0)

    def test_capture_feature_snapshot(self):
        """Captures immutable FeatureSnapshot record for reproducibility."""
        league = League(name="EPL", country="England")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Man City", league_id=league.id)
        t2 = Team(name="Arsenal", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=datetime.now(timezone.utc), status="SCHEDULED")
        self.db.add(f)
        self.db.commit()

        snap = FeatureStoreService.capture_feature_snapshot(self.db, f.id, "v2_match_intelligence")
        self.assertIsNotNone(snap.id)
        self.assertIn("home_team", snap.features_json)
        self.assertEqual(self.db.query(FeatureSnapshot).count(), 1)


if __name__ == "__main__":
    unittest.main()
