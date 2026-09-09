import os
import sys
import json
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import httpx
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
    LiveMatchState, FixtureProviderMapping, LiveObservedSnapshot
)
from services.provider_reconciliation_service import (
    ProviderReconciliationService, ReconciliationStatus
)
from services.fixture_lifecycle_service import (
    FixtureLifecycleService, CanonicalLifecycleState
)
from services.fixture_duplicate_detection_service import (
    FixtureDuplicateDetectionService, DuplicateClassification
)
from services.provider_health_service import ProviderHealthService
from services.circuit_breaker_service import CircuitBreakerService
from services.live_provider_service import LiveProviderAdapterService
from services.data_quality_service import DataQualityService
from main import app


class TestPhase14ProductionIntegrity(unittest.TestCase):
    """
    Comprehensive Phase 14 Test Suite:
    Guarantees production data integrity, provider reconciliation,
    observability, snapshot versioning, and zero fabrication.
    """

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool
        )
        Base.metadata.create_all(bind=self.engine)
        self.TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.TestingSessionLocal()

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

        # Seed authoritative test league and teams
        self.league = League(id=1, name="English Premier League", country="England", season="2025/2026")
        self.arsenal = Team(id=1, name="Arsenal", league_id=1)
        self.chelsea = Team(id=2, name="Chelsea", league_id=1)
        self.liverpool = Team(id=3, name="Liverpool", league_id=1)
        self.man_city = Team(id=4, name="Manchester City", league_id=1)

        self.db.add_all([self.league, self.arsenal, self.chelsea, self.liverpool, self.man_city])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        app.dependency_overrides.clear()

    # -------------------------------------------------------------------------
    # 1. LIVE MATCH
    # -------------------------------------------------------------------------
    def test_01_live_match(self):
        """Live match preserves observed score and clock without fabrication."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        fix = Fixture(
            id=101, league_id=1, home_team_id=1, away_team_id=2,
            match_date=now - timedelta(minutes=35), status="LIVE",
            home_score=1, away_score=0, live_clock="35'", external_id="ESPN-FIX-4018001"
        )
        self.db.add(fix)
        self.db.commit()

        canonical_state = FixtureLifecycleService.get_canonical_lifecycle(fix, minute=35)
        self.assertEqual(canonical_state, CanonicalLifecycleState.LIVE)

        resp = self.client.get("/api/fixtures/101/details")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["home_score"], 1)
        self.assertEqual(data["away_score"], 0)
        self.assertEqual(data["match_minute"], "35'")
        self.assertEqual(data["data_status"], "LIVE")

    # -------------------------------------------------------------------------
    # 2. FINISHED MATCH
    # -------------------------------------------------------------------------
    def test_02_finished_match(self):
        """Finished match preserves final result and canonical FINISHED state."""
        past_date = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
        fix = Fixture(
            id=102, league_id=1, home_team_id=1, away_team_id=2,
            match_date=past_date, status="FINISHED",
            home_score=2, away_score=1, live_clock="FT", external_id="ESPN-FIX-4018002"
        )
        self.db.add(fix)
        self.db.add(HistoricalResult(fixture_id=102, home_score=2, away_score=1, total_goals=3))
        self.db.commit()

        canon_st = FixtureLifecycleService.get_canonical_lifecycle(fix)
        self.assertEqual(canon_st, CanonicalLifecycleState.FINISHED)

        resp = self.client.get("/api/fixtures/102/details")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["home_score"], 2)
        self.assertEqual(data["away_score"], 1)
        self.assertEqual(data["data_status"], "VERIFIED")

    # -------------------------------------------------------------------------
    # 3. SCHEDULED MATCH (Zero Fabrication: never 0-0)
    # -------------------------------------------------------------------------
    def test_03_scheduled_match(self):
        """Scheduled match must return None for unobserved scores, never 0-0."""
        future_date = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=3)
        fix = Fixture(
            id=103, league_id=1, home_team_id=3, away_team_id=4,
            match_date=future_date, status="SCHEDULED",
            home_score=None, away_score=None, external_id="ESPN-FIX-4018003"
        )
        self.db.add(fix)
        self.db.commit()

        resp = self.client.get("/api/fixtures/103/details")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["home_score"])
        self.assertIsNone(data["away_score"])
        self.assertEqual(data["match_status"], "SCHEDULED")

    # -------------------------------------------------------------------------
    # 4. POSTPONED MATCH
    # -------------------------------------------------------------------------
    def test_04_postponed_match(self):
        """Postponed match normalizes to POSTPONED without data fabrication."""
        st = FixtureLifecycleService.normalize_lifecycle_state("STATUS_POSTPONED")
        self.assertEqual(st, CanonicalLifecycleState.POSTPONED)

        st2 = FixtureLifecycleService.normalize_lifecycle_state("P-P")
        self.assertEqual(st2, CanonicalLifecycleState.POSTPONED)

    # -------------------------------------------------------------------------
    # 5. PARTIAL STATISTICS (No 0 or 50% substitution)
    # -------------------------------------------------------------------------
    def test_05_partial_statistics(self):
        """Partial stats return explicit AVAILABLE and UNAVAILABLE source states."""
        fix = Fixture(
            id=105, league_id=1, home_team_id=1, away_team_id=2,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None), status="LIVE",
            external_id="ESPN-FIX-4018005"
        )
        self.db.add(fix)
        self.db.commit()

        # Mock ESPN payload with shots but missing corners & fouls
        mock_payload = {
            "header": {
                "id": "4018005",
                "competitions": [{
                    "id": "4018005",
                    "status": {"type": {"name": "STATUS_IN_PROGRESS", "state": "in"}},
                    "competitors": [
                        {"homeAway": "home", "team": {"name": "Arsenal"}, "score": "1"},
                        {"homeAway": "away", "team": {"name": "Chelsea"}, "score": "0"}
                    ]
                }]
            },
            "boxscore": {
                "teams": [
                    {"team": {"name": "Arsenal"}, "statistics": [{"name": "totalShots", "displayValue": "8"}]},
                    {"team": {"name": "Chelsea"}, "statistics": [{"name": "totalShots", "displayValue": "4"}]}
                ]
            }
        }

        with patch("httpx.Client.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_payload
            mock_get.return_value = mock_resp

            live_res = LiveProviderAdapterService.fetch_live_summary(self.db, 105)
            self.assertEqual(live_res["statistics"]["shots"]["source_status"], "AVAILABLE")
            self.assertEqual(live_res["statistics"]["shots"]["home"], 8.0)
            self.assertEqual(live_res["statistics"]["corners"]["source_status"], "UNAVAILABLE")
            self.assertIsNone(live_res["statistics"]["corners"]["home"])

    # -------------------------------------------------------------------------
    # 6. PROVIDER UNAVAILABLE
    # -------------------------------------------------------------------------
    def test_06_provider_unavailable(self):
        """Provider unavailability serves last verified state without fabrication."""
        fix = Fixture(
            id=106, league_id=1, home_team_id=1, away_team_id=2,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None), status="LIVE",
            external_id="ESPN-FIX-4018006"
        )
        self.db.add(fix)
        self.db.add(LiveMatchState(
            fixture_id=106, minute=22, home_score=1, away_score=0,
            last_updated=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=70)
        ))
        self.db.commit()

        with patch("httpx.Client.get", side_effect=httpx.ConnectError("Connection refused")):
            res = LiveProviderAdapterService.fetch_live_summary(self.db, 106)
            self.assertEqual(res["score"]["home"], 1)
            self.assertEqual(res["score"]["away"], 0)
            self.assertEqual(res["minute"], 22)
            self.assertEqual(res["freshness"], "DELAYED")

    # -------------------------------------------------------------------------
    # 7. PROVIDER TIMEOUT
    # -------------------------------------------------------------------------
    def test_07_provider_timeout(self):
        """Provider timeout triggers circuit breaker timeout tracking and never resets minute or score."""
        fix = Fixture(
            id=107, league_id=1, home_team_id=1, away_team_id=2,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None), status="LIVE",
            external_id="ESPN-FIX-4018007"
        )
        self.db.add(fix)
        self.db.add(LiveMatchState(
            fixture_id=107, minute=65, home_score=2, away_score=2,
            last_updated=datetime.now(timezone.utc).replace(tzinfo=None)
        ))
        self.db.commit()

        circuit = CircuitBreakerService.get_circuit("ESPN")
        prev_timeouts = circuit.timeout_count

        with patch("httpx.Client.get", side_effect=httpx.TimeoutException("Read timeout")):
            res = LiveProviderAdapterService.fetch_live_summary(self.db, 107)
            self.assertEqual(res["minute"], 65)
            self.assertEqual(res["score"]["home"], 2)
            self.assertEqual(res["score"]["away"], 2)
            self.assertGreater(circuit.timeout_count, prev_timeouts)

    # -------------------------------------------------------------------------
    # 8. PROVIDER EVENT MISMATCH
    # -------------------------------------------------------------------------
    def test_08_provider_event_mismatch(self):
        """Reconciliation rejects payload when provider event ID contradicts DB."""
        fix = Fixture(
            id=108, league_id=1, home_team_id=1, away_team_id=2,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None),
            external_id="ESPN-FIX-4018008"
        )
        self.db.add(fix)
        self.db.commit()

        mismatched_payload = {
            "event_id": "9999999", # Different ID
            "home_team": "Arsenal",
            "away_team": "Chelsea"
        }

        res = ProviderReconciliationService.reconcile(fix, mismatched_payload, db=self.db)
        self.assertFalse(res["verified"])
        self.assertEqual(res["status"], ReconciliationStatus.PROVIDER_EVENT_MISMATCH.value)

    # -------------------------------------------------------------------------
    # 9. TEAM MISMATCH
    # -------------------------------------------------------------------------
    def test_09_team_mismatch(self):
        """Reconciliation rejects payload when teams do not match DB."""
        fix = Fixture(
            id=109, league_id=1, home_team_id=1, away_team_id=2,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None),
            external_id="ESPN-FIX-4018009"
        )
        self.db.add(fix)
        self.db.commit()

        bad_payload = {
            "event_id": "4018009",
            "home_team": "Barcelona",
            "away_team": "Real Madrid"
        }

        res = ProviderReconciliationService.reconcile(fix, bad_payload, db=self.db)
        self.assertFalse(res["verified"])
        self.assertIn(res["status"], [ReconciliationStatus.HOME_TEAM_MISMATCH.value, ReconciliationStatus.AWAY_TEAM_MISMATCH.value])

    # -------------------------------------------------------------------------
    # 10. SWAPPED TEAMS
    # -------------------------------------------------------------------------
    def test_10_swapped_teams(self):
        """Reconciliation detects and rejects reversed home/away pairings."""
        fix = Fixture(
            id=110, league_id=1, home_team_id=1, away_team_id=2, # Arsenal vs Chelsea
            match_date=datetime.now(timezone.utc).replace(tzinfo=None),
            external_id="ESPN-FIX-4018010"
        )
        self.db.add(fix)
        self.db.commit()

        swapped_payload = {
            "event_id": "4018010",
            "home_team": "Chelsea",
            "away_team": "Arsenal"
        }

        res = ProviderReconciliationService.reconcile(fix, swapped_payload, db=self.db)
        self.assertFalse(res["verified"])
        self.assertEqual(res["status"], ReconciliationStatus.TEAMS_SWAPPED.value)

    # -------------------------------------------------------------------------
    # 11. WRONG COMPETITION
    # -------------------------------------------------------------------------
    def test_11_wrong_competition(self):
        """Reconciliation rejects payload from conflicting national competition."""
        fix = Fixture(
            id=111, league_id=1, home_team_id=1, away_team_id=2, # EPL
            match_date=datetime.now(timezone.utc).replace(tzinfo=None),
            external_id="ESPN-FIX-4018011"
        )
        self.db.add(fix)
        self.db.commit()

        conflicting_payload = {
            "event_id": "4018011",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "competition": "Spanish La Liga",
            "country": "Spain"
        }

        res = ProviderReconciliationService.reconcile(fix, conflicting_payload, db=self.db)
        self.assertFalse(res["verified"])
        self.assertIn(res["status"], [ReconciliationStatus.COMPETITION_MISMATCH.value, ReconciliationStatus.COUNTRY_MISMATCH.value])

    # -------------------------------------------------------------------------
    # 12. WRONG COUNTRY
    # -------------------------------------------------------------------------
    def test_12_wrong_country(self):
        """Reconciliation rejects payload when country explicitly contradicts DB."""
        fix = Fixture(
            id=112, league_id=1, home_team_id=1, away_team_id=2, # England
            match_date=datetime.now(timezone.utc).replace(tzinfo=None),
            external_id="ESPN-FIX-4018012"
        )
        self.db.add(fix)
        self.db.commit()

        wrong_country_payload = {
            "event_id": "4018012",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "country": "Germany"
        }

        res = ProviderReconciliationService.reconcile(fix, wrong_country_payload, db=self.db)
        self.assertFalse(res["verified"])
        self.assertEqual(res["status"], ReconciliationStatus.COUNTRY_MISMATCH.value)

    # -------------------------------------------------------------------------
    # 13. DUPLICATE FIXTURE CANDIDATE
    # -------------------------------------------------------------------------
    def test_13_duplicate_fixture_candidate(self):
        """Duplicate detection flags confirmed duplicates by provider event ID and possible duplicates by timing."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        f1 = Fixture(id=113, league_id=1, home_team_id=1, away_team_id=2, match_date=now, external_id="ESPN-4018013")
        self.db.add(f1)
        self.db.commit()

        # Candidate from external provider feed with same event ID
        cand_same_event = {
            "id": 999,
            "external_id": "4018013",
            "home_team": "Arsenal FC",
            "away_team": "Chelsea FC",
            "match_date": now + timedelta(minutes=30),
            "competition": "English Premier League"
        }

        cmp1 = FixtureDuplicateDetectionService.compare_fixtures(f1, cand_same_event)
        self.assertEqual(cmp1["classification"], DuplicateClassification.DUPLICATE_CONFIRMED.value)

        # Candidate with matching teams within 2h window but different external ID
        f2 = Fixture(id=114, league_id=1, home_team_id=1, away_team_id=2, match_date=now + timedelta(hours=2), external_id="API-4018014")
        self.db.add(f2)
        self.db.commit()

        cmp2 = FixtureDuplicateDetectionService.compare_fixtures(f1, f2)
        self.assertEqual(cmp2["classification"], DuplicateClassification.DUPLICATE_CONFIRMED.value)

    # -------------------------------------------------------------------------
    # 14. RAPID FIXTURE SWITCHING (No State Bleed)
    # -------------------------------------------------------------------------
    def test_14_rapid_fixture_switching(self):
        """Querying two fixtures back-to-back returns strictly isolated identities."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        f_a = Fixture(id=115, league_id=1, home_team_id=1, away_team_id=2, match_date=now) # Arsenal vs Chelsea
        f_b = Fixture(id=116, league_id=1, home_team_id=3, away_team_id=4, match_date=now) # Liverpool vs Man City
        self.db.add_all([f_a, f_b])
        self.db.commit()

        res_a = self.client.get("/api/fixtures/115/details").json()
        res_b = self.client.get("/api/fixtures/116/details").json()

        self.assertEqual(res_a["home_team"]["name"], "Arsenal")
        self.assertEqual(res_b["home_team"]["name"], "Liverpool")
        self.assertNotEqual(res_a["home_team"]["name"], res_b["home_team"]["name"])

    # -------------------------------------------------------------------------
    # 15. STALE SNAPSHOT
    # -------------------------------------------------------------------------
    def test_15_stale_snapshot(self):
        """Freshness engine accurately identifies STALE (> 180s) and VERY_STALE (> 300s)."""
        now = datetime.now(timezone.utc)
        f_stale = ProviderHealthService.evaluate_live_feed_freshness(now - timedelta(seconds=200))
        self.assertEqual(f_stale["freshness_state"], "STALE")

        f_very_stale = ProviderHealthService.evaluate_live_feed_freshness(now - timedelta(seconds=400))
        self.assertEqual(f_very_stale["freshness_state"], "VERY_STALE")

    # -------------------------------------------------------------------------
    # 16. IDENTICAL SNAPSHOT (Duplicate Elimination)
    # -------------------------------------------------------------------------
    def test_16_identical_snapshot(self):
        """Identical provider payloads reuse the existing snapshot version without duplicating historical rows."""
        fix = Fixture(id=117, league_id=1, home_team_id=1, away_team_id=2, match_date=datetime.now(timezone.utc).replace(tzinfo=None), external_id="ESPN-FIX-4018017")
        self.db.add(fix)
        self.db.commit()

        mock_payload = {
            "header": {
                "id": "4018017",
                "competitions": [{
                    "id": "4018017",
                    "status": {"type": {"name": "STATUS_IN_PROGRESS", "state": "in"}, "displayClock": "15'", "clock": 900.0},
                    "competitors": [
                        {"homeAway": "home", "team": {"name": "Arsenal"}, "score": "0"},
                        {"homeAway": "away", "team": {"name": "Chelsea"}, "score": "0"}
                    ]
                }]
            }
        }

        with patch("httpx.Client.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_payload
            mock_get.return_value = mock_resp

            # Call 1
            res1 = LiveProviderAdapterService.fetch_live_summary(self.db, 117)
            # Call 2 (identical)
            res2 = LiveProviderAdapterService.fetch_live_summary(self.db, 117)

            self.assertEqual(res1["snapshot_version"], res2["snapshot_version"])
            snapshots = self.db.query(LiveObservedSnapshot).filter(LiveObservedSnapshot.fixture_id == 117).all()
            self.assertEqual(len(snapshots), 1)

    # -------------------------------------------------------------------------
    # 17. CHANGED SNAPSHOT (Monotonic Increment)
    # -------------------------------------------------------------------------
    def test_17_changed_snapshot(self):
        """A change in observed state increments the snapshot version monotonically."""
        fix = Fixture(id=118, league_id=1, home_team_id=1, away_team_id=2, match_date=datetime.now(timezone.utc).replace(tzinfo=None), external_id="ESPN-FIX-4018018")
        self.db.add(fix)
        self.db.commit()

        p1 = {
            "header": {
                "id": "4018018",
                "competitions": [{
                    "id": "4018018",
                    "status": {"type": {"name": "STATUS_IN_PROGRESS", "state": "in"}, "displayClock": "10'", "clock": 600.0},
                    "competitors": [
                        {"homeAway": "home", "team": {"name": "Arsenal"}, "score": "0"},
                        {"homeAway": "away", "team": {"name": "Chelsea"}, "score": "0"}
                    ]
                }]
            }
        }

        p2 = {
            "header": {
                "id": "4018018",
                "competitions": [{
                    "id": "4018018",
                    "status": {"type": {"name": "STATUS_IN_PROGRESS", "state": "in"}, "displayClock": "25'", "clock": 1500.0},
                    "competitors": [
                        {"homeAway": "home", "team": {"name": "Arsenal"}, "score": "1"}, # GOAL!
                        {"homeAway": "away", "team": {"name": "Chelsea"}, "score": "0"}
                    ]
                }]
            }
        }

        with patch("httpx.Client.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            mock_resp.json.return_value = p1
            mock_get.return_value = mock_resp
            res1 = LiveProviderAdapterService.fetch_live_summary(self.db, 118)

            mock_resp.json.return_value = p2
            mock_get.return_value = mock_resp
            res2 = LiveProviderAdapterService.fetch_live_summary(self.db, 118)

            self.assertEqual(res1["snapshot_version"], 1)
            self.assertEqual(res2["snapshot_version"], 2)
            self.assertNotEqual(res1["snapshot_hash"], res2["snapshot_hash"])

    # -------------------------------------------------------------------------
    # 18. H2H VENUE INVERSION
    # -------------------------------------------------------------------------
    def test_18_h2h_venue_inversion(self):
        """Historical H2H calculates correctly when current Home played Away historically."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Current: Arsenal (home) vs Chelsea (away)
        curr = Fixture(id=119, league_id=1, home_team_id=1, away_team_id=2, match_date=now)
        # Past: Chelsea hosted Arsenal and Arsenal won 2-1
        past = Fixture(
            id=120, league_id=1, home_team_id=2, away_team_id=1,
            match_date=now - timedelta(days=90), status="FINISHED",
            home_score=1, away_score=2
        )
        self.db.add_all([curr, past])
        self.db.commit()

        resp = self.client.get("/api/fixtures/119/details")
        self.assertEqual(resp.status_code, 200)
        h2h_sum = resp.json()["h2h_summary"]
        # From perspective of current fixture (Arsenal is Home):
        self.assertEqual(h2h_sum["home_wins"], 1)
        self.assertEqual(h2h_sum["away_wins"], 0)
        self.assertEqual(h2h_sum["home_goals"], 2)
        self.assertEqual(h2h_sum["away_goals"], 1)

    # -------------------------------------------------------------------------
    # 19. MISSING STATISTICS (Zero Fabrication)
    # -------------------------------------------------------------------------
    def test_19_missing_statistics(self):
        """Missing statistics strictly return None/UNAVAILABLE, never fake zeros or averages."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        fix = Fixture(
            id=125, league_id=1, home_team_id=1, away_team_id=2,
            match_date=now + timedelta(days=1), status="SCHEDULED",
            external_id="ESPN-FIX-4018025"
        )
        self.db.add(fix)
        self.db.commit()

        dq = DataQualityService.compute_fixture_data_quality(self.db, 125)
        self.assertEqual(dq["factors"]["statistical_coverage"]["status"], "MINIMAL")
        self.assertIn("unavailable", dq["explanation"].lower())

    # -------------------------------------------------------------------------
    # 20. FINISHED MATCH FREEZE
    # -------------------------------------------------------------------------
    def test_20_finished_match_freeze(self):
        """Finished match state is frozen: provider errors never reset score or status."""
        past_date = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        fix = Fixture(
            id=121, league_id=1, home_team_id=1, away_team_id=2,
            match_date=past_date, status="FINISHED",
            home_score=3, away_score=1, live_clock="FT", external_id="ESPN-FIX-4018021"
        )
        self.db.add(fix)
        self.db.commit()

        # Simulate provider outage
        with patch("httpx.Client.get", side_effect=httpx.ConnectError("Outage")):
            res = LiveProviderAdapterService.fetch_live_summary(self.db, 121)
            # Scores must not be wiped to 0-0 or None
            self.assertEqual(fix.home_score, 3)
            self.assertEqual(fix.away_score, 1)
            self.assertEqual(fix.status, "FINISHED")


if __name__ == "__main__":
    unittest.main()
