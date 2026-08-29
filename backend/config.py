import os
from typing import Dict, Any

class Settings:
    """
    Centralized configuration management for Soccer Goal Predictor / Match Intelligence Platform.
    Provides environment-driven configuration with safe development defaults.
    """
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./backend/soccer.db")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    # Provider Settings
    ESPN_API_ENABLED: bool = os.getenv("ESPN_API_ENABLED", "true").lower() == "true"
    FOOTBALL_DATA_API_KEY: str = os.getenv("FOOTBALL_DATA_API_KEY", "")
    API_FOOTBALL_KEY: str = os.getenv("API_FOOTBALL_KEY", "")
    
    # Retry & Backoff
    RETRY_MAX_ATTEMPTS: int = int(os.getenv("RETRY_MAX_ATTEMPTS", "5"))
    RETRY_INITIAL_BACKOFF_SEC: float = float(os.getenv("RETRY_INITIAL_BACKOFF_SEC", "1.0"))
    RETRY_MAX_BACKOFF_SEC: float = float(os.getenv("RETRY_MAX_BACKOFF_SEC", "30.0"))
    RETRY_BACKOFF_FACTOR: float = float(os.getenv("RETRY_BACKOFF_FACTOR", "2.0"))
    
    # Circuit Breaker
    CIRCUIT_FAILURE_THRESHOLD: int = int(os.getenv("CIRCUIT_FAILURE_THRESHOLD", "5"))
    CIRCUIT_RECOVERY_TIMEOUT_SEC: float = float(os.getenv("CIRCUIT_RECOVERY_TIMEOUT_SEC", "60.0"))
    CIRCUIT_HALF_OPEN_SUCCESS_THRESHOLD: int = int(os.getenv("CIRCUIT_HALF_OPEN_SUCCESS_THRESHOLD", "2"))
    
    # Model Validation Gates
    MODEL_GATE_INSUFFICIENT_SAMPLE: int = int(os.getenv("MODEL_GATE_INSUFFICIENT_SAMPLE", "100"))
    MODEL_GATE_VALIDATING_SAMPLE: int = int(os.getenv("MODEL_GATE_VALIDATING_SAMPLE", "300"))
    MODEL_GATE_MAX_VALIDATED_ECE: float = float(os.getenv("MODEL_GATE_MAX_VALIDATED_ECE", "0.07"))
    
    # Live Feed Freshness
    FRESH_FEED_SEC: int = int(os.getenv("FRESH_FEED_SEC", "60"))
    DELAYED_FEED_SEC: int = int(os.getenv("DELAYED_FEED_SEC", "180"))
    STALE_FEED_SEC: int = int(os.getenv("STALE_FEED_SEC", "300"))
    
    # Scheduled Job Intervals (seconds)
    INTERVAL_FIXTURE_INGESTION: int = int(os.getenv("INTERVAL_FIXTURE_INGESTION", "21600")) # 6 hours
    INTERVAL_HISTORICAL_ENRICHMENT: int = int(os.getenv("INTERVAL_HISTORICAL_ENRICHMENT", "43200")) # 12 hours
    INTERVAL_PREMATCH_PREDICTION: int = int(os.getenv("INTERVAL_PREMATCH_PREDICTION", "1800")) # 30 mins
    INTERVAL_LIVE_POLL: int = int(os.getenv("INTERVAL_LIVE_POLL", "30")) # 30 secs
    INTERVAL_POST_MATCH_VERIFICATION: int = int(os.getenv("INTERVAL_POST_MATCH_VERIFICATION", "600")) # 10 mins
    INTERVAL_MODEL_EVALUATION: int = int(os.getenv("INTERVAL_MODEL_EVALUATION", "3600")) # 1 hour
    INTERVAL_PROVIDER_HEALTH: int = int(os.getenv("INTERVAL_PROVIDER_HEALTH", "300")) # 5 mins
    
    # Backups
    BACKUP_DIRECTORY: str = os.getenv("BACKUP_DIRECTORY", os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups"))

    @classmethod
    def get_sanitized_summary(cls) -> Dict[str, Any]:
        """Returns non-sensitive operational configuration overview."""
        return {
            "environment": cls.ENVIRONMENT,
            "database_url": cls.DATABASE_URL.split("?")[0],
            "log_level": cls.LOG_LEVEL,
            "retry_max_attempts": cls.RETRY_MAX_ATTEMPTS,
            "circuit_failure_threshold": cls.CIRCUIT_FAILURE_THRESHOLD,
            "model_gates": {
                "insufficient": cls.MODEL_GATE_INSUFFICIENT_SAMPLE,
                "validating": cls.MODEL_GATE_VALIDATING_SAMPLE,
                "max_ece": cls.MODEL_GATE_MAX_VALIDATED_ECE
            },
            "freshness_thresholds": {
                "fresh_sec": cls.FRESH_FEED_SEC,
                "delayed_sec": cls.DELAYED_FEED_SEC,
                "stale_sec": cls.STALE_FEED_SEC
            }
        }


settings = Settings()
DATABASE_URL = settings.DATABASE_URL
CORS_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://localhost:5000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
    "https://over1-5.onrender.com",
    "https://over1-5-web.onrender.com",
    "*"
]
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")
