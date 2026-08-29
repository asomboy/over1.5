import os
import sys
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import ModelEvaluation, LivePredictionSnapshot, Prediction
    from services.calibration_service import CalibrationService
    from services.model_evaluation_service import ModelEvaluationService
except ImportError:
    from ..models import ModelEvaluation, LivePredictionSnapshot, Prediction
    from .calibration_service import CalibrationService
    from .model_evaluation_service import ModelEvaluationService

logger = logging.getLogger(__name__)

THRESHOLD_INSUFFICIENT = 100
THRESHOLD_VALIDATING = 300
MAX_VALIDATED_ECE = 0.07

ALL_EVALUATION_MARKETS = [
    # Goals
    "over_0_5_goals", "over_1_5_goals", "over_2_5_goals", "over_3_5_goals", "over_4_5_goals", "btts", "home_win", "away_win", "draw",
    # Corners
    "over_7_5_corners", "over_8_5_corners", "over_9_5_corners", "over_10_5_corners", "over_11_5_corners",
    # Cards
    "over_2_5_cards", "over_3_5_cards", "over_4_5_cards", "over_5_5_cards", "over_6_5_cards", "any_red_card"
]


class ProductionValidationService:
    """
    Walk-forward production model validation and automated readiness gates.
    Categorizes predictive models and individual markets into:
    INSUFFICIENT_DATA (N < 100) -> VALIDATING (100 <= N < 300) -> VALIDATED (N >= 300 & ECE <= 0.07) -> DEGRADED / DISABLED.
    """

    @classmethod
    def evaluate_model_readiness_gate(
        cls, sample_size: int, ece: Optional[float], brier: Optional[float], is_degraded: bool = False
    ) -> Dict[str, Any]:
        """Calculates precise production activation gate for any model or market."""
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
    def get_market_level_readiness(cls, db: Session) -> Dict[str, Any]:
        """
        Calculates granular readiness gates for every individual betting market.
        Prevents global sample sizes from disguising low-sample submarkets.
        """
        markets_report = []

        for mkt in ALL_EVALUATION_MARKETS:
            evals = db.query(ModelEvaluation).filter(ModelEvaluation.market == mkt).all()
            n = len(evals)

            if n > 0:
                brier = sum((e.brier_component or 0.25) for e in evals) / n
                log_losses = [e.log_loss_component for e in evals if e.log_loss_component is not None]
                avg_log_loss = (sum(log_losses) / len(log_losses)) if log_losses else None

                # Calculate ECE for market
                preds = [e.predicted_probability for e in evals]
                outcomes = [e.actual_outcome for e in evals]
                cal = CalibrationService.calculate_calibration_curve(preds, outcomes)
                ece = cal.get("ece")
            else:
                brier = None
                avg_log_loss = None
                ece = None

            gate = cls.evaluate_model_readiness_gate(n, ece, brier)

            markets_report.append({
                "market": mkt,
                "sample_size": n,
                "brier_score": round(brier, 4) if brier is not None else None,
                "log_loss": round(avg_log_loss, 4) if avg_log_loss is not None else None,
                "ece": ece,
                "readiness": gate
            })

        return {
            "status": "ok",
            "markets_count": len(markets_report),
            "markets": markets_report,
            "evaluated_at": datetime.now(timezone.utc).isoformat()
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

    @classmethod
    def get_real_leaderboard(cls, db: Session) -> Dict[str, Any]:
        """
        Compares all predictive models against verified real match outcomes.
        Clearly labels data source and readiness state.
        """
        models = [
            {"model_key": "league_baseline", "model_name": "Historical League Baseline", "type": "baseline"},
            {"model_key": "poisson_v1", "model_name": "Independent Poisson Model", "type": "poisson"},
            {"model_key": "dixon_coles_v2", "model_name": "Dixon-Coles Modified Poisson", "type": "goals"},
            {"model_key": "corners_negbin_v2", "model_name": "Negative Binomial Corners", "type": "corners"},
            {"model_key": "cards_referee_v2", "model_name": "Disciplinary & Referee Model", "type": "cards"},
            {"model_key": "adaptive_ensemble_v1", "model_name": "Adaptive Calibrated Ensemble", "type": "ensemble"}
        ]

        leaderboard = []
        for m in models:
            evals = db.query(ModelEvaluation).filter(ModelEvaluation.model_version.contains(m["model_key"])).all()
            n = len(evals)

            if n > 0:
                brier = sum((e.brier_component or 0.25) for e in evals) / n
                log_losses = [e.log_loss_component for e in evals if e.log_loss_component is not None]
                avg_log_loss = (sum(log_losses) / len(log_losses)) if log_losses else None
                data_source = "REAL_DATA"
            else:
                brier = None
                avg_log_loss = None
                data_source = "INSUFFICIENT_DATA"

            gate = cls.evaluate_model_readiness_gate(n, None, brier)

            leaderboard.append({
                "model_key": m["model_key"],
                "model_name": m["model_name"],
                "model_type": m["type"],
                "data_source": data_source,
                "sample_size": n,
                "brier_score": round(brier, 4) if brier is not None else None,
                "log_loss": round(avg_log_loss, 4) if avg_log_loss is not None else None,
                "readiness_state": gate["readiness_state"]
            })

        return {
            "status": "ok",
            "leaderboard": leaderboard,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
