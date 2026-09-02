import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
import json

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from main import app
from database import get_db, Base
import models
from services.canonical_competition_service import CanonicalCompetitionService


class TestFixtureDataIntegrity(unittest.TestCase):
    """
    Phase 12.1 Production Data Integrity & Canonical Fixture Contract Test Suite.
    Verifies:
    1. Canonical fixture schema compliance.
    2. Explicit 404 error contract for non-existent fixtures.
    3. Elimination of fake fallbacks ('Home Team', 'Away Team', fabricated constants).
    4. Multi-endpoint consistency across Intelligence, Shots, Match Statistics, Decision Engine.
    5. Clean H2H empty states and accurate country/competition derivation.
    """

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool
        )
        models.Base.metadata.create_all(bind=cls.engine)

        def override_get_db():
            db = Session(cls.engine)
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()

    def setUp(self):
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.query(models.PredictionDecisionSnapshot).delete()
        self.db.query(models.MatchStatisticsPredictionSnapshot).delete()
        self.db.query(models.ShotPredictionSnapshot).delete()
        self.db.query(models.ModelEvaluation).delete()
        self.db.query(models.Prediction).delete()
        self.db.query(models.Fixture).delete()
        self.db.query(models.Team).delete()
        self.db.query(models.League).delete()
        self.db.commit()
        self.db.close()

    def test_fixture_not_found_404_contract(self):
        """Invalid or non-existent fixture IDs must return 404 with FIXTURE_NOT_FOUND contract."""
        resp = self.client.get("/api/fixtures/999999/details")
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertEqual(data.get("status"), "FIXTURE_NOT_FOUND")
        self.assertIn("not be found", data.get("message", "").lower())

    def test_canonical_fixture_details_response_contract(self):
        """Valid fixture must return canonical response shape with exact entity IDs and no fabricated names."""
        league = models.League(
            name="Spanish LALIGA",
            country="Spain",
            season="2025/2026"
        )
        self.db.add(league)
        self.db.commit()

        home_team = models.Team(
            name="Real Madrid",
            short_code="RMA",
            logo_url="https://example.com/rma.png"
        )
        away_team = models.Team(
            name="Barcelona",
            short_code="BAR",
            logo_url="https://example.com/bar.png"
        )
        self.db.add_all([home_team, away_team])
        self.db.commit()

        match_time = datetime(2026, 9, 5, 20, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)
        fixture = models.Fixture(
            external_id="ESPN-TEST-001",
            league_id=league.id,
            home_team_id=home_team.id,
            away_team_id=away_team.id,
            match_date=match_time,
            status="SCHEDULED",
            venue="Santiago Bernabeu"
        )
        self.db.add(fixture)
        self.db.commit()

        resp = self.client.get(f"/api/fixtures/{fixture.id}/details")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Check canonical top-level fields
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("id"), fixture.id)
        self.assertEqual(data.get("fixture_id"), fixture.id)
        self.assertEqual(data.get("provider_fixture_id"), "ESPN-TEST-001")
        self.assertEqual(data.get("match_status"), "SCHEDULED")

        # Home team verification
        self.assertIsNotNone(data.get("home_team"))
        self.assertEqual(data["home_team"]["id"], home_team.id)
        self.assertEqual(data["home_team"]["name"], "Real Madrid")
        self.assertEqual(data["home_team"]["logo"], "https://example.com/rma.png")

        # Away team verification
        self.assertIsNotNone(data.get("away_team"))
        self.assertEqual(data["away_team"]["id"], away_team.id)
        self.assertEqual(data["away_team"]["name"], "Barcelona")
        self.assertEqual(data["away_team"]["logo"], "https://example.com/bar.png")

        # Competition verification
        self.assertIsNotNone(data.get("competition"))
        self.assertEqual(data["competition"]["id"], league.id)
        self.assertEqual(data["competition"]["name"], "Spanish LALIGA")
        self.assertEqual(data["competition"]["country"], "Spain")
        self.assertEqual(data["competition"]["country_code"], "ES")

    def test_no_fabricated_fallback_strings(self):
        """When teams have null optional fields like short_code or logo_url, null/None is preserved without fake strings."""
        league = models.League(
            name="Test League",
            country="Test Country"
        )
        t_home = models.Team(name="Home Club", short_code=None, logo_url=None)
        t_away = models.Team(name="Away Club", short_code=None, logo_url=None)
        self.db.add_all([league, t_home, t_away])
        self.db.commit()

        match_time = datetime(2026, 9, 6, 15, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)
        fixture = models.Fixture(
            external_id="ESPN-TEST-PARTIAL",
            league_id=league.id,
            home_team_id=t_home.id,
            away_team_id=t_away.id,
            match_date=match_time,
            status="SCHEDULED"
        )
        self.db.add(fixture)
        self.db.commit()

        resp = self.client.get(f"/api/fixtures/{fixture.id}/details")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Must preserve real names and keep null fields as None without fake fallbacks
        self.assertEqual(data["home_team"]["name"], "Home Club")
        self.assertEqual(data["away_team"]["name"], "Away Club")
        self.assertIsNone(data["home_team"]["short_code"])
        self.assertIsNone(data["away_team"]["short_code"])
        self.assertIsNone(data["home_team"]["logo"])
        self.assertIsNone(data["away_team"]["logo"])

    def test_h2h_history_empty_and_populated_states(self):
        """H2H history handles both 0 matches and multiple past matches cleanly without crash or fabricated items."""
        t1 = models.Team(name="Arsenal", short_code="ARS")
        t2 = models.Team(name="Chelsea", short_code="CHE")
        l = models.League(name="English Premier League", country="England")
        self.db.add_all([t1, t2, l])
        self.db.commit()

        f_current = models.Fixture(
            external_id="TEST-H2H-CURR",
            league_id=l.id,
            home_team_id=t1.id,
            away_team_id=t2.id,
            match_date=datetime(2026, 9, 10, 16, 30, 0),
            status="SCHEDULED"
        )
        self.db.add(f_current)
        self.db.commit()

        # 1. Zero H2H matches recorded
        resp = self.client.get(f"/api/fixtures/{f_current.id}/details")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("h2h_history"), [])

        # 2. Add past completed H2H match
        past_fix = models.Fixture(
            external_id="TEST-H2H-PAST-1",
            league_id=l.id,
            home_team_id=t2.id,
            away_team_id=t1.id,
            match_date=datetime(2026, 3, 15, 15, 0, 0),
            status="FINISHED",
            home_score=2,
            away_score=1
        )
        self.db.add(past_fix)
        self.db.commit()

        resp2 = self.client.get(f"/api/fixtures/{f_current.id}/details")
        self.assertEqual(resp2.status_code, 200)
        h2h = resp2.json().get("h2h_history")
        self.assertEqual(len(h2h), 1)
        self.assertEqual(h2h[0]["score"], "2-1")
        self.assertEqual(h2h[0]["total_goals"], 3)
        self.assertEqual(h2h[0]["home_team_name"], "Chelsea")
        self.assertEqual(h2h[0]["away_team_name"], "Arsenal")

    def test_canonical_competition_resolution_no_hallucinated_country(self):
        """Unknown or generic international tournaments must not invent arbitrary national countries."""
        ident_intl = CanonicalCompetitionService.resolve_competition(league_name="UEFA Champions League")
        self.assertEqual(ident_intl.country_name, "Europe")
        self.assertEqual(ident_intl.country_code, "EU")

        ident_scot = CanonicalCompetitionService.resolve_competition(league_name="Scottish Premiership")
        self.assertEqual(ident_scot.country_name, "Scotland")
        self.assertEqual(ident_scot.country_code, "GB-SCT")

        ident_gen = CanonicalCompetitionService.resolve_competition(league_name="International Friendly Match")
        self.assertEqual(ident_gen.country_name, "International")

    def test_phase_9_to_12_endpoint_compatibility(self):
        """Valid fixture returns valid schemas across Phase 9-12 endpoints without breaking contracts."""
        l = models.League(name="Italian Serie A", country="Italy")
        t1 = models.Team(name="Juventus", short_code="JUV")
        t2 = models.Team(name="AC Milan", short_code="MIL")
        self.db.add_all([l, t1, t2])
        self.db.commit()

        f = models.Fixture(
            external_id="TEST-SERIE-A",
            league_id=l.id,
            home_team_id=t1.id,
            away_team_id=t2.id,
            match_date=datetime(2026, 9, 12, 19, 45, 0),
            status="SCHEDULED"
        )
        self.db.add(f)
        self.db.commit()

        # Match Intelligence
        resp_intel = self.client.get(f"/api/fixtures/{f.id}/match-intelligence")
        self.assertIn(resp_intel.status_code, [200, 404])

        # Shots
        resp_shots = self.client.get(f"/api/fixtures/{f.id}/shots")
        self.assertEqual(resp_shots.status_code, 200)
        self.assertEqual(resp_shots.json().get("status"), "AVAILABLE")

        # Match Statistics
        resp_stats = self.client.get(f"/api/fixtures/{f.id}/match-statistics")
        self.assertEqual(resp_stats.status_code, 200)
        self.assertEqual(resp_stats.json().get("status"), "AVAILABLE")

        # Decision Engine
        resp_dec = self.client.get(f"/api/fixtures/{f.id}/decision")
        self.assertEqual(resp_dec.status_code, 200)
        self.assertIn("production_status", resp_dec.json())


if __name__ == "__main__":
    unittest.main()
