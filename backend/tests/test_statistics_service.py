import os
import sys
import unittest
from typing import cast
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base, get_db
from models import League, Team, Fixture, HistoricalResult, TeamStatistics, LeagueStatistics
from services.statistics_service import (
    calculate_team_statistics,
    calculate_all_team_statistics,
    get_team_statistics,
    get_all_team_statistics,
    calculate_league_statistics,
    calculate_all_league_statistics,
    get_league_statistics,
    get_all_league_statistics,
)
from main import app


class TestStatisticsService(unittest.TestCase):

    def setUp(self):
        # In-memory SQLite DB with StaticPool for multi-threaded testing
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

    def test_calculate_team_statistics_defaults_to_last_10(self):
        # Create League
        league = League(name="Premier League", country="England", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        # Create Teams
        team_a = Team(name="Arsenal", short_code="ARS", league_id=league.id)
        team_b = Team(name="Chelsea", short_code="CHE", league_id=league.id)
        self.db.add_all([team_a, team_b])
        self.db.commit()

        base_date = datetime.now(timezone.utc) - timedelta(days=30)

        # Add 12 completed home matches for Arsenal (team_a as home_team)
        # The last 10 should be analyzed (indices 2 to 11)
        home_scores = [1, 0,  3, 2, 4, 1, 2, 3, 1, 0, 2, 3]  # last 10: sum=21, avg=2.1
        home_conceded = [0, 0, 1, 1, 2, 0, 1, 1, 0, 1, 0, 1] # last 10: sum=8, avg=0.8

        for i in range(12):
            fixture = Fixture(
                league_id=league.id,
                home_team_id=team_a.id,
                away_team_id=team_b.id,
                match_date=base_date + timedelta(days=i),
                status="FINISHED",
            )
            self.db.add(fixture)
            self.db.commit()

            result = HistoricalResult(
                fixture_id=fixture.id,
                home_score=home_scores[i],
                away_score=home_conceded[i],
                total_goals=home_scores[i] + home_conceded[i],
            )
            self.db.add(result)
            self.db.commit()

        # Add 5 completed away matches for Arsenal (team_a as away_team)
        away_scores = [2, 1, 3, 2, 0]   # Arsenal scored away => sum=8, avg=1.6
        away_conceded = [1, 0, 1, 2, 1] # Arsenal conceded away => sum=5, avg=1.0

        for i in range(5):
            fixture = Fixture(
                league_id=league.id,
                home_team_id=team_b.id,
                away_team_id=team_a.id,
                match_date=base_date + timedelta(days=15 + i),
                status="FINISHED",
            )
            self.db.add(fixture)
            self.db.commit()

            result = HistoricalResult(
                fixture_id=fixture.id,
                home_score=away_conceded[i],
                away_score=away_scores[i],
                total_goals=away_scores[i] + away_conceded[i],
            )
            self.db.add(result)
            self.db.commit()

        # Calculate statistics for Arsenal
        stats = calculate_team_statistics(self.db, team_id=cast(int, team_a.id), last_n_matches=10)

        self.assertIsNotNone(stats)
        assert stats is not None
        self.assertEqual(stats.team_id, team_a.id)
        self.assertEqual(stats.matches_analyzed_home, 10)
        self.assertEqual(stats.matches_analyzed_away, 5)
        self.assertEqual(stats.avg_home_goals_scored, 2.1)
        self.assertEqual(stats.avg_home_goals_conceded, 0.8)
        self.assertEqual(stats.avg_away_goals_scored, 1.6)
        self.assertEqual(stats.avg_away_goals_conceded, 1.0)
        self.assertGreater(stats.home_attack_strength, 0.0)
        self.assertGreater(stats.home_defense_strength, 0.0)
        self.assertGreater(stats.away_attack_strength, 0.0)
        self.assertGreater(stats.away_defense_strength, 0.0)

    def test_strength_calculations_and_storage(self):
        league = League(name="Eredivisie", country="Netherlands", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        team_a = Team(name="Ajax", short_code="AJX", league_id=league.id)
        team_b = Team(name="PSV", short_code="PSV", league_id=league.id)
        self.db.add_all([team_a, team_b])
        self.db.commit()

        base_date = datetime.now(timezone.utc) - timedelta(days=10)

        # Match 1: Ajax (home) 2 - 0 PSV (away)
        f1 = Fixture(league_id=league.id, home_team_id=team_a.id, away_team_id=team_b.id, match_date=base_date, status="FINISHED")
        self.db.add(f1)
        self.db.commit()
        r1 = HistoricalResult(fixture_id=f1.id, home_score=2, away_score=0, total_goals=2)
        self.db.add(r1)
        self.db.commit()

        # Match 2: PSV (home) 1 - 1 Ajax (away)
        f2 = Fixture(league_id=league.id, home_team_id=team_b.id, away_team_id=team_a.id, match_date=base_date + timedelta(days=5), status="FINISHED")
        self.db.add(f2)
        self.db.commit()
        r2 = HistoricalResult(fixture_id=f2.id, home_score=1, away_score=1, total_goals=2)
        self.db.add(r2)
        self.db.commit()

        # Calculate statistics for Ajax (team_a)
        stats_a = calculate_team_statistics(self.db, team_id=cast(int, team_a.id))
        self.assertIsNotNone(stats_a)
        assert stats_a is not None
        self.assertEqual(stats_a.home_attack_strength, 1.3333)
        self.assertEqual(stats_a.home_defense_strength, 0.0)
        self.assertEqual(stats_a.away_attack_strength, 2.0)
        self.assertEqual(stats_a.away_defense_strength, 0.6667)

        # Calculate statistics for PSV (team_b)
        stats_b = calculate_team_statistics(self.db, team_id=cast(int, team_b.id))
        self.assertIsNotNone(stats_b)
        assert stats_b is not None
        self.assertEqual(stats_b.home_attack_strength, 0.6667)
        self.assertEqual(stats_b.home_defense_strength, 2.0)
        self.assertEqual(stats_b.away_attack_strength, 0.0)
        self.assertEqual(stats_b.away_defense_strength, 1.3333)

        # Query TeamStatistics table directly from DB to verify persistent storage
        stored_stat_a = self.db.query(TeamStatistics).filter(TeamStatistics.team_id == team_a.id).first()
        self.assertIsNotNone(stored_stat_a)
        assert stored_stat_a is not None
        self.assertEqual(stored_stat_a.home_attack_strength, 1.3333)
        self.assertEqual(stored_stat_a.home_defense_strength, 0.0)
        self.assertEqual(stored_stat_a.away_attack_strength, 2.0)
        self.assertEqual(stored_stat_a.away_defense_strength, 0.6667)

    def test_calculate_team_statistics_no_matches(self):
        league = League(name="La Liga", country="Spain", season="2025/2026")
        team = Team(name="Getafe", short_code="GET", league_id=league.id)
        self.db.add_all([league, team])
        self.db.commit()

        stats = calculate_team_statistics(self.db, team_id=cast(int, team.id))

        self.assertIsNotNone(stats)
        assert stats is not None
        self.assertEqual(stats.matches_analyzed_home, 0)
        self.assertEqual(stats.matches_analyzed_away, 0)
        self.assertEqual(stats.avg_home_goals_scored, 0.0)
        self.assertEqual(stats.avg_home_goals_conceded, 0.0)
        self.assertEqual(stats.avg_away_goals_scored, 0.0)
        self.assertEqual(stats.avg_away_goals_conceded, 0.0)
        self.assertEqual(stats.home_attack_strength, 1.0)
        self.assertEqual(stats.home_defense_strength, 1.0)
        self.assertEqual(stats.away_attack_strength, 1.0)
        self.assertEqual(stats.away_defense_strength, 1.0)

    def test_api_statistics_endpoints(self):
        league = League(name="Serie A", country="Italy", season="2025/2026")
        team1 = Team(name="Milan", short_code="MIL", league_id=league.id)
        team2 = Team(name="Inter", short_code="INT", league_id=league.id)
        self.db.add_all([league, team1, team2])
        self.db.commit()

        # Recalculate via POST endpoint
        response = self.client.post("/api/statistics/recalculate")
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertEqual(res_data["status"], "ok")
        self.assertEqual(len(res_data["data"]), 2)

        # Get all statistics via GET endpoint
        response_get = self.client.get("/api/statistics")
        self.assertEqual(response_get.status_code, 200)
        all_stats = response_get.json()
        self.assertEqual(all_stats["count"], 2)

        # Get specific team statistics via GET endpoint
        response_team = self.client.get(f"/api/statistics/{team1.id}")
        self.assertEqual(response_team.status_code, 200)
        team_stat = response_team.json()
        self.assertEqual(team_stat["data"]["team_id"], team1.id)

    def test_calculate_league_statistics(self):
        league = League(name="Bundesliga", country="Germany", season="2025/2026")
        team1 = Team(name="Bayern", short_code="BAY", league_id=league.id)
        team2 = Team(name="Dortmund", short_code="BVB", league_id=league.id)
        self.db.add_all([league, team1, team2])
        self.db.commit()

        base_date = datetime.now(timezone.utc) - timedelta(days=20)

        # Match 1: Bayern (home) 3 - 1 Dortmund (away)
        f1 = Fixture(league_id=league.id, home_team_id=team1.id, away_team_id=team2.id, match_date=base_date, status="FINISHED")
        self.db.add(f1)
        self.db.commit()
        r1 = HistoricalResult(fixture_id=f1.id, home_score=3, away_score=1, total_goals=4)
        self.db.add(r1)
        self.db.commit()

        # Match 2: Dortmund (home) 2 - 2 Bayern (away)
        f2 = Fixture(league_id=league.id, home_team_id=team2.id, away_team_id=team1.id, match_date=base_date + timedelta(days=7), status="FINISHED")
        self.db.add(f2)
        self.db.commit()
        r2 = HistoricalResult(fixture_id=f2.id, home_score=2, away_score=2, total_goals=4)
        self.db.add(r2)
        self.db.commit()

        # Calculate league statistics
        l_stats = calculate_league_statistics(self.db, cast(int, league.id))
        self.assertIsNotNone(l_stats)
        assert l_stats is not None
        self.assertEqual(l_stats.league_id, league.id)
        self.assertEqual(l_stats.total_matches_analyzed, 2)
        self.assertEqual(l_stats.avg_home_goals, 2.5)  # (3 + 2) / 2 = 2.5
        self.assertEqual(l_stats.avg_away_goals, 1.5)  # (1 + 2) / 2 = 1.5

    def test_api_league_statistics_endpoints(self):
        league = League(name="Ligue 1", country="France", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        # Recalculate via POST endpoint
        response_post = self.client.post("/api/statistics/league/recalculate")
        self.assertEqual(response_post.status_code, 200)
        res_data = response_post.json()
        self.assertEqual(res_data["status"], "ok")

        # Get all league statistics via GET endpoint
        response_get = self.client.get("/api/statistics/league")
        self.assertEqual(response_get.status_code, 200)
        get_data = response_get.json()
        self.assertEqual(get_data["status"], "ok")

        # Get specific league statistics via GET endpoint
        response_single = self.client.get(f"/api/statistics/league/{league.id}")
        self.assertEqual(response_single.status_code, 200)
        single_data = response_single.json()
        self.assertEqual(single_data["data"]["league_id"], league.id)



if __name__ == "__main__":
    unittest.main()
