import os
import sys
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import (
        Fixture, HistoricalResult, MatchStatistics, FeatureSnapshot,
        Prediction, ModelEvaluation
    )
    from services.feature_store_service import FeatureStoreService
except ImportError:
    from ..models import (
        Fixture, HistoricalResult, MatchStatistics, FeatureSnapshot,
        Prediction, ModelEvaluation
    )
    from .feature_store_service import FeatureStoreService

logger = logging.getLogger(__name__)


class ValidationDatasetService:
    """
    Constructs reproducible chronological validation datasets with strict zero future leakage.
    Ensures that for every evaluated fixture, historical features only consume matches prior to kickoff.
    """

    @classmethod
    def build_chronological_dataset(
        cls,
        db: Session,
        competition_id: Optional[int] = None,
        min_date: Optional[datetime] = None,
        max_date: Optional[datetime] = None,
        limit: int = 500
    ) -> List[Dict[str, Any]]:
        """
        Builds chronologically ordered evaluation dataset of completed fixtures.
        """
        query = (
            db.query(Fixture)
            .filter(Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]))
            .order_by(Fixture.match_date.asc())
        )

        if competition_id:
            query = query.filter(Fixture.league_id == competition_id)

        if min_date:
            query = query.filter(Fixture.match_date >= min_date.replace(tzinfo=None))
        if max_date:
            query = query.filter(Fixture.match_date <= max_date.replace(tzinfo=None))

        fixtures = query.limit(limit).all()

        dataset = []
        for fix in fixtures:
            # 1. Check verified outcome
            res = db.query(HistoricalResult).filter(HistoricalResult.fixture_id == fix.id).first()
            stats = db.query(MatchStatistics).filter(MatchStatistics.fixture_id == fix.id).first()
            
            if not res and fix.home_score is None:
                continue # Incomplete match record

            h_score = res.home_score if res else fix.home_score
            a_score = res.away_score if res else fix.away_score

            h_corners = stats.home_corners if stats else None
            a_corners = stats.away_corners if stats else None
            tot_corners = (h_corners + a_corners) if (h_corners is not None and a_corners is not None) else None

            h_cards = (stats.home_yellow_cards or 0) if stats else None
            a_cards = (stats.away_yellow_cards or 0) if stats else None
            tot_cards = (h_cards + a_cards) if (h_cards is not None and a_cards is not None) else None

            # 2. Get or capture feature snapshot with zero future leakage
            snapshot = db.query(FeatureSnapshot).filter(FeatureSnapshot.fixture_id == fix.id).first()
            if not snapshot:
                try:
                    snapshot = FeatureStoreService.capture_feature_snapshot(db, fix.id)
                except Exception as ex:
                    logger.debug(f"Feature snapshot capture skipped for fixture {fix.id}: {ex}")

            sample = {
                "fixture_id": fix.id,
                "kickoff_time": fix.match_date.isoformat() if fix.match_date else None,
                "competition_id": fix.league_id,
                "competition": fix.league.name if fix.league else "Unknown",
                "home_team": fix.home_team.name if fix.home_team else "Home",
                "away_team": fix.away_team.name if fix.away_team else "Away",
                "feature_snapshot_id": snapshot.id if snapshot else None,
                "actual_outcomes": {
                    "home_score": h_score,
                    "away_score": a_score,
                    "total_goals": (h_score + a_score) if (h_score is not None and a_score is not None) else None,
                    "home_corners": h_corners,
                    "away_corners": a_corners,
                    "total_corners": tot_corners,
                    "home_yellow_cards": h_cards,
                    "away_yellow_cards": a_cards,
                    "total_cards": tot_cards,
                    "referee_name": stats.referee_name if stats else None
                },
                "data_quality": "OBSERVED"
            }
            dataset.append(sample)

        return dataset
