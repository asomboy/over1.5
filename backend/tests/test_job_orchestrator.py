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
from models import Fixture, League, Team, JobExecution
from services.job_orchestrator_service import JobOrchestratorService


class TestJobOrchestrator(unittest.TestCase):

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

    def test_job_execution_and_persistence(self):
        """Job execution creates persistent JobExecution record with timing and status."""
        res = JobOrchestratorService.execute_job(self.db, "fixture_ingestion")
        self.assertEqual(res["status"], "SUCCESS")
        self.assertIn("execution_id", res)
        self.assertGreaterEqual(res["duration_ms"], 0.0)

        # Check DB record
        job_recs = self.db.query(JobExecution).all()
        self.assertEqual(len(job_recs), 1)
        self.assertEqual(job_recs[0].status, "SUCCESS")
        self.assertEqual(job_recs[0].job_name, "fixture_ingestion")

    def test_job_history_retrieval(self):
        """Job history query returns recorded runs."""
        JobOrchestratorService.execute_job(self.db, "provider_health")
        JobOrchestratorService.execute_job(self.db, "model_evaluation")

        history = JobOrchestratorService.get_job_history(self.db, limit=5)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["job_name"], "model_evaluation")


if __name__ == "__main__":
    unittest.main()
