import os
import sys
import re
import logging
from enum import Enum
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


class CanonicalLifecycleState(str, Enum):
    """
    Deterministic 13-state fixture lifecycle state machine for production integrity.
    Every fixture maps cleanly to one of these states.
    """
    SCHEDULED = "SCHEDULED"
    PRE_MATCH = "PRE_MATCH"
    LIVE = "LIVE"
    HALFTIME = "HALFTIME"
    LIVE_2H = "LIVE_2H"
    EXTRA_TIME = "EXTRA_TIME"
    PENALTY_SHOOTOUT = "PENALTY_SHOOTOUT"
    FINISHED = "FINISHED"
    POSTPONED = "POSTPONED"
    CANCELLED = "CANCELLED"
    ABANDONED = "ABANDONED"
    SUSPENDED = "SUSPENDED"
    UNKNOWN = "UNKNOWN"


class FixtureLifecycleService:
    """
    Centralized Fixture Lifecycle State Machine & Normalizer.
    Manages deterministic state normalization and automated lifecycle transitions.
    """

    @classmethod
    def normalize_lifecycle_state(
        cls,
        status: Optional[str],
        clock: Optional[str] = None,
        minute: Optional[int] = None,
        kickoff: Optional[datetime] = None
    ) -> CanonicalLifecycleState:
        """
        Normalizes any provider status string into one of the 13 canonical lifecycle states.
        """
        if not status:
            return CanonicalLifecycleState.UNKNOWN

        s = str(status).strip().upper()
        c = str(clock or "").strip().upper()
        tokens = set(re.split(r'[^A-Z0-9]+', s)) - {""}
        c_tokens = set(re.split(r'[^A-Z0-9]+', c)) - {""}

        # 1. Cancelled / Abandoned / Suspended / Postponed
        if "CANCEL" in s or "CANC" in tokens:
            return CanonicalLifecycleState.CANCELLED
        if "ABANDON" in s or "ABD" in tokens:
            return CanonicalLifecycleState.ABANDONED
        if "SUSPEND" in s or "INTERRUPT" in s or "SUSP" in tokens:
            return CanonicalLifecycleState.SUSPENDED
        if "POSTPONE" in s or "P-P" in s or "DELAYED_START" in s or "PPD" in tokens:
            return CanonicalLifecycleState.POSTPONED

        # 2. Shootout / Extra Time (must precede generic FULL/FINAL token matching when explicit)
        if "SHOOTOUT" in s or "PENALTIES" in s or bool(tokens & {"PK", "PEN"}):
            return CanonicalLifecycleState.PENALTY_SHOOTOUT
        if "EXTRA_TIME" in s or bool(tokens & {"AET", "ET", "1ET", "2ET"}):
            return CanonicalLifecycleState.EXTRA_TIME

        # 3. Finished
        if any(x in s for x in ["FINISHED", "FULL_TIME", "ENDED"]) or bool(tokens & {"FINAL", "FT"}):
            return CanonicalLifecycleState.FINISHED

        # 4. Halftime
        if any(x in s for x in ["HALFTIME", "HALF_TIME"]) or bool(tokens & {"HT", "MID"}):
            return CanonicalLifecycleState.HALFTIME
        if "HALF" in c or "HT" in c_tokens:
            return CanonicalLifecycleState.HALFTIME

        # 5. Live Second Half
        is_live_tok = ("IN_PROGRESS" in s or "IN_PLAY" in s or bool(tokens & {"LIVE", "INPROGRESS", "INPLAY"}))
        if "SECOND_HALF" in s or "2H" in tokens:
            return CanonicalLifecycleState.LIVE_2H
        if minute is not None and minute > 45 and is_live_tok:
            return CanonicalLifecycleState.LIVE_2H

        # 6. Live First Half / Generic Live
        if "FIRST_HALF" in s or "1H" in tokens or is_live_tok:
            return CanonicalLifecycleState.LIVE

        # 7. Pre-match
        is_sched_tok = ("NOT_STARTED" in s or bool(tokens & {"SCHEDULED", "TIMED", "NS"}))
        if any(x in s for x in ["PRE_MATCH", "WARMUP", "LINEUPS"]):
            return CanonicalLifecycleState.PRE_MATCH
        if kickoff and is_sched_tok:
            try:
                now_utc = datetime.now(timezone.utc)
                k_utc = kickoff.replace(tzinfo=timezone.utc) if kickoff.tzinfo is None else kickoff
                diff_sec = (k_utc - now_utc).total_seconds()
                if 0 <= diff_sec <= 1800:
                    return CanonicalLifecycleState.PRE_MATCH
            except Exception:
                pass

        # 8. Scheduled
        if is_sched_tok or "PRE" in tokens or "PRE_EVENT" in s:
            return CanonicalLifecycleState.SCHEDULED

        return CanonicalLifecycleState.UNKNOWN

    @classmethod
    def get_canonical_lifecycle(cls, fixture: Fixture, minute: Optional[int] = None) -> CanonicalLifecycleState:
        """Resolves the canonical lifecycle state for a database Fixture."""
        if not fixture:
            return CanonicalLifecycleState.UNKNOWN
        return cls.normalize_lifecycle_state(
            status=fixture.status,
            clock=fixture.live_clock,
            minute=minute,
            kickoff=fixture.match_date
        )

    @classmethod
    def get_fixture_lifecycle_state(cls, db: Session, fixture_id: int) -> Dict[str, Any]:
        """Calculates lifecycle state for a given fixture (retaining backward compatibility)."""
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

        canonical_state = cls.normalize_lifecycle_state(raw_status, fixture.live_clock, None, fixture.match_date)

        return {
            "fixture_id": fixture.id,
            "match_date": (fixture.match_date.isoformat() + "Z") if (fixture.match_date and fixture.match_date.tzinfo is None) else (fixture.match_date.isoformat() if fixture.match_date else None),
            "raw_status": raw_status,
            "lifecycle_state": state,
            "canonical_lifecycle": canonical_state.value,
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
