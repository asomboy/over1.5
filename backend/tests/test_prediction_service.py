import os
import sys
import json
import unittest
from typing import cast
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base, get_db
from models import League, Team, Fixture, HistoricalResult, TeamStatistics, LeagueStatistics, Prediction
from services.prediction_service import PoissonPredictionEngine
from main import app


class TestPredictionService(unittest.TestCase):

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

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        app.dependency_overrides.clear()

    def test_calculate_poisson_probabilities_scipy(self):
        # Test calculation with home lambda = 2.1, away lambda = 1.2
        res = PoissonPredictionEngine.calculate_poisson_probabilities(lambda_home=2.1, lambda_away=1.2)

        self.assertIn("home_win_probability", res)
        self.assertIn("draw_probability", res)
        self.assertIn("away_win_probability", res)
        self.assertIn("over_0_5_probability", res)
        self.assertIn("over_1_5_probability", res)
        self.assertIn("over_2_5_probability", res)
        self.assertIn("over_3_5_probability", res)
        self.assertIn("over_4_5_probability", res)
        self.assertIn("under_2_5_probability", res)
        self.assertIn("most_likely_score", res)
        self.assertIn("top_5_scorelines", res)

        # Check Under 2.5 + Over 2.5 sum to 1.0 (approx due to rounding)
        self.assertAlmostEqual(res["over_2_5_probability"] + res["under_2_5_probability"], 1.0, places=3)
        self.assertEqual(len(res["top_5_scorelines"]), 5)
        self.assertIsInstance(res["most_likely_score"], str)

    def test_predict_fixture_and_db_persistence(self):
        league = League(name="Premier League", country="England", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        team_a = Team(name="Liverpool", short_code="LIV", league_id=league.id)
        team_b = Team(name="Chelsea", short_code="CHE", league_id=league.id)
        self.db.add_all([team_a, team_b])
        self.db.commit()

        # Add fixture
        fixture = Fixture(
            league_id=league.id,
            home_team_id=team_a.id,
            away_team_id=team_b.id,
            match_date=datetime.now(timezone.utc) + timedelta(days=2),
            status="SCHEDULED",
            venue="Anfield"
        )
        self.db.add(fixture)
        self.db.commit()

        # Run prediction engine
        prediction = PoissonPredictionEngine.predict_fixture(self.db, cast(int, fixture.id))

        self.assertIsNotNone(prediction)
        assert prediction is not None
        self.assertEqual(prediction.fixture_id, fixture.id)
        self.assertTrue(float(prediction.predicted_home_score or 0) > 0.0)
        self.assertTrue(float(prediction.predicted_away_score or 0) > 0.0)
        self.assertTrue(float(prediction.expected_goals_xg or 0) > 0.0)

        # Verify DB query
        db_pred = self.db.query(Prediction).filter(Prediction.fixture_id == fixture.id).first()
        self.assertIsNotNone(db_pred)
        assert db_pred is not None
        self.assertIsNotNone(db_pred.over_0_5_probability)
        self.assertIsNotNone(db_pred.over_1_5_probability)
        self.assertIsNotNone(db_pred.over_2_5_probability)
        self.assertIsNotNone(db_pred.over_3_5_probability)
        self.assertIsNotNone(db_pred.over_4_5_probability)
        self.assertIsNotNone(db_pred.under_2_5_probability)
        self.assertIsNotNone(db_pred.most_likely_score)
        self.assertIsNotNone(db_pred.top_scorelines_json)
        assert db_pred.top_scorelines_json is not None

        top_scorelines = json.loads(cast(str, db_pred.top_scorelines_json))
        self.assertEqual(len(top_scorelines), 5)

    def test_api_prediction_endpoints(self):
        league = League(name="La Liga", country="Spain", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        team_a = Team(name="Real Madrid", short_code="RMA", league_id=league.id)
        team_b = Team(name="Barcelona", short_code="BAR", league_id=league.id)
        self.db.add_all([team_a, team_b])
        self.db.commit()

        fixture = Fixture(
            league_id=league.id,
            home_team_id=team_a.id,
            away_team_id=team_b.id,
            match_date=datetime.now(timezone.utc) + timedelta(days=3),
            status="SCHEDULED",
        )
        self.db.add(fixture)
        self.db.commit()

        # Trigger predict-all endpoint
        res = self.client.post("/api/predictions/predict-all")
        self.assertEqual(res.status_code, 200)
        res_data = res.json()
        self.assertEqual(res_data["status"], "ok")
        self.assertEqual(res_data["count"], 1)

        # Retrieve prediction endpoint
        res_get = self.client.get(f"/api/predictions/{fixture.id}")
        self.assertEqual(res_get.status_code, 200)
        get_data = res_get.json()
        self.assertEqual(get_data["status"], "ok")
        self.assertEqual(get_data["data"]["fixture_id"], fixture.id)


if __name__ == "__main__":
    unittest.main()
