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
from models import League, Team, Fixture, HistoricalResult, MatchStatistics, CornerPredictionSnapshot
from services.corners_service import (
    CornersPredictionEngine,
    CornerDataQualityService,
    CornerSnapshotService,
    CornersModelEvaluationService,
    CornersBacktestService,
    CORNER_MODEL_VERSION,
    FALLBACK_LEAGUE_HOME_CORNERS,
    FALLBACK_LEAGUE_AWAY_CORNERS,
    DEFAULT_FALLBACK_DISPERSION
)
from services.ingestion_service import DataIngestionService
from main import app


class TestCornersPhase21Hardening(unittest.TestCase):

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
    # 1. TRUE CORNER DATA COVERAGE & SEPARATION FROM SAMPLE SIZE
    # =========================================================================

    def test_true_coverage_and_sample_size_calculation(self):
        """Rule 1 & 2: Coverage = verified / eligible (not pseudo-formula); sample size separated."""
        league = League(name="Premier League", country="England", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        team_a = Team(name="Arsenal", short_code="ARS", league_id=league.id)
        team_b = Team(name="Chelsea", short_code="CHE", league_id=league.id)
        self.db.add_all([team_a, team_b])
        self.db.commit()

        # Create 20 completed matches: 15 with corner data, 5 with missing corner data
        now = datetime.now(timezone.utc)
        for i in range(20):
            f = Fixture(
                league_id=league.id,
                home_team_id=team_a.id,
                away_team_id=team_b.id,
                match_date=now - timedelta(days=i * 7),
                status="FINISHED",
                home_score=2,
                away_score=1
            )
            self.db.add(f)
            self.db.commit()

            if i < 15:
                stats = MatchStatistics(
                    fixture_id=f.id,
                    home_corners=6,
                    away_corners=4,
                    total_corners=10,
                    data_source="observed",
                    data_quality="verified"
                )
                self.db.add(stats)
        self.db.commit()

        cov = CornerDataQualityService.get_fixture_corner_coverage(self.db, cast(int, team_a.id), cast(int, team_b.id), league.id)

        # 20 matches per team matchup -> total eligible = 40 (20 for Arsenal + 20 for Chelsea)
        # verified corner matches = 30 (15 for Arsenal + 15 for Chelsea)
        self.assertEqual(cov["eligible_match_count"], 40)
        self.assertEqual(cov["observed_corner_match_count"], 30)
        self.assertEqual(cov["corner_sample_size"], 30)
        self.assertEqual(cov["missing_corner_match_count"], 10)
        self.assertAlmostEqual(cov["corner_data_coverage"], 30 / 40.0, places=4)
        self.assertEqual(cov["corner_data_coverage"], 0.75)

    # =========================================================================
    # 2. HIERARCHICAL LEAGUE BASELINE RESOLUTION
    # =========================================================================

    def test_hierarchical_league_baseline_resolution(self):
        """Rule 3: Priority: Competition (>=15) -> Shrunk (5-14) -> Global (>=25) -> Fallback (5.4, 4.6)."""
        # A. Fallback
        h, a, tot, source, n = CornersPredictionEngine.resolve_league_baseline(self.db, league_id=999)
        self.assertEqual(source, "fallback")
        self.assertEqual(h, FALLBACK_LEAGUE_HOME_CORNERS)
        self.assertEqual(a, FALLBACK_LEAGUE_AWAY_CORNERS)

        # B. Ingest 18 matches in League A with specific averages (e.g. 6.5 home, 3.5 away)
        league_a = League(name="High Corner League", country="Test", season="2025/2026")
        self.db.add(league_a)
        self.db.commit()

        t1 = Team(name="T1", short_code="T1", league_id=league_a.id)
        t2 = Team(name="T2", short_code="T2", league_id=league_a.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        now = datetime.now(timezone.utc)
        for i in range(18):
            f = Fixture(league_id=league_a.id, home_team_id=t1.id, away_team_id=t2.id, match_date=now - timedelta(days=i * 2), status="FINISHED")
            self.db.add(f)
            self.db.commit()
            self.db.add(MatchStatistics(fixture_id=f.id, home_corners=7, away_corners=3, total_corners=10))
        self.db.commit()

        h_comp, a_comp, tot_comp, src_comp, n_comp = CornersPredictionEngine.resolve_league_baseline(self.db, league_id=league_a.id)
        self.assertEqual(src_comp, "competition")
        self.assertEqual(n_comp, 18)
        self.assertEqual(h_comp, 7.0)
        self.assertEqual(a_comp, 3.0)

    # =========================================================================
    # 3. INGESTION VALIDATION & NON-NEGATIVE CONSTRAINTS
    # =========================================================================

    def test_ingestion_validates_non_negative_and_preserves_verified_data(self):
        """Rule 4: Ingestion validates home_corners >= 0, away_corners >= 0, total = home + away, and is idempotent."""
        league = League(name="Ingestion League", country="Test", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Alpha", short_code="ALP", league_id=league.id)
        t2 = Team(name="Beta", short_code="BET", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        payload = [{
            "external_id": "TEST-FIX-1",
            "league_id": league.id,
            "home_team_id": t1.id,
            "away_team_id": t2.id,
            "match_date": datetime.now(timezone.utc).isoformat(),
            "status": "FINISHED",
            "home_score": 2,
            "away_score": 1,
            "home_corners": 5,
            "away_corners": 4
        }]

        DataIngestionService.ingest_fixtures(self.db, payload, commit=True)

        fixture = self.db.query(Fixture).filter(Fixture.external_id == "TEST-FIX-1").first()
        self.assertIsNotNone(fixture)
        stats = self.db.query(MatchStatistics).filter(MatchStatistics.fixture_id == fixture.id).first()
        self.assertIsNotNone(stats)
        self.assertEqual(stats.home_corners, 5)
        self.assertEqual(stats.away_corners, 4)
        self.assertEqual(stats.total_corners, 9)

        # Re-ingest with null corners — should NOT overwrite verified numbers with null
        update_payload = [{
            "external_id": "TEST-FIX-1",
            "league_id": league.id,
            "home_team_id": t1.id,
            "away_team_id": t2.id,
            "match_date": datetime.now(timezone.utc).isoformat(),
            "status": "FINISHED",
            "home_score": 2,
            "away_score": 1,
            "home_corners": None,
            "away_corners": None
        }]
        DataIngestionService.ingest_fixtures(self.db, update_payload, commit=True)
        stats_reloaded = self.db.query(MatchStatistics).filter(MatchStatistics.fixture_id == fixture.id).first()
        self.assertEqual(stats_reloaded.home_corners, 5)
        self.assertEqual(stats_reloaded.away_corners, 4)

    # =========================================================================
    # 4. DATA AUDIT ENDPOINT (/api/corners/data-quality)
    # =========================================================================

    def test_data_quality_audit_endpoint(self):
        """Rule 5: GET /api/corners/data-quality returns eligible_matches, coverage, and competition breakdown."""
        resp = self.client.get("/api/corners/data-quality")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertIn("eligible_matches", data)
        self.assertIn("matches_with_corner_data", data)
        self.assertIn("missing_corner_data", data)
        self.assertIn("coverage", data)
        self.assertIn("competitions", data)

    # =========================================================================
    # 5. STRICT TEMPORAL LEAKAGE CONTROL
    # =========================================================================

    def test_strict_temporal_leakage_prevention(self):
        """Rule 10: Features and baselines for match at time T must strictly exclude data from time >= T."""
        league = League(name="Temporal League", country="Test", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Team 1", short_code="T1", league_id=league.id)
        t2 = Team(name="Team 2", short_code="T2", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        # Match 1: Day 1 (5 corners)
        # Match 2: Day 5 (8 corners)
        # Match 3: Day 10 (15 corners - FUTURE MATCH)
        t_base = datetime(2025, 5, 1, 12, 0, 0)
        f1 = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=t_base, status="FINISHED")
        f2 = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=t_base + timedelta(days=5), status="FINISHED")
        f3 = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=t_base + timedelta(days=10), status="FINISHED")
        self.db.add_all([f1, f2, f3])
        self.db.commit()

        self.db.add_all([
            MatchStatistics(fixture_id=f1.id, home_corners=5, away_corners=3, total_corners=8),
            MatchStatistics(fixture_id=f2.id, home_corners=8, away_corners=4, total_corners=12),
            MatchStatistics(fixture_id=f3.id, home_corners=15, away_corners=10, total_corners=25)
        ])
        self.db.commit()

        # Predicting f2 at Day 5 must ONLY see f1 (Day 1), NEVER f3 (Day 10)
        cov_f2 = CornerDataQualityService.get_fixture_corner_coverage(self.db, cast(int, t1.id), cast(int, t2.id), league.id, target_date=f2.match_date)
        # Home sample size should be exactly 1 (f1)
        self.assertEqual(cov_f2["home_sample_size"], 1)

        feat_f2 = CornersPredictionEngine.get_team_corner_features(self.db, cast(int, t1.id), is_home=True, target_date=f2.match_date)
        self.assertEqual(feat_f2["avg_corners_for"], 5.0)

    # =========================================================================
    # 6. PREDICTION SNAPSHOT PRESERVATION & RESULT VERIFICATION
    # =========================================================================

    def test_prediction_snapshot_storage_and_result_verification(self):
        """Rule 11 & 12: Pre-match snapshot is preserved and verified upon match completion without altering probabilities."""
        league = League(name="Snapshot League", country="Test", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="Home Snap", short_code="HSP", league_id=league.id)
        t2 = Team(name="Away Snap", short_code="ASP", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        fixture = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=datetime.now(timezone.utc), status="SCHEDULED")
        self.db.add(fixture)
        self.db.commit()

        # Ingest 3 prior matches so prediction is available
        now = datetime.now(timezone.utc)
        for i in range(3):
            pf = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=now - timedelta(days=(i+1)*5), status="FINISHED")
            self.db.add(pf)
            self.db.commit()
            self.db.add(MatchStatistics(fixture_id=pf.id, home_corners=6, away_corners=4, total_corners=10))
        self.db.commit()

        # 1. Generate pre-match prediction snapshot
        pred = CornersPredictionEngine.predict_corners(self.db, cast(int, fixture.id), save_snapshot=True)
        self.assertTrue(pred["available"])

        snapshot = self.db.query(CornerPredictionSnapshot).filter(CornerPredictionSnapshot.fixture_id == fixture.id).first()
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.model_version, CORNER_MODEL_VERSION)
        self.assertFalse(snapshot.is_verified)
        original_o85_prob = snapshot.over_8_5_prob

        # 2. Match finishes with 11 corners
        verified_snap = CornerSnapshotService.verify_finished_fixture_corners(self.db, cast(int, fixture.id), actual_home=7, actual_away=4)
        self.assertIsNotNone(verified_snap)
        self.assertTrue(verified_snap.is_verified)
        self.assertEqual(verified_snap.actual_home_corners, 7)
        self.assertEqual(verified_snap.actual_away_corners, 4)
        self.assertEqual(verified_snap.actual_total_corners, 11)
        # Original pre-match probability must remain immutable
        self.assertEqual(verified_snap.over_8_5_prob, original_o85_prob)

    # =========================================================================
    # 7. MULTI-MODEL BENCHMARK & PRODUCTION VALIDATION STATUS
    # =========================================================================

    def test_insufficient_data_status_and_model_benchmark(self):
        """Rule 8, 9, 14: Distinguish insufficient data (<100) from validated and evaluate 4 model benchmark."""
        # 1. Database with small sample (<100) returns 'insufficient_data'
        bt_resp = self.client.get("/api/corners/performance")
        self.assertEqual(bt_resp.status_code, 200)
        data = bt_resp.json()
        self.assertEqual(data["status"], "insufficient_data")
        self.assertIn("UNVALIDATED", data["validation_status"])

        # 2. Test multi-model comparison evaluation directly on synthetic test sample
        league = League(name="Benchmark League", country="Test", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        t1 = Team(name="BM 1", short_code="BM1", league_id=league.id)
        t2 = Team(name="BM 2", short_code="BM2", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.commit()

        now = datetime.now(timezone.utc)
        test_pairs = []
        for i in range(12):
            f = Fixture(league_id=league.id, home_team_id=t1.id, away_team_id=t2.id, match_date=now - timedelta(days=i * 3), status="FINISHED")
            self.db.add(f)
            self.db.commit()
            stats = MatchStatistics(fixture_id=f.id, home_corners=5 + (i % 3), away_corners=4 + (i % 2), total_corners=9 + (i % 3) + (i % 2))
            self.db.add(stats)
            test_pairs.append((f, stats))
        self.db.commit()

        benchmark_results = CornersModelEvaluationService.evaluate_model_comparison(self.db, test_pairs)
        self.assertIn("model_a_league_baseline", benchmark_results)
        self.assertIn("model_b_poisson", benchmark_results)
        self.assertIn("model_c_static_nb", benchmark_results)
        self.assertIn("model_d_production_nb", benchmark_results)

        for m_name in ["model_a_league_baseline", "model_b_poisson", "model_c_static_nb", "model_d_production_nb"]:
            self.assertIn("overall_brier_score", benchmark_results[m_name])
            self.assertIn("mae_total_corners", benchmark_results[m_name])
            self.assertIn("rmse_total_corners", benchmark_results[m_name])


if __name__ == "__main__":
    unittest.main()
