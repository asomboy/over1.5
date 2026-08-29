import os
import sys
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import ModelEvaluation
    from services.calibration_service import CalibrationService
    from services.model_evaluation_service import ModelEvaluationService
except ImportError:
    from ..models import ModelEvaluation
    from .calibration_service import CalibrationService
    from .model_evaluation_service import ModelEvaluationService

logger = logging.getLogger(__name__)

THRESHOLD_INSUFFICIENT = 100
THRESHOLD_VALIDATING = 300
MAX_VALIDATED_ECE = 0.07


class ProductionValidationService:
    """
    Walk-forward production model validation and automated readiness gates.
    Categorizes predictive models into:
    INSUFFICIENT_DATA (N < 100) -> VALIDATING (100 <= N < 300) -> VALIDATED (N >= 300 & ECE <= 0.07) -> DEGRADED / DISABLED.
    """

    @classmethod
    def evaluate_model_readiness_gate(
        cls, sample_size: int, ece: Optional[float], brier: Optional[float], is_degraded: bool = False
    ) -> Dict[str, Any]:
        """Calculates precise production activation gate for any model."""
        if sample_size < THRESHOLD_INSUFFICIENT:
            return {
                "readiness_state": "INSUFFICIENT_DATA",
                "label": "Insufficient Observed Data",
                "can_activate_ensemble": False,
                "confidence_multiplier": 0.60,
                "sample_progress": round(sample_size / float(THRESHOLD_INSUFFICIENT), 2)
            }
        elif is_degraded:
            return {
                "readiness_state": "DEGRADED",
                "label": "Performance Degraded",
                "can_activate_ensemble": False,
                "confidence_multiplier": 0.70,
                "sample_progress": 1.0
            }
        elif sample_size < THRESHOLD_VALIDATING:
            return {
                "readiness_state": "VALIDATING",
                "label": "Empirically Validating",
                "can_activate_ensemble": True,
                "confidence_multiplier": 0.85,
                "sample_progress": round(sample_size / float(THRESHOLD_VALIDATING), 2)
            }
        else:
            # N >= 300
            if ece is not None and ece <= MAX_VALIDATED_ECE:
                return {
                    "readiness_state": "VALIDATED",
                    "label": "Production Validated",
                    "can_activate_ensemble": True,
                    "confidence_multiplier": 1.0,
                    "sample_progress": 1.0
                }
            else:
                return {
                    "readiness_state": "VALIDATING",
                    "label": "High Sample / Calibration Refinement Needed",
                    "can_activate_ensemble": True,
                    "confidence_multiplier": 0.85,
                    "sample_progress": 1.0
                }

    @classmethod
    def get_all_models_readiness_report(cls, db: Session) -> Dict[str, Any]:
        """Generates comprehensive multi-family model readiness diagnostics."""
        model_families = [
            ("goals", "Dixon-Coles v2 Goals Engine"),
            ("corners", "Negative Binomial Corners Engine"),
            ("cards", "Disciplinary Cards Engine"),
            ("live_goals", "Dynamic Live In-Play Engine")
        ]

        # Drift check
        drift_data = ModelEvaluationService.get_model_drift_analysis(db)
        is_globally_degraded = (drift_data.get("status") in ["MODEL_DRIFT_WARNING", "DEGRADED"])

        models_report = []

        for p_type, label in model_families:
            perf = ModelEvaluationService.get_model_performance(db, prediction_type=p_type)
            n = perf.get("sample_size", 0)
            ece = perf.get("ece")
            brier = perf.get("brier_score")

            gate = cls.evaluate_model_readiness_gate(n, ece, brier, is_degraded=is_globally_degraded)

            models_report.append({
                "prediction_type": p_type,
                "model_name": label,
                "sample_size": n,
                "brier_score": brier,
                "log_loss": perf.get("log_loss"),
                "ece": ece,
                "accuracy": perf.get("accuracy"),
                "readiness": gate
            })

        return {
            "status": "ok",
            "models": models_report,
            "drift_status": drift_data.get("status", "STABLE"),
            "evaluated_at": datetime.now(timezone.utc).isoformat()
        }
