import time
import json
import logging
import uuid
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from contextlib import contextmanager

logger = logging.getLogger("match_intelligence")


class ObservabilityService:
    """
    Structured logging and operational tracing for Match Intelligence workflows.
    Produces structured JSON logs with correlation IDs, execution timings, and domain contexts.
    """

    @classmethod
    def generate_execution_id(cls, prefix: str = "exec") -> str:
        """Generates unique correlation execution ID."""
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    @classmethod
    def log_event(
        cls,
        event_name: str,
        category: str, # ingestion, prediction, live_poll, lifecycle, evaluation, provider, alert
        severity: str = "INFO",
        execution_id: Optional[str] = None,
        duration_ms: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Outputs structured operational event log."""
        log_payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event_name,
            "category": category,
            "severity": severity,
            "execution_id": execution_id or cls.generate_execution_id(),
            "duration_ms": duration_ms,
            "details": details or {}
        }

        msg = json.dumps(log_payload)
        if severity == "ERROR" or severity == "CRITICAL":
            logger.error(msg)
        elif severity == "WARNING":
            logger.warning(msg)
        else:
            logger.info(msg)

        return log_payload

    @classmethod
    @contextmanager
    def trace_operation(cls, operation_name: str, category: str, execution_id: Optional[str] = None):
        """Context manager measuring execution duration and logging structured start/finish events."""
        exec_id = execution_id or cls.generate_execution_id()
        start = time.time()
        cls.log_event(f"{operation_name}_started", category, severity="INFO", execution_id=exec_id)
        try:
            yield exec_id
            elapsed = round((time.time() - start) * 1000.0, 2)
            cls.log_event(f"{operation_name}_completed", category, severity="INFO", execution_id=exec_id, duration_ms=elapsed)
        except Exception as ex:
            elapsed = round((time.time() - start) * 1000.0, 2)
            cls.log_event(
                f"{operation_name}_failed", category, severity="ERROR",
                execution_id=exec_id, duration_ms=elapsed, details={"error": str(ex)}
            )
            raise ex
