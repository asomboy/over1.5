import os
import sys
import unittest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base
from models import (
    Fixture, League, Team, Prediction, MatchStatistics,
    UnifiedMatchIntelligenceSnapshot
)
from services.unified_match_intelligence_service import UnifiedMatchIntelligenceService


class TestUnifiedMatchIntelligence(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.TestingSessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )
        Base.metadata.create_all(bind=self.engine)
        self.db = self.TestingSessionLocal()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_unified_match_intelligence_synthesis(self):
        """Synthesizes goals, corners, cards, match-states, consistency, and ranked signals."""
        league = League(name="Premier League", country="England")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Liverpool", league_id=league.id)
        t2 = Team(name="Everton", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=datetime.now(timezone.utc), status="SCHEDULED"
        )
        self.db.add(f)
        self.db.commit()

        # Add pre-calculated prediction
        self.db.add(Prediction(
            fixture_id=f.id,
            predicted_home_score=2.3,
            predicted_away_score=0.8,
            home_win_probability=0.68,
            draw_probability=0.20,
            away_win_probability=0.12,
            confidence_score=75
        ))
        # Add match statistics placeholder
        self.db.add(MatchStatistics(
            fixture_id=f.id,
            referee_name="Michael Oliver"
        ))
        self.db.commit()

        intel = UnifiedMatchIntelligenceService.get_unified_match_intelligence(self.db, f.id)

        # Assert structure
        self.assertEqual(intel["fixture_id"], f.id)
        self.assertEqual(intel["home_team"], "Liverpool")
        self.assertEqual(intel["away_team"], "Everton")
        self.assertIn("markets", intel)
        self.assertIn("goals", intel["markets"])
        self.assertIn("corners", intel["markets"])
        self.assertIn("cards", intel["markets"])
        self.assertIn("shots", intel["markets"]) # Phase 10 first-class prediction
        self.assertIn("expected_total_shots", intel["markets"]["shots"])
        self.assertIn("shots_on_target", intel["markets"])
        self.assertIn("expected_total_sot", intel["markets"]["shots_on_target"])

        # Assert match state tags & confidence
        self.assertIn("PRE_MATCH", intel["match_state_classification"])
        self.assertGreaterEqual(intel["unified_confidence"], 0.15)
        self.assertLessEqual(intel["unified_confidence"], 0.95)

        # Assert consistency
        self.assertIn("consistency_score", intel["cross_market_consistency"])
        self.assertGreater(intel["cross_market_consistency"]["consistency_score"], 0.0)

    def test_cross_market_consistency_evaluator(self):
        """Detects contradictions between high goal expectations and low corners or divergence in BTTS."""
        # Aligned
        aligned = UnifiedMatchIntelligenceService._evaluate_cross_market_consistency(
            goals={"total_xg": 2.6, "probabilities": {"btts": 0.52, "over_2_5": 0.54}},
            corners={"total_expected_corners": 9.5},
            cards={"total_expected_cards": 4.0, "referee_strictness_index": 1.0},
            live=None
        )
        self.assertEqual(aligned["status"], "ALIGNED")
        self.assertEqual(aligned["consistency_score"], 1.0)

        # Contradiction: extreme high goals (3.8) with very low corners (5.0)
        contradiction = UnifiedMatchIntelligenceService._evaluate_cross_market_consistency(
            goals={"total_xg": 3.8, "probabilities": {"btts": 0.75, "over_2_5": 0.85}},
            corners={"total_expected_corners": 5.0},
            cards={"total_expected_cards": 4.0, "referee_strictness_index": 1.0},
            live=None
        )
        self.assertIn("HIGH_GOAL_LOW_CORNER_DISCREPANCY", contradiction["contradiction_flags"])
        self.assertLess(contradiction["consistency_score"], 1.0)

    def test_match_state_classification(self):
        """Classifies dominance, tempo, tension, and live state."""
        league = League(name="La Liga", country="Spain")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Real Madrid", league_id=league.id)
        t2 = Team(name="Getafe", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=datetime.now(timezone.utc), status="LIVE"
        )
        self.db.add(f)
        self.db.commit()

        tags = UnifiedMatchIntelligenceService._classify_match_state(
            fixture=f,
            goals={"home_xg": 2.5, "away_xg": 0.6, "total_xg": 3.1},
            corners={"total_expected_corners": 11.2},
            cards={"total_expected_cards": 5.2, "referee_strictness_index": 1.25},
            live={"current_minute": 75, "home_score": 2, "away_score": 1, "momentum_pressure": 70}
        )

        self.assertIn("HOME_DOMINANT", tags)
        self.assertIn("HIGH_TEMPO", tags)
        self.assertIn("DISCIPLINARY_TENSION", tags)
        self.assertIn("LATE_URGENCY", tags)
        self.assertIn("GOAL_PRESSURE", tags)

    def test_capture_unified_snapshot(self):
        """Creates and persists immutable UnifiedMatchIntelligenceSnapshot."""
        league = League(name="Serie A", country="Italy")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Milan", league_id=league.id)
        t2 = Team(name="Inter", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        f = Fixture(
            league_id=league.id, home_team_id=t1.id, away_team_id=t2.id,
            match_date=datetime.now(timezone.utc), status="SCHEDULED"
        )
        self.db.add(f)
        self.db.commit()

        snap = UnifiedMatchIntelligenceService.capture_unified_snapshot(self.db, f.id)
        self.assertIsNotNone(snap.id)
        self.assertEqual(snap.fixture_id, f.id)
        self.assertEqual(self.db.query(UnifiedMatchIntelligenceSnapshot).count(), 1)


if __name__ == "__main__":
    unittest.main()
