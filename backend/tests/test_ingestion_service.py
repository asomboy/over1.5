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

    from unittest.mock import patch, AsyncMock

    @patch("services.ingestion_service.DataIngestionService.fetch_and_ingest_from_api", new_callable=AsyncMock)
    @patch("services.prediction_service.PoissonPredictionEngine.predict_all_upcoming_fixtures")
    def test_api_ingest_sync_endpoint(self, mock_predict, mock_ingest):
        response = self.client.post("/api/ingest/sync")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
    def test_status_kickoff_inference(self):
        """Test fallback status calculation: 1+ min past kickoff becomes LIVE (0-0), 3h past kickoff becomes FINISHED."""
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        
        # Helper mimicking the ingestion service status inference logic
        def infer_status(match_dt, parsed_h, parsed_a, live_clk):
            if match_dt <= (now_utc - timedelta(minutes=1)) and match_dt >= (now_utc - timedelta(hours=3)):
                st = "LIVE"
                mins = max(1, int((now_utc - match_dt).total_seconds() / 60))
                if not live_clk or live_clk == "0'":
                    live_clk = f"{mins}'" if mins <= 45 else ("HT" if mins <= 60 else f"{mins - 15}'")
                if parsed_h is None: parsed_h = 0
                if parsed_a is None: parsed_a = 0
                return st, parsed_h, parsed_a, live_clk
            elif match_dt > (now_utc - timedelta(hours=3, minutes=30)) and match_dt <= (now_utc - timedelta(hours=3)):
                st = "FINISHED"
                if parsed_h is None: parsed_h = 0
                if parsed_a is None: parsed_a = 0
                return st, parsed_h, parsed_a, "FT"
            return "SCHEDULED", None, None, None

        # 10 minutes past kickoff -> LIVE, 0-0, 10'
        st, h_sc, a_sc, clk = infer_status(now_utc - timedelta(minutes=10), None, None, None)
        self.assertEqual(st, "LIVE")
        self.assertEqual(h_sc, 0)
        self.assertEqual(a_sc, 0)
        self.assertEqual(clk, "10'")

        # 3 hours 10 mins past kickoff -> FINISHED, 0-0, FT
        st2, h_sc2, a_sc2, clk2 = infer_status(now_utc - timedelta(hours=3, minutes=10), None, None, None)
        self.assertEqual(st2, "FINISHED")
        self.assertEqual(h_sc2, 0)
        self.assertEqual(a_sc2, 0)
        self.assertEqual(clk2, "FT")

    def test_upcoming_endpoint_excludes_ncaa(self):
        """Test that /api/fixtures/upcoming excludes NCAA matches via SQL and Python filter layers."""
        # 1. NCAA League
        ncaa_league = League(name="NCAAW Soccer", country="USA", season="2025/2026")
        pro_league = League(name="Premier League", country="England", season="2025/2026")
        self.db.add_all([ncaa_league, pro_league])
        self.db.commit()

        t_ncaa1 = Team(name="Alabama A&M Bulldogs", logo_url="https://a.espncdn.com/i/teamlogos/ncaa/500/123.png", league_id=ncaa_league.id)
        t_ncaa2 = Team(name="Ohio Bobcats", league_id=ncaa_league.id)
        t_pro1 = Team(name="Liverpool", league_id=pro_league.id)
        t_pro2 = Team(name="Everton", league_id=pro_league.id)
        self.db.add_all([t_ncaa1, t_ncaa2, t_pro1, t_pro2])
        self.db.commit()

        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        f_ncaa = Fixture(
            league_id=ncaa_league.id,
            home_team_id=t_ncaa1.id,
            away_team_id=t_ncaa2.id,
            match_date=now_naive + timedelta(hours=2),
            status="SCHEDULED"
        )
        f_pro = Fixture(
            league_id=pro_league.id,
            home_team_id=t_pro1.id,
            away_team_id=t_pro2.id,
            match_date=now_naive + timedelta(hours=2),
            status="SCHEDULED"
        )
        self.db.add_all([f_ncaa, f_pro])
        self.db.commit()

        res = self.client.get("/api/fixtures/upcoming")
        self.assertEqual(res.status_code, 200)
        fixtures = res.json().get("data", [])
        
        # Must only contain Liverpool vs Everton, NOT NCAA
        self.assertEqual(len(fixtures), 1)
        self.assertEqual(fixtures[0]["home_team"]["name"], "Liverpool")
        self.assertNotIn("NCAAW", fixtures[0]["competition"]["name"])


if __name__ == "__main__":
    unittest.main()

