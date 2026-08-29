import time
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class CircuitState:
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """
    In-memory and persistent circuit breaker protecting against cascading external provider failures.
    Transitions: CLOSED -> OPEN (on failure threshold) -> HALF_OPEN (after recovery timeout) -> CLOSED (on probe success).
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout_sec: float = 60.0,
        half_open_success_threshold: int = 2
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_sec = recovery_timeout_sec
        self.half_open_success_threshold = half_open_success_threshold
        
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_state_change = time.time()
        self.last_failure_time = 0.0

    def can_execute(self) -> bool:
        """Determines whether a call is allowed through the circuit."""
        now = time.time()
        if self.state == CircuitState.CLOSED:
            return True
        elif self.state == CircuitState.OPEN:
            if (now - self.last_state_change) >= self.recovery_timeout_sec:
                logger.info(f"Circuit Breaker [{self.name}]: Recovery timeout elapsed. Probing in HALF_OPEN state.")
                self.state = CircuitState.HALF_OPEN
                self.success_count = 0
                self.last_state_change = now
                return True
            return False
        elif self.state == CircuitState.HALF_OPEN:
            # Allow limited probe requests
            return True
        return False

    def record_success(self) -> None:
        """Records a successful execution."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.half_open_success_threshold:
                logger.info(f"Circuit Breaker [{self.name}]: Probe successful. Circuit transitioning to CLOSED.")
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count = 0
                self.last_state_change = time.time()
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0

    def record_failure(self) -> None:
        """Records an execution failure."""
        now = time.time()
        self.last_failure_time = now
        if self.state == CircuitState.CLOSED:
            self.failure_count += 1
            if self.failure_count >= self.failure_threshold:
                logger.warning(f"Circuit Breaker [{self.name}]: Threshold {self.failure_threshold} reached. Circuit OPENED.")
                self.state = CircuitState.OPEN
                self.last_state_change = now
        elif self.state == CircuitState.HALF_OPEN:
            logger.warning(f"Circuit Breaker [{self.name}]: Probe failed in HALF_OPEN. Re-opening circuit.")
            self.state = CircuitState.OPEN
            self.last_state_change = now

    def get_status(self) -> Dict[str, Any]:
        """Returns circuit state diagnostics."""
        return {
            "name": self.name,
            "state": self.state,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_state_change": self.last_state_change,
            "is_available": self.can_execute()
        }


class CircuitBreakerService:
    """Registry maintaining circuit breakers for external data providers."""
    _circuits: Dict[str, CircuitBreaker] = {}

    @classmethod
    def get_circuit(
        cls,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout_sec: float = 60.0,
        half_open_success_threshold: int = 2
    ) -> CircuitBreaker:
        """Retrieves or creates named CircuitBreaker instance."""
        if name not in cls._circuits:
            cls._circuits[name] = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                recovery_timeout_sec=recovery_timeout_sec,
                half_open_success_threshold=half_open_success_threshold
            )
        return cls._circuits[name]

    @classmethod
    def can_call_provider(cls, provider: str) -> bool:
        """Helper to quickly check if provider circuit is allowing calls."""
        return cls.get_circuit(provider).can_execute()

    @classmethod
    def get_all_circuits_status(cls) -> Dict[str, Any]:
        """Returns diagnostic state across all registered circuits."""
        return {name: c.get_status() for name, c in cls._circuits.items()}
