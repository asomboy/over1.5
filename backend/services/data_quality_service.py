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
