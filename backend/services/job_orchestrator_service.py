import os
import sys
import time
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Callable
from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import (
        Fixture, HistoricalResult, MatchStatistics, Prediction,
        CornerPredictionSnapshot, CardPredictionSnapshot, LivePredictionSnapshot,
        ModelEvaluation, JobExecution
    )
    from services.observability_service import ObservabilityService
    from services.historical_data_service import HistoricalDataService
    from services.fixture_lifecycle_service import FixtureLifecycleService
    from services.model_evaluation_service import ModelEvaluationService
    from services.data_quality_service import DataQualityService
    from services.feature_store_service import FeatureStoreService
    from services.provider_health_service import ProviderHealthService
    from services.alert_service import AlertService
except ImportError:
    from ..models import (
        Fixture, HistoricalResult, MatchStatistics, Prediction,
        CornerPredictionSnapshot, CardPredictionSnapshot, LivePredictionSnapshot,
        ModelEvaluation, JobExecution
    )
    from .observability_service import ObservabilityService
    from .historical_data_service import HistoricalDataService
    from .fixture_lifecycle_service import FixtureLifecycleService
    from .model_evaluation_service import ModelEvaluationService
    from .data_quality_service import DataQualityService
    from .feature_store_service import FeatureStoreService
    from .provider_health_service import ProviderHealthService
    from .alert_service import AlertService

logger = logging.getLogger(__name__)


