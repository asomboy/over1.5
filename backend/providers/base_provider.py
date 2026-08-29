from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class ProviderResult:
    """Standardized normalized response payload from any external data provider."""
    provider: str
    success: bool
    data: Optional[Any] = None
    status_code: Optional[int] = None
    latency_ms: float = 0.0
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    data_quality: str = "observed" # observed, verified, degraded
    fetched_at: str = datetime.now(timezone.utc).isoformat()


class BaseProvider(ABC):
    """
    Abstract Base Class defining the provider interface for football match data.
    All external provider implementations must normalize incoming JSON to domain dictionaries.
    """

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def fetch_upcoming_fixtures(self, days_ahead: int = 7) -> ProviderResult:
        """Fetches upcoming scheduled matches."""
        pass

    @abstractmethod
    def fetch_historical_results(self, date_str: str) -> ProviderResult:
        """Fetches final scores and boxscores for a specific date."""
        pass

    @abstractmethod
    def fetch_match_statistics(self, match_external_id: str) -> ProviderResult:
        """Fetches detailed match statistics (corners, cards, possession, fouls, referee)."""
        pass

    @abstractmethod
    def fetch_live_match_state(self, match_external_id: str) -> ProviderResult:
        """Fetches in-play live match state (clock, scores, live corners/cards/pressure)."""
        pass
