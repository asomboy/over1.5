import json
import os
import sys
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import AlertPreference
except ImportError:
    from ..models import AlertPreference


DEFAULT_ALLOWED_MARKETS = [
    "At Least 1 More Goal",
    "Full Match Over 1.5 Goals",
    "Full Match Over 2.5 Goals",
    "Full Match Over 3.5 Goals",
]


class AlertPreferencesService:
    """Manages persisted alert thresholds, league/market filters, quiet hours, and notification toggles."""

    @staticmethod
    def _parse_json_list(raw: Optional[str], fallback: List[str]) -> List[str]:
        if not raw:
            return list(fallback)
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except Exception:
            pass
        return list(fallback)

    @classmethod
    def serialize(cls, row: AlertPreference) -> Dict[str, Any]:
        """Serializes an AlertPreference ORM row into clean JSON."""
        updated_iso = (
            row.updated_at.isoformat() + "Z"
            if row.updated_at and getattr(row.updated_at, "tzinfo", None) is None
            else (row.updated_at.isoformat() if row.updated_at else None)
        )
        return {
            "profile_key": row.profile_key,
            "telegram_live_enabled": bool(row.telegram_live_enabled),
            "telegram_digest_enabled": bool(row.telegram_digest_enabled),
            "min_live_probability": round(float(row.min_live_probability or 0.74), 2),
            "min_live_confidence": int(row.min_live_confidence or 70),
            "min_time_remaining": round(float(row.min_time_remaining or 10.0), 1),
            "allowed_markets": cls._parse_json_list(row.allowed_markets_json, DEFAULT_ALLOWED_MARKETS),
            "allowed_leagues": cls._parse_json_list(row.allowed_leagues_json, []),
            "quiet_hours_start": row.quiet_hours_start,
            "quiet_hours_end": row.quiet_hours_end,
            "browser_push_enabled": bool(row.browser_push_enabled),
            "audio_chime_enabled": bool(row.audio_chime_enabled),
            "updated_at": updated_iso,
        }

    @classmethod
    def get_or_create_preferences(
        cls, db: Session, profile_key: str = "default"
    ) -> Dict[str, Any]:
        """Fetches or initializes the persisted alert preferences row."""
        row = (
            db.query(AlertPreference)
            .filter(AlertPreference.profile_key == profile_key)
            .first()
        )
        if not row:
            row = AlertPreference(profile_key=profile_key)
            db.add(row)
            db.commit()
            db.refresh(row)
        return cls.serialize(row)

    @classmethod
    def update_preferences(
        cls, db: Session, payload: Dict[str, Any], profile_key: str = "default"
    ) -> Dict[str, Any]:
        """Validates bounds and persists updated alert rules and notification preferences."""
        row = (
            db.query(AlertPreference)
            .filter(AlertPreference.profile_key == profile_key)
            .first()
        )
        if not row:
            row = AlertPreference(profile_key=profile_key)
            db.add(row)

        if "telegram_live_enabled" in payload and payload["telegram_live_enabled"] is not None:
            row.telegram_live_enabled = bool(payload["telegram_live_enabled"])
        if "telegram_digest_enabled" in payload and payload["telegram_digest_enabled"] is not None:
            row.telegram_digest_enabled = bool(payload["telegram_digest_enabled"])
        if "min_live_probability" in payload and payload["min_live_probability"] is not None:
            prob = float(payload["min_live_probability"])
            row.min_live_probability = round(max(0.55, min(0.95, prob)), 2)
        if "min_live_confidence" in payload and payload["min_live_confidence"] is not None:
            conf = int(payload["min_live_confidence"])
            row.min_live_confidence = max(50, min(95, conf))
        if "min_time_remaining" in payload and payload["min_time_remaining"] is not None:
            rem = float(payload["min_time_remaining"])
            row.min_time_remaining = round(max(5.0, min(60.0, rem)), 1)
        if "allowed_markets" in payload and isinstance(payload["allowed_markets"], list):
            clean_m = [str(m).strip() for m in payload["allowed_markets"] if str(m).strip()][:20]
            row.allowed_markets_json = json.dumps(clean_m)
        if "allowed_leagues" in payload and isinstance(payload["allowed_leagues"], list):
            clean_l = [str(l).strip() for l in payload["allowed_leagues"] if str(l).strip()][:50]
            row.allowed_leagues_json = json.dumps(clean_l)
        if "quiet_hours_start" in payload:
            qs = payload["quiet_hours_start"]
            row.quiet_hours_start = max(0, min(23, int(qs))) if qs is not None else None
        if "quiet_hours_end" in payload:
            qe = payload["quiet_hours_end"]
            row.quiet_hours_end = max(0, min(23, int(qe))) if qe is not None else None
        if "browser_push_enabled" in payload and payload["browser_push_enabled"] is not None:
            row.browser_push_enabled = bool(payload["browser_push_enabled"])
        if "audio_chime_enabled" in payload and payload["audio_chime_enabled"] is not None:
            row.audio_chime_enabled = bool(payload["audio_chime_enabled"])

        db.commit()
        db.refresh(row)
        return cls.serialize(row)

    @staticmethod
    def is_within_quiet_hours(
        utc_hour: int, q_start: Optional[int], q_end: Optional[int]
    ) -> bool:
        """Returns True if UTC hour falls inside quiet hours [q_start, q_end), wrapping midnight."""
        if q_start is None or q_end is None or q_start == q_end:
            return False
        if q_start < q_end:
            return q_start <= utc_hour < q_end
        return utc_hour >= q_start or utc_hour < q_end

