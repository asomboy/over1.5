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
    from models import ProviderHealth, LiveMatchState
except ImportError:
    from ..models import ProviderHealth, LiveMatchState

logger = logging.getLogger(__name__)


class ProviderHealthService:
    """
    Provider health monitoring and live feed freshness service.
    Tracks operational health of external APIs and assesses live in-play data latency.
    """

    @classmethod
    def record_provider_event(
        cls,
        db: Session,
        provider: str,
        success: bool,
        latency_ms: float = 0.0,
        missing_data: bool = False
    ) -> None:
        """Logs an operational request event for an external provider."""
        try:
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
            log = (
                db.query(ProviderHealth)
                .filter(ProviderHealth.provider == provider, ProviderHealth.timestamp >= today_start)
                .first()
            )

            if not log:
                log = ProviderHealth(
                    provider=provider,
                    timestamp=datetime.now(timezone.utc),
                    request_count=0,
                    success_count=0,
                    failure_count=0,
                    average_latency_ms=0.0,
                    missing_data_rate=0.0,
                    status="HEALTHY"
                )
                db.add(log)

            log.request_count += 1
            if success:
                log.success_count += 1
            else:
                log.failure_count += 1

            # Update rolling average latency
            prev_total = log.average_latency_ms * (log.request_count - 1)
            log.average_latency_ms = round((prev_total + latency_ms) / max(1, log.request_count), 2)

            fail_rate = log.failure_count / max(1, log.request_count)
            if fail_rate > 0.40:
                log.status = "UNAVAILABLE"
            elif fail_rate > 0.15:
                log.status = "DEGRADED"
            else:
                log.status = "HEALTHY"

            db.commit()
        except Exception as ex:
            logger.debug(f"Error logging provider health: {ex}")

    @classmethod
    def get_providers_status(cls, db: Session) -> List[Dict[str, Any]]:
        """Returns current operational status across known external data providers."""
        providers = ["espn", "football_data", "api_football"]
        results = []

        for p in providers:
            log = (
                db.query(ProviderHealth)
                .filter(ProviderHealth.provider == p)
                .order_by(ProviderHealth.timestamp.desc())
                .first()
            )

            if log:
                results.append({
                    "provider": p,
                    "status": log.status,
                    "request_count": log.request_count,
                    "success_count": log.success_count,
                    "failure_count": log.failure_count,
                    "average_latency_ms": log.average_latency_ms,
                    "last_updated": log.timestamp.isoformat() if log.timestamp else None
                })
            else:
                results.append({
                    "provider": p,
                    "status": "HEALTHY",
                    "request_count": 0,
                    "success_count": 0,
                    "failure_count": 0,
                    "average_latency_ms": 0.0,
                    "last_updated": None
                })

        return results

    FRESH_THRESHOLD_SEC = 60
    DELAYED_THRESHOLD_SEC = 180
    STALE_THRESHOLD_SEC = 300

    @classmethod
    def evaluate_live_feed_freshness(
        cls,
        last_updated_time: Optional[datetime],
        fresh_sec: Optional[int] = None,
        delayed_sec: Optional[int] = None,
        stale_sec: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Assesses live match feed freshness based on seconds since last update.
        FRESH (< 60s), DELAYED (60-180s), STALE (180-300s), VERY_STALE (> 300s), UNAVAILABLE.
        Thresholds are configurable.
        """
        th_fresh = fresh_sec or cls.FRESH_THRESHOLD_SEC
        th_delayed = delayed_sec or cls.DELAYED_THRESHOLD_SEC
        th_stale = stale_sec or cls.STALE_THRESHOLD_SEC

        if not last_updated_time:
            return {
                "freshness_state": "UNAVAILABLE",
                "freshness_status": "UNAVAILABLE",
                "age_seconds": None,
                "confidence_penalty": 0.35,
                "retrieved_at": None
            }

        now_utc = datetime.now(timezone.utc)
        # SQLite naive timestamp adjustment
        ts = last_updated_time.replace(tzinfo=timezone.utc) if last_updated_time.tzinfo is None else last_updated_time
        age = max(0, int((now_utc - ts).total_seconds()))

        if age < th_fresh:
            state = "FRESH"
            penalty = 0.0
        elif age <= th_delayed:
            state = "DELAYED"
            penalty = 0.10
        elif age <= th_stale:
            state = "STALE"
            penalty = 0.25
        else:
            state = "VERY_STALE"
            penalty = 0.40

        return {
            "freshness_state": state,
            "freshness_status": state,
            "age_seconds": age,
            "confidence_penalty": penalty,
            "retrieved_at": ts.isoformat()
        }

