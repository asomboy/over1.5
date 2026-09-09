import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base, get_db
import models
from main import app


class TestPhase13H2HAndProvenance(unittest.TestCase):
    """
    Phase 13 Mandatory Test Suite:
    - H2H strictly fixture-relative re-orientation:
      Home and Away stats calculated from current fixture orientation, NOT historical venue.
    - Historical provenance preservation: historical fixture ID, date, historical home team,
      historical away team, score, competition.
    - Exclusion of unverified records (missing scores / teams).
    """

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

    def test_fixture_relative_h2h_orientation(self):
        """
        Current Fixture: Team X (Home) vs Team Y (Away)

        Historical match 1 (Venue inverted):
        Team Y (historical home) 2 - 1 Team X (historical away)
        -> From current fixture's perspective:
           Current Home (X) scored 1, Current Away (Y) scored 2.
           Outcome: AWAY_WIN (Team Y won)

        Historical match 2 (Same venue):
        Team X (historical home) 3 - 0 Team Y (historical away)
        -> From current fixture's perspective:
           Current Home (X) scored 3, Current Away (Y) scored 0.
           Outcome: HOME_WIN (Team X won)

        Historical match 3 (Venue inverted):
        Team Y (historical home) 1 - 1 Team X (historical away)
        -> Outcome: DRAW. Both scored 1.

        Summary:
        - total_matches: 3
        - home_wins: 1
        - draws: 1
        - away_wins: 1
        - home_goals: 1 + 3 + 1 = 5
        - away_goals: 2 + 0 + 1 = 3
        """
        league = models.League(name="Spanish LALIGA", country="Spain", season="2025/2026")
        self.db.add(league)
        self.db.flush()

        team_x = models.Team(name="Team X", short_code="TMX", league_id=league.id)
        team_y = models.Team(name="Team Y", short_code="TMY", league_id=league.id)
        self.db.add_all([team_x, team_y])
        self.db.flush()

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Historical 1 (Inverted venue)
        h1 = models.Fixture(
            league_id=league.id,
            home_team_id=team_y.id,
            away_team_id=team_x.id,
            match_date=now - timedelta(days=90),
            status="FINISHED",
            home_score=2,
            away_score=1
        )
        # Historical 2 (Standard venue)
        h2 = models.Fixture(
            league_id=league.id,
            home_team_id=team_x.id,
            away_team_id=team_y.id,
            match_date=now - timedelta(days=60),
            status="FINISHED",
            home_score=3,
            away_score=0
        )
        # Historical 3 (Inverted venue draw)
        h3 = models.Fixture(
            league_id=league.id,
            home_team_id=team_y.id,
            away_team_id=team_x.id,
            match_date=now - timedelta(days=30),
            status="FINISHED",
            home_score=1,
            away_score=1
        )
        # Unverified historical match (missing score) - MUST BE EXCLUDED
        h_unverified = models.Fixture(
            league_id=league.id,
            home_team_id=team_x.id,
            away_team_id=team_y.id,
            match_date=now - timedelta(days=120),
            status="FINISHED",
            home_score=None,
            away_score=None
        )

        # Current upcoming fixture
        f_current = models.Fixture(
            league_id=league.id,
            home_team_id=team_x.id,
            away_team_id=team_y.id,
            match_date=now + timedelta(days=2),
            status="SCHEDULED"
        )
        self.db.add_all([h1, h2, h3, h_unverified, f_current])
        self.db.commit()

        # Query details endpoint
        res = self.client.get(f"/api/fixtures/{f_current.id}/details")
        self.assertEqual(res.status_code, 200)
        data = res.json()

        # Verify H2H Summary
        h2h_summary = data.get("h2h_summary", {})
        self.assertEqual(h2h_summary.get("total_matches"), 3)
        self.assertEqual(h2h_summary.get("home_wins"), 1)
        self.assertEqual(h2h_summary.get("draws"), 1)
        self.assertEqual(h2h_summary.get("away_wins"), 1)
        self.assertEqual(h2h_summary.get("home_goals"), 5)
        self.assertEqual(h2h_summary.get("away_goals"), 3)
        self.assertTrue(h2h_summary.get("relative_orientation_verified"))

        # Verify H2H History Records retain provenance
        h2h_history = data.get("h2h_history", [])
        self.assertEqual(len(h2h_history), 3)

        for record in h2h_history:
            self.assertIn("historical_fixture_id", record)
            self.assertIn("date", record)
            self.assertIn("historical_home_team", record)
            self.assertIn("historical_away_team", record)
            self.assertIn("score", record)
            self.assertIn("competition", record)
            self.assertTrue(record.get("verified"))
            self.assertIn("current_fixture_relative", record)

        # Check the inverted match specifically (h1: Team Y 2 - 1 Team X)
        rec_h1 = next(r for r in h2h_history if r["historical_fixture_id"] == h1.id)
        self.assertEqual(rec_h1["historical_home_team"], "Team Y")
        self.assertEqual(rec_h1["historical_away_team"], "Team X")
        self.assertEqual(rec_h1["score"], "2-1")
        rel = rec_h1["current_fixture_relative"]
        self.assertEqual(rel["fixture_home_team"], "Team X")
        self.assertEqual(rel["fixture_away_team"], "Team Y")
        self.assertEqual(rel["fixture_home_goals"], 1)
        self.assertEqual(rel["fixture_away_goals"], 2)
        self.assertEqual(rel["outcome"], "AWAY_WIN")


if __name__ == "__main__":
    unittest.main()
