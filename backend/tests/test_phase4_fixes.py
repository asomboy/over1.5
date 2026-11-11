import os
import sys
import asyncio
import sqlite3
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base, set_sqlite_pragma, SQLALCHEMY_DATABASE_URL
from models import (
    League, Team, Fixture, Prediction, SystemAlert, ProviderHealth,
    PredictionDecisionSnapshot
)
from services.accumulator_service import AccumulatorGeneratorService
from services.alert_service import AlertService
from services.provider_health_service import ProviderHealthService
from services.decision_intelligence_service import DecisionIntelligenceService
from services.ingestion_service import DataIngestionService


class TestPhase4Fixes(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = self.SessionLocal()

        self.league1 = League(name="English Premier League", country="England", season="2025/2026")
        self.league2 = League(name="Spanish LALIGA", country="Spain", season="2025/2026")
        self.db.add_all([self.league1, self.league2])
        self.db.commit()

        self.t1 = Team(name="Arsenal", league_id=self.league1.id)
        self.t2 = Team(name="Chelsea", league_id=self.league1.id)
        self.t3 = Team(name="Real Madrid", league_id=self.league2.id)
        self.t4 = Team(name="Barcelona", league_id=self.league2.id)
        self.db.add_all([self.t1, self.t2, self.t3, self.t4])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_accumulator_london_day_grouping_and_iso_z_and_null_skip(self):
        # Use a fixed "now" before BST midnight so 23:30 UTC falls on July 2 in Europe/London (BST UTC+1)
        fixed_now = datetime(2026, 7, 1, 20, 0, 0, tzinfo=timezone.utc)
        late_utc_match = datetime(2026, 7, 1, 23, 30, 0)  # 2026-07-02 00:30 BST in Europe/London

        f_valid = Fixture(
            league_id=self.league1.id, home_team_id=self.t1.id, away_team_id=self.t2.id,
            match_date=late_utc_match, status="SCHEDULED", external_id="ACC-VALID",
        )
        f_incomplete = Fixture(
            league_id=self.league2.id, home_team_id=self.t3.id, away_team_id=self.t4.id,
            match_date=late_utc_match, status="SCHEDULED", external_id="ACC-NULL",
        )
        self.db.add_all([f_valid, f_incomplete])
        self.db.commit()

        self.db.add_all([
            Prediction(
                fixture_id=f_valid.id,
                predicted_home_score=1.9, predicted_away_score=1.1, expected_goals_xg=3.0,
                home_win_probability=0.58, draw_probability=0.22, away_win_probability=0.20,
                over_1_5_probability=0.84, over_2_5_probability=0.61, btts_probability=0.55,
                confidence_score=0.78, most_likely_score="2-1",
            ),
            Prediction(
                fixture_id=f_incomplete.id,
                predicted_home_score=1.5, predicted_away_score=1.2, expected_goals_xg=2.7,
                home_win_probability=0.45, draw_probability=0.28, away_win_probability=0.27,
                over_1_5_probability=None,  # Missing primary probability must be skipped
                over_2_5_probability=0.50, btts_probability=0.50,
            ),
        ])
        self.db.commit()

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now if tz else fixed_now.replace(tzinfo=None)

        with patch("services.accumulator_service.datetime", _FixedDatetime):
            res_july1 = AccumulatorGeneratorService.generate_accumulators(self.db, match_day="2026-07-01")
            res_july2 = AccumulatorGeneratorService.generate_accumulators(self.db, match_day="2026-07-02")

        self.assertEqual(res_july1["total_candidates_analyzed"], 0)
        self.assertEqual(res_july2["total_candidates_analyzed"], 1)
        leg = res_july2["accumulators"]["safe_double"]["legs"][0]
        self.assertTrue(leg["match_date"].endswith("Z"), f"Expected UTC Z suffix, got {leg['match_date']}")
        self.assertEqual(leg["fixture_id"], f_valid.id)

    def test_models_and_services_store_naive_utc_and_fk_pragma(self):
        # Verify ORM column defaults produce naive UTC
        self.assertIsNone(self.league1.created_at.tzinfo)
        self.assertIsNone(self.t1.created_at.tzinfo)

        # Verify AlertService naive UTC writes
        alert = AlertService.emit_alert(self.db, "DATA_QUALITY", "Test alert", severity="WARNING")
        self.assertIsNotNone(alert)
        self.assertIsNone(alert.created_at.tzinfo)
        AlertService.resolve_alert(self.db, alert.alert_id)
        refreshed_alert = self.db.query(SystemAlert).filter(SystemAlert.id == alert.id).first()
        self.assertIsNone(refreshed_alert.resolved_at.tzinfo)

        # Verify ProviderHealthService naive UTC writes
        ProviderHealthService.record_provider_event(self.db, "espn", success=True, latency_ms=120.0)
        ph = self.db.query(ProviderHealth).filter(ProviderHealth.provider == "espn").first()
        self.assertIsNotNone(ph)
        self.assertIsNone(ph.timestamp.tzinfo)

        # Verify DecisionIntelligenceService idempotency & naive UTC write
        fix = Fixture(
            league_id=self.league1.id, home_team_id=self.t1.id, away_team_id=self.t2.id,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=3),
            status="SCHEDULED", external_id="DEC-NAIVE-01",
        )
        self.db.add(fix)
        self.db.commit()
        decisions = [{
            "market": "over_1_5",
            "selection": "Over 1.5",
            "probability": 0.80,
            "confidence": 0.72,
            "decision_score": 0.68,
            "risk_tier": "LOW",
            "signal_status": "SHADOW_SIGNAL",
            "sample_size": 120,
            "is_live": False,
        }]
        with patch.object(DecisionIntelligenceService, "get_fixture_decisions", return_value=decisions):
            snaps = DecisionIntelligenceService.save_decision_snapshots(self.db, fix.id)
        self.assertEqual(len(snaps), 1)
        self.assertIsNone(snaps[0].created_at.tzinfo)
        self.assertIsNone(snaps[0].prediction_timestamp.tzinfo)

        # Verify SQLite PRAGMA foreign_keys=ON in database.set_sqlite_pragma
        if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
            raw_conn = sqlite3.connect(":memory:")
            try:
                set_sqlite_pragma(raw_conn, None)
                fk_val = raw_conn.execute("PRAGMA foreign_keys").fetchone()[0]
                self.assertEqual(fk_val, 1)
            finally:
                raw_conn.close()

    def test_espn_ingestion_none_fields_and_fk_safety(self):
        """Regression test: None subfields in ESPN feeds must not roll back uncommitted leagues/teams or trigger FK errors."""
        fk_engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        event.listen(fk_engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(bind=fk_engine)
        FkSession = sessionmaker(autocommit=False, autoflush=False, bind=fk_engine)
        fk_db = FkSession()
        try:
            future_iso = (datetime.now(timezone.utc) + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
            sample_payload = {
                "leagues": [{"name": "English Premier League"}],
                "events": [
                    {
                        "id": "990001",
                        "date": future_iso,
                        "season": {"slug": "eng.1"},
                        "status": {"type": {"name": "STATUS_SCHEDULED", "state": "pre"}},
                        "competitions": [{
                            "venue": None,
                            "notes": [None],
                            "odds": [{"tracking": None}],
                            "competitors": [
                                {"id": "101", "homeAway": "home", "team": {"displayName": "Arsenal", "abbreviation": "ARS"}},
                                {"id": "102", "homeAway": "away", "team": {"displayName": "Chelsea", "abbreviation": "CHE"}},
                            ],
                        }],
                    },
                    {
                        "id": "990002",
                        "date": future_iso,
                        "season": None,
                        "status": None,
                        "competitions": [{
                            "venue": {"fullName": "Etihad Stadium"},
                            "competitors": [
                                {"id": "103", "homeAway": "home", "team": {"displayName": "Manchester City", "abbreviation": "MCI"}},
                                {"id": "104", "homeAway": "away", "team": {"displayName": "Liverpool", "abbreviation": "LIV"}},
                            ],
                        }],
                    },
                ],
            }

            class FakeResp:
                status_code = 200
                def json(self):
                    return sample_payload

            class FakeClient:
                def __init__(self, *args, **kwargs):
                    pass
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *args):
                    return False
                async def get(self, url):
                    if "/eng.1/" in url:
                        return FakeResp()
                    resp = MagicMock()
                    resp.status_code = 404
                    return resp

            with patch("services.ingestion_service.httpx.AsyncClient", FakeClient):
                res = asyncio.run(DataIngestionService.fetch_and_ingest_from_api(fk_db))

            self.assertEqual(res.get("status"), "ok")
            self.assertEqual(res.get("fixtures_ingested"), 2)
            self.assertEqual(fk_db.query(Fixture).count(), 2)
        finally:
            fk_db.close()
            fk_engine.dispose()

    def test_espn_postponed_with_post_state_not_finished(self):
        """Postponed ESPN fixtures with status.type.state == 'post' must remain POSTPONED, not FINISHED."""
        import asyncio
        from unittest.mock import patch, MagicMock
        from sqlalchemy import create_engine, event
        from sqlalchemy.orm import sessionmaker
        from models import Base, Fixture
        from services.ingestion_service import DataIngestionService

        fk_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        event.listen(fk_engine, "connect", lambda c, _: c.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(bind=fk_engine)
        FkSession = sessionmaker(bind=fk_engine)
        fk_db = FkSession()
        try:
            future_iso = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            sample_payload = {
                "leagues": [{"name": "A-League", "slug": "aus.1", "season": {"slug": "aus.1"}}],
                "events": [{
                    "id": "990101",
                    "date": future_iso,
                    "season": {"slug": "aus.1"},
                    "status": {"type": {"name": "STATUS_POSTPONED", "state": "post"}},
                    "competitions": [{
                        "competitors": [
                            {"id": "201", "homeAway": "home", "team": {"displayName": "Newcastle Jets"}},
                            {"id": "202", "homeAway": "away", "team": {"displayName": "Adelaide United"}},
                        ]
                    }],
                }],
            }

            class FakeResp:
                status_code = 200
                def json(self):
                    return sample_payload

            class FakeClient:
                def __init__(self, *args, **kwargs):
                    pass
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *args):
                    return False
                async def get(self, url):
                    if "/all/scoreboard" in url:
                        return FakeResp()
                    resp = MagicMock()
                    resp.status_code = 404
                    return resp

            with patch("services.ingestion_service.httpx.AsyncClient", FakeClient):
                res = asyncio.run(DataIngestionService.fetch_and_ingest_from_api(fk_db))

            self.assertEqual(res.get("status"), "ok")
            self.assertEqual(res.get("source"), "espn_realtime_api")
            fix = fk_db.query(Fixture).filter(Fixture.external_id == "ESPN-FIX-990101").first()
            self.assertIsNotNone(fix)
            self.assertEqual(fix.status, "POSTPONED")
        finally:
            fk_db.close()
            fk_engine.dispose()

    def test_batch_cache_prediction_equivalence_and_team_ids_filter(self):
        """Batch-cached predictions match uncached predict_fixture exactly and team_ids filtering limits stats updates."""
        from models import League, Team, Fixture, HistoricalResult, Prediction, CornerPredictionSnapshot, TeamStatistics
        from services.statistics_service import calculate_all_team_statistics
        from services.prediction_service import PoissonPredictionEngine

        lg = League(external_id="TEST-BATCH-LG", name="Test Batch League", country="Testland", season="2025/2026")
        self.db.add(lg)
        self.db.commit()
        self.db.refresh(lg)

        teams = [
            Team(external_id=f"TB-T{i}", name=f"Batch FC {i}", league_id=lg.id)
            for i in range(4)
        ]
        self.db.add_all(teams)
        self.db.commit()
        for t in teams:
            self.db.refresh(t)

        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        for day_off in range(1, 5):
            hf = Fixture(
                external_id=f"TB-HIST-{day_off}",
                league_id=lg.id,
                home_team_id=teams[0].id,
                away_team_id=teams[1].id,
                match_date=now_naive - timedelta(days=day_off * 3),
                status="FINISHED",
            )
            self.db.add(hf)
            self.db.flush()
            self.db.add(HistoricalResult(
                fixture_id=hf.id, home_score=2, away_score=1, total_goals=3,
                half_time_home_score=1, half_time_away_score=0,
            ))

        up1 = Fixture(
            external_id="TB-UP-1", league_id=lg.id, home_team_id=teams[0].id, away_team_id=teams[1].id,
            match_date=now_naive + timedelta(hours=3), status="SCHEDULED",
        )
        up2 = Fixture(
            external_id="TB-UP-2", league_id=lg.id, home_team_id=teams[0].id, away_team_id=teams[2].id,
            match_date=now_naive + timedelta(hours=6), status="SCHEDULED",
        )
        self.db.add_all([up1, up2])
        self.db.commit()
        self.db.refresh(up1)
        self.db.refresh(up2)

        updated_subset = calculate_all_team_statistics(self.db, team_ids={teams[0].id, teams[1].id})
        self.assertEqual({s.team_id for s in updated_subset}, {teams[0].id, teams[1].id})
        self.assertIsNone(self.db.query(TeamStatistics).filter(TeamStatistics.team_id == teams[3].id).first())

        p1_uncached = PoissonPredictionEngine.predict_fixture(self.db, up1.id, commit=True, batch_cache=None)
        p2_uncached = PoissonPredictionEngine.predict_fixture(self.db, up2.id, commit=True, batch_cache=None)
        expected = {
            up1.id: (p1_uncached.predicted_home_score, p1_uncached.predicted_away_score, p1_uncached.over_1_5_probability, p1_uncached.confidence_score),
            up2.id: (p2_uncached.predicted_home_score, p2_uncached.predicted_away_score, p2_uncached.over_1_5_probability, p2_uncached.confidence_score),
        }

        from models import CardPredictionSnapshot
        from services.cards_service import CardsPredictionEngine

        c1_uncached = CardsPredictionEngine.predict_cards(
            self.db, up1.id, target_date=up1.match_date, save_snapshot=False, batch_cache=None
        )
        c1_cached = CardsPredictionEngine.predict_cards(
            self.db, up1.id, target_date=up1.match_date, save_snapshot=False, batch_cache={}
        )
        self.assertEqual(c1_uncached["expected"], c1_cached["expected"])
        self.assertEqual(c1_uncached["total_markets"], c1_cached["total_markets"])

        self.db.query(Prediction).delete()
        self.db.query(CornerPredictionSnapshot).delete()
        self.db.query(CardPredictionSnapshot).delete()
        self.db.commit()

        preds = PoissonPredictionEngine.predict_all_upcoming_fixtures(self.db, upcoming_only=True)
        self.assertGreaterEqual(len(preds), 2)
        for fix_id, exp_vals in expected.items():
            row = self.db.query(Prediction).filter(Prediction.fixture_id == fix_id).first()
            self.assertIsNotNone(row)
            self.assertEqual(
                (row.predicted_home_score, row.predicted_away_score, row.over_1_5_probability, row.confidence_score),
                exp_vals,
            )
            snap = self.db.query(CornerPredictionSnapshot).filter(CornerPredictionSnapshot.fixture_id == fix_id).first()
            self.assertIsNotNone(snap)
            card_snap = self.db.query(CardPredictionSnapshot).filter(CardPredictionSnapshot.fixture_id == fix_id).first()
            self.assertIsNotNone(card_snap)

    def test_upcoming_endpoint_serializes_multi_market_snapshots(self):
        """GET /api/fixtures/upcoming exposes corners, cards, over_4_5, and btts_no when present and None otherwise."""
        from fastapi.testclient import TestClient
        from main import app
        from database import get_db
        from models import CornerPredictionSnapshot, CardPredictionSnapshot

        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        f_with_markets = Fixture(
            external_id="MKT-WITH-1",
            league_id=self.league1.id,
            home_team_id=self.t1.id,
            away_team_id=self.t2.id,
            match_date=now_naive + timedelta(hours=4),
            status="SCHEDULED",
        )
        f_no_snapshots = Fixture(
            external_id="MKT-NONE-2",
            league_id=self.league1.id,
            home_team_id=self.t2.id,
            away_team_id=self.t1.id,
            match_date=now_naive + timedelta(hours=8),
            status="SCHEDULED",
        )
        self.db.add_all([f_with_markets, f_no_snapshots])
        self.db.commit()
        self.db.refresh(f_with_markets)
        self.db.refresh(f_no_snapshots)

        self.db.add(Prediction(
            fixture_id=f_with_markets.id,
            predicted_home_score=1.8,
            predicted_away_score=1.2,
            expected_goals_xg=3.0,
            home_win_probability=0.52,
            draw_probability=0.25,
            away_win_probability=0.23,
            over_0_5_probability=0.95,
            over_1_5_probability=0.80,
            over_2_5_probability=0.59,
            over_3_5_probability=0.35,
            over_4_5_probability=0.18,
            under_2_5_probability=0.41,
            btts_probability=0.58,
            confidence_score=74,
            most_likely_score="2-1",
            model_version="v2_dc_test",
        ))
        self.db.add(CornerPredictionSnapshot(
            fixture_id=f_with_markets.id,
            expected_home_corners=5.8,
            expected_away_corners=4.4,
            expected_total_corners=10.2,
            over_7_5_prob=0.81,
            under_7_5_prob=0.19,
            over_8_5_prob=0.71,
            under_8_5_prob=0.29,
            over_9_5_prob=0.59,
            under_9_5_prob=0.41,
            over_10_5_prob=0.46,
            under_10_5_prob=0.54,
            over_11_5_prob=0.34,
            under_11_5_prob=0.66,
            home_over_3_5_prob=0.78,
            home_over_4_5_prob=0.64,
            home_over_5_5_prob=0.49,
            away_over_3_5_prob=0.62,
            away_over_4_5_prob=0.45,
            away_over_5_5_prob=0.31,
            confidence_score=72,
        ))
        self.db.add(CardPredictionSnapshot(
            fixture_id=f_with_markets.id,
            expected_home_cards=1.9,
            expected_away_cards=2.4,
            expected_total_cards=4.3,
            over_1_5_prob=0.92,
            under_1_5_prob=0.08,
            over_2_5_prob=0.81,
            under_2_5_prob=0.19,
            over_3_5_prob=0.64,
            under_3_5_prob=0.36,
            over_4_5_prob=0.45,
            under_4_5_prob=0.55,
            over_5_5_prob=0.27,
            under_5_5_prob=0.73,
            over_6_5_prob=0.14,
            under_6_5_prob=0.86,
            home_over_0_5_prob=0.85,
            home_over_1_5_prob=0.56,
            home_over_2_5_prob=0.29,
            away_over_0_5_prob=0.90,
            away_over_1_5_prob=0.68,
            away_over_2_5_prob=0.41,
            any_red_card_prob=0.19,
            confidence_score=68,
        ))
        self.db.commit()

        def _override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = _override_get_db
        try:
            client = TestClient(app)
            resp = client.get("/api/fixtures/upcoming?hours=72&limit=50")
            self.assertEqual(resp.status_code, 200)
            items = {row["external_id"]: row for row in resp.json()["data"]}
            self.assertIn("MKT-WITH-1", items)
            self.assertIn("MKT-NONE-2", items)

            row1 = items["MKT-WITH-1"]
            self.assertAlmostEqual(row1["prediction"]["over_4_5_probability"], 0.18, places=4)
            self.assertAlmostEqual(row1["prediction"]["btts_no_probability"], 0.42, places=4)
            self.assertEqual(row1["corners"]["expected"]["total"], 10.2)
            self.assertAlmostEqual(row1["corners"]["total_markets"]["over_9_5"], 0.59, places=4)
            self.assertEqual(row1["cards"]["expected"]["total"], 4.3)
            self.assertAlmostEqual(row1["cards"]["total_markets"]["over_3_5"], 0.64, places=4)
            self.assertAlmostEqual(row1["cards"]["red_card_risk"]["any_red_prob"], 0.19, places=4)

            row2 = items["MKT-NONE-2"]
            self.assertIsNone(row2["corners"])
            self.assertIsNone(row2["cards"])
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
