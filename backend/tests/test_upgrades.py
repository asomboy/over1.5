import os
import sys
import unittest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base, get_db
from models import League, Team, Fixture, HistoricalResult, Prediction
from services.prediction_service import PoissonPredictionEngine
from main import app


class TestSoccerPredictorUpgrades(unittest.TestCase):

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

    def test_accumulator_generator_service(self):
        l1 = League(external_id="PL", name="Premier League", country="England", season="2025/2026")
        l2 = League(external_id="LL", name="La Liga", country="Spain", season="2025/2026")
        self.db.add_all([l1, l2])
        self.db.commit()

        t1 = Team(external_id="ARS", name="Arsenal", league_id=l1.id)
        t2 = Team(external_id="CHE", name="Chelsea", league_id=l1.id)
        t3 = Team(external_id="RMA", name="Real Madrid", league_id=l2.id)
        t4 = Team(external_id="BAR", name="FC Barcelona", league_id=l2.id)
        self.db.add_all([t1, t2, t3, t4])
        self.db.commit()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        f1 = Fixture(league_id=l1.id, home_team_id=t1.id, away_team_id=t2.id, match_date=now, status="SCHEDULED")
        f2 = Fixture(league_id=l2.id, home_team_id=t3.id, away_team_id=t4.id, match_date=now, status="SCHEDULED")
        self.db.add_all([f1, f2])
        self.db.commit()

        PoissonPredictionEngine.predict_fixture(self.db, f1.id)
        PoissonPredictionEngine.predict_fixture(self.db, f2.id)

        # Test API Endpoint /api/accumulators/generate
        resp = self.client.get("/api/accumulators/generate")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("accumulators", data)
        self.assertIn("safe_double", data["accumulators"])

    def test_fixture_details_endpoint(self):
        l1 = League(external_id="PL", name="Premier League", country="England", season="2025/2026")
        self.db.add(l1)
        self.db.commit()

        t1 = Team(external_id="ARS", name="Arsenal", league_id=l1.id)
        t2 = Team(external_id="CHE", name="Chelsea", league_id=l1.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        f1 = Fixture(league_id=l1.id, home_team_id=t1.id, away_team_id=t2.id, match_date=now, status="SCHEDULED")
        self.db.add(f1)
        self.db.commit()

        PoissonPredictionEngine.predict_fixture(self.db, f1.id)

        resp = self.client.get(f"/api/fixtures/{f1.id}/details")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["fixture_id"], f1.id)
        self.assertIn("h2h_history", data)
        self.assertIn("prediction", data)

    def test_telegram_test_notification_endpoint(self):
        resp = self.client.post("/api/notifications/telegram/test")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("status", data)

    def test_telegram_zero_pick_suppression(self):
        from services.telegram_service import TelegramNotificationService
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # Empty picks list should return False immediately without sending any message
        result = loop.run_until_complete(TelegramNotificationService.broadcast_daily_top_picks([]))
        self.assertFalse(result)
        # Outcome recap with empty items should return False
        recap_res = loop.run_until_complete(TelegramNotificationService.broadcast_outcome_recap([], "Daily Picks", "Monday, Aug 17, 2026"))
        self.assertFalse(recap_res)
        loop.close()


if __name__ == "__main__":
    unittest.main()
