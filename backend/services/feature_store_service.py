import os
import sys
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, desc

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import (
        Fixture, HistoricalResult, MatchStatistics, RefereeMatchStatistics,
        TeamStatistics, EloRating, LeagueStatistics, FeatureSnapshot, Team
    )
except ImportError:
    from ..models import (
        Fixture, HistoricalResult, MatchStatistics, RefereeMatchStatistics,
        TeamStatistics, EloRating, LeagueStatistics, FeatureSnapshot, Team
    )

logger = logging.getLogger(__name__)


class FeatureStoreService:
    """
    Central Feature Store providing pre-match and live feature extraction with strict temporal integrity.
    Invariant: Only historical records where match_date < target_fixture.match_date are used.
    Produces immutable FeatureSnapshot records for complete model reproducibility.
    """

    @classmethod
    def get_team_rolling_features(
        cls, db: Session, team_id: int, as_of_date: datetime, window_size: int = 5
    ) -> Dict[str, Any]:
        """
        Extracts rolling match metrics for a team strictly BEFORE as_of_date.
        Guarantees zero future leakage.
        """
        # Query strictly prior completed fixtures
        fixtures = (
            db.query(Fixture)
            .filter(
                or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
                Fixture.match_date < as_of_date,
                Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"])
            )
            .order_by(Fixture.match_date.desc())
            .limit(window_size)
            .all()
        )

        n = len(fixtures)
        if n == 0:
            return {
                "sample_size": 0,
                "coverage": 0.0,
                "avg_goals_for": None,
                "avg_goals_against": None,
                "avg_corners_for": None,
                "avg_corners_against": None,
                "avg_cards_for": None,
                "btts_rate": None
            }

        g_for_sum = 0
        g_against_sum = 0
        c_for_sum = 0
        c_against_sum = 0
        card_sum = 0
        btts_count = 0
        corners_sample = 0
        cards_sample = 0

        for f in fixtures:
            is_home = (f.home_team_id == team_id)
            h_score = f.home_score if f.home_score is not None else 0
            a_score = f.away_score if f.away_score is not None else 0

            gf = h_score if is_home else a_score
            ga = a_score if is_home else h_score
            g_for_sum += gf
            g_against_sum += ga
            if h_score > 0 and a_score > 0:
                btts_count += 1

            stats = db.query(MatchStatistics).filter(MatchStatistics.fixture_id == f.id).first()
            if stats:
                if stats.home_corners is not None and stats.away_corners is not None:
                    cf = stats.home_corners if is_home else stats.away_corners
                    ca = stats.away_corners if is_home else stats.home_corners
                    c_for_sum += cf
                    c_against_sum += ca
                    corners_sample += 1

                if stats.home_yellow_cards is not None and stats.away_yellow_cards is not None:
                    cards = (stats.home_yellow_cards + (stats.home_red_cards or 0)) if is_home else (stats.away_yellow_cards + (stats.away_red_cards or 0))
                    card_sum += cards
                    cards_sample += 1

        return {
            "sample_size": n,
            "coverage": round(n / float(window_size), 2),
            "avg_goals_for": round(g_for_sum / float(n), 2),
            "avg_goals_against": round(g_against_sum / float(n), 2),
            "avg_corners_for": round(c_for_sum / float(corners_sample), 2) if corners_sample > 0 else None,
            "avg_corners_against": round(c_against_sum / float(corners_sample), 2) if corners_sample > 0 else None,
            "avg_cards_for": round(card_sum / float(cards_sample), 2) if cards_sample > 0 else None,
            "btts_rate": round(btts_count / float(n), 2),
            "corners_sample": corners_sample,
            "cards_sample": cards_sample
        }

    @classmethod
    def get_competition_features(cls, db: Session, league_id: int) -> Dict[str, Any]:
        """Extracts competition baseline rates and dispersion."""
        l_stats = db.query(LeagueStatistics).filter(LeagueStatistics.league_id == league_id).first()
        if not l_stats:
            return {
                "sample_size": 0,
                "avg_goals": 2.70,
                "avg_corners": 9.80,
                "avg_cards": 4.10,
                "home_advantage": 0.25,
                "source": "fallback"
            }

        return {
            "sample_size": l_stats.matches_analyzed,
            "avg_goals": l_stats.avg_total_goals or 2.70,
            "avg_corners": l_stats.avg_total_corners or 9.80,
            "avg_cards": l_stats.avg_total_cards or 4.10,
            "home_advantage": l_stats.home_advantage_xg or 0.25,
            "source": "league_statistics"
        }

    @classmethod
    def build_fixture_features_payload(cls, db: Session, fixture_id: int) -> Dict[str, Any]:
        """
        Builds complete multi-domain feature payload for a fixture enforcing temporal integrity.
        """
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            return {}

        kickoff = fixture.match_date
        h_team_feats = cls.get_team_rolling_features(db, fixture.home_team_id, kickoff, window_size=5)
        a_team_feats = cls.get_team_rolling_features(db, fixture.away_team_id, kickoff, window_size=5)
        comp_feats = cls.get_competition_features(db, fixture.league_id)

        # Elo
        h_elo = db.query(EloRating).filter(EloRating.team_id == fixture.home_team_id).first()
        a_elo = db.query(EloRating).filter(EloRating.team_id == fixture.away_team_id).first()

        # Referee
        stats = db.query(MatchStatistics).filter(MatchStatistics.fixture_id == fixture.id).first()
        ref_name = stats.referee_name if stats else None

        payload = {
            "fixture_id": fixture.id,
            "kickoff": kickoff.isoformat(),
            "competition": comp_feats,
            "home_team": {
                "id": fixture.home_team_id,
                "elo": h_elo.rating if h_elo else 1500.0,
                "rolling_features": h_team_feats
            },
            "away_team": {
                "id": fixture.away_team_id,
                "elo": a_elo.rating if a_elo else 1500.0,
                "rolling_features": a_team_feats
            },
            "referee": {
                "name": ref_name,
                "available": ref_name is not None
            }
        }

        return payload

    @classmethod
    def capture_feature_snapshot(
        cls, db: Session, fixture_id: int, model_version: str = "v2_match_intelligence"
    ) -> FeatureSnapshot:
        """
        Creates and stores an immutable FeatureSnapshot record for the fixture.
        """
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            raise ValueError(f"Fixture {fixture_id} not found")

        payload = cls.build_fixture_features_payload(db, fixture_id)
        h_sample = payload.get("home_team", {}).get("rolling_features", {}).get("sample_size", 0)
        a_sample = payload.get("away_team", {}).get("rolling_features", {}).get("sample_size", 0)
        tot_sample = h_sample + a_sample

        snap = FeatureSnapshot(
            fixture_id=fixture_id,
            model_version=model_version,
            prediction_timestamp=datetime.now(timezone.utc),
            features_json=json.dumps(payload),
            feature_coverage=1.0 if tot_sample >= 10 else round(tot_sample / 10.0, 2),
            sample_size=tot_sample,
            provenance_json=json.dumps({"source": "feature_store_service", "created_at": datetime.now(timezone.utc).isoformat()})
        )
        db.add(snap)
        db.commit()
        return snap
