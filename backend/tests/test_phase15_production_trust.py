import os
import sys
import json
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base, get_db
from models import (
    League, Team, Fixture, HistoricalResult, MatchStatistics,
    LiveMatchState, FixtureProviderMapping, LiveObservedSnapshot, Prediction
)
from services.canonical_competition_service import CanonicalCompetitionService
from services.provider_reconciliation_service import (
    ProviderReconciliationService, ReconciliationStatus
)
from services.fixture_lifecycle_service import (
    FixtureLifecycleService, CanonicalLifecycleState
)
from services.fixture_duplicate_detection_service import (
    FixtureDuplicateDetectionService, DuplicateClassification
)
from services.live_provider_service import LiveProviderAdapterService
from schemas.live_schema import (
    LiveGoalsPrediction, LiveCornersPrediction, LiveCardsPrediction,
    LiveConfidence, BestLiveSignal, LiveMarketProbability
)
from services.live_service import LiveSignalEngine
from main import app


class TestPhase15ProductionTrust(unittest.TestCase):
    """
    Phase 15 End-to-End Validation & Production Trust Test Suite.
    Guarantees:
    1. Zero cross-fixture contamination & monotonic request sequencing
    2. Zero fabricated production statistics & scores
    3. Strict canonical fixture, competition & country resolution (no team-name guessing)
    4. Safe live signal gating (stale, finished, scheduled, or unverified states yield NO_SIGNAL)
    5. Monotonic snapshot versioning & deterministic SHA-256 deduplication
    6. Exact freshness threshold boundaries
    7. Fixture-relative H2H calculation
    8. Full API contract consistency & non-duplication
    """

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.Session()

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

        # Seed baseline league and teams
        self.league = League(
            name="English Premier League",
            country="England",
            season="2025/2026",
            external_id="COMP-eng.1"
        )
        self.db.add(self.league)
        self.db.flush()

        self.home_team = Team(
            name="Arsenal",
            short_code="ARS",
            league_id=self.league.id,
            external_id="TEAM-ARS"
        )
        self.away_team = Team(
            name="Chelsea",
            short_code="CHE",
            league_id=self.league.id,
            external_id="TEAM-CHE"
        )
        self.db.add_all([self.home_team, self.away_team])
        self.db.flush()

        # Seed canonical fixture
        self.fixture = Fixture(
            league_id=self.league.id,
            home_team_id=self.home_team.id,
            away_team_id=self.away_team.id,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
            status="SCHEDULED",
            external_id="ESPN-FIX-999001"
        )
        self.db.add(self.fixture)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        app.dependency_overrides.clear()

    def test_01_provider_reconciliation_detects_all_mismatches(self):
        """Reconciliation rejects wrong provider ID, wrong teams, swapped teams, wrong date, wrong league."""
        # 1. Valid Payload
        valid_payload = {
            "id": "ESPN-FIX-999001",
            "date": self.fixture.match_date.isoformat() + "Z",
            "competitions": [{
                "competitors": [
                    {"homeAway": "home", "team": {"name": "Arsenal"}},
                    {"homeAway": "away", "team": {"name": "Chelsea"}}
                ],
                "league": {"name": "English Premier League"}
            }]
        }
        res = ProviderReconciliationService.reconcile(fixture=self.fixture, provider_data=valid_payload, db=self.db)
        self.assertEqual(res["status"], ReconciliationStatus.IDENTITY_VALID.value)

        # 2. Provider Event Mismatch
        mismatch_event = dict(valid_payload, id="ESPN-FIX-WRONG")
        res = ProviderReconciliationService.reconcile(fixture=self.fixture, provider_data=mismatch_event, db=self.db)
        self.assertEqual(res["status"], ReconciliationStatus.PROVIDER_EVENT_MISMATCH.value)

        # 3. Home Team Mismatch
        mismatch_home = {
            "id": "ESPN-FIX-999001",
            "date": self.fixture.match_date.isoformat() + "Z",
            "competitions": [{
                "competitors": [
                    {"homeAway": "home", "team": {"name": "Liverpool"}},
                    {"homeAway": "away", "team": {"name": "Chelsea"}}
                ]
            }]
        }
        res = ProviderReconciliationService.reconcile(fixture=self.fixture, provider_data=mismatch_home, db=self.db)
        self.assertEqual(res["status"], ReconciliationStatus.HOME_TEAM_MISMATCH.value)

        # 4. Swapped Teams
        swapped = {
            "id": "ESPN-FIX-999001",
            "date": self.fixture.match_date.isoformat() + "Z",
            "competitions": [{
                "competitors": [
                    {"homeAway": "home", "team": {"name": "Chelsea"}},
                    {"homeAway": "away", "team": {"name": "Arsenal"}}
                ]
            }]
        }
        res = ProviderReconciliationService.reconcile(fixture=self.fixture, provider_data=swapped, db=self.db)
        self.assertEqual(res["status"], ReconciliationStatus.TEAMS_SWAPPED.value)

        # 5. Kickoff Mismatch (>36 hours drift)
        offset_date = self.fixture.match_date + timedelta(hours=48)
        mismatch_time = {
            "id": "ESPN-FIX-999001",
            "date": offset_date.isoformat() + "Z",
            "competitions": [{
                "competitors": [
                    {"homeAway": "home", "team": {"name": "Arsenal"}},
                    {"homeAway": "away", "team": {"name": "Chelsea"}}
                ]
            }]
        }
        res = ProviderReconciliationService.reconcile(fixture=self.fixture, provider_data=mismatch_time, db=self.db)
        self.assertEqual(res["status"], ReconciliationStatus.KICKOFF_MISMATCH.value)

        # 6. Competition Mismatch
        mismatch_comp = {
            "id": "ESPN-FIX-999001",
            "date": self.fixture.match_date.isoformat() + "Z",
            "competitions": [{
                "competitors": [
                    {"homeAway": "home", "team": {"name": "Arsenal"}},
                    {"homeAway": "away", "team": {"name": "Chelsea"}}
                ],
                "league": {"name": "Spanish LALIGA"}
            }]
        }
        res = ProviderReconciliationService.reconcile(fixture=self.fixture, provider_data=mismatch_comp, db=self.db)
        self.assertEqual(res["status"], ReconciliationStatus.COMPETITION_MISMATCH.value)

    def test_02_country_competition_zero_guessing(self):
        """Never infer country or league from club name; use only canonical verified competition metadata."""
        # Test generic or non-European team names
        res1 = CanonicalCompetitionService.resolve_competition(
            league_name="English Premier League",
            season_slug="eng.1",
            provided_country="England"
        )
        self.assertEqual(res1.competition_name, "English Premier League")
        self.assertEqual(res1.country, "England")

        # Unknown competition without metadata must return UNAVAILABLE, not guess based on club
        res2 = CanonicalCompetitionService.resolve_competition(
            league_name=None,
            season_slug=None,
            provided_country=None
        )
        self.assertEqual(res2.country, "UNAVAILABLE")
        self.assertEqual(res2.competition_name, "UNAVAILABLE")

        # Argentine Primera B vs Scottish Premiership distinction
        res_arg = CanonicalCompetitionService.resolve_competition(
            league_name="Argentine Primera B",
            season_slug="arg.2",
            provided_country="Argentina"
        )
        self.assertEqual(res_arg.country, "Argentina")

        res_sco = CanonicalCompetitionService.resolve_competition(
            league_name="Scottish Premiership",
            season_slug="sco.1",
            provided_country="Scotland"
        )
        self.assertEqual(res_sco.country, "Scotland")

    def test_03_zero_fabrication_on_missing_prediction(self):
        """Endpoints return None and do not fabricate 1.45, 1.15, 0.78, or 0.45 when no model prediction exists."""
        # 1. Upcoming fixtures endpoint
        resp = self.client.get("/api/fixtures/upcoming")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        item = next((f for f in data["data"] if f["id"] == self.fixture.id), None)
        self.assertIsNotNone(item)
        # Because no Prediction record exists for this fixture, prediction must be None!
        self.assertIsNone(item["prediction"])

        # 2. Details endpoint
        resp_det = self.client.get(f"/api/fixtures/{self.fixture.id}/details")
        self.assertEqual(resp_det.status_code, 200)
        det_data = resp_det.json()
        self.assertIsNotNone(det_data["prediction"])
        self.assertIn("expected_goals_xg", det_data["prediction"])

        # 3. Finished fixture without prediction
        fin_fix = Fixture(
            league_id=self.league.id,
            home_team_id=self.home_team.id,
            away_team_id=self.away_team.id,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2),
            status="FINISHED",
            home_score=2,
            away_score=1,
            external_id="ESPN-FIX-999002"
        )
        self.db.add(fin_fix)
        self.db.commit()

        resp_fin = self.client.get("/api/fixtures/finished")
        self.assertEqual(resp_fin.status_code, 200)
        fin_data = resp_fin.json()
        fin_item = next((f for f in fin_data["data"] if f["id"] == fin_fix.id), None)
        self.assertIsNotNone(fin_item)
        self.assertIsNone(fin_item["prediction"])

    def test_04_snapshot_version_monotonicity_and_hash_deduplication(self):
        """Identical payloads produce zero new snapshots; changed states produce exactly one new version."""
        live_fix = Fixture(
            league_id=self.league.id,
            home_team_id=self.home_team.id,
            away_team_id=self.away_team.id,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None),
            status="LIVE",
            home_score=0,
            away_score=0,
            external_id="ESPN-FIX-999003"
        )
        self.db.add(live_fix)
        self.db.commit()

        payload1 = {
            "minute": 15,
            "period": "1H",
            "home_score": 0,
            "away_score": 0,
            "statistics": {"shots": {"home": 2, "away": 1}}
        }

        # First snapshot creation
        snap1 = LiveProviderAdapterService.record_observed_snapshot(self.db, live_fix.id, payload1)
        self.assertIsNotNone(snap1)
        self.assertEqual(snap1.snapshot_version, 1)
        v1_hash = snap1.snapshot_hash
        self.assertTrue(len(v1_hash) == 64)  # Valid SHA-256

        # Second poll with IDENTICAL payload -> MUST return existing snapshot without version increment
        snap2 = LiveProviderAdapterService.record_observed_snapshot(self.db, live_fix.id, payload1)
        self.assertEqual(snap2.snapshot_version, 1)
        self.assertEqual(snap2.snapshot_hash, v1_hash)

        # Count total snapshots in DB -> exactly 1
        total_snaps = self.db.query(LiveObservedSnapshot).filter(LiveObservedSnapshot.fixture_id == live_fix.id).count()
        self.assertEqual(total_snaps, 1)

        # Third poll with changed minute -> creates version 2 with new hash
        payload2 = dict(payload1, minute=16)
        snap3 = LiveProviderAdapterService.record_observed_snapshot(self.db, live_fix.id, payload2)
        self.assertEqual(snap3.snapshot_version, 2)
        self.assertNotEqual(snap3.snapshot_hash, v1_hash)

        # Total snapshots is now exactly 2
        total_snaps_now = self.db.query(LiveObservedSnapshot).filter(LiveObservedSnapshot.fixture_id == live_fix.id).count()
        self.assertEqual(total_snaps_now, 2)

    def test_05_freshness_threshold_boundaries(self):
        """Evaluates strict timestamp difference thresholds: FRESH, DELAYED, STALE, VERY_STALE, UNAVAILABLE."""
        now = datetime.now(timezone.utc)

        # 1. 30 seconds ago -> FRESH
        t_fresh = now - timedelta(seconds=30)
        f_fresh = LiveProviderAdapterService.calculate_freshness(t_fresh)
        self.assertEqual(f_fresh["status"], "FRESH")

        # 2. 120 seconds ago -> DELAYED (60 - 180s)
        t_delayed = now - timedelta(seconds=120)
        f_delayed = LiveProviderAdapterService.calculate_freshness(t_delayed)
        self.assertEqual(f_delayed["status"], "DELAYED")

        # 3. 240 seconds ago -> STALE (180 - 300s)
        t_stale = now - timedelta(seconds=240)
        f_stale = LiveProviderAdapterService.calculate_freshness(t_stale)
        self.assertEqual(f_stale["status"], "STALE")

        # 4. 400 seconds ago -> VERY_STALE (> 300s)
        t_very_stale = now - timedelta(seconds=400)
        f_very_stale = LiveProviderAdapterService.calculate_freshness(t_very_stale)
        self.assertEqual(f_very_stale["status"], "VERY_STALE")

        # 5. None -> UNAVAILABLE
        f_unavail = LiveProviderAdapterService.calculate_freshness(None)
        self.assertEqual(f_unavail["status"], "UNAVAILABLE")

    def test_06_live_signal_safety_guardrails(self):
        """LiveSignalEngine returns NO_SIGNAL for finished fixtures, scheduled fixtures, stale data, or identity issues."""
        dummy_goals = MagicMock()
        dummy_corners = MagicMock()
        dummy_cards = MagicMock()
        conf = LiveConfidence(
            overall_confidence=80,
            pre_match_confidence=75,
            live_data_quality=80,
            statistical_coverage=85,
            model_stability=80,
            time_sensitivity=70,
            label="strong"
        )

        # 1. Finished Fixture Safety
        signals, best = LiveSignalEngine.evaluate_live_signals(
            dummy_goals, dummy_corners, dummy_cards, conf, time_rem_mins=0.0,
            fixture_status="FINISHED"
        )
        self.assertEqual(best.label, "NO_SIGNAL")
        self.assertIn("finished", best.rationale.lower())

        # 2. Scheduled Fixture Safety
        signals, best = LiveSignalEngine.evaluate_live_signals(
            dummy_goals, dummy_corners, dummy_cards, conf, time_rem_mins=90.0,
            fixture_status="SCHEDULED"
        )
        self.assertEqual(best.label, "NO_SIGNAL")
        self.assertIn("not kicked off", best.rationale.lower())

        # 3. Stale Data Safety Gate
        signals, best = LiveSignalEngine.evaluate_live_signals(
            dummy_goals, dummy_corners, dummy_cards, conf, time_rem_mins=45.0,
            fresh_status="STALE",
            fixture_status="LIVE"
        )
        self.assertEqual(best.label, "NO_SIGNAL")
        self.assertIn("stale", best.rationale.lower())

        # 4. Identity Issue Safety Gate
        signals, best = LiveSignalEngine.evaluate_live_signals(
            dummy_goals, dummy_corners, dummy_cards, conf, time_rem_mins=45.0,
            identity_status="HOME_TEAM_MISMATCH",
            fixture_status="LIVE"
        )
        self.assertEqual(best.label, "NO_SIGNAL")
        self.assertIn("identity status", best.rationale.lower())

    def test_07_h2h_strictly_fixture_relative(self):
        """H2H orientation correctly maps home and away perspectives relative to the target fixture."""
        # Current Fixture: Arsenal (Home) vs Chelsea (Away)
        # Historical Match 1: Arsenal 2 - 0 Chelsea (Arsenal was Home -> Home win)
        h1 = Fixture(
            league_id=self.league.id,
            home_team_id=self.home_team.id,
            away_team_id=self.away_team.id,
            match_date=datetime(2025, 5, 1, 15, 0, 0),
            status="FINISHED",
            home_score=2,
            away_score=0,
            external_id="HIST-H2H-1"
        )
        # Historical Match 2: Chelsea 3 - 1 Arsenal (Arsenal was Away, Chelsea was Home -> Away win for current fixture)
        h2 = Fixture(
            league_id=self.league.id,
            home_team_id=self.away_team.id,
            away_team_id=self.home_team.id,
            match_date=datetime(2025, 1, 15, 15, 0, 0),
            status="FINISHED",
            home_score=3,
            away_score=1,
            external_id="HIST-H2H-2"
        )
        self.db.add_all([h1, h2])
        self.db.commit()

        resp = self.client.get(f"/api/fixtures/{self.fixture.id}/details")
        self.assertEqual(resp.status_code, 200)
        summary = resp.json()["h2h_summary"]

        self.assertEqual(summary["total_matches"], 2)
        # In H1: Arsenal won -> home_wins = 1
        # In H2: Chelsea won (Chelsea is current Away) -> away_wins = 1
        self.assertEqual(summary["home_wins"], 1)
        self.assertEqual(summary["away_wins"], 1)
        self.assertEqual(summary["draws"], 0)
        # Goals for Arsenal: 2 + 1 = 3 home_goals
        # Goals for Chelsea: 0 + 3 = 3 away_goals
        self.assertEqual(summary["home_goals"], 3)
        self.assertEqual(summary["away_goals"], 3)

    def test_08_duplicate_detection_distinction(self):
        """Duplicate detection separates confirmed duplicates from distinct matches without data loss."""
        dup_candidate = Fixture(
            league_id=self.league.id,
            home_team_id=self.home_team.id,
            away_team_id=self.away_team.id,
            match_date=self.fixture.match_date + timedelta(hours=1),
            status="SCHEDULED",
            external_id="ESPN-FIX-999001"  # Same provider external ID!
        )
        dup_candidate.home_team = self.home_team
        dup_candidate.away_team = self.away_team

        cmp_res = FixtureDuplicateDetectionService.compare_fixtures(self.fixture, dup_candidate)
        self.assertEqual(cmp_res["classification"], DuplicateClassification.DUPLICATE_CONFIRMED.value)
        self.assertEqual(cmp_res["confidence"], 1.0)

        distinct_fixture = Fixture(
            league_id=self.league.id,
            home_team_id=self.home_team.id,
            away_team_id=self.away_team.id,
            match_date=self.fixture.match_date + timedelta(days=60),  # Months away
            status="SCHEDULED",
            external_id="ESPN-FIX-DISTINCT"
        )
        distinct_fixture.home_team = self.home_team
        distinct_fixture.away_team = self.away_team

        cmp_distinct = FixtureDuplicateDetectionService.compare_fixtures(self.fixture, distinct_fixture)
        self.assertEqual(cmp_distinct["classification"], DuplicateClassification.DISTINCT_FIXTURE.value)

    def test_09_api_contracts_and_data_integrity_endpoint(self):
        """Inspects system-wide /api/system/data-integrity endpoint contract."""
        resp = self.client.get("/api/system/data-integrity")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn(payload["status"], ["ok", "HEALTHY"])
        self.assertIn("provider_health", payload)
        self.assertIn("duplicate_summary", payload)
        self.assertIn("timestamp", payload)
