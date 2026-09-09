import re
import unicodedata
import logging
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple, Union

from models import Fixture

logger = logging.getLogger(__name__)


class IdentityDiagnostic(str, Enum):
    IDENTITY_VALID = "IDENTITY_VALID"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    PROVIDER_EVENT_MISMATCH = "PROVIDER_EVENT_MISMATCH"
    TEAM_MISMATCH = "TEAM_MISMATCH"
    TEAMS_SWAPPED = "TEAMS_SWAPPED"
    KICKOFF_MISMATCH = "KICKOFF_MISMATCH"
    COMPETITION_MISMATCH = "COMPETITION_MISMATCH"
    INCOMPLETE_IDENTITY = "INCOMPLETE_IDENTITY"


@dataclass(frozen=True)
class CanonicalFixtureIdentity:
    """Canonical Identity Contract for a Football Fixture."""
    fixture_id: int
    external_id: Optional[str]
    home_team: str
    away_team: str
    competition: str
    country: str
    kickoff: Optional[datetime]


@dataclass
class IdentityValidationResult:
    """Structured result from fixture identity validation."""
    status: IdentityDiagnostic
    is_valid: bool
    reason: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class FixtureIdentityService:
    """
    Canonical Fixture Identity & Validation Service.
    Guarantees that fixtures are primarily identified by fixture_id,
    with external IDs kept distinct, and validates provider payloads
    or candidate objects against database records to prevent cross-contamination.
    """

    @staticmethod
    def clean_team_name(name: Optional[str]) -> str:
        """
        Normalizes a team name by stripping accents, common club affixes,
        and punctuation to enable robust, fuzz-tolerant matching.
        """
        if not name:
            return ""
        # Strip accents
        n = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('utf-8').lower()
        # Strip common soccer prefixes / suffixes
        n = re.sub(r'\b(fc|cf|sc|cd|afc|fk|vfb|ac|club|de|united|city|sv|sk|bsc|spfl)\b', '', n)
        # Remove non-alphanumeric chars
        n = re.sub(r'[^a-z0-9]', '', n)
        return n.strip()

    @classmethod
    def names_match(cls, name_a: Optional[str], name_b: Optional[str]) -> bool:
        """
        Returns True if two cleaned team names represent the same club.
        """
        c_a = cls.clean_team_name(name_a)
        c_b = cls.clean_team_name(name_b)
        if not c_a or not c_b:
            return False
        if c_a == c_b:
            return True
        if c_a in c_b or c_b in c_a:
            return True
        # Prefix match for truncated names of >= 4 letters
        if len(c_a) >= 4 and len(c_b) >= 4 and (c_a[:4] == c_b[:4]):
            return True
        return False

    @classmethod
    def extract_canonical_identity(cls, fixture: Fixture) -> CanonicalFixtureIdentity:
        """
        Extracts the canonical fixture identity contract from a database Fixture record.
        """
        home_name = fixture.home_team.name if fixture.home_team else ""
        away_name = fixture.away_team.name if fixture.away_team else ""
        comp_name = fixture.league.name if fixture.league else ""
        country_name = fixture.league.country if (fixture.league and fixture.league.country) else "International"

        return CanonicalFixtureIdentity(
            fixture_id=fixture.id,
            external_id=fixture.external_id,
            home_team=home_name,
            away_team=away_name,
            competition=comp_name,
            country=country_name,
            kickoff=fixture.match_date
        )

    @classmethod
    def validate_fixture_identity(
        cls,
        fixture: Fixture,
        candidate_or_payload: Union[Dict[str, Any], CanonicalFixtureIdentity]
    ) -> IdentityValidationResult:
        """
        Authoritative validator checking candidate or external provider payload against
        a canonical database Fixture.
        Detects:
        - Incomplete identity (missing teams or kickoff)
        - Provider event ID mismatch
        - Swapped home and away teams
        - Team name mismatch
        - Kickoff window mismatch (> 36 hours)
        - Competition mismatch
        """
        if not fixture or not fixture.id:
            return IdentityValidationResult(
                status=IdentityDiagnostic.INCOMPLETE_IDENTITY,
                is_valid=False,
                reason="Target database fixture is missing or has no primary fixture_id."
            )

        db_home = fixture.home_team.name if fixture.home_team else ""
        db_away = fixture.away_team.name if fixture.away_team else ""
        db_kickoff = fixture.match_date

        if not db_home or not db_away:
            return IdentityValidationResult(
                status=IdentityDiagnostic.INCOMPLETE_IDENTITY,
                is_valid=False,
                reason=f"Target fixture {fixture.id} has missing home or away team in database."
            )

        # Handle CanonicalFixtureIdentity candidate directly
        if isinstance(candidate_or_payload, CanonicalFixtureIdentity):
            cand = candidate_or_payload
            # External ID mismatch check
            if fixture.external_id and cand.external_id:
                clean_db_ext = str(fixture.external_id).replace("ESPN-", "").strip()
                clean_cand_ext = str(cand.external_id).replace("ESPN-", "").strip()
                if clean_db_ext != clean_cand_ext:
                    return IdentityValidationResult(
                        status=IdentityDiagnostic.PROVIDER_EVENT_MISMATCH,
                        is_valid=False,
                        reason=f"External provider ID mismatch: DB='{clean_db_ext}' vs Candidate='{clean_cand_ext}'"
                    )

            # Check for swapped teams
            if cls.names_match(db_home, cand.away_team) and cls.names_match(db_away, cand.home_team):
                return IdentityValidationResult(
                    status=IdentityDiagnostic.TEAMS_SWAPPED,
                    is_valid=False,
                    reason=f"Home and away teams are swapped: DB='{db_home} vs {db_away}' vs Cand='{cand.home_team} vs {cand.away_team}'"
                )

            # Check home team
            if not cls.names_match(db_home, cand.home_team):
                return IdentityValidationResult(
                    status=IdentityDiagnostic.TEAM_MISMATCH,
                    is_valid=False,
                    reason=f"Home team mismatch: DB='{db_home}' vs Candidate='{cand.home_team}'"
                )

            # Check away team
            if not cls.names_match(db_away, cand.away_team):
                return IdentityValidationResult(
                    status=IdentityDiagnostic.TEAM_MISMATCH,
                    is_valid=False,
                    reason=f"Away team mismatch: DB='{db_away}' vs Candidate='{cand.away_team}'"
                )

            # Kickoff check
            if db_kickoff and cand.kickoff:
                diff_hours = abs((db_kickoff - cand.kickoff).total_seconds()) / 3600.0
                if diff_hours > 36.0:
                    return IdentityValidationResult(
                        status=IdentityDiagnostic.KICKOFF_MISMATCH,
                        is_valid=False,
                        reason=f"Kickoff mismatch: DB={db_kickoff} vs Candidate={cand.kickoff} (diff={diff_hours:.1f}h > 36h)"
                    )

            return IdentityValidationResult(
                status=IdentityDiagnostic.IDENTITY_VALID,
                is_valid=True,
                reason="Candidate fixture matches canonical identity."
            )

        # Handle Raw Provider Payload (e.g. ESPN summary payload)
        payload = candidate_or_payload
        header = payload.get("header", {})
        comps = header.get("competitions", [])
        if not comps:
            return IdentityValidationResult(
                status=IdentityDiagnostic.INCOMPLETE_IDENTITY,
                is_valid=False,
                reason="No competitions found in provider header payload."
            )

        comp = comps[0]

        # 1. Provider Event ID Validation
        prov_event_id = header.get("id") or comp.get("id")
        if prov_event_id and fixture.external_id:
            clean_prov = str(prov_event_id).replace("ESPN-", "").strip()
            clean_db = str(fixture.external_id).replace("ESPN-", "").strip()
            if clean_prov != clean_db:
                return IdentityValidationResult(
                    status=IdentityDiagnostic.PROVIDER_EVENT_MISMATCH,
                    is_valid=False,
                    reason=f"Event ID mismatch: DB='{clean_db}' vs Prov='{clean_prov}'"
                )

        # 2. Kickoff Window Validation (threshold: 36h)
        prov_date_str = comp.get("date")
        if prov_date_str and db_kickoff:
            try:
                prov_dt = datetime.fromisoformat(prov_date_str.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
                diff_hours = abs((prov_dt - db_kickoff).total_seconds()) / 3600.0
                if diff_hours > 36.0:
                    return IdentityValidationResult(
                        status=IdentityDiagnostic.KICKOFF_MISMATCH,
                        is_valid=False,
                        reason=f"Kickoff window mismatch: DB={db_kickoff} vs Prov={prov_dt} (diff={diff_hours:.1f}h > 36h)"
                    )
            except Exception as ex:
                logger.debug(f"Date parsing in fixture identity validation: {ex}")

        # 3. Competitor Extraction & Team Matching
        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            return IdentityValidationResult(
                status=IdentityDiagnostic.INCOMPLETE_IDENTITY,
                is_valid=False,
                reason="Less than 2 competitors found in provider payload."
            )

        prov_home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        prov_away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

        p_h_name = prov_home.get("team", {}).get("name") or prov_home.get("team", {}).get("displayName") or ""
        p_a_name = prov_away.get("team", {}).get("name") or prov_away.get("team", {}).get("displayName") or ""

        # Check for swapped teams
        if cls.names_match(db_home, p_a_name) and cls.names_match(db_away, p_h_name):
            return IdentityValidationResult(
                status=IdentityDiagnostic.TEAMS_SWAPPED,
                is_valid=False,
                reason=f"Swapped home/away teams detected: DB='{db_home} vs {db_away}' vs Prov='{p_h_name} vs {p_a_name}'"
            )

        # Check home team
        if not cls.names_match(db_home, p_h_name):
            return IdentityValidationResult(
                status=IdentityDiagnostic.TEAM_MISMATCH,
                is_valid=False,
                reason=f"Home team mismatch: DB='{db_home}' vs Prov='{p_h_name}'"
            )

        # Check away team
        if not cls.names_match(db_away, p_a_name):
            return IdentityValidationResult(
                status=IdentityDiagnostic.TEAM_MISMATCH,
                is_valid=False,
                reason=f"Away team mismatch: DB='{db_away}' vs Prov='{p_a_name}'"
            )

        return IdentityValidationResult(
            status=IdentityDiagnostic.IDENTITY_VALID,
            is_valid=True,
            reason="Payload matches canonical fixture identity."
        )
