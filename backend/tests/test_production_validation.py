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
from models import League, Team, Fixture, ModelEvaluation
from services.production_validation_service import (
    ProductionValidationService, THRESHOLD_INSUFFICIENT, THRESHOLD_VALIDATING
)


class TestProductionValidation(unittest.TestCase):

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

    def test_production_readiness_gates(self):
        """Validates transitions across INSUFFICIENT_DATA, VALIDATING, and VALIDATED."""
        # 1. N < 100 -> INSUFFICIENT_DATA
        gate_1 = ProductionValidationService.evaluate_model_readiness_gate(50, ece=0.04, brier=0.15)
        self.assertEqual(gate_1["readiness_state"], "INSUFFICIENT_DATA")
        self.assertFalse(gate_1["can_activate_ensemble"])

        # 2. 100 <= N < 300 -> VALIDATING
        gate_2 = ProductionValidationService.evaluate_model_readiness_gate(150, ece=0.05, brier=0.18)
        self.assertEqual(gate_2["readiness_state"], "VALIDATING")
        self.assertTrue(gate_2["can_activate_ensemble"])

        # 3. N >= 300 with good ECE -> VALIDATED
        gate_3 = ProductionValidationService.evaluate_model_readiness_gate(350, ece=0.05, brier=0.16)
        self.assertEqual(gate_3["readiness_state"], "VALIDATED")
        self.assertTrue(gate_3["can_activate_ensemble"])
        self.assertEqual(gate_3["confidence_multiplier"], 1.0)

        # 4. Degraded model -> DEGRADED
        gate_4 = ProductionValidationService.evaluate_model_readiness_gate(350, ece=0.15, brier=0.28, is_degraded=True)
        self.assertEqual(gate_4["readiness_state"], "DEGRADED")
        self.assertFalse(gate_4["can_activate_ensemble"])


if __name__ == "__main__":
    unittest.main()
