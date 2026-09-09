import os
import sys
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import (
        Fixture, HistoricalResult, MatchStatistics, RefereeMatchStatistics,
        LivePredictionSnapshot, League, Team, DataQualitySnapshot
    )
except ImportError:
    from ..models import (
        Fixture, HistoricalResult, MatchStatistics, RefereeMatchStatistics,
        LivePredictionSnapshot, League, Team, DataQualitySnapshot
    )

logger = logging.getLogger(__name__)


class DataQualityService:
    """
    Central data coverage registry computing exact mathematical data quality ratios
    across goals, corners, cards, referee assignments, and live snapshots.
    Formula: coverage = observed_records / max(1, eligible_records)
    """

    @classmethod
    def calculate_global_coverage(cls, db: Session, persist: bool = False) -> Dict[str, Any]:
        """Calculates exact global data coverage across completed fixtures."""
        eligible_matches = db.query(Fixture).filter(Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"])).count()
        total_fixtures = db.query(Fixture).count()

        # 1. Goals coverage
        goals_observed = (
            db.query(HistoricalResult)
            .join(Fixture, HistoricalResult.fixture_id == Fixture.id)
            .filter(
                Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                HistoricalResult.home_score.isnot(None),
                HistoricalResult.away_score.isnot(None)
            )
            .count()
        )
        goals_coverage = round(goals_observed / max(1, eligible_matches), 4)

        # 2. Corners coverage
        corners_observed = (
            db.query(MatchStatistics)
            .join(Fixture, MatchStatistics.fixture_id == Fixture.id)
            .filter(
                Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                MatchStatistics.home_corners.isnot(None),
                MatchStatistics.away_corners.isnot(None)
            )
            .count()
        )
        corners_coverage = round(corners_observed / max(1, eligible_matches), 4)

        # 3. Cards coverage
        cards_observed = (
            db.query(MatchStatistics)
            .join(Fixture, MatchStatistics.fixture_id == Fixture.id)
            .filter(
                Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                MatchStatistics.home_yellow_cards.isnot(None),
                MatchStatistics.away_yellow_cards.isnot(None)
            )
            .count()
        )
        cards_coverage = round(cards_observed / max(1, eligible_matches), 4)

        # 4. Referee coverage
        referee_observed = (
            db.query(MatchStatistics)
            .join(Fixture, MatchStatistics.fixture_id == Fixture.id)
            .filter(
                Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                or_(MatchStatistics.referee_id.isnot(None), MatchStatistics.referee_name.isnot(None))
            )
            .count()
        )
        referee_coverage = round(referee_observed / max(1, eligible_matches), 4)

        # 5. Live Snapshot coverage
        live_eligible = db.query(Fixture).filter(Fixture.status.in_(["LIVE", "FINISHED", "FT"])).count()
        live_observed = (
            db.query(LivePredictionSnapshot.fixture_id)
            .distinct()
            .count()
        )
        live_coverage = round(live_observed / max(1, live_eligible), 4)

        metrics = {
            "total_fixtures": total_fixtures,
            "eligible_completed_matches": eligible_matches,
            "goals_coverage": {
                "metric": "goals_data_coverage",
                "eligible": eligible_matches,
                "observed": goals_observed,
                "missing": eligible_matches - goals_observed,
                "coverage_ratio": goals_coverage
            },
            "corners_coverage": {
                "metric": "corner_data_coverage",
                "eligible": eligible_matches,
                "observed": corners_observed,
                "missing": eligible_matches - corners_observed,
                "coverage_ratio": corners_coverage
            },
            "cards_coverage": {
                "metric": "card_data_coverage",
                "eligible": eligible_matches,
                "observed": cards_observed,
                "missing": eligible_matches - cards_observed,
                "coverage_ratio": cards_coverage
            },
            "referee_coverage": {
                "metric": "referee_data_coverage",
                "eligible": eligible_matches,
                "observed": referee_observed,
                "missing": eligible_matches - referee_observed,
                "coverage_ratio": referee_coverage
            },
            "live_coverage": {
                "metric": "live_snapshot_coverage",
                "eligible": live_eligible,
                "observed": live_observed,
                "missing": live_eligible - live_observed,
                "coverage_ratio": live_coverage
            },
            "calculated_at": datetime.now(timezone.utc).isoformat()
        }

        if persist:
            for cat_key in ["goals_coverage", "corners_coverage", "cards_coverage", "referee_coverage", "live_coverage"]:
                item = metrics[cat_key]
                snap = DataQualitySnapshot(
                    scope_type="global",
                    scope_id=None,
                    metric=item["metric"],
                    eligible_count=item["eligible"],
                    observed_count=item["observed"],
                    missing_count=item["missing"],
                    coverage_ratio=item["coverage_ratio"],
                    calculated_at=datetime.now(timezone.utc)
                )
                db.add(snap)
            db.commit()

        return metrics

    @classmethod
    def calculate_competition_coverage(cls, db: Session) -> List[Dict[str, Any]]:
        """Calculates data quality and coverage breakdown grouped by league / competition."""
        leagues = db.query(League).all()
        results = []

        for lg in leagues:
            eligible = (
                db.query(Fixture)
                .filter(Fixture.league_id == lg.id, Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]))
                .count()
            )

            goals_obs = (
                db.query(HistoricalResult)
                .join(Fixture, HistoricalResult.fixture_id == Fixture.id)
                .filter(
                    Fixture.league_id == lg.id,
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    HistoricalResult.home_score.isnot(None)
                )
                .count()
            )

            corners_obs = (
                db.query(MatchStatistics)
                .join(Fixture, MatchStatistics.fixture_id == Fixture.id)
                .filter(
                    Fixture.league_id == lg.id,
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    MatchStatistics.home_corners.isnot(None)
                )
                .count()
            )

            cards_obs = (
                db.query(MatchStatistics)
                .join(Fixture, MatchStatistics.fixture_id == Fixture.id)
                .filter(
                    Fixture.league_id == lg.id,
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    MatchStatistics.home_yellow_cards.isnot(None)
                )
                .count()
            )

            ref_obs = (
                db.query(MatchStatistics)
                .join(Fixture, MatchStatistics.fixture_id == Fixture.id)
                .filter(
                    Fixture.league_id == lg.id,
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    or_(MatchStatistics.referee_id.isnot(None), MatchStatistics.referee_name.isnot(None))
                )
                .count()
            )

            results.append({
                "league_id": lg.id,
                "competition": lg.name,
                "country": lg.country,
                "eligible_matches": eligible,
                "goals_coverage": round(goals_obs / max(1, eligible), 4),
                "corners_coverage": round(corners_obs / max(1, eligible), 4),
                "cards_coverage": round(cards_obs / max(1, eligible), 4),
                "referee_coverage": round(ref_obs / max(1, eligible), 4)
            })

        results.sort(key=lambda x: x["eligible_matches"], reverse=True)
        return results

    @classmethod
    def compute_fixture_data_quality(
        cls,
        db: Session,
        fixture_id: int,
        live_state: Optional[Any] = None,
        provider_diagnostic: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Computes deterministic fixture-level data quality score (0-100) and label:
        EXCELLENT (85-100), GOOD (70-84), MODERATE (50-69), POOR (1-49), UNAVAILABLE (0).
        Evaluates:
        1. Identity Validity (0-25)
        2. Provider Availability (0-20)
        3. Freshness (0-20)
        4. Statistical Coverage (0-20)
        5. Event Completeness (0-15)
        Never fabricates missing values; explains why the score was assigned.
        """
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            return {
                "fixture_id": fixture_id,
                "score": 0,
                "label": "UNAVAILABLE",
                "factors": {},
                "explanation": f"Fixture {fixture_id} not found in database."
            }

        score = 0
        factors = {}
        explanations = []

        # 1. Identity Validity (max 25)
        id_score = 25
        id_status = "VALID"
        if provider_diagnostic:
            if not provider_diagnostic.get("verified", False):
                id_score = 0
                id_status = provider_diagnostic.get("status", "IDENTITY_MISMATCH")
                explanations.append(f"Identity verification failed: {provider_diagnostic.get('reason', '')}")
            else:
                explanations.append("Fixture identity verified with provider.")
        else:
            if not fixture.external_id:
                id_score = 15
                id_status = "PARTIAL_NO_EXTERNAL_ID"
                explanations.append("Fixture lacks external provider event ID.")
            else:
                explanations.append("Fixture has valid external ID.")

        factors["identity_validity"] = {"score": id_score, "max": 25, "status": id_status}
        score += id_score

        # If identity is completely invalid, the entire data quality is UNAVAILABLE
        if id_score == 0:
            return {
                "fixture_id": fixture_id,
                "score": 0,
                "label": "UNAVAILABLE",
                "factors": factors,
                "explanation": "Fixture failed identity validation. Data rejected to prevent contamination."
            }

        # 2. Provider Availability & Health (max 20)
        prov_score = 20
        prov_status = "AVAILABLE"
        if provider_diagnostic and provider_diagnostic.get("status") in ["PROVIDER_UNAVAILABLE", "PROVIDER_EVENT_NOT_FOUND"]:
            prov_score = 0
            prov_status = "UNAVAILABLE"
            explanations.append("Provider is currently offline or event not found.")
        else:
            explanations.append("Provider feed accessible.")
        factors["provider_availability"] = {"score": prov_score, "max": 20, "status": prov_status}
        score += prov_score

        # 3. Freshness (max 20)
        fresh_score = 20
        fresh_status = "FRESH"
        now_utc = datetime.now(timezone.utc)

        ts = None
        if live_state and hasattr(live_state, "last_updated") and live_state.last_updated:
            ts = live_state.last_updated
        elif fixture.status in ["FINISHED", "FT", "AET", "PEN"]:
            fresh_score = 20
            fresh_status = "VERIFIED_COMPLETED"
            explanations.append("Completed match state is permanent and verified.")

        if ts:
            ts_utc = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
            age = max(0, int((now_utc - ts_utc).total_seconds()))
            if age < 60:
                fresh_score = 20
                fresh_status = "FRESH"
            elif age <= 180:
                fresh_score = 14
                fresh_status = "DELAYED"
                explanations.append(f"Feed update is delayed ({age}s ago).")
            elif age <= 300:
                fresh_score = 8
                fresh_status = "STALE"
                explanations.append(f"Feed update is stale ({age}s ago).")
            else:
                fresh_score = 2
                fresh_status = "VERY_STALE"
                explanations.append(f"Feed update is very stale ({age}s ago).")

        factors["freshness"] = {"score": fresh_score, "max": 20, "status": fresh_status}
        score += fresh_score

        # 4. Statistical Coverage (max 20)
        stats = db.query(MatchStatistics).filter(MatchStatistics.fixture_id == fixture_id).first()
        stat_score = 0
        stat_status = "UNAVAILABLE"

        has_shots = (stats and stats.home_shots is not None) or (live_state and getattr(live_state, "home_shots", None) is not None)
        has_corners = (stats and stats.home_corners is not None) or (live_state and getattr(live_state, "home_corners", None) is not None)
        has_possession = (stats and stats.home_possession is not None) or (live_state and getattr(live_state, "home_possession", None) is not None)
        has_cards = (stats and stats.home_yellow_cards is not None) or (live_state and getattr(live_state, "home_yellow_cards", None) is not None)

        if has_shots and has_corners and has_possession and has_cards:
            stat_score = 20
            stat_status = "FULL"
            explanations.append("Comprehensive statistical coverage (shots, corners, possession, cards).")
        elif has_shots or has_corners:
            stat_score = 12
            stat_status = "PARTIAL"
            explanations.append("Partial statistical coverage available.")
        else:
            stat_score = 4 if fixture.status == "SCHEDULED" else 0
            stat_status = "MINIMAL" if fixture.status == "SCHEDULED" else "UNAVAILABLE"
            explanations.append("In-depth match statistics unavailable from provider.")

        factors["statistical_coverage"] = {"score": stat_score, "max": 20, "status": stat_status}
        score += stat_score

        # 5. Event Completeness & Historical Reliability (max 15)
        event_score = 15
        hist = db.query(HistoricalResult).filter(HistoricalResult.fixture_id == fixture_id).first()
        if fixture.status in ["FINISHED", "FT"] and not hist:
            event_score = 5
            explanations.append("Historical result record pending.")
        elif fixture.status in ["FINISHED", "FT"] and hist:
            explanations.append("Historical final score validated.")
        else:
            event_score = 15

        factors["event_completeness"] = {"score": event_score, "max": 15, "status": "COMPLETE" if event_score >= 12 else "PARTIAL"}
        score += event_score

        # Map to final score label
        score = min(100, max(0, score))
        if score >= 85:
            label = "EXCELLENT"
        elif score >= 70:
            label = "GOOD"
        elif score >= 50:
            label = "MODERATE"
        elif score > 0:
            label = "POOR"
        else:
            label = "UNAVAILABLE"

        return {
            "fixture_id": fixture_id,
            "score": score,
            "label": label,
            "factors": factors,
            "explanation": " ".join(explanations)
        }

