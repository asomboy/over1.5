import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import (
        Fixture, HistoricalResult, MatchStatistics, Prediction,
        CornerPredictionSnapshot, CardPredictionSnapshot, LivePredictionSnapshot,
        ModelEvaluation
    )
    from services.feature_store_service import FeatureStoreService
    from services.model_evaluation_service import ModelEvaluationService
except ImportError:
    from ..models import (
        Fixture, HistoricalResult, MatchStatistics, Prediction,
        CornerPredictionSnapshot, CardPredictionSnapshot, LivePredictionSnapshot,
        ModelEvaluation
    )
    from .feature_store_service import FeatureStoreService
    from .model_evaluation_service import ModelEvaluationService

logger = logging.getLogger(__name__)


class FixtureLifecycleService:
    """
    Automated fixture lifecycle orchestrator managing state transitions:
    SCHEDULED -> PRE_MATCH_READY -> LIVE -> FINISHED_PENDING_VERIFICATION -> VERIFIED -> EVALUATED.
    """

    @classmethod
    def get_fixture_lifecycle_state(cls, db: Session, fixture_id: int) -> Dict[str, Any]:
        """Calculates precise lifecycle state for a given fixture."""
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            return {"error": "Fixture not found", "status": "UNKNOWN"}

        raw_status = fixture.status
        has_pred = db.query(Prediction).filter(Prediction.fixture_id == fixture_id).first() is not None
        has_eval = db.query(ModelEvaluation).filter(ModelEvaluation.fixture_id == fixture_id).first() is not None
        has_stats = db.query(MatchStatistics).filter(MatchStatistics.fixture_id == fixture_id).first() is not None

        if raw_status in ["FINISHED", "FT", "AET", "PEN"]:
            if has_eval:
                state = "EVALUATED"
            elif has_stats:
                state = "VERIFIED"
            else:
                state = "FINISHED_PENDING_VERIFICATION"
        elif raw_status == "LIVE":
            state = "LIVE"
        elif has_pred:
            state = "PRE_MATCH_READY"
        else:
            state = "SCHEDULED"

        return {
            "fixture_id": fixture.id,
            "match_date": fixture.match_date.isoformat(),
            "raw_status": raw_status,
            "lifecycle_state": state,
            "has_predictions": has_pred,
            "has_verified_statistics": has_stats,
            "has_evaluations": has_eval
        }

    @classmethod
    def process_lifecycle_transition(cls, db: Session, fixture_id: int) -> Dict[str, Any]:
        """
        Executes automatic actions corresponding to current lifecycle position.
        """
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            return {"status": "error", "message": "Fixture not found"}

        now_utc = datetime.now(timezone.utc)
        # SQLite naive datetime handling
        match_time = fixture.match_date.replace(tzinfo=timezone.utc) if fixture.match_date.tzinfo is None else fixture.match_date

        # 1. Finished match -> Trigger verification and evaluation
        if fixture.status in ["FINISHED", "FT", "AET", "PEN"]:
            eval_count = ModelEvaluationService.evaluate_finished_fixture(db, fixture.id)
            return {
                "fixture_id": fixture.id,
                "transition": "EVALUATION_COMPLETED",
                "evaluations_created": eval_count
            }

        # 2. Upcoming match approaching kickoff (< 2 hours) -> Capture feature snapshot if not captured
        time_to_kickoff = (match_time - now_utc).total_seconds()
        if 0 <= time_to_kickoff <= 7200:
            try:
                snap = FeatureStoreService.capture_feature_snapshot(db, fixture.id)
                return {
                    "fixture_id": fixture.id,
                    "transition": "PRE_MATCH_READY",
                    "feature_snapshot_id": snap.id
                }
            except Exception as ex:
                logger.debug(f"Feature snapshot capture skipped: {ex}")

        return {
            "fixture_id": fixture.id,
            "transition": "NO_ACTION_REQUIRED",
            "current_status": fixture.status
        }
