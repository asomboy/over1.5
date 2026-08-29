import os
import sys
import unittest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base, get_db
from main import app


class TestOperationsAPI(unittest.TestCase):

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

    def test_job_endpoints(self):
        """API endpoints: /api/system/jobs, /api/system/jobs/{name}/run, /api/system/jobs/{name}/history."""
        # List jobs
        resp_list = self.client.get("/api/system/jobs")
        self.assertEqual(resp_list.status_code, 200)
        self.assertIn("available_jobs", resp_list.json())

        # Trigger job run
        resp_run = self.client.post("/api/system/jobs/fixture_ingestion/run")
        self.assertEqual(resp_run.status_code, 200)
        data_run = resp_run.json()
        self.assertEqual(data_run["status"], "SUCCESS")

        # Check job history
        resp_hist = self.client.get("/api/system/jobs/fixture_ingestion/history")
        self.assertEqual(resp_hist.status_code, 200)
        data_hist = resp_hist.json()
        self.assertEqual(len(data_hist["history"]), 1)

    def test_alerts_endpoints(self):
        """API endpoints: /api/system/alerts, /api/system/alerts/{id}/resolve."""
        from services.alert_service import AlertService

        alt = AlertService.emit_alert(self.db, "DATA_QUALITY", "Coverage threshold test", "WARNING")
        
        resp_get = self.client.get("/api/system/alerts")
        self.assertEqual(resp_get.status_code, 200)
        data_get = resp_get.json()
        self.assertEqual(len(data_get["alerts"]), 1)

        resp_res = self.client.post(f"/api/system/alerts/{alt.alert_id}/resolve")
        self.assertEqual(resp_res.status_code, 200)
        self.assertTrue(resp_res.json()["resolved"])


if __name__ == "__main__":
    unittest.main()
