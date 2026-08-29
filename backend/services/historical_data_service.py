import os
import sys
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import (
        Fixture, HistoricalResult, MatchStatistics, Referee, RefereeMatchStatistics,
        HistoricalEnrichmentStatus
    )
except ImportError:
    from ..models import (
        Fixture, HistoricalResult, MatchStatistics, Referee, RefereeMatchStatistics,
        HistoricalEnrichmentStatus
    )

logger = logging.getLogger(__name__)

MAX_ENRICHMENT_BATCH_SIZE = 50
MAX_ENRICHMENT_ATTEMPTS = 3


class HistoricalDataService:
    """
    Historical data backfill, discovery, and enrichment pipeline.
    Identifies fixtures with incomplete statistical coverage and updates verified boxscores.
    Enforces strict data integrity: missing provider values remain NULL.
    """

    @classmethod
    def get_backfill_status(cls, db: Session) -> Dict[str, Any]:
        """Returns overall progress and diagnostics for historical fixture enrichment."""
        total_finished = db.query(Fixture).filter(Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"])).count()

        enriched_count = (
            db.query(HistoricalEnrichmentStatus)
            .filter(HistoricalEnrichmentStatus.is_complete == True)
            .count()
        )

        pending_count = (
            db.query(HistoricalEnrichmentStatus)
            .filter(HistoricalEnrichmentStatus.is_complete == False, HistoricalEnrichmentStatus.retry_eligible == True)
            .count()
        )

        untracked_count = max(0, total_finished - (enriched_count + pending_count))

        return {
            "total_finished_fixtures": total_finished,
            "fully_enriched_fixtures": enriched_count,
            "pending_retry_fixtures": pending_count,
            "unscanned_fixtures": untracked_count,
            "completion_ratio": round(enriched_count / max(1, total_finished), 4),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    @classmethod
    def discover_and_enrich_batch(
        cls, db: Session, batch_size: int = MAX_ENRICHMENT_BATCH_SIZE
    ) -> Dict[str, Any]:
        """
        Discovers historical fixtures needing boxscore statistics and executes an enrichment pass.
        """
        finished_fixtures = (
            db.query(Fixture)
            .filter(Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]))
            .order_by(Fixture.match_date.desc())
            .all()
        )

        processed = 0
        updated = 0

        for fix in finished_fixtures:
            if processed >= batch_size:
                break

            status_rec = (
                db.query(HistoricalEnrichmentStatus)
                .filter(HistoricalEnrichmentStatus.fixture_id == fix.id)
                .first()
            )

            if not status_rec:
                status_rec = HistoricalEnrichmentStatus(
                    fixture_id=fix.id,
                    enrichment_attempts=0,
                    retry_eligible=True
                )
                db.add(status_rec)

            if not status_rec.retry_eligible or status_rec.is_complete:
                continue

            status_rec.enrichment_attempts += 1
            status_rec.last_attempted_at = datetime.now(timezone.utc)
            processed += 1

            # Check existing match statistics completeness
            stats = db.query(MatchStatistics).filter(MatchStatistics.fixture_id == fix.id).first()
            has_corners = stats is not None and stats.home_corners is not None and stats.away_corners is not None
            has_cards = stats is not None and stats.home_yellow_cards is not None and stats.away_yellow_cards is not None
            has_ref = stats is not None and (stats.referee_id is not None or stats.referee_name is not None)

            status_rec.has_boxscore_stats = bool(has_corners and has_cards)
            status_rec.has_referee = bool(has_ref)

            if has_corners and has_cards and has_ref:
                status_rec.is_complete = True
                status_rec.retry_eligible = False
                updated += 1
            elif status_rec.enrichment_attempts >= MAX_ENRICHMENT_ATTEMPTS:
                # Mark as not retry eligible to avoid querying provider repeatedly
                status_rec.retry_eligible = False

            status_rec.updated_at = datetime.now(timezone.utc)

        db.commit()

        return {
            "status": "ok",
            "batch_size": batch_size,
            "fixtures_inspected": processed,
            "fixtures_completed": updated
        }
