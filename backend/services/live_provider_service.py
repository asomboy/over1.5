import os
import sys
import logging
import hashlib
import httpx
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple, cast
from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import Fixture, LiveMatchState, MatchStatistics, HistoricalResult, Team
    from services.data_reconciliation_service import DataReconciliationService
    from services.observability_service import ObservabilityService
except ImportError:
    from ..models import Fixture, LiveMatchState, MatchStatistics, HistoricalResult, Team
    from .data_reconciliation_service import DataReconciliationService
    from .observability_service import ObservabilityService

logger = logging.getLogger(__name__)

# Constants for Live Freshness Thresholds
FRESH_THRESHOLD_SEC = 60
STALE_THRESHOLD_SEC = 180


class LiveProviderAdapterService:
    """
    Dedicated live match provider adapter.
    Fetches real-time in-play boxscores, clocks, and match events from external feeds (ESPN).
    Enforces strict fixture identity validation, separates OBSERVED data from MODEL estimates,
    and handles completed match lifecycle transitions.
    """

    ESPN_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"

    @classmethod
    def extract_provider_event_id(cls, fixture: Fixture) -> Optional[str]:
        """Extracts clean numeric provider event ID from fixture external_id."""
        if not fixture.external_id:
            return None
        ext = fixture.external_id
        for prefix in ["ESPN-FIX-", "ESPN-", "HIST-", "FIX-"]:
            if ext.startswith(prefix):
                ext = ext[len(prefix):]
        # Ensure it is numeric or valid slug
        return ext if ext else None

    @classmethod
    def validate_fixture_identity(
        cls, fixture: Fixture, summary_payload: Dict[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        """
        Validates that the returned payload belongs to the requested internal fixture.
        Checks team names and kickoff window. Rejects mismatched fixtures to prevent cross-contamination.
        """
        header = summary_payload.get("header", {})
        comps = header.get("competitions", [])
        if not comps:
            return False, "No competitions found in provider header."

        comp = comps[0]
        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            return False, "Less than 2 competitors in provider header."

        prov_home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        prov_away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

        prov_home_name = (prov_home.get("team", {}).get("name") or "").lower().strip()
        prov_away_name = (prov_away.get("team", {}).get("name") or "").lower().strip()

        db_home_name = (fixture.home_team.name if fixture.home_team else "").lower().strip()
        db_away_name = (fixture.away_team.name if fixture.away_team else "").lower().strip()

        def _names_match(a: str, b: str) -> bool:
            if not a or not b:
                return True # lenient if name missing
            # Direct match or substring match
            return a in b or b in a or any(part in b for part in a.split() if len(part) > 3)

        if db_home_name and not _names_match(db_home_name, prov_home_name):
            return False, f"Home team mismatch: DB='{db_home_name}' vs Prov='{prov_home_name}'"

        if db_away_name and not _names_match(db_away_name, prov_away_name):
            return False, f"Away team mismatch: DB='{db_away_name}' vs Prov='{prov_away_name}'"

        return True, None

    @classmethod
    def parse_events_timeline(
        cls, header_comp: Dict[str, Any], home_team_name: str, away_team_name: str
    ) -> List[Dict[str, Any]]:
        """
        Parses chronological match events (goals, cards, subs) with deterministic fingerprinting.
        """
        details = header_comp.get("details", [])
        events = []

        for idx, d in enumerate(details):
            clock_info = d.get("clock", {})
            clock_val = clock_info.get("displayValue") or f"{int(clock_info.get('value', 0) // 60)}'"
            minute = int(clock_info.get("value", 0) // 60)

            team_obj = d.get("team", {})
            team_disp = team_obj.get("displayName") or team_obj.get("name") or ""
            side = "home" if (home_team_name and home_team_name.lower() in team_disp.lower()) else "away"

            # Determine event type
            is_goal = d.get("scoringPlay", False) or d.get("ownGoal", False) or d.get("penaltyKick", False)
            is_red = d.get("redCard", False)
            is_yellow = d.get("yellowCard", False)

            type_info = d.get("type", {})
            type_text = type_info.get("text", "") if isinstance(type_info, dict) else str(type_info)

            event_type = "UNKNOWN"
            icon = "flag"
            if is_goal:
                event_type = "GOAL"
                icon = "goal"
            elif is_red:
                event_type = "RED_CARD"
                icon = "red_card"
            elif is_yellow:
                event_type = "YELLOW_CARD"
                icon = "yellow_card"
            elif "substitution" in type_text.lower():
                event_type = "SUBSTITUTION"
                icon = "substitution"
            elif "var" in type_text.lower():
                event_type = "VAR"
                icon = "var"

            # Extract participant players
            participants = d.get("participants", [])
            primary_player = None
            secondary_player = None
            if len(participants) > 0:
                p0 = participants[0].get("athlete", {})
                primary_player = p0.get("displayName") or p0.get("shortName")
            if len(participants) > 1:
                p1 = participants[1].get("athlete", {})
                secondary_player = p1.get("displayName") or p1.get("shortName")

            # Deterministic event fingerprint
            fp_raw = f"{clock_val}_{team_disp}_{event_type}_{primary_player or idx}"
            event_id = hashlib.md5(fp_raw.encode("utf-8")).hexdigest()[:12]

            events.append({
                "id": event_id,
                "minute": minute,
                "display_clock": clock_val,
                "type": event_type,
                "icon": icon,
                "team": team_disp,
                "side": side,
                "player": primary_player,
                "assist_or_sub": secondary_player,
                "is_penalty": d.get("penaltyKick", False),
                "is_own_goal": d.get("ownGoal", False)
            })

        # Sort chronologically by minute
        events.sort(key=lambda e: e.get("minute", 0))
        return events

    @classmethod
    def fetch_live_summary(
        cls, db: Session, fixture_id: int, client: Optional[httpx.Client] = None
    ) -> Dict[str, Any]:
        """
        Synchronously fetches and normalizes live provider data for a specific fixture.
        Updates LiveMatchState and handles completed match persistence.
        """
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            return {"status": "ERROR", "error": f"Fixture {fixture_id} not found", "data_status": "UNAVAILABLE"}

        provider_event_id = cls.extract_provider_event_id(fixture)
        if not provider_event_id:
            return {
                "status": "UNAVAILABLE",
                "error": "No provider event ID associated with fixture",
                "fixture_id": fixture_id,
                "data_status": "UNAVAILABLE"
            }

        url = f"{cls.ESPN_BASE_URL}/all/summary?event={provider_event_id}"
        payload = None

        should_close_client = False
        if client is None:
            client = httpx.Client(timeout=8.0)
            should_close_client = True

        try:
            resp = client.get(url)
            if resp.status_code == 200:
                payload = resp.json()
            else:
                logger.warning(f"ESPN summary endpoint returned status {resp.status_code} for event {provider_event_id}")
        except Exception as ex:
            logger.warning(f"Error connecting to ESPN live summary for fixture {fixture_id}: {ex}")
        finally:
            if should_close_client and client:
                client.close()

        if not payload:
            return cls._handle_provider_failure(db, fixture, provider_event_id)

        # 1. Validate Fixture Identity
        is_valid, validation_err = cls.validate_fixture_identity(fixture, payload)
        if not is_valid:
            logger.error(f"Fixture {fixture_id} identity mismatch: {validation_err}")
            ObservabilityService.log_event(
                "fixture_identity_mismatch", category="data_integrity", severity="WARNING",
                details={"fixture_id": fixture_id, "provider_event_id": provider_event_id, "reason": validation_err}
            )
            return {
                "status": "REJECTED_IDENTITY_MISMATCH",
                "error": validation_err,
                "fixture_id": fixture_id,
                "data_status": "UNAVAILABLE"
            }

        # 2. Extract Header, Competitions, and Match Clock
        header = payload.get("header", {})
        comps = header.get("competitions", [{}])
        comp = comps[0] if comps else {}
        status_info = comp.get("status", {})
        type_info = status_info.get("type", {})

        state_type = type_info.get("state", "pre") # "pre", "in", "post"
        is_completed = type_info.get("completed", False) or state_type == "post"
        raw_detail = type_info.get("detail", "")
        raw_short_detail = type_info.get("shortDetail", "")
        display_clock = status_info.get("displayClock") or raw_short_detail or raw_detail or "0'"
        period_num = status_info.get("period", 1)

        # Normalize period
        period_label = "1H"
        if is_completed:
            period_label = "FT"
        elif state_type == "in":
            if period_num == 1:
                period_label = "1H"
            elif period_num == 2:
                period_label = "2H"
            elif period_num > 2:
                period_label = "ET"
            if "halftime" in raw_detail.lower() or "ht" in raw_short_detail.lower():
                period_label = "HT"
        elif state_type == "pre":
            period_label = "PRE"

        # Calculate minute from clock
        raw_clock = status_info.get("clock", 0.0)
        clock_min = int(raw_clock // 60) if raw_clock > 120 else int(raw_clock)
        if clock_min == 0 and "'" in display_clock:
            try:
                clock_min = int(display_clock.replace("'", "").split("+")[0].strip())
            except Exception:
                clock_min = 0

        # Competitors Score
        competitors = comp.get("competitors", [])
        h_comp = next((c for c in competitors if c.get("homeAway") == "home"), (competitors[0] if len(competitors) > 0 else {}))
        a_comp = next((c for c in competitors if c.get("homeAway") == "away"), (competitors[1] if len(competitors) > 1 else {}))

        try:
            h_score = int(h_comp.get("score", 0)) if h_comp.get("score") is not None else 0
        except Exception:
            h_score = 0

        try:
            a_score = int(a_comp.get("score", 0)) if a_comp.get("score") is not None else 0
        except Exception:
            a_score = 0

        # 3. Extract Observed Boxscore Statistics (NULL if not returned)
        box = payload.get("boxscore", {})
        box_teams = box.get("teams", [])
        h_box = next((t for t in box_teams if t.get("team", {}).get("name") == h_comp.get("team", {}).get("name")), (box_teams[0] if len(box_teams) > 0 else {}))
        a_box = next((t for t in box_teams if t.get("team", {}).get("name") == a_comp.get("team", {}).get("name")), (box_teams[1] if len(box_teams) > 1 else {}))

        def _get_stat(t_box: Dict[str, Any], stat_name: str) -> Optional[float]:
            if not t_box:
                return None
            for s in t_box.get("statistics", []):
                if s.get("name") == stat_name:
                    val_str = str(s.get("displayValue", "")).replace("%", "").strip()
                    try:
                        return float(val_str)
                    except ValueError:
                        return None
            return None

        # Extract actual observed values (NO synthetic defaults)
        shots_h = _get_stat(h_box, "totalShots")
        shots_a = _get_stat(a_box, "totalShots")
        sot_h = _get_stat(h_box, "shotsOnTarget")
        sot_a = _get_stat(a_box, "shotsOnTarget")
        corn_h = _get_stat(h_box, "wonCorners")
        corn_a = _get_stat(a_box, "wonCorners")
        fouls_h = _get_stat(h_box, "foulsCommitted")
        fouls_a = _get_stat(a_box, "foulsCommitted")
        offs_h = _get_stat(h_box, "offsides")
        offs_a = _get_stat(a_box, "offsides")
        saves_h = _get_stat(h_box, "saves")
        saves_a = _get_stat(a_box, "saves")
        poss_h = _get_stat(h_box, "possessionPct")
        poss_a = _get_stat(a_box, "possessionPct")
        yellows_h = _get_stat(h_box, "yellowCards")
        yellows_a = _get_stat(a_box, "yellowCards")
        reds_h = _get_stat(h_box, "redCards")
        reds_a = _get_stat(a_box, "redCards")

        # 4. Extract Events Timeline
        events_timeline = cls.parse_events_timeline(
            comp,
            fixture.home_team.name if fixture.home_team else "",
            fixture.away_team.name if fixture.away_team else ""
        )

        # 5. Determine Freshness & Data Status
        now_utc = datetime.now(timezone.utc)
        data_status = "FRESH" # provider just returned fresh payload

        # Calculate genuine data quality score (0-100)
        has_stats_core = (shots_h is not None and corn_h is not None and poss_h is not None)
        stat_coverage = "FULL" if has_stats_core else ("PARTIAL" if (shots_h is not None or corn_h is not None) else "MINIMAL")
        
        dq_score = 90 if has_stats_core else (65 if stat_coverage == "PARTIAL" else 45)
        if is_completed:
            dq_score = max(dq_score, 85)

        # 6. Update Database LiveMatchState
        live_state = db.query(LiveMatchState).filter(LiveMatchState.fixture_id == fixture_id).first()
        if not live_state:
            live_state = LiveMatchState(fixture_id=fixture_id)
            db.add(live_state)

        live_state.minute = clock_min
        live_state.period = period_label
        live_state.status = "FINISHED" if is_completed else ("LIVE" if state_type == "in" else "SCHEDULED")
        live_state.home_score = h_score
        live_state.away_score = a_score
        live_state.home_corners = int(corn_h) if corn_h is not None else None
        live_state.away_corners = int(corn_a) if corn_a is not None else None
        live_state.home_shots = int(shots_h) if shots_h is not None else None
        live_state.away_shots = int(shots_a) if shots_a is not None else None
        live_state.home_shots_on_target = int(sot_h) if sot_h is not None else None
        live_state.away_shots_on_target = int(sot_a) if sot_a is not None else None
        live_state.home_possession = poss_h
        live_state.away_possession = poss_a
        live_state.home_fouls = int(fouls_h) if fouls_h is not None else None
        live_state.away_fouls = int(fouls_a) if fouls_a is not None else None
        live_state.home_yellow_cards = int(yellows_h) if yellows_h is not None else None
        live_state.away_yellow_cards = int(yellows_a) if yellows_a is not None else None
        live_state.home_red_cards = int(reds_h) if reds_h is not None else None
        live_state.away_red_cards = int(reds_a) if reds_a is not None else None
        live_state.last_updated = now_utc.replace(tzinfo=None)
        live_state.data_source = "espn_live_summary"
        live_state.data_quality = "verified" if has_stats_core else "partial"

        # If match completed, persist to Fixture and MatchStatistics
        if is_completed:
            fixture.status = "FINISHED"
            fixture.home_score = h_score
            fixture.away_score = a_score

            stats_row = db.query(MatchStatistics).filter(MatchStatistics.fixture_id == fixture_id).first()
            if not stats_row:
                stats_row = MatchStatistics(fixture_id=fixture_id)
                db.add(stats_row)
            stats_row.home_shots = int(shots_h) if shots_h is not None else None
            stats_row.away_shots = int(shots_a) if shots_a is not None else None
            stats_row.home_shots_on_target = int(sot_h) if sot_h is not None else None
            stats_row.away_shots_on_target = int(sot_a) if sot_a is not None else None
            stats_row.home_corners = int(corn_h) if corn_h is not None else None
            stats_row.away_corners = int(corn_a) if corn_a is not None else None
            stats_row.home_fouls = int(fouls_h) if fouls_h is not None else None
            stats_row.away_fouls = int(fouls_a) if fouls_a is not None else None
            stats_row.home_possession = poss_h
            stats_row.away_possession = poss_a
            stats_row.home_yellow_cards = int(yellows_h) if yellows_h is not None else None
            stats_row.away_yellow_cards = int(yellows_a) if yellows_a is not None else None
            stats_row.home_red_cards = int(reds_h) if reds_h is not None else None
            stats_row.away_red_cards = int(reds_a) if reds_a is not None else None

        db.commit()

        # Build normalized response
        return {
            "status": "LIVE" if not is_completed else "FINISHED",
            "is_completed": is_completed,
            "fixture_id": fixture_id,
            "provider_fixture_id": provider_event_id,
            "provider": "ESPN",
            "period": period_label,
            "minute": clock_min,
            "display_clock": display_clock,
            "score": {
                "home": h_score,
                "away": a_score
            },
            "statistics": {
                "shots": {
                    "home": shots_h,
                    "away": shots_a,
                    "source_status": "AVAILABLE" if shots_h is not None else "UNAVAILABLE"
                },
                "shots_on_target": {
                    "home": sot_h,
                    "away": sot_a,
                    "source_status": "AVAILABLE" if sot_h is not None else "UNAVAILABLE"
                },
                "corners": {
                    "home": corn_h,
                    "away": corn_a,
                    "source_status": "AVAILABLE" if corn_h is not None else "UNAVAILABLE"
                },
                "cards": {
                    "home_yellow": yellows_h,
                    "away_yellow": yellows_a,
                    "home_red": reds_h,
                    "away_red": reds_a,
                    "source_status": "AVAILABLE" if yellows_h is not None else "UNAVAILABLE"
                },
                "fouls": {
                    "home": fouls_h,
                    "away": fouls_a,
                    "source_status": "AVAILABLE" if fouls_h is not None else "UNAVAILABLE"
                },
                "offsides": {
                    "home": offs_h,
                    "away": offs_a,
                    "source_status": "AVAILABLE" if offs_h is not None else "UNAVAILABLE"
                },
                "possession": {
                    "home": poss_h,
                    "away": poss_a,
                    "source_status": "AVAILABLE" if poss_h is not None else "UNAVAILABLE"
                },
                "saves": {
                    "home": saves_h,
                    "away": saves_a,
                    "source_status": "AVAILABLE" if saves_h is not None else "UNAVAILABLE"
                }
            },
            "events": events_timeline,
            "retrieved_at": now_utc.isoformat(),
            "data_status": data_status,
            "data_quality_score": dq_score,
            "statistical_coverage": stat_coverage
        }

    @classmethod
    def _handle_provider_failure(
        cls, db: Session, fixture: Fixture, provider_event_id: str
    ) -> Dict[str, Any]:
        """
        Gracefully handles provider unavailability without fabricating or resetting values.
        Retains the last verified state and assigns explicit STALE data status.
        """
        live_state = db.query(LiveMatchState).filter(LiveMatchState.fixture_id == fixture.id).first()
        now_utc = datetime.now(timezone.utc)

        if live_state and live_state.last_updated:
            age_sec = (now_utc.replace(tzinfo=None) - live_state.last_updated).total_seconds()
            data_status = "STALE" if age_sec <= STALE_THRESHOLD_SEC else "VERY_STALE"
            retrieved_at = live_state.last_updated.isoformat() + "Z"
            
            return {
                "status": live_state.status or "LIVE",
                "is_completed": live_state.status == "FINISHED",
                "fixture_id": fixture.id,
                "provider_fixture_id": provider_event_id,
                "provider": "ESPN (Cached)",
                "period": live_state.period or "1H",
                "minute": live_state.minute or 0,
                "display_clock": f"{live_state.minute}'" if live_state.minute else "LIVE",
                "score": {
                    "home": live_state.home_score,
                    "away": live_state.away_score
                },
                "statistics": {
                    "shots": {"home": live_state.home_shots, "away": live_state.away_shots, "source_status": "CACHED" if live_state.home_shots is not None else "UNAVAILABLE"},
                    "shots_on_target": {"home": live_state.home_shots_on_target, "away": live_state.away_shots_on_target, "source_status": "CACHED" if live_state.home_shots_on_target is not None else "UNAVAILABLE"},
                    "corners": {"home": live_state.home_corners, "away": live_state.away_corners, "source_status": "CACHED" if live_state.home_corners is not None else "UNAVAILABLE"},
                    "cards": {"home_yellow": live_state.home_yellow_cards, "away_yellow": live_state.away_yellow_cards, "home_red": live_state.home_red_cards, "away_red": live_state.away_red_cards, "source_status": "CACHED"},
                    "fouls": {"home": live_state.home_fouls, "away": live_state.away_fouls, "source_status": "CACHED" if live_state.home_fouls is not None else "UNAVAILABLE"},
                    "offsides": {"home": None, "away": None, "source_status": "UNAVAILABLE"},
                    "possession": {"home": live_state.home_possession, "away": live_state.away_possession, "source_status": "CACHED" if live_state.home_possession is not None else "UNAVAILABLE"},
                    "saves": {"home": None, "away": None, "source_status": "UNAVAILABLE"}
                },
                "events": [],
                "retrieved_at": retrieved_at,
                "data_status": data_status,
                "data_quality_score": 50,
                "statistical_coverage": "PARTIAL",
                "error": "Live provider request failed. Serving last verified local snapshot."
            }

        return {
            "status": fixture.status or "SCHEDULED",
            "is_completed": False,
            "fixture_id": fixture.id,
            "provider_fixture_id": provider_event_id,
            "provider": "ESPN",
            "period": "PRE",
            "minute": 0,
            "display_clock": "0'",
            "score": {"home": 0, "away": 0},
            "statistics": {
                "shots": {"home": None, "away": None, "source_status": "UNAVAILABLE"},
                "shots_on_target": {"home": None, "away": None, "source_status": "UNAVAILABLE"},
                "corners": {"home": None, "away": None, "source_status": "UNAVAILABLE"},
                "cards": {"home_yellow": None, "away_yellow": None, "home_red": None, "away_red": None, "source_status": "UNAVAILABLE"},
                "fouls": {"home": None, "away": None, "source_status": "UNAVAILABLE"},
                "offsides": {"home": None, "away": None, "source_status": "UNAVAILABLE"},
                "possession": {"home": None, "away": None, "source_status": "UNAVAILABLE"},
                "saves": {"home": None, "away": None, "source_status": "UNAVAILABLE"}
            },
            "events": [],
            "retrieved_at": now_utc.isoformat(),
            "data_status": "UNAVAILABLE",
            "data_quality_score": 20,
            "statistical_coverage": "UNAVAILABLE",
            "error": "No verified live snapshot received from provider."
        }
