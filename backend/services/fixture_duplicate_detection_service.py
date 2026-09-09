import re
import logging
from enum import Enum
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Union
from sqlalchemy.orm import Session

from models import Fixture

try:
    from services.fixture_identity_service import FixtureIdentityService
except ImportError:
    from .fixture_identity_service import FixtureIdentityService

try:
    from services.canonical_competition_service import CanonicalCompetitionService
except ImportError:
    from .canonical_competition_service import CanonicalCompetitionService

logger = logging.getLogger(__name__)


class DuplicateClassification(str, Enum):
    DUPLICATE_CONFIRMED = "DUPLICATE_CONFIRMED"
    DUPLICATE_POSSIBLE = "DUPLICATE_POSSIBLE"
    DISTINCT_FIXTURE = "DISTINCT_FIXTURE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class FixtureDuplicateDetectionService:
    """
    Deterministic Duplicate Fixture Detection Engine.
    Evaluates candidate fixtures against database records without ever deleting or
    merging automatically solely on team names. Preserves full provenance.
    """

    @classmethod
    def clean_event_id(cls, event_id: Optional[str]) -> str:
        if not event_id:
            return ""
        return str(event_id).replace("ESPN-FIX-", "").replace("ESPN-", "").replace("FIX-", "").strip()

    @classmethod
    def compare_fixtures(
        cls,
        fixture_a: Union[Fixture, Dict[str, Any]],
        fixture_b: Union[Fixture, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Compares two fixture records (models or dict representations) and classifies
        whether they are DUPLICATE_CONFIRMED, DUPLICATE_POSSIBLE, DISTINCT_FIXTURE, or INSUFFICIENT_DATA.
        """
        # Helper to extract fields
        def get_val(f, key, model_attr):
            if isinstance(f, dict):
                return f.get(key)
            return getattr(f, model_attr, None)

        def get_team_name(f, is_home: bool):
            if isinstance(f, dict):
                return f.get("home_team" if is_home else "away_team")
            team_obj = getattr(f, "home_team" if is_home else "away_team", None)
            return team_obj.name if team_obj else None

        id_a = get_val(fixture_a, "id", "id")
        id_b = get_val(fixture_b, "id", "id")

        if id_a is not None and id_b is not None and id_a == id_b:
            return {
                "classification": DuplicateClassification.DISTINCT_FIXTURE.value,
                "fixture_a_id": id_a,
                "fixture_b_id": id_b,
                "confidence": 1.0,
                "reasons": ["Same database record (identity identical)."],
                "provenance_preserved": True
            }

        ext_a = cls.clean_event_id(get_val(fixture_a, "external_id", "external_id"))
        ext_b = cls.clean_event_id(get_val(fixture_b, "external_id", "external_id"))

        home_a = get_team_name(fixture_a, True)
        away_a = get_team_name(fixture_a, False)
        home_b = get_team_name(fixture_b, True)
        away_b = get_team_name(fixture_b, False)

        date_a = get_val(fixture_a, "match_date", "match_date")
        date_b = get_val(fixture_b, "match_date", "match_date")

        # Insufficient data check
        if not home_a or not away_a or not home_b or not away_b or not date_a or not date_b:
            return {
                "classification": DuplicateClassification.INSUFFICIENT_DATA.value,
                "fixture_a_id": id_a,
                "fixture_b_id": id_b,
                "confidence": 0.0,
                "reasons": ["Missing team identities or match kickoff timestamps."],
                "provenance_preserved": True
            }

        reasons = []

        # 1. Exact Provider Event ID match
        if ext_a and ext_b and ext_a == ext_b:
            reasons.append(f"Matching provider event ID: '{ext_a}'")
            return {
                "classification": DuplicateClassification.DUPLICATE_CONFIRMED.value,
                "fixture_a_id": id_a,
                "fixture_b_id": id_b,
                "confidence": 1.0,
                "reasons": reasons,
                "provenance_preserved": True
            }

        # Normalize dates
        dt_a = date_a.astimezone(timezone.utc).replace(tzinfo=None) if (isinstance(date_a, datetime) and date_a.tzinfo) else date_a
        dt_b = date_b.astimezone(timezone.utc).replace(tzinfo=None) if (isinstance(date_b, datetime) and date_b.tzinfo) else date_b

        time_diff_hours = abs((dt_a - dt_b).total_seconds()) / 3600.0

        home_match = FixtureIdentityService.names_match(home_a, home_b)
        away_match = FixtureIdentityService.names_match(away_a, away_b)
        swapped = FixtureIdentityService.names_match(home_a, away_b) and FixtureIdentityService.names_match(away_a, home_b)

        # 2. Distinct fixtures due to team mismatch or long time gap
        if not (home_match and away_match) and not swapped:
            return {
                "classification": DuplicateClassification.DISTINCT_FIXTURE.value,
                "fixture_a_id": id_a,
                "fixture_b_id": id_b,
                "confidence": 0.95,
                "reasons": [f"Team identities differ: ({home_a} vs {away_a}) vs ({home_b} vs {away_b})"],
                "provenance_preserved": True
            }

        # If time difference is large (e.g. > 48 hours), it's a distinct match (e.g. return leg or future season match)
        if time_diff_hours > 48.0:
            return {
                "classification": DuplicateClassification.DISTINCT_FIXTURE.value,
                "fixture_a_id": id_a,
                "fixture_b_id": id_b,
                "confidence": 0.98,
                "reasons": [f"Time difference ({time_diff_hours:.1f}h) exceeds duplicate window (>48h) indicating distinct match calendar."],
                "provenance_preserved": True
            }

        # Competition comparison
        comp_a = get_val(fixture_a, "competition", "league")
        comp_b = get_val(fixture_b, "competition", "league")
        name_comp_a = comp_a.name if hasattr(comp_a, "name") else (str(comp_a) if comp_a else "")
        name_comp_b = comp_b.name if hasattr(comp_b, "name") else (str(comp_b) if comp_b else "")

        canon_a = CanonicalCompetitionService.resolve_competition(league_name=name_comp_a)
        canon_b = CanonicalCompetitionService.resolve_competition(league_name=name_comp_b)

        comps_compatible = (
            canon_a.competition_name == "UNAVAILABLE" or
            canon_b.competition_name == "UNAVAILABLE" or
            canon_a.competition_name == canon_b.competition_name or
            canon_a.country == canon_b.country
        )

        # 3. Confirmed Duplicate: same teams, time diff < 12h, compatible competitions
        if home_match and away_match and time_diff_hours <= 12.0 and comps_compatible:
            reasons.append(f"Identical home and away teams within {time_diff_hours:.1f}h window.")
            if canon_a.competition_name == canon_b.competition_name and canon_a.competition_name != "UNAVAILABLE":
                reasons.append(f"Matching competition: {canon_a.competition_name}")
            return {
                "classification": DuplicateClassification.DUPLICATE_CONFIRMED.value,
                "fixture_a_id": id_a,
                "fixture_b_id": id_b,
                "confidence": 0.95,
                "reasons": reasons,
                "provenance_preserved": True
            }

        # 4. Possible Duplicate: same teams within 48h, or swapped teams within 12h
        if home_match and away_match and time_diff_hours <= 48.0:
            reasons.append(f"Matching teams within 48 hours ({time_diff_hours:.1f}h diff), possible reschedule or duplicate calendar entry.")
            return {
                "classification": DuplicateClassification.DUPLICATE_POSSIBLE.value,
                "fixture_a_id": id_a,
                "fixture_b_id": id_b,
                "confidence": 0.70,
                "reasons": reasons,
                "provenance_preserved": True
            }

        if swapped and time_diff_hours <= 12.0:
            reasons.append(f"Reversed home/away team pairing within {time_diff_hours:.1f}h window.")
            return {
                "classification": DuplicateClassification.DUPLICATE_POSSIBLE.value,
                "fixture_a_id": id_a,
                "fixture_b_id": id_b,
                "confidence": 0.65,
                "reasons": reasons,
                "provenance_preserved": True
            }

        return {
            "classification": DuplicateClassification.DISTINCT_FIXTURE.value,
            "fixture_a_id": id_a,
            "fixture_b_id": id_b,
            "confidence": 0.85,
            "reasons": ["Inconclusive match parameters, treating as distinct to prevent data loss."],
            "provenance_preserved": True
        }

    @classmethod
    def find_duplicate_candidates_for_fixture(
        cls,
        db: Session,
        fixture: Fixture,
        window_hours: float = 36.0
    ) -> List[Dict[str, Any]]:
        """
        Scans active database fixtures to find duplicate candidates for a specific fixture.
        Does not merge or delete records.
        """
        if not fixture or not fixture.match_date:
            return []

        min_date = fixture.match_date - timedelta(hours=window_hours)
        max_date = fixture.match_date + timedelta(hours=window_hours)

        candidates = db.query(Fixture).filter(
            Fixture.id != fixture.id,
            Fixture.match_date >= min_date,
            Fixture.match_date <= max_date
        ).all()

        results = []
        for cand in candidates:
            res = cls.compare_fixtures(fixture, cand)
            if res["classification"] in [
                DuplicateClassification.DUPLICATE_CONFIRMED.value,
                DuplicateClassification.DUPLICATE_POSSIBLE.value
            ]:
                results.append(res)

        return results

    @classmethod
    def get_system_duplicate_summary(cls, db: Session) -> Dict[str, Any]:
        """
        Aggregates system-wide duplicate candidate counts for observability.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        upcoming = db.query(Fixture).filter(
            Fixture.match_date >= now - timedelta(days=2),
            Fixture.match_date <= now + timedelta(days=3)
        ).all()

        confirmed_count = 0
        possible_count = 0
        seen_pairs = set()

        for i, f1 in enumerate(upcoming):
            for f2 in upcoming[i + 1:]:
                pair_key = tuple(sorted([f1.id, f2.id]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                cmp = cls.compare_fixtures(f1, f2)
                if cmp["classification"] == DuplicateClassification.DUPLICATE_CONFIRMED.value:
                    confirmed_count += 1
                elif cmp["classification"] == DuplicateClassification.DUPLICATE_POSSIBLE.value:
                    possible_count += 1

        return {
            "duplicate_confirmed_count": confirmed_count,
            "duplicate_possible_count": possible_count,
            "fixtures_audited": len(upcoming)
        }
