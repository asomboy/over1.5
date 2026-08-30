import os
import sys
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import DataProvenance, DataConflict, Fixture
    from services.observability_service import ObservabilityService
except ImportError:
    from ..models import DataProvenance, DataConflict, Fixture
    from .observability_service import ObservabilityService

logger = logging.getLogger(__name__)


class DataReconciliationService:
    """
    Validates statistical integrity, logs field-level provider provenance,
    and detects conflicting data points between multiple provider feeds.
    """

    # =========================================================================
    # 1. STATISTICAL SANITY CHECKS
    # =========================================================================

    @classmethod
    def validate_goals(cls, home_score: Optional[int], away_score: Optional[int]) -> Tuple[bool, Optional[str]]:
        """Verifies goals sanity: non-negative integers."""
        if home_score is not None and home_score < 0:
            return False, f"Invalid negative home_score: {home_score}"
        if away_score is not None and away_score < 0:
            return False, f"Invalid negative away_score: {away_score}"
        return True, None

    @classmethod
    def validate_corners(cls, home_corners: Optional[int], away_corners: Optional[int]) -> Tuple[bool, Optional[str]]:
        """Verifies corners sanity: non-negative integers."""
        if home_corners is not None and home_corners < 0:
            return False, f"Invalid negative home_corners: {home_corners}"
        if away_corners is not None and away_corners < 0:
            return False, f"Invalid negative away_corners: {away_corners}"
        return True, None

    @classmethod
    def validate_cards(
        cls,
        home_yellow: Optional[int],
        away_yellow: Optional[int],
        home_red: Optional[int] = 0,
        away_red: Optional[int] = 0
    ) -> Tuple[bool, Optional[str]]:
        """Verifies disciplinary cards sanity: non-negative integers."""
        if home_yellow is not None and home_yellow < 0:
            return False, f"Invalid negative home_yellow: {home_yellow}"
        if away_yellow is not None and away_yellow < 0:
            return False, f"Invalid negative away_yellow: {away_yellow}"
        if home_red is not None and home_red < 0:
            return False, f"Invalid negative home_red: {home_red}"
        if away_red is not None and away_red < 0:
            return False, f"Invalid negative away_red: {away_red}"
        return True, None

    @classmethod
    def validate_possession(cls, home_poss: Optional[float], away_poss: Optional[float]) -> Tuple[bool, Optional[str]]:
        """Verifies possession percentages: between 0 and 100, summing to ~100%."""
        if home_poss is not None:
            if home_poss < 0 or home_poss > 100:
                return False, f"Possession out of bounds [0, 100]: {home_poss}"
        if away_poss is not None:
            if away_poss < 0 or away_poss > 100:
                return False, f"Possession out of bounds [0, 100]: {away_poss}"
        if home_poss is not None and away_poss is not None:
            total = home_poss + away_poss
            if abs(total - 100.0) > 3.0: # Allow minor rounding tolerance
                return False, f"Possession percentages do not sum to 100% (total={total})"
        return True, None

    @classmethod
    def validate_shots(cls, total_shots: Optional[int], shots_on_target: Optional[int]) -> Tuple[bool, Optional[str]]:
        """Verifies shots on target do not exceed total shots."""
        if total_shots is not None and total_shots < 0:
            return False, f"Invalid negative total_shots: {total_shots}"
        if shots_on_target is not None and shots_on_target < 0:
            return False, f"Invalid negative shots_on_target: {shots_on_target}"
        if total_shots is not None and shots_on_target is not None:
            if shots_on_target > total_shots:
                return False, f"shots_on_target ({shots_on_target}) exceeds total_shots ({total_shots})"
        return True, None

    @classmethod
    def validate_fouls(cls, home_fouls: Optional[int], away_fouls: Optional[int]) -> Tuple[bool, Optional[str]]:
        """Verifies fouls sanity: non-negative integers."""
        if home_fouls is not None and home_fouls < 0:
            return False, f"Invalid negative home_fouls: {home_fouls}"
        if away_fouls is not None and away_fouls < 0:
            return False, f"Invalid negative away_fouls: {away_fouls}"
        return True, None

    @classmethod
    def validate_offsides(cls, home_offsides: Optional[int], away_offsides: Optional[int]) -> Tuple[bool, Optional[str]]:
        """Verifies offsides sanity: non-negative integers."""
        if home_offsides is not None and home_offsides < 0:
            return False, f"Invalid negative home_offsides: {home_offsides}"
        if away_offsides is not None and away_offsides < 0:
            return False, f"Invalid negative away_offsides: {away_offsides}"
        return True, None

    @classmethod
    def validate_saves(cls, home_saves: Optional[int], away_saves: Optional[int]) -> Tuple[bool, Optional[str]]:
        """Verifies goalkeeper saves sanity: non-negative integers."""
        if home_saves is not None and home_saves < 0:
            return False, f"Invalid negative home_saves: {home_saves}"
        if away_saves is not None and away_saves < 0:
            return False, f"Invalid negative away_saves: {away_saves}"
        return True, None

    @classmethod
    def validate_blocked_shots(cls, blocked: Optional[int], total_shots: Optional[int]) -> Tuple[bool, Optional[str]]:
        """Verifies blocked shots do not exceed total shots."""
        if blocked is not None and blocked < 0:
            return False, f"Invalid negative blocked_shots: {blocked}"
        if blocked is not None and total_shots is not None:
            if blocked > total_shots:
                return False, f"blocked_shots ({blocked}) exceeds total_shots ({total_shots})"
        return True, None

    @classmethod
    def validate_shot_location(
        cls, inside_box: Optional[int], outside_box: Optional[int], total_shots: Optional[int]
    ) -> Tuple[bool, Optional[str]]:
        """Verifies inside and outside box shot decomposition coherence."""
        if inside_box is not None and inside_box < 0:
            return False, f"Invalid negative inside_box_shots: {inside_box}"
        if outside_box is not None and outside_box < 0:
            return False, f"Invalid negative outside_box_shots: {outside_box}"
        if inside_box is not None and outside_box is not None and total_shots is not None:
            if (inside_box + outside_box) > total_shots:
                return False, f"inside_box ({inside_box}) + outside_box ({outside_box}) exceeds total_shots ({total_shots})"
        return True, None

    @classmethod
    def validate_match_statistics_bounds(
        cls,
        home_shots: Optional[int] = None,
        away_shots: Optional[int] = None,
        home_shots_on_target: Optional[int] = None,
        away_shots_on_target: Optional[int] = None,
        home_corners: Optional[int] = None,
        away_corners: Optional[int] = None,
        home_yellow: Optional[int] = None,
        away_yellow: Optional[int] = None,
        home_red: Optional[int] = None,
        away_red: Optional[int] = None,
        home_possession: Optional[float] = None,
        away_possession: Optional[float] = None,
        home_fouls: Optional[int] = None,
        away_fouls: Optional[int] = None,
        home_offsides: Optional[int] = None,
        away_offsides: Optional[int] = None,
        home_saves: Optional[int] = None,
        away_saves: Optional[int] = None,
        home_blocked: Optional[int] = None,
        away_blocked: Optional[int] = None,
        home_inside_box: Optional[int] = None,
        away_inside_box: Optional[int] = None,
        home_outside_box: Optional[int] = None,
        away_outside_box: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Unified statistical bounds validator across all match statistics dimensions.
        """
        errors = []

        if home_shots is not None and home_shots < 0:
            errors.append(f"Negative home_shots: {home_shots}")
        if away_shots is not None and away_shots < 0:
            errors.append(f"Negative away_shots: {away_shots}")
        if home_shots_on_target is not None and home_shots_on_target < 0:
            errors.append(f"Negative home_shots_on_target: {home_shots_on_target}")
        if away_shots_on_target is not None and away_shots_on_target < 0:
            errors.append(f"Negative away_shots_on_target: {away_shots_on_target}")

        if home_shots is not None and home_shots_on_target is not None:
            if home_shots_on_target > home_shots:
                errors.append(f"home_shots_on_target ({home_shots_on_target}) exceeds home_shots ({home_shots})")

        if away_shots is not None and away_shots_on_target is not None:
            if away_shots_on_target > away_shots:
                errors.append(f"away_shots_on_target ({away_shots_on_target}) exceeds away_shots ({away_shots})")

        v_corn, err_corn = cls.validate_corners(home_corners, away_corners)
        if not v_corn: errors.append(err_corn)

        v_cards, err_cards = cls.validate_cards(home_yellow, away_yellow, home_red, away_red)
        if not v_cards: errors.append(err_cards)

        v_poss, err_poss = cls.validate_possession(home_possession, away_possession)
        if not v_poss: errors.append(err_poss)

        v_fouls, err_fouls = cls.validate_fouls(home_fouls, away_fouls)
        if not v_fouls: errors.append(err_fouls)

        v_offs, err_offs = cls.validate_offsides(home_offsides, away_offsides)
        if not v_offs: errors.append(err_offs)

        v_saves, err_saves = cls.validate_saves(home_saves, away_saves)
        if not v_saves: errors.append(err_saves)

        v_h_block, err_h_block = cls.validate_blocked_shots(home_blocked, home_shots)
        if not v_h_block: errors.append(err_h_block)

        v_a_block, err_a_block = cls.validate_blocked_shots(away_blocked, away_shots)
        if not v_a_block: errors.append(err_a_block)

        v_h_loc, err_h_loc = cls.validate_shot_location(home_inside_box, home_outside_box, home_shots)
        if not v_h_loc: errors.append(err_h_loc)

        v_a_loc, err_a_loc = cls.validate_shot_location(away_inside_box, away_outside_box, away_shots)
        if not v_a_loc: errors.append(err_a_loc)

        return {
            "valid": len(errors) == 0,
            "errors": errors
        }

    # =========================================================================
    # 2. PROVENANCE RECORDING
    # =========================================================================

    @classmethod
    def record_provenance(
        cls,
        db: Session,
        fixture_id: int,
        field_name: str,
        value: Any,
        provider: str = "espn",
        provider_record_id: Optional[str] = None,
        source_type: str = "OBSERVED",
        confidence: float = 1.0
    ) -> Optional[DataProvenance]:
        """
        Creates or updates field-level data provenance record.
        """
        if value is None:
            return None

        val_str = str(value)
        # Check existing provenance
        prov = (
            db.query(DataProvenance)
            .filter(
                DataProvenance.fixture_id == fixture_id,
                DataProvenance.field_name == field_name,
                DataProvenance.provider == provider
            )
            .first()
        )

        if prov:
            prov.value = val_str
            prov.retrieved_at = datetime.now(timezone.utc)
            prov.source_type = source_type
            prov.confidence = confidence
            db.commit()
            return prov

        prov = DataProvenance(
            fixture_id=fixture_id,
            field_name=field_name,
            value=val_str,
            provider=provider,
            provider_record_id=provider_record_id,
            retrieved_at=datetime.now(timezone.utc),
            source_type=source_type,
            confidence=confidence,
            is_verified=True,
            created_at=datetime.now(timezone.utc)
        )
        db.add(prov)
        db.commit()
        return prov

    @classmethod
    def get_fixture_provenance(cls, db: Session, fixture_id: int) -> List[Dict[str, Any]]:
        """Retrieves complete field provenance history for a specific fixture."""
        records = db.query(DataProvenance).filter(DataProvenance.fixture_id == fixture_id).all()
        return [
            {
                "field_name": r.field_name,
                "value": r.value,
                "provider": r.provider,
                "provider_record_id": r.provider_record_id,
                "retrieved_at": r.retrieved_at.isoformat() if r.retrieved_at else None,
                "source_type": r.source_type,
                "confidence": r.confidence,
                "is_verified": r.is_verified
            }
            for r in records
        ]

    # =========================================================================
    # 3. CONFLICT DETECTION & RECONCILIATION
    # =========================================================================

    @classmethod
    def detect_and_record_conflict(
        cls,
        db: Session,
        fixture_id: int,
        field_name: str,
        primary_val: Any,
        primary_prov: str,
        incoming_val: Any,
        incoming_prov: str
    ) -> Optional[DataConflict]:
        """
        Detects discrepancies between primary and secondary feeds.
        If values mismatch, logs a DataConflict record for operator review.
        """
        if primary_val is None or incoming_val is None:
            return None

        p_str = str(primary_val).strip().lower()
        i_str = str(incoming_val).strip().lower()

        if p_str == i_str:
            return None # Perfectly matched!

        # Check existing active conflict
        conflict = (
            db.query(DataConflict)
            .filter(
                DataConflict.fixture_id == fixture_id,
                DataConflict.field_name == field_name,
                DataConflict.status == "CONFLICT"
            )
            .first()
        )

        if conflict:
            conflict.primary_value = str(primary_val)
            conflict.conflicting_value = str(incoming_val)
            db.commit()
            return conflict

        conflict = DataConflict(
            fixture_id=fixture_id,
            field_name=field_name,
            primary_provider=primary_prov,
            primary_value=str(primary_val),
            conflicting_provider=incoming_prov,
            conflicting_value=str(incoming_val),
            status="CONFLICT",
            created_at=datetime.now(timezone.utc)
        )
        db.add(conflict)
        db.commit()

        ObservabilityService.log_event(
            "data_conflict_detected",
            category="data_quality",
            severity="WARNING",
            details={
                "fixture_id": fixture_id,
                "field": field_name,
                "primary": f"{primary_prov}:{primary_val}",
                "conflicting": f"{incoming_prov}:{incoming_val}"
            }
        )

        return conflict

    @classmethod
    def resolve_conflict(
        cls,
        db: Session,
        conflict_id: int,
        resolved_value: str,
        notes: Optional[str] = None
    ) -> bool:
        """Resolves an open data conflict with audit notes."""
        conflict = db.query(DataConflict).filter(DataConflict.id == conflict_id).first()
        if not conflict:
            return False

        conflict.status = "RESOLVED"
        conflict.resolved_value = resolved_value
        conflict.resolution_notes = notes
        conflict.resolved_at = datetime.now(timezone.utc)
        db.commit()
        return True

    @classmethod
    def get_all_conflicts(cls, db: Session, status: Optional[str] = "CONFLICT") -> List[Dict[str, Any]]:
        """Lists data conflicts across all fixtures."""
        q = db.query(DataConflict)
        if status:
            q = q.filter(DataConflict.status == status)

        records = q.order_by(DataConflict.created_at.desc()).all()
        return [
            {
                "id": c.id,
                "fixture_id": c.fixture_id,
                "field_name": c.field_name,
                "primary_provider": c.primary_provider,
                "primary_value": c.primary_value,
                "conflicting_provider": c.conflicting_provider,
                "conflicting_value": c.conflicting_value,
                "status": c.status,
                "resolved_value": c.resolved_value,
                "created_at": c.created_at.isoformat() if c.created_at else None
            }
            for c in records
        ]