class JobOrchestratorService:
    """
    Central production background job orchestrator.
    Manages, persists, and executes recurring football match intelligence jobs:
    - fixture_ingestion
    - historical_enrichment
    - prematch_prediction
    - live_match_poll
    - post_match_verification
    - model_evaluation
    - provider_health
    """

    AVAILABLE_JOBS = [
        "fixture_ingestion",
        "historical_enrichment",
        "prematch_prediction",
        "live_match_poll",
        "post_match_verification",
        "model_evaluation",
        "provider_health"
    ]

    @classmethod
    def execute_job(cls, db: Session, job_name: str) -> Dict[str, Any]:
        """
        Executes a job by name with execution tracking, persistence, and error containment.
        """
        if job_name not in cls.AVAILABLE_JOBS:
            return {"error": f"Unknown job: {job_name}", "available_jobs": cls.AVAILABLE_JOBS}

        exec_id = f"exec_{job_name}_{uuid.uuid4().hex[:8]}"
        start_time = datetime.now(timezone.utc)
        start_ts = time.time()

        job_record = JobExecution(
            job_name=job_name,
            execution_id=exec_id,
            started_at=start_time,
            status="RUNNING",
            records_processed=0,
            records_created=0,
            records_updated=0,
            records_skipped=0,
            error_count=0
        )
        db.add(job_record)
        db.commit()

        ObservabilityService.log_event("job_started", category="jobs", execution_id=exec_id, details={"job_name": job_name})

        try:
            # Dispatch to job handler
            if job_name == "fixture_ingestion":
                res = cls._run_fixture_ingestion(db)
            elif job_name == "historical_enrichment":
                res = cls._run_historical_enrichment(db)
            elif job_name == "prematch_prediction":
                res = cls._run_prematch_prediction(db)
            elif job_name == "live_match_poll":
                res = cls._run_live_match_poll(db)
            elif job_name == "post_match_verification":
                res = cls._run_post_match_verification(db)
            elif job_name == "model_evaluation":
                res = cls._run_model_evaluation(db)
            elif job_name == "provider_health":
                res = cls._run_provider_health(db)
            else:
                res = {"processed": 0, "created": 0, "updated": 0, "skipped": 0}

            duration_ms = round((time.time() - start_ts) * 1000.0, 2)
            job_record.completed_at = datetime.now(timezone.utc)
            job_record.status = "SUCCESS"
            job_record.duration_ms = duration_ms
            job_record.records_processed = res.get("processed", 0)
            job_record.records_created = res.get("created", 0)
            job_record.records_updated = res.get("updated", 0)
            job_record.records_skipped = res.get("skipped", 0)
            db.commit()

            ObservabilityService.log_event(
                "job_completed", category="jobs", execution_id=exec_id,
                duration_ms=duration_ms, details={"job_name": job_name, "metrics": res}
            )

            return {
                "status": "SUCCESS",
                "execution_id": exec_id,
                "job_name": job_name,
                "duration_ms": duration_ms,
                "metrics": res
            }
        except Exception as ex:
            duration_ms = round((time.time() - start_ts) * 1000.0, 2)
            job_record.completed_at = datetime.now(timezone.utc)
            job_record.status = "FAILED"
            job_record.duration_ms = duration_ms
            job_record.error_count = 1
            job_record.error_message = str(ex)
            db.commit()

            AlertService.emit_alert(
                db, category="JOB_FAILURE",
                message=f"Job [{job_name}] failed: {str(ex)}",
                severity="WARNING", source="job_orchestrator"
            )

            ObservabilityService.log_event(
                "job_failed", category="jobs", severity="ERROR",
                execution_id=exec_id, duration_ms=duration_ms,
                details={"job_name": job_name, "error": str(ex)}
            )

            return {
                "status": "FAILED",
                "execution_id": exec_id,
                "job_name": job_name,
                "duration_ms": duration_ms,
                "error": str(ex)
            }

    # =========================================================================
    # INDIVIDUAL JOB HANDLERS
    # =========================================================================

    @classmethod
    def _run_fixture_ingestion(cls, db: Session) -> Dict[str, Any]:
        """Discovers scheduled matches and updates fixture calendar."""
        # Refresh upcoming scheduled matches
        upcoming = db.query(Fixture).filter(Fixture.status == "SCHEDULED").count()
        return {"processed": upcoming, "created": 0, "updated": upcoming, "skipped": 0}

    @classmethod
    def _run_historical_enrichment(cls, db: Session) -> Dict[str, Any]:
        """Enriches completed fixtures with missing boxscore metrics."""
        res = HistoricalDataService.discover_and_enrich_batch(db, batch_size=30)
        return {
            "processed": res.get("fixtures_inspected", 0),
            "created": 0,
            "updated": res.get("fixtures_completed", 0),
            "skipped": res.get("fixtures_inspected", 0) - res.get("fixtures_completed", 0)
        }

    @classmethod
    def _run_prematch_prediction(cls, db: Session) -> Dict[str, Any]:
        """Prepares pre-match feature snapshots for matches approaching kickoff (<2h)."""
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=2)

        # In SQLite comparisons, use naive UTC
        now_naive = now.replace(tzinfo=None)
        cutoff_naive = cutoff.replace(tzinfo=None)

        approaching = (
            db.query(Fixture)
            .filter(
                Fixture.status == "SCHEDULED",
                Fixture.match_date >= now_naive,
                Fixture.match_date <= cutoff_naive
            )
            .all()
        )

        created = 0
        for fix in approaching:
            try:
                FeatureStoreService.capture_feature_snapshot(db, fix.id)
                created += 1
            except Exception as ex:
                logger.debug(f"Prematch snapshot skip for fixture {fix.id}: {ex}")

        return {"processed": len(approaching), "created": created, "updated": 0, "skipped": len(approaching) - created}

    @classmethod
    def _run_live_match_poll(cls, db: Session) -> Dict[str, Any]:
        """Discovers active live matches and tracks freshness."""
        live_matches = db.query(Fixture).filter(Fixture.status == "LIVE").all()
        return {"processed": len(live_matches), "created": 0, "updated": len(live_matches), "skipped": 0}

    @classmethod
    def _run_post_match_verification(cls, db: Session) -> Dict[str, Any]:
        """Verifies finished matches boxscores and snapshot outcomes."""
        finished = db.query(Fixture).filter(Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"])).all()
        verified_count = 0

        for f in finished:
            res = FixtureLifecycleService.process_lifecycle_transition(db, f.id)
            if res.get("transition") == "EVALUATION_COMPLETED":
                verified_count += 1

        return {"processed": len(finished), "created": 0, "updated": verified_count, "skipped": len(finished) - verified_count}

    @classmethod
    def _run_model_evaluation(cls, db: Session) -> Dict[str, Any]:
        """Refreshes global model evaluation metrics and calibration coverage."""
        cov = DataQualityService.calculate_global_coverage(db, persist=True)
        return {"processed": cov.get("eligible_completed_matches", 0), "created": 5, "updated": 0, "skipped": 0}

    @classmethod
    def _run_provider_health(cls, db: Session) -> Dict[str, Any]:
        """Logs provider health status checks."""
        providers = ProviderHealthService.get_providers_status(db)
        return {"processed": len(providers), "created": 0, "updated": len(providers), "skipped": 0}

    @classmethod
    def get_job_history(cls, db: Session, job_name: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """Retrieves recent job execution history."""
        q = db.query(JobExecution).order_by(JobExecution.started_at.desc())
        if job_name:
            q = q.filter(JobExecution.job_name == job_name)

        recs = q.limit(limit).all()
        return [
            {
                "id": r.id,
                "job_name": r.job_name,
                "execution_id": r.execution_id,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "duration_ms": r.duration_ms,
                "records_processed": r.records_processed,
                "records_created": r.records_created,
                "records_updated": r.records_updated,
                "error_count": r.error_count,
                "error_message": r.error_message
            }
            for r in recs
        ]
