import os
import sys
import logging
import hashlib
import httpx
import time
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple, cast
from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import Fixture, LiveMatchState, MatchStatistics, HistoricalResult, Team, FixtureProviderMapping, LiveObservedSnapshot
    from services.data_reconciliation_service import DataReconciliationService
    from services.observability_service import ObservabilityService
    from services.provider_reconciliation_service import ProviderReconciliationService
    from services.circuit_breaker_service import CircuitBreakerService
except ImportError:
    from ..models import Fixture, LiveMatchState, MatchStatistics, HistoricalResult, Team, FixtureProviderMapping, LiveObservedSnapshot
    from .data_reconciliation_service import DataReconciliationService
    from .observability_service import ObservabilityService
    from .provider_reconciliation_service import ProviderReconciliationService
    from .circuit_breaker_service import CircuitBreakerService

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
        Checks team names (with accent normalization) and kickoff window (<= 36h).
        Rejects mismatched fixtures to prevent cross-contamination.
        """
        import unicodedata
        import re

        header = summary_payload.get("header", {})
        comps = header.get("competitions", [])
        if not comps:
            return False, "No competitions found in provider header."

        comp = comps[0]

        # 0. Provider Event ID Validation
        prov_event_id = header.get("id") or comp.get("id")
        expected_event_id = cls.extract_provider_event_id(fixture)
        if prov_event_id and expected_event_id:
            clean_prov = str(prov_event_id).replace("ESPN-", "").strip()
            clean_exp = str(expected_event_id).replace("ESPN-", "").strip()
            if clean_prov != clean_exp:
                return False, f"Event ID mismatch: DB='{clean_exp}' vs Prov='{clean_prov}'"

        # 1. Kickoff Window Validation (prevent cross-season event ID confusion)
        prov_date_str = comp.get("date")
        if prov_date_str and fixture.match_date:
            try:
                prov_dt = datetime.fromisoformat(prov_date_str.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
                diff_hours = abs((prov_dt - fixture.match_date).total_seconds()) / 3600.0
                if diff_hours > 36.0:
                    return False, f"Kickoff window mismatch: DB={fixture.match_date} vs Prov={prov_dt} (diff={diff_hours:.1f}h > 36h)"
            except Exception as dt_ex:
                logger.debug(f"Date comparison skip in live validation: {dt_ex}")

        # 2. Team Name Normalization & Comparison
        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            return False, "Less than 2 competitors in provider header."

        prov_home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        prov_away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

        def _clean_team_name(name: str) -> str:
            if not name:
                return ""
            # Strip accents
            n = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('utf-8').lower()
            # Strip common soccer suffixes / prefixes
            n = re.sub(r'\b(fc|cf|sc|cd|afc|fk|vfb|ac|club|de|united|city)\b', '', n)
            # Remove non-alphanumeric chars
            n = re.sub(r'[^a-z0-9]', '', n)
            return n.strip()

        p_h_clean = _clean_team_name(prov_home.get("team", {}).get("name") or "")
        p_a_clean = _clean_team_name(prov_away.get("team", {}).get("name") or "")
        db_h_clean = _clean_team_name(fixture.home_team.name if fixture.home_team else "")
        db_a_clean = _clean_team_name(fixture.away_team.name if fixture.away_team else "")

        def _names_match(a: str, b: str) -> bool:
            if not a or not b:
                return True
            return a in b or b in a or (len(a) >= 4 and len(b) >= 4 and (a[:4] == b[:4]))

        if db_h_clean and not _names_match(db_h_clean, p_h_clean):
            return False, f"Home team mismatch: DB='{fixture.home_team.name if fixture.home_team else ''}' vs Prov='{prov_home.get('team', {}).get('name')}'"

        if db_a_clean and not _names_match(db_a_clean, p_a_clean):
            return False, f"Away team mismatch: DB='{fixture.away_team.name if fixture.away_team else ''}' vs Prov='{prov_away.get('team', {}).get('name')}'"

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

        circuit = CircuitBreakerService.get_circuit("ESPN")
        if not circuit.can_execute():
            logger.warning(f"Circuit Breaker for ESPN is OPEN. Serving last verified snapshot for fixture {fixture_id}.")
            return cls._handle_provider_failure(db, fixture, provider_event_id, error_msg="Provider circuit breaker is OPEN (high failure rate). Serving last verified local snapshot.")

        url = f"{cls.ESPN_BASE_URL}/all/summary?event={provider_event_id}"
        payload = None

        should_close_client = False
        if client is None:
            client = httpx.Client(timeout=8.0)
            should_close_client = True

        start_time = time.time()
        try:
            resp = client.get(url)
            latency_ms = (time.time() - start_time) * 1000.0
            if resp.status_code == 200:
                payload = resp.json()
                circuit.record_success(latency_ms=latency_ms)
            else:
                logger.warning(f"ESPN summary endpoint returned status {resp.status_code} for event {provider_event_id}")
                circuit.record_http_error(resp.status_code)
        except httpx.TimeoutException:
            logger.warning(f"Timeout connecting to ESPN live summary for fixture {fixture_id}")
            circuit.record_timeout()
        except Exception as ex:
            logger.warning(f"Error connecting to ESPN live summary for fixture {fixture_id}: {ex}")
            circuit.record_failure()
        finally:
            if should_close_client and client:
                client.close()

        if not payload:
            return cls._handle_provider_failure(db, fixture, provider_event_id)

        # 1. Deterministic Provider Reconciliation & Verification
        reconcile_res = ProviderReconciliationService.reconcile(
            fixture=fixture,
            provider_data=payload,
            provider_name="ESPN",
            db=db,
            persist_mapping=True
        )
        if not reconcile_res.get("verified", False):
            circuit.record_identity_mismatch()
            val_err = reconcile_res.get("reason", "Provider identity verification failed.")
            logger.error(f"Fixture {fixture_id} identity mismatch: {val_err}")
            ObservabilityService.log_event(
                "fixture_identity_mismatch", category="data_integrity", severity="WARNING",
                details={"fixture_id": fixture_id, "provider_event_id": provider_event_id, "reason": val_err, "diagnostic": reconcile_res}
            )
            return {
                "status": "REJECTED_IDENTITY_MISMATCH",
                "error": val_err,
                "fixture_id": fixture_id,
                "data_status": "UNAVAILABLE",
                "identity_status": reconcile_res.get("status"),
                "reconciliation": reconcile_res
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

        if state_type == "pre":
            clock_min = None
            display_clock = "PRE"

        # Competitors Score
        competitors = comp.get("competitors", [])
        h_comp = next((c for c in competitors if c.get("homeAway") == "home"), (competitors[0] if len(competitors) > 0 else {}))
        a_comp = next((c for c in competitors if c.get("homeAway") == "away"), (competitors[1] if len(competitors) > 1 else {}))

        h_score_raw = h_comp.get("score")
        a_score_raw = a_comp.get("score")

        try:
            h_score = int(h_score_raw) if h_score_raw is not None else (0 if state_type in ("in", "post") else None)
        except Exception:
            h_score = 0 if state_type in ("in", "post") else None

        try:
            a_score = int(a_score_raw) if a_score_raw is not None else (0 if state_type in ("in", "post") else None)
        except Exception:
            a_score = 0 if state_type in ("in", "post") else None

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

        live_state.minute = clock_min if clock_min is not None else 0
        live_state.period = period_label
        live_state.status = "FINISHED" if is_completed else ("LIVE" if state_type == "in" else "SCHEDULED")
        if h_score is not None:
            live_state.home_score = h_score
        if a_score is not None:
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

        # 7. Immutable Live Snapshot Versioning & Hashing
        raw_hash_str = f"{fixture_id}_{provider_event_id}_{clock_min}_{h_score}_{a_score}_{shots_h}_{shots_a}_{sot_h}_{sot_a}_{corn_h}_{corn_a}_{yellows_h}_{yellows_a}_{reds_h}_{reds_a}_{fouls_h}_{fouls_a}_{poss_h}_{poss_a}_{period_label}_{len(events_timeline)}"
        snapshot_hash = hashlib.sha256(raw_hash_str.encode("utf-8")).hexdigest()

        latest_snap = db.query(LiveObservedSnapshot).filter(
            LiveObservedSnapshot.fixture_id == fixture_id
        ).order_by(LiveObservedSnapshot.snapshot_version.desc()).first()

        if latest_snap and latest_snap.snapshot_hash == snapshot_hash:
            snapshot_version = latest_snap.snapshot_version
        else:
            snapshot_version = (latest_snap.snapshot_version + 1) if latest_snap else 1
            observed_snap = LiveObservedSnapshot(
                fixture_id=fixture_id,
                provider_name="ESPN",
                provider_event_id=str(provider_event_id),
                snapshot_version=snapshot_version,
                observed_at=now_utc.replace(tzinfo=None),
                retrieved_at=now_utc.replace(tzinfo=None),
                match_state=period_label,
                minute=clock_min or 0,
                home_score=h_score,
                away_score=a_score,
                statistics_json=json.dumps({
                    "shots": [shots_h, shots_a],
                    "shots_on_target": [sot_h, sot_a],
                    "corners": [corn_h, corn_a],
                    "possession": [poss_h, poss_a],
                    "fouls": [fouls_h, fouls_a],
                    "yellow_cards": [yellows_h, yellows_a],
                    "red_cards": [reds_h, reds_a]
                }),
                events_json=json.dumps(events_timeline),
                data_quality="EXCELLENT" if has_stats_core else ("PARTIAL" if stat_coverage == "PARTIAL" else "MINIMAL"),
                freshness="FRESH",
                snapshot_hash=snapshot_hash
            )
            db.add(observed_snap)

        db.commit()

        # Build normalized response
        return {
            "status": "FINISHED" if is_completed else ("LIVE" if state_type == "in" else "SCHEDULED"),
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
            "age_seconds": 0,
            "freshness": "FRESH",
            "freshness_status": "FRESH",
            "snapshot_version": snapshot_version,
            "snapshot_hash": snapshot_hash,
            "identity_status": "IDENTITY_VALID",
            "data_status": data_status,
            "data_quality_score": dq_score,
            "statistical_coverage": stat_coverage
        }

    @classmethod
    def record_observed_snapshot(
        cls, db: Session, fixture_id: int, payload: Dict[str, Any]
    ) -> LiveObservedSnapshot:
        """
        Deterministic, immutable snapshot recording.
        Calculates SHA-256 hash across minute, score, statistics, and events.
        If hash is identical to latest snapshot, returns existing snapshot without incrementing version.
        If hash differs, increments snapshot_version monotonically and persists a new snapshot.
        """
        now_utc = datetime.now(timezone.utc)
        provider_event_id = payload.get("provider_event_id") or "ESPN-LIVE"
        minute = payload.get("minute", 0)
        period = payload.get("period", "1H")
        home_score = payload.get("home_score", 0)
        away_score = payload.get("away_score", 0)
        stats = payload.get("statistics", {})
        shots = stats.get("shots", {}) if isinstance(stats, dict) else {}
        shots_h = shots.get("home") if isinstance(shots, dict) else None
        shots_a = shots.get("away") if isinstance(shots, dict) else None
        events = payload.get("events", [])

        raw_hash_str = f"{fixture_id}_{provider_event_id}_{minute}_{home_score}_{away_score}_{shots_h}_{shots_a}_{period}_{len(events)}"
        snapshot_hash = hashlib.sha256(raw_hash_str.encode("utf-8")).hexdigest()

        latest_snap = db.query(LiveObservedSnapshot).filter(
            LiveObservedSnapshot.fixture_id == fixture_id
        ).order_by(LiveObservedSnapshot.snapshot_version.desc()).first()

        if latest_snap and latest_snap.snapshot_hash == snapshot_hash:
            return latest_snap

        snapshot_version = (latest_snap.snapshot_version + 1) if latest_snap else 1
        observed_snap = LiveObservedSnapshot(
            fixture_id=fixture_id,
            provider_name="ESPN",
            provider_event_id=str(provider_event_id),
            snapshot_version=snapshot_version,
            observed_at=now_utc.replace(tzinfo=None),
            retrieved_at=now_utc.replace(tzinfo=None),
            match_state=period,
            minute=minute,
            home_score=home_score,
            away_score=away_score,
            statistics_json=json.dumps(stats),
            events_json=json.dumps(events),
            data_quality="EXCELLENT",
            freshness="FRESH",
            snapshot_hash=snapshot_hash
        )
        db.add(observed_snap)
        db.commit()
        db.refresh(observed_snap)
        return observed_snap

    @classmethod
    def calculate_freshness(cls, last_updated: Optional[datetime]) -> Dict[str, Any]:
        """
        Calculates standardized freshness category:
        FRESH < 60s, DELAYED 60-180s, STALE 180-300s, VERY_STALE > 300s, UNAVAILABLE.
        """
        if not last_updated:
            return {"status": "UNAVAILABLE", "age_seconds": None}
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        if isinstance(last_updated, datetime) and last_updated.tzinfo:
            last_updated = last_updated.astimezone(timezone.utc).replace(tzinfo=None)
        age_sec = max(0.0, (now_utc - last_updated).total_seconds())
        if age_sec < FRESH_THRESHOLD_SEC:
            status = "FRESH"
        elif age_sec <= STALE_THRESHOLD_SEC:
            status = "DELAYED"
        elif age_sec <= 300:
            status = "STALE"
        else:
            status = "VERY_STALE"
        return {"status": status, "age_seconds": int(age_sec)}

    @classmethod
    def _handle_provider_failure(
        cls, db: Session, fixture: Fixture, provider_event_id: str, error_msg: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Gracefully handles provider unavailability without fabricating or resetting values.
        Retains the last verified state and assigns explicit STALE data status.
        Never resets home_score or away_score to 0 or minute to 0!
        """
        live_state = db.query(LiveMatchState).filter(LiveMatchState.fixture_id == fixture.id).first()
        latest_snap = db.query(LiveObservedSnapshot).filter(
            LiveObservedSnapshot.fixture_id == fixture.id
        ).order_by(LiveObservedSnapshot.snapshot_version.desc()).first()

        now_utc = datetime.now(timezone.utc)

        if live_state and live_state.last_updated:
            f_info = cls.calculate_freshness(live_state.last_updated)
            data_status = f_info["status"]
            age_sec = f_info["age_seconds"] or 0
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
                "age_seconds": max(0, int(age_sec)),
                "freshness": data_status,
                "freshness_status": data_status,
                "snapshot_version": latest_snap.snapshot_version if latest_snap else 1,
                "snapshot_hash": latest_snap.snapshot_hash if latest_snap else "cached",
                "identity_status": "IDENTITY_VALID",
                "data_status": data_status,
                "data_quality_score": 50,
                "statistical_coverage": "PARTIAL",
                "error": error_msg or "Live provider request failed. Serving last verified local snapshot."
            }

        return {
            "status": fixture.status or "SCHEDULED",
            "is_completed": False,
            "fixture_id": fixture.id,
            "provider_fixture_id": provider_event_id,
            "provider": "ESPN",
            "period": "PRE",
            "minute": None,
            "display_clock": "—",
            "score": {"home": None, "away": None},
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
            "age_seconds": None,
            "freshness": "UNAVAILABLE",
            "freshness_status": "UNAVAILABLE",
            "snapshot_version": 0,
            "snapshot_hash": "none",
            "identity_status": "UNAVAILABLE",
            "data_status": "UNAVAILABLE",
            "data_quality_score": 0,
            "statistical_coverage": "UNAVAILABLE",
            "error": error_msg or "No verified live snapshot received from provider."
        }
