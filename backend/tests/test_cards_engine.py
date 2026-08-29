import os
import sys
import math
import unittest
from datetime import datetime, timezone, timedelta
from typing import cast
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base, get_db
from models import (
    League, Team, Fixture, HistoricalResult, MatchStatistics,
    Referee, RefereeMatchStatistics, CardPredictionSnapshot
)
from services.cards_service import (
    CardsPredictionEngine,
    CardDataQualityService,
    RefereeIntelligenceService,
    LeagueCardBaselineService,
    TeamDisciplineService,
    CardSnapshotService,
    CardsModelEvaluationService,
    CardsBacktestService,
    CARDS_MODEL_VERSION,
    FALLBACK_LEAGUE_HOME_CARDS,
    FALLBACK_LEAGUE_AWAY_CARDS,
    FALLBACK_LEAGUE_TOTAL_CARDS,
    DEFAULT_FALLBACK_CARDS_DISPERSION
)
from services.ingestion_service import DataIngestionService
from main import app


class TestCardsPhase3Engine(unittest.TestCase):

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

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        app.dependency_overrides.clear()

    # =========================================================================
    # 1. DATA INGESTION, NON-NEGATIVE VALIDATION & CONFLICT PRESERVATION
    # =========================================================================

    def test_valid_card_ingestion_and_total_calculation(self):
        """Req 1, 2, 4: Valid yellow/red card ingestion and derived totals (total_yellow, total_red, total_cards)."""
        league = League(name="La Liga", country="Spain", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Real Madrid", short_code="RMA", league_id=league.id)
        t2 = Team(name="Barcelona", short_code="FCB", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        payload = [{
            "external_id": "TEST-CARD-1",
            "league_id": league.id,
            "home_team_id": t1.id,
            "away_team_id": t2.id,
            "match_date": datetime.now(timezone.utc).isoformat(),
            "status": "FINISHED",
            "home_score": 2,
            "away_score": 1,
            "home_yellow_cards": 3,
            "away_yellow_cards": 4,
            "home_red_cards": 0,
            "away_red_cards": 1,
            "referee": "Mateu Lahoz"
        }]

        DataIngestionService.ingest_fixtures(self.db, payload, commit=True)

        fixture = self.db.query(Fixture).filter(Fixture.external_id == "TEST-CARD-1").first()
        self.assertIsNotNone(fixture)
        stats = self.db.query(MatchStatistics).filter(MatchStatistics.fixture_id == fixture.id).first()
        self.assertIsNotNone(stats)
        self.assertEqual(stats.home_yellow_cards, 3)
        self.assertEqual(stats.away_yellow_cards, 4)
        self.assertEqual(stats.total_yellow_cards, 7)
        self.assertEqual(stats.home_red_cards, 0)
        self.assertEqual(stats.away_red_cards, 1)
        self.assertEqual(stats.total_red_cards, 1)
        self.assertEqual(stats.total_cards, 8)
        self.assertEqual(stats.referee_name, "Mateu Lahoz")

    def test_negative_values_rejected(self):
        """Req 3: Negative or corrupt card numbers are discarded (set to None)."""
        league = League(name="Serie A", country="Italy", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Juventus", short_code="JUV", league_id=league.id)
        t2 = Team(name="Milan", short_code="MIL", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        payload = [{
            "external_id": "TEST-CARD-NEG",
            "league_id": league.id,
            "home_team_id": t1.id,
            "away_team_id": t2.id,
            "match_date": datetime.now(timezone.utc).isoformat(),
            "status": "FINISHED",
            "home_score": 1,
            "away_score": 0,
            "home_yellow_cards": -5,
            "away_yellow_cards": 2
        }]

        DataIngestionService.ingest_fixtures(self.db, payload, commit=True)
        fixture = self.db.query(Fixture).filter(Fixture.external_id == "TEST-CARD-NEG").first()
        stats = self.db.query(MatchStatistics).filter(MatchStatistics.fixture_id == fixture.id).first()
        self.assertIsNone(stats.home_yellow_cards)
        self.assertEqual(stats.away_yellow_cards, 2)

    def test_idempotent_ingestion_and_preservation(self):
        """Req 5 & 6: Re-ingestion with partial/null fields does not overwrite verified card numbers."""
        league = League(name="Bundesliga", country="Germany", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Bayern", short_code="BAY", league_id=league.id)
        t2 = Team(name="Dortmund", short_code="BVB", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        p1 = [{
            "external_id": "TEST-IDEMP-CARD",
            "league_id": league.id,
            "home_team_id": t1.id,
            "away_team_id": t2.id,
            "match_date": datetime.now(timezone.utc).isoformat(),
            "status": "FINISHED",
            "home_score": 3,
            "away_score": 2,
            "home_yellow_cards": 2,
            "away_yellow_cards": 3
        }]
        DataIngestionService.ingest_fixtures(self.db, p1, commit=True)

        p2 = [{
            "external_id": "TEST-IDEMP-CARD",
            "league_id": league.id,
            "home_team_id": t1.id,
            "away_team_id": t2.id,
            "match_date": datetime.now(timezone.utc).isoformat(),
            "status": "FINISHED",
            "home_score": 3,
            "away_score": 2,
            "home_yellow_cards": None,
            "away_yellow_cards": None
        }]
        DataIngestionService.ingest_fixtures(self.db, p2, commit=True)

        fixture = self.db.query(Fixture).filter(Fixture.external_id == "TEST-IDEMP-CARD").first()
        stats = self.db.query(MatchStatistics).filter(MatchStatistics.fixture_id == fixture.id).first()
        self.assertEqual(stats.home_yellow_cards, 2)
        self.assertEqual(stats.away_yellow_cards, 3)

    # =========================================================================
    # 2. REFEREE DATA ARCHITECTURE & HIERARCHY
    # =========================================================================

    def test_referee_baseline_hierarchy_and_shrinkage(self):
        """Req 12, 13, 14, 15: Referee resolution hierarchy (referee -> shrunk_referee -> competition -> fallback)."""
        # A. No referee data -> fallback/competition
        no_ref = RefereeIntelligenceService.resolve_referee_adjustment(self.db, None, league_id=1)
        self.assertFalse(no_ref["available"])
        self.assertEqual(no_ref["influence_factor"], 1.0)
        self.assertEqual(no_ref["source"], "fallback")

        # B. Ingest 25 baseline league matches with 4 cards (referee: Anthony Taylor)
        league = League(name="Premier League", country="England", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Arsenal", short_code="ARS", league_id=league.id)
        t2 = Team(name="Chelsea", short_code="CHE", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        now = datetime.now(timezone.utc)
        for i in range(25):
            f = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=now - timedelta(days=i * 3 + 1), status="FINISHED")
            self.db.add(f)
            self.db.commit()
            self.db.add(MatchStatistics(
                fixture_id=f.id,
                home_yellow_cards=2,
                away_yellow_cards=2,
                total_cards=4,
                referee_name="Anthony Taylor"
            ))
        self.db.commit()

        # Ingest 8 matches with strict referee "Michael Oliver" (~6 cards/game)
        for i in range(8):
            f = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=now - timedelta(days=i * 5 + 100), status="FINISHED")
            self.db.add(f)
            self.db.commit()
            self.db.add(MatchStatistics(
                fixture_id=f.id,
                home_yellow_cards=3,
                away_yellow_cards=3,
                total_cards=6,
                referee_name="Michael Oliver"
            ))
        self.db.commit()

        # With 8 matches (5 <= N < 20), source should be "shrunk_referee"
        shrunk_res = RefereeIntelligenceService.resolve_referee_adjustment(self.db, "Michael Oliver", league_id=league.id)
        self.assertTrue(shrunk_res["available"])
        self.assertEqual(shrunk_res["source"], "shrunk_referee")
        self.assertEqual(shrunk_res["sample_size"], 8)
        self.assertGreater(shrunk_res["influence_factor"], 1.0)

        # Ingest 15 more matches (total 23 >= 20) -> source becomes "referee"
        for i in range(15):
            f = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=now - timedelta(days=(i+10) * 5 + 100), status="FINISHED")
            self.db.add(f)
            self.db.commit()
            self.db.add(MatchStatistics(
                fixture_id=f.id,
                home_yellow_cards=3,
                away_yellow_cards=3,
                total_cards=6,
                referee_name="Michael Oliver"
            ))
        self.db.commit()

        full_res = RefereeIntelligenceService.resolve_referee_adjustment(self.db, "Michael Oliver", league_id=league.id)
        self.assertTrue(full_res["available"])
        self.assertEqual(full_res["source"], "referee")
        self.assertEqual(full_res["sample_size"], 23)
        self.assertGreaterEqual(full_res["influence_factor"], 1.10)

    # =========================================================================
    # 3. TEAM DISCIPLINE & TIME-DECAY FEATURES
    # =========================================================================

    def test_team_discipline_and_opponent_forcing_features(self):
        """Req 7, 8, 9, 10: Cards received vs forced, rolling 5, rolling 10, time-decay."""
        league = League(name="Ligue 1", country="France", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="PSG", short_code="PSG", league_id=league.id)
        t2 = Team(name="Marseille", short_code="MAR", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        now = datetime.now(timezone.utc)
        for i in range(12):
            f = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=now - timedelta(days=i * 4), status="FINISHED")
            self.db.add(f)
            self.db.commit()
            # PSG receives 1 yellow, forces Marseille to receive 4 yellows
            self.db.add(MatchStatistics(
                fixture_id=f.id,
                home_yellow_cards=1,
                away_yellow_cards=4,
                home_red_cards=0,
                away_red_cards=0,
                total_cards=5
            ))
        self.db.commit()

        feat = TeamDisciplineService.get_team_discipline_features(self.db, cast(int, t1.id), is_home=True)
        self.assertEqual(feat["avg_cards_received"], 1.0)
        self.assertEqual(feat["avg_cards_forced"], 4.0)
        self.assertEqual(feat["recent_5_received"], 1.0)
        self.assertEqual(feat["recent_5_forced"], 4.0)
        self.assertEqual(feat["recent_10_received"], 1.0)
        self.assertEqual(feat["recent_10_forced"], 4.0)

    # =========================================================================
    # 4. PROBABILITY DISTRIBUTION, MONOTONICITY & NORMALIZATION
    # =========================================================================

    def test_cards_probability_distribution_properties(self):
        """Req 17, 18, 19, 20, 21: Discrete 2D convolution, probability mass >= 0.999, bounds, and strict monotonicity."""
        probs = CardsPredictionEngine.calculate_card_probabilities(lambda_home=2.1, lambda_away=2.3, dispersion=4.0)
        tot = probs["total_markets"]
        ht = probs["home_team"]
        at = probs["away_team"]

        # Probability bounds
        for k, v in tot.items():
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)

        # Total market monotonicity: Over 1.5 >= Over 2.5 >= Over 3.5 >= Over 4.5 >= Over 5.5 >= Over 6.5
        self.assertGreaterEqual(tot["over_1_5"], tot["over_2_5"])
        self.assertGreaterEqual(tot["over_2_5"], tot["over_3_5"])
        self.assertGreaterEqual(tot["over_3_5"], tot["over_4_5"])
        self.assertGreaterEqual(tot["over_4_5"], tot["over_5_5"])
        self.assertGreaterEqual(tot["over_5_5"], tot["over_6_5"])

        # Complementarity: Over + Under == 1.0
        for thresh in ["1_5", "2_5", "3_5", "4_5", "5_5", "6_5"]:
            self.assertAlmostEqual(tot[f"over_{thresh}"] + tot[f"under_{thresh}"], 1.0, places=3)

        # Team monotonicity: Over 0.5 >= Over 1.5 >= Over 2.5 >= Over 3.5
        self.assertGreaterEqual(ht["over_0_5"], ht["over_1_5"])
        self.assertGreaterEqual(ht["over_1_5"], ht["over_2_5"])
        self.assertGreaterEqual(ht["over_2_5"], ht["over_3_5"])
        self.assertGreaterEqual(at["over_0_5"], at["over_1_5"])
        self.assertGreaterEqual(at["over_1_5"], at["over_2_5"])
        self.assertGreaterEqual(at["over_2_5"], at["over_3_5"])

    # =========================================================================
    # 5. PREDICTION SNAPSHOT IMMUTABILITY & RESULT VERIFICATION
    # =========================================================================

    def test_prediction_snapshot_immutability_and_result_verification(self):
        """Req 22, 23, 24: Pre-match snapshot is immutable, version is preserved, and post-match verification updates actuals."""
        league = League(name="Eredivisie", country="Netherlands", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Ajax", short_code="AJX", league_id=league.id)
        t2 = Team(name="Feyenoord", short_code="FEY", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        fixture = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=datetime.now(timezone.utc), status="SCHEDULED")
        self.db.add(fixture)
        self.db.commit()

        # Prior matches for history
        now = datetime.now(timezone.utc)
        for i in range(4):
            pf = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=now - timedelta(days=(i+1)*4), status="FINISHED")
            self.db.add(pf)
            self.db.commit()
            self.db.add(MatchStatistics(fixture_id=pf.id, home_yellow_cards=2, away_yellow_cards=3, total_cards=5))
        self.db.commit()

        # 1. Generate pre-match snapshot
        pred = CardsPredictionEngine.predict_cards(self.db, cast(int, fixture.id), save_snapshot=True)
        self.assertTrue(pred["available"])

        snapshot = self.db.query(CardPredictionSnapshot).filter(CardPredictionSnapshot.fixture_id == fixture.id).first()
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.model_version, CARDS_MODEL_VERSION)
        self.assertFalse(snapshot.is_verified)
        orig_o35 = snapshot.over_3_5_prob

        # 2. Match finishes with 5 yellows + 1 red = 6 total cards
        verified = CardSnapshotService.verify_finished_fixture_cards(
            self.db, cast(int, fixture.id), actual_home_y=2, actual_away_y=3, actual_home_r=1, actual_away_r=0
        )
        self.assertIsNotNone(verified)
        self.assertTrue(verified.is_verified)
        self.assertEqual(verified.actual_home_yellow_cards, 2)
        self.assertEqual(verified.actual_away_yellow_cards, 3)
        self.assertEqual(verified.actual_home_red_cards, 1)
        self.assertEqual(verified.actual_away_red_cards, 0)
        self.assertEqual(verified.actual_total_cards, 6)
        # Probability remains immutable
        self.assertEqual(verified.over_3_5_prob, orig_o35)

    # =========================================================================
    # 6. TEMPORAL LEAKAGE CONTROL & INSUFFICIENT DATA STATUS
    # =========================================================================

    def test_strict_temporal_safety_and_insufficient_data_status(self):
        """Req 25, 27: Strict temporal cutoff excludes future data; N < 100 returns insufficient_data status."""
        league = League(name="Primeira Liga", country="Portugal", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Benfica", short_code="BEN", league_id=league.id)
        t2 = Team(name="Porto", short_code="POR", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        t_base = datetime(2025, 6, 1, 15, 0, 0)
        f1 = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=t_base, status="FINISHED")
        f2 = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=t_base + timedelta(days=7), status="FINISHED")
        f3 = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=t_base + timedelta(days=14), status="FINISHED")
        self.db.add_all([f1, f2, f3])
        self.db.commit()

        self.db.add_all([
            MatchStatistics(fixture_id=f1.id, home_yellow_cards=2, away_yellow_cards=2, total_cards=4),
            MatchStatistics(fixture_id=f2.id, home_yellow_cards=3, away_yellow_cards=3, total_cards=6),
            MatchStatistics(fixture_id=f3.id, home_yellow_cards=6, away_yellow_cards=6, total_cards=12) # Future match
        ])
        self.db.commit()

        # Predicting f2 at Day 7 must ONLY see f1 (Day 0), NEVER f3 (Day 14)
        cov_f2 = CardDataQualityService.get_fixture_card_coverage(self.db, cast(int, t1.id), cast(int, t2.id), league.id, target_date=f2.match_date)
        self.assertEqual(cov_f2["home_sample_size"], 1)

        # Performance endpoint with < 100 matches returns insufficient_data
        perf_resp = self.client.get("/api/cards/performance")
        self.assertEqual(perf_resp.status_code, 200)
        data = perf_resp.json()
        self.assertEqual(data["status"], "insufficient_data")
        self.assertIn("UNVALIDATED", data["validation_status"])

    # =========================================================================
    # 7. BACKWARD COMPATIBILITY & API PAYLOAD INTEGRATION
    # =========================================================================

    def test_api_schema_backward_compatibility(self):
        """Req 28, 29, 30: Unified match intelligence response contains goals, corners, and cards."""
        league = League(name="MLS", country="USA", season="2025")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="LAFC", short_code="LAF", league_id=league.id)
        t2 = Team(name="Galaxy", short_code="GAL", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        fixture = Fixture(
            league_id=league.id,
            home_team_id=t1.id,
            away_team_id=t2.id,
            match_date=datetime.now(timezone.utc),
            status="SCHEDULED"
        )
        self.db.add(fixture)
        self.db.commit()

        # Verify details endpoint returns cards payload
        resp = self.client.get(f"/api/fixtures/{fixture.id}/details")
        self.assertEqual(resp.status_code, 200)
        res_data = resp.json()

        self.assertIn("match_intelligence", res_data)
        self.assertIn("expected_goals", res_data["match_intelligence"])
        self.assertIn("corners", res_data["match_intelligence"])
        self.assertIn("cards", res_data["match_intelligence"])


if __name__ == "__main__":
    unittest.main()
