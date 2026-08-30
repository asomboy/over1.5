import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base
from models import (
    Fixture, League, Team, MatchStatistics, Prediction,
    ModelEvaluation, PredictionDecisionSnapshot
)
from services.decision_intelligence_service import DecisionIntelligenceService


class TestDecisionIntelligence(unittest.TestCase):

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

        self.league = League(name="Premier League", country="England")
        self.db.add(self.league)
        self.db.commit()

        self.t1 = Team(name="Arsenal", league_id=self.league.id)
        self.t2 = Team(name="Chelsea", league_id=self.league.id)
        self.db.add_all([self.t1, self.t2])
        self.db.commit()

        self.fixture = Fixture(
            league_id=self.league.id, home_team_id=self.t1.id, away_team_id=self.t2.id,
            match_date=datetime.now(timezone.utc) + timedelta(days=1), status="SCHEDULED"
        )
        self.db.add(self.fixture)
        self.db.commit()

        # Add pre-calculated prediction
        self.db.add(Prediction(
            fixture_id=self.fixture.id,
            predicted_home_score=2.1,
            predicted_away_score=1.2,
            home_win_probability=0.55,
            draw_probability=0.25,
            away_win_probability=0.20,
            confidence_score=75
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_decision_normalization_and_bounds(self):
        """Verifies probability, confidence, decision score, and quality bounds."""
        decisions = DecisionIntelligenceService.get_fixture_decisions(self.db, self.fixture.id)
        self.assertGreater(len(decisions), 0)

        for d in decisions:
            self.assertGreaterEqual(d["probability"], 0.0)
            self.assertLessEqual(d["probability"], 1.0)

            self.assertGreaterEqual(d["confidence"], 0.0)
            self.assertLessEqual(d["confidence"], 1.0)

            self.assertGreaterEqual(d["decision_score"], 0.0)
            self.assertLessEqual(d["decision_score"], 1.0)

            self.assertIn(d["signal_status"], [
                "PRODUCTION_SIGNAL", "SHADOW_SIGNAL", "INSUFFICIENT_DATA", "DEGRADED", "NO_SIGNAL", "UNAVAILABLE"
            ])
            self.assertIn(d["risk_tier"], ["LOW", "MEDIUM", "HIGH", "NO_SIGNAL"])

    def test_sample_size_and_readiness_gating(self):
        """Verifies that low sample sizes are gated as INSUFFICIENT_DATA and N >= 300 can be PRODUCTION_SIGNAL."""
        # Without evaluations in DB, sample_size = 0 => INSUFFICIENT_DATA
        decisions = DecisionIntelligenceService.get_fixture_decisions(self.db, self.fixture.id)
        for d in decisions:
            self.assertEqual(d["signal_status"], "INSUFFICIENT_DATA")

        # Now inject 350 verified evaluation records with distinct fixtures and low ECE
        for i in range(350):
            p = 0.80
            outcome = 1.0 if (i % 5 != 0) else 0.0 # 80% win rate
            f_temp = Fixture(
                league_id=self.league.id, home_team_id=self.t1.id, away_team_id=self.t2.id,
                match_date=datetime.now(timezone.utc) - timedelta(days=i + 1), status="FINISHED"
            )
            self.db.add(f_temp)
            self.db.flush()

            self.db.add(ModelEvaluation(
                fixture_id=f_temp.id,
                prediction_type="GOALS",
                model_version="v2_match_intelligence",
                market="over_1_5",
                prediction_timestamp=datetime.now(timezone.utc),
                predicted_probability=p,
                actual_outcome=outcome,
                brier_component=(p - outcome) ** 2
            ))
        self.db.commit()

        decisions_after = DecisionIntelligenceService.get_fixture_decisions(self.db, self.fixture.id)
        o15 = next((d for d in decisions_after if d["market"] == "Over 1.5 Goals"), None)
        self.assertIsNotNone(o15)
        self.assertEqual(o15["sample_size"], 300) # Capped at query limit 300
        self.assertEqual(o15["signal_status"], "PRODUCTION_SIGNAL")
        self.assertEqual(o15["model_readiness"], "VALIDATED")

    def test_top_signals_ranking(self):
        """Verifies get_top_signals returns up to 3 highest decision_score qualifying signals."""
        # Add evaluations so signals qualify
        for i in range(150):
            f_temp = Fixture(
                league_id=self.league.id, home_team_id=self.t1.id, away_team_id=self.t2.id,
                match_date=datetime.now(timezone.utc) - timedelta(days=i + 1), status="FINISHED"
            )
            self.db.add(f_temp)
            self.db.flush()

            self.db.add(ModelEvaluation(
                fixture_id=f_temp.id,
                prediction_type="GOALS",
                model_version="v2_match_intelligence",
                market="over_1_5",
                prediction_timestamp=datetime.now(timezone.utc),
                predicted_probability=0.78,
                actual_outcome=1.0,
                brier_component=0.04
            ))
        self.db.commit()

        top_obj = DecisionIntelligenceService.get_top_signals(self.db, self.fixture.id)
        self.assertIn("status", top_obj)
        self.assertIn("signals", top_obj)
        self.assertLessEqual(len(top_obj["signals"]), 3)

        if len(top_obj["signals"]) >= 2:
            s1 = top_obj["signals"][0]["decision_score"]
            s2 = top_obj["signals"][1]["decision_score"]
            self.assertGreaterEqual(s1, s2)

    def test_immutable_decision_snapshots_and_idempotency(self):
        """Verifies snapshots are created and deduplicated within the idempotency window."""
        snaps1 = DecisionIntelligenceService.save_decision_snapshots(self.db, self.fixture.id)
        self.assertGreater(len(snaps1), 0)

        initial_count = self.db.query(PredictionDecisionSnapshot).count()
        self.assertEqual(initial_count, len(snaps1))

        # Repeated execution within 5 minutes must not duplicate records
        snaps2 = DecisionIntelligenceService.save_decision_snapshots(self.db, self.fixture.id)
        self.assertEqual(len(snaps2), 0)
        self.assertEqual(self.db.query(PredictionDecisionSnapshot).count(), initial_count)

    def test_match_decision_summary(self):
        """Verifies comprehensive match decision summary payload."""
        summary = DecisionIntelligenceService.get_match_decision_summary(self.db, self.fixture.id)
        self.assertEqual(summary["fixture_id"], self.fixture.id)
        self.assertIn("overall_confidence", summary)
        self.assertIn("production_status", summary)
        self.assertIn("top_signals", summary)
        self.assertIn("all_candidate_signals", summary)
        self.assertIn("market_summary", summary)


if __name__ == "__main__":
    unittest.main()
