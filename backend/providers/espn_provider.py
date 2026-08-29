import time
import requests
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from .base_provider import BaseProvider, ProviderResult
from services.retry_service import RetryService
from services.circuit_breaker_service import CircuitBreakerService

logger = logging.getLogger(__name__)


class ESPNProvider(BaseProvider):
    """
    Standardized provider adapter for ESPN FC Public APIs.
    Normalizes fixture metadata, live in-play states, corners, disciplinary statistics, and referee names.
    Protected by exponential backoff and circuit breaker boundaries.
    """

    def __init__(self, name: str = "espn", timeout_sec: float = 8.0):
        super().__init__(name)
        self.timeout_sec = timeout_sec
        self.base_url = "https://site.api.espn.com/apis/site/v2/sports/soccer"

    def _execute_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> ProviderResult:
        """Internal helper executing HTTP request through retry and circuit breaker boundaries."""
        circuit = CircuitBreakerService.get_circuit(self.name)
        if not circuit.can_execute():
            return ProviderResult(
                provider=self.name,
                success=False,
                error_type="CIRCUIT_OPEN",
                error_message="Circuit breaker is OPEN. Calls to ESPN are temporarily paused.",
                data_quality="degraded"
            )

        start_time = time.time()

        def _raw_call():
            url = f"{self.base_url}/{endpoint}" if not endpoint.startswith("http") else endpoint
            resp = requests.get(url, params=params, timeout=self.timeout_sec)
            resp.raise_for_status()
            return resp

        try:
            resp = RetryService.execute_with_retry(_raw_call, max_attempts=3, initial_backoff=0.5)
            latency = round((time.time() - start_time) * 1000.0, 2)
            circuit.record_success()
            return ProviderResult(
                provider=self.name,
                success=True,
                data=resp.json(),
                status_code=resp.status_code,
                latency_ms=latency,
                data_quality="observed"
            )
        except Exception as ex:
            latency = round((time.time() - start_time) * 1000.0, 2)
            circuit.record_failure()
            category = RetryService.classify_error(ex)
            return ProviderResult(
                provider=self.name,
                success=False,
                status_code=getattr(getattr(ex, "response", None), "status_code", None),
                latency_ms=latency,
                error_type=category.value,
                error_message=str(ex),
                data_quality="unavailable"
            )

    def fetch_upcoming_fixtures(self, days_ahead: int = 7) -> ProviderResult:
        """Fetches scoreboard across major soccer leagues."""
        return self._execute_request("scoreboards")

    def fetch_historical_results(self, date_str: str) -> ProviderResult:
        """Fetches scoreboard for a specific date (YYYYMMDD)."""
        return self._execute_request("scoreboards", params={"dates": date_str})

    def fetch_match_statistics(self, match_external_id: str) -> ProviderResult:
        """Fetches match summary boxscore containing corners, shots, fouls, cards, and referee."""
        return self._execute_request(f"summary", params={"event": match_external_id})

    def fetch_live_match_state(self, match_external_id: str) -> ProviderResult:
        """Fetches live match state and summary."""
        return self._execute_request(f"summary", params={"event": match_external_id})
