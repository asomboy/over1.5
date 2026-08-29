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
from models import ModelEvaluation, Fixture, League, Team
from services.production_validation_service import ProductionValidationService


class TestWalkForwardValidation(unittest.TestCase):

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

    def test_market_level_readiness_gates(self):
        """Calculates granular sample gates per market, separating market sample from global sample."""
        league = League(name="Ligue 1", country="France")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="PSG", league_id=league.id)
        t2 = Team(name="Monaco", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=datetime.now(timezone.utc), status="FINISHED")
        self.db.add(f)
        self.db.commit()

        # Add 5 evaluations for over_1_5_goals and 0 for over_11_5_corners
        for i in range(5):
            self.db.add(ModelEvaluation(
                fixture_id=f.id,
                prediction_type="goals",
                market="over_1_5_goals",
                model_version=f"v2_match_intelligence_{i}",
                prediction_timestamp=datetime.now(timezone.utc),
                predicted_probability=0.75,
                actual_outcome=1.0,
                brier_component=0.0625,
                is_live=False,
                match_minute=0
            ))
        self.db.commit()

        rep = ProductionValidationService.get_market_level_readiness(self.db)
        self.assertIn("markets", rep)

        o15 = next(m for m in rep["markets"] if m["market"] == "over_1_5_goals")
        o115c = next(m for m in rep["markets"] if m["market"] == "over_11_5_corners")

        self.assertEqual(o15["sample_size"], 5)
        self.assertEqual(o15["readiness"]["readiness_state"], "INSUFFICIENT_DATA")
        self.assertEqual(o115c["sample_size"], 0)
        self.assertEqual(o115c["readiness"]["readiness_state"], "INSUFFICIENT_DATA")


if __name__ == "__main__":
    unittest.main()
