import os
import sys
import unittest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base, get_db
from models import League, Team, Fixture, HistoricalResult, TeamStatistics, LeagueStatistics
from services.ingestion_service import DataIngestionService
from main import app


class TestIngestionService(unittest.TestCase):

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

    def test_ingest_leagues_and_teams_deduplication(self):
        # 1. Ingest Leagues with external_id
        leagues_data = [
            {"external_id": "PL", "name": "Premier League", "country": "England", "season": "2025/2026"},
            {"external_id": "PL", "name": "Premier League", "country": "England", "season": "2025/2026"}
        ]
        leagues = DataIngestionService.ingest_leagues(self.db, leagues_data)
        self.assertEqual(len(leagues), 2)
        
        # Database check: total stored leagues should be 1
        all_leagues = self.db.query(League).all()
        self.assertEqual(len(all_leagues), 1)
        self.assertEqual(all_leagues[0].name, "Premier League")

        # 2. Ingest Teams with duplicate prevention
        teams_data = [
            {"external_id": "ARS", "name": "Arsenal", "short_code": "ARS", "league_id": all_leagues[0].id},
            {"external_id": "ARS", "name": "Arsenal FC", "short_code": "ARS", "league_id": all_leagues[0].id},
            {"external_id": "CHE", "name": "Chelsea", "short_code": "CHE", "league_id": all_leagues[0].id}
        ]
        teams = DataIngestionService.ingest_teams(self.db, teams_data)
        all_teams = self.db.query(Team).all()
        self.assertEqual(len(all_teams), 2)
        
        arsenal = self.db.query(Team).filter(Team.external_id == "ARS").first()
        self.assertIsNotNone(arsenal)
        assert arsenal is not None
        self.assertEqual(arsenal.name, "Arsenal FC")  # Updated on 2nd ingestion

    def test_ingest_fixtures_upcoming_and_update_to_finished(self):
        league = League(external_id="SA", name="Serie A", country="Italy", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        team_a = Team(external_id="INT", name="Inter", league_id=league.id)
        team_b = Team(external_id="MIL", name="Milan", league_id=league.id)
        self.db.add_all([team_a, team_b])
        self.db.commit()

        match_time = datetime.now(timezone.utc)

        # Step 1: Ingest upcoming scheduled fixture
        fixture_payload = [
            {
                "external_id": "DERBY-01",
                "league_id": league.id,
                "home_team_id": team_a.id,
                "away_team_id": team_b.id,
                "match_date": match_time.isoformat(),
                "status": "SCHEDULED",
                "venue": "San Siro"
            }
        ]
        ingested = DataIngestionService.ingest_fixtures(self.db, fixture_payload)
        self.assertEqual(len(ingested), 1)

        stored_fixture = self.db.query(Fixture).first()
        self.assertIsNotNone(stored_fixture)
        assert stored_fixture is not None
        self.assertEqual(stored_fixture.status, "SCHEDULED")
        self.assertIsNone(stored_fixture.historical_result)

        # Step 2: Re-ingest fixture as FINISHED with scores
        updated_payload = [
            {
                "external_id": "DERBY-01",
                "league_id": league.id,
                "home_team_id": team_a.id,
                "away_team_id": team_b.id,
                "match_date": match_time.isoformat(),
                "status": "FINISHED",
                "home_score": 2,
                "away_score": 1,
                "venue": "San Siro"
            }
        ]
        ingested_updated = DataIngestionService.ingest_fixtures(self.db, updated_payload)
        self.assertEqual(len(ingested_updated), 1)

        # Ensure no duplicate fixture created
        all_fixtures = self.db.query(Fixture).all()
        self.assertEqual(len(all_fixtures), 1)
        self.assertEqual(all_fixtures[0].status, "FINISHED")

        # HistoricalResult record should exist and be updated
        result = self.db.query(HistoricalResult).first()
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.home_score, 2)
        self.assertEqual(result.away_score, 1)

        # Team statistics should have auto-updated
        stat_a = self.db.query(TeamStatistics).filter(TeamStatistics.team_id == team_a.id).first()
        self.assertIsNotNone(stat_a)
        assert stat_a is not None
        self.assertEqual(stat_a.matches_analyzed_home, 1)
        self.assertEqual(stat_a.avg_home_goals_scored, 2.0)

    def test_api_ingest_sync_endpoint(self):
        response = self.client.post("/api/ingest/sync")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("message", data)


if __name__ == "__main__":
    unittest.main()
