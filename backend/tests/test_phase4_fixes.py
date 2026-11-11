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


if __name__ == "__main__":
    unittest.main()
