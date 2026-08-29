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
    from models import SystemAlert
    from services.observability_service import ObservabilityService
except ImportError:
    from ..models import SystemAlert
    from .observability_service import ObservabilityService

logger = logging.getLogger(__name__)

DEFAULT_ALERT_COOLDOWN_MINUTES = 30


class AlertService:
    """
    Operational Alerting System with deduplication, cooldown suppression, and resolution management.
    """

    @classmethod
    def emit_alert(
        cls,
        db: Session,
        category: str,
        message: str,
        severity: str = "WARNING",
        source: str = "system",
        cooldown_minutes: int = DEFAULT_ALERT_COOLDOWN_MINUTES
    ) -> Optional[SystemAlert]:
        """
        Emits an alert if not currently suppressed by an active alert cooldown in the same category.
        """
        now = datetime.now(timezone.utc)
        # SQLite naive datetime handling
        now_naive = now.replace(tzinfo=None)

        # Check existing active alert in same category & message
        existing = (
            db.query(SystemAlert)
            .filter(
                SystemAlert.category == category,
                SystemAlert.is_active == True
            )
            .first()
        )

        if existing:
            # Check if cooldown is active
            if existing.cooldown_until and existing.cooldown_until > now_naive:
                logger.debug(f"Alert [{category}] suppressed due to active cooldown.")
                return existing

            # Update existing alert timestamp & cooldown
            existing.message = message
            existing.severity = severity
            existing.cooldown_until = now_naive + timedelta(minutes=cooldown_minutes)
            db.commit()
            return existing

        # Create new alert
        alert_id = f"alt_{category.lower()}_{int(now.timestamp())}"
        alert = SystemAlert(
            alert_id=alert_id,
            category=category,
            severity=severity,
            message=message,
            source=source,
            is_active=True,
            cooldown_until=now_naive + timedelta(minutes=cooldown_minutes),
            created_at=now
        )
        db.add(alert)
        db.commit()

        ObservabilityService.log_event(
            "system_alert_emitted",
            category="alert",
            severity=severity,
            details={"alert_id": alert_id, "category": category, "message": message}
        )

        return alert

    @classmethod
    def resolve_alert(cls, db: Session, alert_id: str) -> bool:
        """Resolves an active alert."""
        alert = db.query(SystemAlert).filter(SystemAlert.alert_id == alert_id).first()
        if not alert:
            return False

        alert.is_active = False
        alert.resolved_at = datetime.now(timezone.utc)
        db.commit()
        return True

    @classmethod
    def get_active_alerts(cls, db: Session) -> List[Dict[str, Any]]:
        """Returns list of all active operational alerts."""
        alerts = (
            db.query(SystemAlert)
            .filter(SystemAlert.is_active == True)
            .order_by(SystemAlert.created_at.desc())
            .all()
        )

        return [
            {
                "alert_id": a.alert_id,
                "category": a.category,
                "severity": a.severity,
                "message": a.message,
                "source": a.source,
                "created_at": a.created_at.isoformat() if a.created_at else None
            }
            for a in alerts
        ]
