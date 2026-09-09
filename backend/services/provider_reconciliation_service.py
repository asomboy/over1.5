import re
import json
import logging
import unicodedata
from enum import Enum
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Union

from sqlalchemy.orm import Session
from models import Fixture, FixtureProviderMapping

try:
    from services.canonical_competition_service import CanonicalCompetitionService
except ImportError:
    from .canonical_competition_service import CanonicalCompetitionService

try:
    from services.fixture_identity_service import FixtureIdentityService
except ImportError:
    from .fixture_identity_service import FixtureIdentityService

logger = logging.getLogger(__name__)


class ReconciliationStatus(str, Enum):
    IDENTITY_VALID = "IDENTITY_VALID"
    PROVIDER_EVENT_NOT_FOUND = "PROVIDER_EVENT_NOT_FOUND"
    PROVIDER_EVENT_MISMATCH = "PROVIDER_EVENT_MISMATCH"
    HOME_TEAM_MISMATCH = "HOME_TEAM_MISMATCH"
    AWAY_TEAM_MISMATCH = "AWAY_TEAM_MISMATCH"
    TEAMS_SWAPPED = "TEAMS_SWAPPED"
    KICKOFF_MISMATCH = "KICKOFF_MISMATCH"
    COMPETITION_MISMATCH = "COMPETITION_MISMATCH"
    COUNTRY_MISMATCH = "COUNTRY_MISMATCH"
    STATUS_MISMATCH = "STATUS_MISMATCH"
    INCOMPLETE_PROVIDER_DATA = "INCOMPLETE_PROVIDER_DATA"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"


class SeverityLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ProviderReconciliationService:
    """
    Deterministic Provider Reconciliation Engine.
    Compares internal canonical fixture identity against external provider data
    across 9 required identity checks.
    """

    @classmethod
    def clean_team_name(cls, name: Optional[str]) -> str:
        if not name:
            return ""
        return FixtureIdentityService.clean_team_name(name)

    @classmethod
    def names_match(cls, name_a: Optional[str], name_b: Optional[str]) -> bool:
        if not name_a or not name_b:
            return False
        return FixtureIdentityService.names_match(name_a, name_b)

    @classmethod
    def parse_provider_kickoff(cls, dt_val: Any) -> Optional[datetime]:
        if not dt_val:
            return None
        if isinstance(dt_val, datetime):
            if dt_val.tzinfo is not None:
                return dt_val.astimezone(timezone.utc).replace(tzinfo=None)
            return dt_val
        if isinstance(dt_val, str):
            try:
                # Handle ISO 8601 strings e.g. 2026-03-09T18:00Z or 2026-03-09T18:00:00+00:00
                cleaned = dt_val.replace("Z", "+00:00")
                parsed = datetime.fromisoformat(cleaned)
                if parsed.tzinfo is not None:
                    return parsed.astimezone(timezone.utc).replace(tzinfo=None)
                return parsed
            except Exception:
                return None
        return None

    @classmethod
    def reconcile(
        cls,
        fixture: Fixture,
        provider_data: Optional[Dict[str, Any]],
        provider_name: str = "ESPN",
        db: Optional[Session] = None,
        persist_mapping: bool = True
    ) -> Dict[str, Any]:
        """
        Executes all 9 reconciliation checks between database Fixture and provider data.
        Returns a structured diagnostic dict matching the Phase 14 spec:
        {
          "status": "...",
          "severity": "INFO|WARNING|ERROR|CRITICAL",
          "fixture_id": ...,
          "provider": ...,
          "provider_event_id": ...,
          "checks": {...},
          "verified": true|false,
          "reason": "...",
          "timestamp": "..."
        }
        """
        now_utc = datetime.now(timezone.utc)
        now_str = now_utc.isoformat()
        fixture_id = fixture.id if fixture else None

        checks_result: Dict[str, Dict[str, Any]] = {
            "provider_event_existence": {"passed": False, "detail": None},
            "provider_event_id": {"passed": False, "detail": None},
            "data_completeness": {"passed": False, "detail": None},
            "team_ordering": {"passed": False, "detail": None},
            "home_team_identity": {"passed": False, "detail": None},
            "away_team_identity": {"passed": False, "detail": None},
            "kickoff_timestamp": {"passed": False, "detail": None},
            "competition": {"passed": False, "detail": None},
            "country": {"passed": False, "detail": None},
            "match_status": {"passed": False, "detail": None},
        }

        # Check 1: Provider availability / Event existence
        if provider_data is None:
            res = {
                "status": ReconciliationStatus.PROVIDER_UNAVAILABLE.value,
                "severity": SeverityLevel.CRITICAL.value,
                "fixture_id": fixture_id,
                "provider": provider_name,
                "provider_event_id": None,
                "checks": checks_result,
                "verified": False,
                "reason": "Provider returned None or provider feed is unavailable.",
                "timestamp": now_str
            }
            cls._record_mapping_result(db, fixture, provider_name, None, res, persist=persist_mapping)
            return res

        if not provider_data or provider_data.get("not_found"):
            res = {
                "status": ReconciliationStatus.PROVIDER_EVENT_NOT_FOUND.value,
                "severity": SeverityLevel.ERROR.value,
                "fixture_id": fixture_id,
                "provider": provider_name,
                "provider_event_id": provider_data.get("event_id") if provider_data else None,
                "checks": checks_result,
                "verified": False,
                "reason": "Provider event was not found in provider calendar/endpoints.",
                "timestamp": now_str
            }
            cls._record_mapping_result(db, fixture, provider_name, provider_data.get("event_id"), res, persist=persist_mapping)
            return res

        checks_result["provider_event_existence"] = {"passed": True, "detail": "Provider event payload received"}

        # Extract normalized provider fields
        # Support both flattened dictionary and raw ESPN header payloads
        prov_event_id = str(provider_data.get("event_id") or provider_data.get("id") or "").strip()
        header = provider_data.get("header", {})
        comps = header.get("competitions", [])
        if comps and not prov_event_id:
            prov_event_id = str(header.get("id") or comps[0].get("id") or "").strip()

        # Check 2: Provider event ID matching
        db_ext_id = str(fixture.external_id or "").replace("ESPN-FIX-", "").replace("ESPN-", "").replace("FIX-", "").strip() if fixture else ""
        clean_prov_id = prov_event_id.replace("ESPN-FIX-", "").replace("ESPN-", "").replace("FIX-", "").strip()

        if db_ext_id and clean_prov_id and db_ext_id != clean_prov_id:
            checks_result["provider_event_id"] = {
                "passed": False,
                "detail": f"Provider event ID mismatch: DB={db_ext_id} vs Provider={clean_prov_id}"
            }
            res = {
                "status": ReconciliationStatus.PROVIDER_EVENT_MISMATCH.value,
                "severity": SeverityLevel.ERROR.value,
                "fixture_id": fixture_id,
                "provider": provider_name,
                "provider_event_id": prov_event_id,
                "checks": checks_result,
                "verified": False,
                "reason": f"Provider event ID '{clean_prov_id}' does not match fixture external ID '{db_ext_id}'.",
                "timestamp": now_str
            }
            cls._record_mapping_result(db, fixture, provider_name, prov_event_id, res, persist=persist_mapping)
            return res

        checks_result["provider_event_id"] = {
            "passed": True,
            "detail": f"Provider event ID verified: {clean_prov_id or db_ext_id}"
        }

        # Extract Provider Teams & Info
        prov_home = provider_data.get("home_team")
        prov_away = provider_data.get("away_team")
        prov_kickoff_raw = provider_data.get("kickoff") or provider_data.get("date")
        prov_status = provider_data.get("status") or provider_data.get("state")

        # Fallback to ESPN header if nested
        if comps:
            c0 = comps[0]
            competitors = c0.get("competitors", [])
            for c in competitors:
                if c.get("homeAway") == "home":
                    prov_home = prov_home or c.get("team", {}).get("displayName") or c.get("team", {}).get("name")
                elif c.get("homeAway") == "away":
                    prov_away = prov_away or c.get("team", {}).get("displayName") or c.get("team", {}).get("name")
            prov_kickoff_raw = prov_kickoff_raw or c0.get("date")
            prov_status = prov_status or header.get("status", {}).get("type", {}).get("name")

        # Check 3: Completeness
        db_home = fixture.home_team.name if (fixture and fixture.home_team) else ""
        db_away = fixture.away_team.name if (fixture and fixture.away_team) else ""
        db_kickoff = fixture.match_date if fixture else None

        if not prov_home or not prov_away:
            checks_result["data_completeness"] = {
                "passed": False,
                "detail": f"Missing provider team names: home='{prov_home}', away='{prov_away}'"
            }
            res = {
                "status": ReconciliationStatus.INCOMPLETE_PROVIDER_DATA.value,
                "severity": SeverityLevel.ERROR.value,
                "fixture_id": fixture_id,
                "provider": provider_name,
                "provider_event_id": prov_event_id,
                "checks": checks_result,
                "verified": False,
                "reason": "Provider payload lacks required home or away team identity.",
                "timestamp": now_str
            }
            cls._record_mapping_result(db, fixture, provider_name, prov_event_id, res, persist=persist_mapping)
            return res

        checks_result["data_completeness"] = {"passed": True, "detail": "Home and away identities present"}

        # Check 4: Team Ordering / Swapped Teams
        home_matches_home = cls.names_match(db_home, prov_home)
        away_matches_away = cls.names_match(db_away, prov_away)
        home_matches_away = cls.names_match(db_home, prov_away)
        away_matches_home = cls.names_match(db_away, prov_home)

        if home_matches_away and away_matches_home and not (home_matches_home and away_matches_away):
            checks_result["team_ordering"] = {
                "passed": False,
                "detail": f"Teams are reversed: DB '{db_home} vs {db_away}' vs Provider '{prov_home} vs {prov_away}'"
            }
            res = {
                "status": ReconciliationStatus.TEAMS_SWAPPED.value,
                "severity": SeverityLevel.ERROR.value,
                "fixture_id": fixture_id,
                "provider": provider_name,
                "provider_event_id": prov_event_id,
                "checks": checks_result,
                "verified": False,
                "reason": f"Home and away teams are reversed: DB is '{db_home} vs {db_away}' but Provider is '{prov_home} vs {prov_away}'.",
                "timestamp": now_str
            }
            cls._record_mapping_result(db, fixture, provider_name, prov_event_id, res, persist=persist_mapping)
            return res

        checks_result["team_ordering"] = {"passed": True, "detail": "Team ordering verified"}

        # Check 5: Home Team Identity
        if not home_matches_home:
            checks_result["home_team_identity"] = {
                "passed": False,
                "detail": f"Home team mismatch: DB='{db_home}' vs Provider='{prov_home}'"
            }
            res = {
                "status": ReconciliationStatus.HOME_TEAM_MISMATCH.value,
                "severity": SeverityLevel.ERROR.value,
                "fixture_id": fixture_id,
                "provider": provider_name,
                "provider_event_id": prov_event_id,
                "checks": checks_result,
                "verified": False,
                "reason": f"Home team mismatch: DB has '{db_home}' while provider has '{prov_home}'.",
                "timestamp": now_str
            }
            cls._record_mapping_result(db, fixture, provider_name, prov_event_id, res, persist=persist_mapping)
            return res

        checks_result["home_team_identity"] = {"passed": True, "detail": f"Home team matches: '{db_home}'"}

        # Check 6: Away Team Identity
        if not away_matches_away:
            checks_result["away_team_identity"] = {
                "passed": False,
                "detail": f"Away team mismatch: DB='{db_away}' vs Provider='{prov_away}'"
            }
            res = {
                "status": ReconciliationStatus.AWAY_TEAM_MISMATCH.value,
                "severity": SeverityLevel.ERROR.value,
                "fixture_id": fixture_id,
                "provider": provider_name,
                "provider_event_id": prov_event_id,
                "checks": checks_result,
                "verified": False,
                "reason": f"Away team mismatch: DB has '{db_away}' while provider has '{prov_away}'.",
                "timestamp": now_str
            }
            cls._record_mapping_result(db, fixture, provider_name, prov_event_id, res, persist=persist_mapping)
            return res

        checks_result["away_team_identity"] = {"passed": True, "detail": f"Away team matches: '{db_away}'"}

        # Check 7: Kickoff Timestamp (Tolerance: 36 hours for match calendar drift)
        prov_kickoff = cls.parse_provider_kickoff(prov_kickoff_raw)
        if db_kickoff and prov_kickoff:
            drift_hours = abs((db_kickoff - prov_kickoff).total_seconds()) / 3600.0
            if drift_hours > 36.0:
                checks_result["kickoff_timestamp"] = {
                    "passed": False,
                    "detail": f"Kickoff drift excessive: DB={db_kickoff} vs Provider={prov_kickoff} ({drift_hours:.1f}h > 36h)"
                }
                res = {
                    "status": ReconciliationStatus.KICKOFF_MISMATCH.value,
                    "severity": SeverityLevel.ERROR.value,
                    "fixture_id": fixture_id,
                    "provider": provider_name,
                    "provider_event_id": prov_event_id,
                    "checks": checks_result,
                    "verified": False,
                    "reason": f"Kickoff time drift exceeds 36 hours (DB: {db_kickoff}, Prov: {prov_kickoff}).",
                    "timestamp": now_str
                }
                cls._record_mapping_result(db, fixture, provider_name, prov_event_id, res, persist=persist_mapping)
                return res
            elif drift_hours > 4.0:
                checks_result["kickoff_timestamp"] = {
                    "passed": True,
                    "detail": f"Kickoff drift warning: {drift_hours:.1f}h difference (within tolerance)"
                }
            else:
                checks_result["kickoff_timestamp"] = {"passed": True, "detail": "Kickoff timestamp aligned"}
        else:
            checks_result["kickoff_timestamp"] = {"passed": True, "detail": "Kickoff timestamp comparison skipped (one or both absent)"}

        # Check 8: Competition Identity
        prov_comp = provider_data.get("competition") or provider_data.get("league")
        db_league_name = fixture.league.name if (fixture and fixture.league) else ""
        if prov_comp and db_league_name:
            c_prov = CanonicalCompetitionService.resolve_competition(league_name=str(prov_comp))
            c_db = CanonicalCompetitionService.resolve_competition(league_name=db_league_name)
            if c_prov.competition_name != "UNAVAILABLE" and c_db.competition_name != "UNAVAILABLE":
                # Check for explicit conflict in distinct national leagues (e.g. EPL vs La Liga)
                if c_prov.competition_name != c_db.competition_name and c_prov.country != "UNAVAILABLE" and c_db.country != "UNAVAILABLE" and c_prov.country != c_db.country:
                    checks_result["competition"] = {
                        "passed": False,
                        "detail": f"Competition conflict: DB='{c_db.competition_name}' vs Prov='{c_prov.competition_name}'"
                    }
                    res = {
                        "status": ReconciliationStatus.COMPETITION_MISMATCH.value,
                        "severity": SeverityLevel.ERROR.value,
                        "fixture_id": fixture_id,
                        "provider": provider_name,
                        "provider_event_id": prov_event_id,
                        "checks": checks_result,
                        "verified": False,
                        "reason": f"Competition mismatch: DB league '{c_db.competition_name}' does not match provider '{c_prov.competition_name}'.",
                        "timestamp": now_str
                    }
                    cls._record_mapping_result(db, fixture, provider_name, prov_event_id, res, persist=persist_mapping)
                    return res

        checks_result["competition"] = {"passed": True, "detail": "Competition verified or compatible"}

        # Check 9: Country Identity
        prov_country = provider_data.get("country")
        db_country = fixture.league.country if (fixture and fixture.league) else ""
        if prov_country and db_country and prov_country != "UNAVAILABLE" and db_country != "UNAVAILABLE":
            if prov_country.strip().lower() != db_country.strip().lower() and prov_country.strip().lower() != "international" and db_country.strip().lower() != "international":
                checks_result["country"] = {
                    "passed": False,
                    "detail": f"Country mismatch: DB='{db_country}' vs Provider='{prov_country}'"
                }
                res = {
                    "status": ReconciliationStatus.COUNTRY_MISMATCH.value,
                    "severity": SeverityLevel.ERROR.value,
                    "fixture_id": fixture_id,
                    "provider": provider_name,
                    "provider_event_id": prov_event_id,
                    "checks": checks_result,
                    "verified": False,
                    "reason": f"Country mismatch: DB has '{db_country}' but provider indicates '{prov_country}'.",
                    "timestamp": now_str
                }
                cls._record_mapping_result(db, fixture, provider_name, prov_event_id, res, persist=persist_mapping)
                return res

        checks_result["country"] = {"passed": True, "detail": "Country verified or compatible"}

        # Check 10: Status check (detect severe status contradictions)
        if prov_status and fixture and fixture.status:
            p_st = str(prov_status).upper()
            f_st = str(fixture.status).upper()
            if ("CANCELLED" in p_st or "ABANDONED" in p_st) and f_st == "FINISHED":
                checks_result["match_status"] = {
                    "passed": False,
                    "detail": f"Status contradiction: DB='{f_st}' vs Provider='{p_st}'"
                }
                res = {
                    "status": ReconciliationStatus.STATUS_MISMATCH.value,
                    "severity": SeverityLevel.WARNING.value,
                    "fixture_id": fixture_id,
                    "provider": provider_name,
                    "provider_event_id": prov_event_id,
                    "checks": checks_result,
                    "verified": False,
                    "reason": f"Status mismatch: DB is '{f_st}' but provider reports '{p_st}'.",
                    "timestamp": now_str
                }
                cls._record_mapping_result(db, fixture, provider_name, prov_event_id, res, persist=persist_mapping)
                return res

        checks_result["match_status"] = {"passed": True, "detail": "Status aligned"}

        # All 9 checks passed successfully!
        res = {
            "status": ReconciliationStatus.IDENTITY_VALID.value,
            "severity": SeverityLevel.INFO.value,
            "fixture_id": fixture_id,
            "provider": provider_name,
            "provider_event_id": prov_event_id or clean_prov_id,
            "checks": checks_result,
            "verified": True,
            "reason": "All 9 identity and provenance checks verified successfully.",
            "timestamp": now_str
        }

        cls._record_mapping_result(db, fixture, provider_name, prov_event_id or clean_prov_id, res, persist=persist_mapping)
        return res

    @classmethod
    def _record_mapping_result(
        cls,
        db: Optional[Session],
        fixture: Optional[Fixture],
        provider_name: str,
        provider_event_id: Optional[str],
        diagnostic: Dict[str, Any],
        persist: bool = True
    ) -> None:
        """
        Safely records or updates the FixtureProviderMapping persistence table.
        Guarantees historical mapping integrity and prevents active mapping conflicts.
        """
        if not persist or not db or not fixture or not fixture.id:
            return

        clean_event_id = str(provider_event_id or fixture.external_id or "").strip()
        if not clean_event_id:
            return

        try:
            now_utc = datetime.now(timezone.utc)
            # Find existing mapping for (provider_name, provider_event_id)
            mapping = db.query(FixtureProviderMapping).filter(
                FixtureProviderMapping.provider_name == provider_name,
                FixtureProviderMapping.provider_event_id == clean_event_id
            ).first()

            if not mapping:
                # Also check by (fixture_id, provider_name)
                mapping = db.query(FixtureProviderMapping).filter(
                    FixtureProviderMapping.fixture_id == fixture.id,
                    FixtureProviderMapping.provider_name == provider_name
                ).first()

            is_valid = diagnostic.get("verified", False)
            reason = diagnostic.get("reason", "")
            val_json = json.dumps(diagnostic)

            if mapping:
                mapping.last_verified_at = now_utc if is_valid else mapping.last_verified_at
                mapping.mapping_status = "VERIFIED" if is_valid else "REJECTED"
                mapping.last_error = None if is_valid else reason
                mapping.validation_json = val_json
                if is_valid and not mapping.first_verified_at:
                    mapping.first_verified_at = now_utc
            else:
                mapping = FixtureProviderMapping(
                    fixture_id=fixture.id,
                    provider_name=provider_name,
                    provider_event_id=clean_event_id,
                    mapping_status="VERIFIED" if is_valid else "REJECTED",
                    first_verified_at=now_utc if is_valid else None,
                    last_verified_at=now_utc if is_valid else None,
                    last_error=None if is_valid else reason,
                    validation_json=val_json
                )
                db.add(mapping)

            db.commit()
        except Exception as e:
            logger.warning(f"Failed to record FixtureProviderMapping for fixture {fixture.id}: {e}")
            try:
                db.rollback()
            except Exception:
                pass
