import time
import random
import logging
from typing import Callable, Any, Optional, Dict, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class ErrorCategory(str, Enum):
    TRANSIENT = "TRANSIENT"
    RATE_LIMITED = "RATE_LIMITED"
    AUTHENTICATION = "AUTHENTICATION"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION = "VALIDATION"
    PERMANENT = "PERMANENT"
    UNKNOWN = "UNKNOWN"


class RetryService:
    """
    Exponential backoff and retry management with jitter and error categorization.
    Prevents endless loops on permanent errors (auth, 404, validation).
    """

    @classmethod
    def classify_error(cls, exc: Exception) -> ErrorCategory:
        """Classifies exception to determine whether retry is eligible."""
        msg = str(exc).lower()
        if any(w in msg for w in ["401", "403", "unauthorized", "forbidden", "invalid api key"]):
            return ErrorCategory.AUTHENTICATION
        elif any(w in msg for w in ["404", "not found"]):
            return ErrorCategory.NOT_FOUND
        elif any(w in msg for w in ["422", "validation error", "valueerror"]):
            return ErrorCategory.VALIDATION
        elif any(w in msg for w in ["429", "rate limit", "too many requests"]):
            return ErrorCategory.RATE_LIMITED
        elif any(w in msg for w in ["timeout", "connection reset", "502", "503", "504", "temporary failure", "network"]):
            return ErrorCategory.TRANSIENT
        else:
            return ErrorCategory.UNKNOWN

    @classmethod
    def execute_with_retry(
        cls,
        func: Callable[..., Any],
        *args: Any,
        max_attempts: int = 4,
        initial_backoff: float = 0.5,
        backoff_factor: float = 2.0,
        max_backoff: float = 10.0,
        jitter: bool = True,
        **kwargs: Any
    ) -> Any:
        """
        Executes a callable with exponential backoff and jitter.
        Aborts immediately on non-retryable error categories.
        """
        last_exception = None
        backoff = initial_backoff

        for attempt in range(1, max_attempts + 1):
            try:
                return func(*args, **kwargs)
            except Exception as ex:
                last_exception = ex
                category = cls.classify_error(ex)

                # Permanent failures must not retry
                if category in [ErrorCategory.AUTHENTICATION, ErrorCategory.NOT_FOUND, ErrorCategory.VALIDATION, ErrorCategory.PERMANENT]:
                    logger.warning(f"Aborting retry on permanent error [{category.value}]: {ex}")
                    raise ex

                if attempt == max_attempts:
                    logger.error(f"Max retry attempts ({max_attempts}) reached for {func.__name__}: {ex}")
                    raise ex

                # Calculate backoff with jitter
                sleep_time = min(max_backoff, backoff)
                if jitter:
                    sleep_time = sleep_time * (0.8 + 0.4 * random.random())

                logger.debug(f"Attempt {attempt}/{max_attempts} failed ({category.value}). Retrying in {sleep_time:.2f}s...")
                time.sleep(sleep_time)
                backoff *= backoff_factor

        if last_exception:
            raise last_exception
