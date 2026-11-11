import os
import sys
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
    LiveMatchState, FeatureSnapshot, LeagueStatistics, TeamStatistics, Prediction
)
from services.canonical_competition_service import CanonicalCompetitionService
from services.fixture_identity_service import FixtureIdentityService
from services.fixture_lifecycle_service import FixtureLifecycleService, CanonicalLifecycleState
from services.statistics_service import calculate_league_statistics, calculate_team_statistics
from services.validation_dataset_service import ValidationDatasetService
from services.live_provider_service import LiveProviderAdapterService
from services.live_service import LiveMatchIntelligenceService, LiveNarrativeEngine
from main import app


class TestPhase3Fixes(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = self.SessionLocal()

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

        self.league_eng = League(name="English Premier League", country="England", season="2025/2026")
        self.league_esp = League(name="Spanish LALIGA", country="Spain", season="2025/2026")
        self.db.add_all([self.league_eng, self.league_esp])
        self.db.commit()

        self.home = Team(name="Arsenal", league_id=self.league_eng.id)
        self.away = Team(name="Chelsea", league_id=self.league_eng.id)
        self.esp_home = Team(name="Real Madrid", league_id=self.league_esp.id)
        self.esp_away = Team(name="Barcelona", league_id=self.league_esp.id)
        self.db.add_all([self.home, self.away, self.esp_home, self.esp_away])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        app.dependency_overrides.clear()

    def test_canonical_competition_specific_before_generic_and_slug(self):
        res_at = CanonicalCompetitionService.resolve_competition(league_name="Austrian Bundesliga")
        self.assertEqual(res_at.country_name, "Austria")

        res_br = CanonicalCompetitionService.resolve_competition(league_name="Brazilian Serie A")
        self.assertEqual(res_br.country_name, "Brazil")

        res_ca = CanonicalCompetitionService.resolve_competition(league_name="Canadian Premier League")
        self.assertEqual(res_ca.country_name, "Canada")

        res_sco = CanonicalCompetitionService.resolve_competition(
            league_name="Regular Season",
            season_slug="scottish-premiership",
        )
        self.assertEqual(res_sco.country_name, "Scotland")

    def test_fixture_identity_team_name_matching(self):
        self.assertFalse(FixtureIdentityService.names_match("Manchester United", "Manchester City"))
        self.assertFalse(FixtureIdentityService.names_match("Real Madrid", "Real Betis"))
        self.assertTrue(FixtureIdentityService.names_match("Arsenal FC", "Arsenal"))
        self.assertTrue(FixtureIdentityService.names_match("Bayern Munich", "Bayern"))

    def test_fixture_lifecycle_word_token_precedence(self):
        self.assertEqual(
            FixtureLifecycleService.normalize_lifecycle_state("SUSPENDED"),
            CanonicalLifecycleState.SUSPENDED,
        )
        self.assertEqual(
            FixtureLifecycleService.normalize_lifecycle_state("HALF_TIME"),
            CanonicalLifecycleState.HALFTIME,
        )
        self.assertEqual(
            FixtureLifecycleService.normalize_lifecycle_state("STATUS_AET"),
            CanonicalLifecycleState.EXTRA_TIME,
        )
        self.assertEqual(
            FixtureLifecycleService.normalize_lifecycle_state("PEN"),
            CanonicalLifecycleState.PENALTY_SHOOTOUT,
        )
        self.assertEqual(
            FixtureLifecycleService.normalize_lifecycle_state("FULL_TIME"),
            CanonicalLifecycleState.FINISHED,
        )

    def test_statistics_ignores_non_finished_even_with_historical_rows(self):
        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
        f_fin = Fixture(
            league_id=self.league_eng.id, home_team_id=self.home.id, away_team_id=self.away.id,
            match_date=past - timedelta(days=1), status="FINISHED", home_score=2, away_score=1,
            external_id="ESPN-FIN-STATS",
        )
        f_live = Fixture(
            league_id=self.league_eng.id, home_team_id=self.home.id, away_team_id=self.away.id,
            match_date=past, status="LIVE", home_score=4, away_score=0,
            external_id="ESPN-LIVE-STATS",
        )
        f_canc = Fixture(
            league_id=self.league_eng.id, home_team_id=self.home.id, away_team_id=self.away.id,
            match_date=past + timedelta(hours=3), status="CANCELLED", home_score=5, away_score=5,
            external_id="ESPN-CANC-STATS",
        )
        self.db.add_all([f_fin, f_live, f_canc])
        self.db.commit()

        self.db.add_all([
            HistoricalResult(fixture_id=f_fin.id, home_score=2, away_score=1, total_goals=3),
            HistoricalResult(fixture_id=f_live.id, home_score=4, away_score=0, total_goals=4),
            HistoricalResult(fixture_id=f_canc.id, home_score=5, away_score=5, total_goals=10),
        ])
        self.db.commit()

        l_stat = calculate_league_statistics(self.db, self.league_eng.id)
        self.assertIsNotNone(l_stat)
        self.assertEqual(l_stat.total_matches_analyzed, 1)

        t_stat = calculate_team_statistics(self.db, self.home.id, league_stats=l_stat)
        self.assertIsNotNone(t_stat)
        self.assertEqual(t_stat.matches_analyzed_home, 1)
        self.assertAlmostEqual(t_stat.avg_home_goals_scored, 2.0, places=4)

    def test_validation_dataset_read_only_and_includes_red_cards(self):
        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        f = Fixture(
            league_id=self.league_eng.id, home_team_id=self.home.id, away_team_id=self.away.id,
            match_date=past, status="FINISHED", home_score=1, away_score=1,
            external_id="ESPN-VAL-REDCARD",
        )
        self.db.add(f)
        self.db.commit()

        self.db.add_all([
            HistoricalResult(fixture_id=f.id, home_score=1, away_score=1, total_goals=2),
            MatchStatistics(
                fixture_id=f.id,
                home_yellow_cards=2, away_yellow_cards=1,
                home_red_cards=1, away_red_cards=0,
            ),
        ])
        self.db.commit()

        self.assertEqual(self.db.query(FeatureSnapshot).count(), 0)
        dataset = ValidationDatasetService.build_chronological_dataset(self.db, persist_missing=False)
        self.assertEqual(len(dataset), 1)
        self.assertEqual(dataset[0]["actual_outcomes"]["total_cards"], 4)
        self.assertEqual(self.db.query(FeatureSnapshot).count(), 0)

        resp = self.client.get("/api/models/real-validation")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.db.query(FeatureSnapshot).count(), 0)

    def test_live_intelligence_captures_prev_state_before_overwrite(self):
        fix = Fixture(
            league_id=self.league_eng.id, home_team_id=self.home.id, away_team_id=self.away.id,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=30),
            status="LIVE", home_score=1, away_score=0, external_id="ESPN-LIVE-PREV",
        )
        self.db.add(fix)
        self.db.commit()

        # Prior state has fewer shots, corners, and red cards than incoming update
        prior = LiveMatchState(
            fixture_id=fix.id, minute=30, period="1H", status="LIVE",
            home_score=1, away_score=0,
            home_shots=4, away_shots=2,
            home_shots_on_target=2, away_shots_on_target=1,
            home_corners=2, away_corners=1,
            home_yellow_cards=1, away_yellow_cards=0,
            home_red_cards=0, away_red_cards=0,
        )
        self.db.add(prior)
        self.db.commit()

        incoming_prov = {
            "status": "LIVE",
            "is_completed": False,
            "minute": 35,
            "period": "1H",
            "display_clock": "35'",
            "score": {"home": 1, "away": 0},
            "statistics": {
                "shots": {"home": 6.0, "away": 3.0},
                "shots_on_target": {"home": 3.0, "away": 1.0},
                "corners": {"home": 4.0, "away": 1.0},
                "cards": {"home_yellow": 1.0, "away_yellow": 0.0, "home_red": 0.0, "away_red": 1.0},
            },
            "events": [],
            "freshness": {"status": "FRESH", "age_seconds": 5},
            "identity_validation": {"valid": True, "error": None},
        }

        def fake_fetch(db, fid, client=None):
            row = db.query(LiveMatchState).filter(LiveMatchState.fixture_id == fid).first()
            row.minute = 35
            row.home_shots = 6
            row.away_shots = 3
            row.home_corners = 4
            row.away_red_cards = 1
            db.commit()
            return incoming_prov

        captured = {}
        orig_gen = LiveNarrativeEngine.generate_narrative

        def spy_gen(fixture_id, home_team, away_team, live_prov, prev_state=None):
            captured["prev_state"] = prev_state
            return orig_gen(fixture_id, home_team, away_team, live_prov, prev_state)

        with patch.object(LiveProviderAdapterService, "fetch_live_summary", side_effect=fake_fetch), \
             patch.object(LiveNarrativeEngine, "generate_narrative", side_effect=spy_gen):
            intel = LiveMatchIntelligenceService.get_live_intelligence(self.db, fix.id)

        self.assertIsNotNone(intel)
        self.assertIsNotNone(captured.get("prev_state"))
        self.assertEqual(captured["prev_state"]["statistics"]["shots"]["home"], 4)
        self.assertEqual(captured["prev_state"]["statistics"]["corners"]["home"], 2)
        self.assertEqual(captured["prev_state"]["statistics"]["cards"]["away_red"], 0)
        joined = " ".join(
            (item.get("statement") or item.get("message") or str(item)) if isinstance(item, dict) else str(item)
            for item in intel.get("narrative", [])
        ).lower()
        self.assertTrue("red card" in joined and "corner" in joined, f"Expected delta narrative, got {intel.get('narrative')}")

    def test_live_provider_display_clock_and_missing_scores(self):
        fix = Fixture(
            league_id=self.league_eng.id, home_team_id=self.home.id, away_team_id=self.away.id,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=90),
            status="LIVE", external_id="ESPN-FIX-CLOCK1",
        )
        self.db.add(fix)
        self.db.commit()

        def make_payload(display_clock, raw_clock, include_scores=True):
            c_home = {"homeAway": "home", "team": {"name": "Arsenal"}}
            c_away = {"homeAway": "away", "team": {"name": "Chelsea"}}
            if include_scores:
                c_home["score"] = "1"
                c_away["score"] = "0"
            return {
                "header": {
                    "competitions": [{
                        "status": {
                            "clock": raw_clock,
                            "displayClock": display_clock,
                            "period": 2 if display_clock.startswith("90") else 1,
                            "type": {"name": "STATUS_IN_PROGRESS", "state": "in", "completed": False, "detail": display_clock},
                        },
                        "competitors": [c_home, c_away],
                        "details": [],
                    }]
                },
                "boxscore": {"teams": []},
            }

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = make_payload("90'+3'", 0.0, include_scores=False)
        mock_client.get.return_value = mock_resp

        res_93 = LiveProviderAdapterService.fetch_live_summary(self.db, fix.id, client=mock_client)
        self.assertEqual(res_93["minute"], 93)
        self.assertIsNone(res_93["score"]["home"])
        self.assertIsNone(res_93["score"]["away"])

        mock_resp.json.return_value = make_payload("1'", 60.0, include_scores=True)
        res_1 = LiveProviderAdapterService.fetch_live_summary(self.db, fix.id, client=mock_client)
        self.assertEqual(res_1["minute"], 1)
        self.assertEqual(res_1["score"]["home"], 1)
        self.assertEqual(res_1["score"]["away"], 0)

    def test_fixtures_endpoints_honor_sql_filters_without_bypass(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        day1 = (now + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
        day2 = (now + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)
        past1 = (now - timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
        past2 = (now - timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)

        u1 = Fixture(league_id=self.league_eng.id, home_team_id=self.home.id, away_team_id=self.away.id, match_date=day1, status="SCHEDULED", external_id="U1")
        u2 = Fixture(league_id=self.league_eng.id, home_team_id=self.away.id, away_team_id=self.home.id, match_date=day2, status="SCHEDULED", external_id="U2")
        u3 = Fixture(league_id=self.league_esp.id, home_team_id=self.esp_home.id, away_team_id=self.esp_away.id, match_date=day1, status="SCHEDULED", external_id="U3")

        p1 = Fixture(league_id=self.league_eng.id, home_team_id=self.home.id, away_team_id=self.away.id, match_date=past1, status="FINISHED", home_score=2, away_score=0, external_id="P1")
        p2 = Fixture(league_id=self.league_eng.id, home_team_id=self.away.id, away_team_id=self.home.id, match_date=past2, status="FINISHED", home_score=1, away_score=1, external_id="P2")
        p3 = Fixture(league_id=self.league_esp.id, home_team_id=self.esp_home.id, away_team_id=self.esp_away.id, match_date=past1, status="FINISHED", home_score=3, away_score=2, external_id="P3")

        self.db.add_all([u1, u2, u3, p1, p2, p3])
        self.db.commit()

        # Limit filter
        r_lim = self.client.get("/api/fixtures/upcoming?limit=2")
        self.assertEqual(r_lim.status_code, 200)
        self.assertEqual(len(r_lim.json()["data"]), 2)

        # League + date filters
        day1_str = day1.strftime("%Y-%m-%d")
        r_eng_d1 = self.client.get(f"/api/fixtures/upcoming?league=English%20Premier%20League&date={day1_str}")
        self.assertEqual(r_eng_d1.status_code, 200)
        d_eng = r_eng_d1.json()["data"]
        self.assertEqual(len(d_eng), 1)
        self.assertEqual(d_eng[0]["id"], u1.id)

        # Empty filtered query must NOT bypass and return unfiltered matches
        r_empty = self.client.get("/api/fixtures/upcoming?league=NonExistentLeagueXYZ")
        self.assertEqual(r_empty.status_code, 200)
        self.assertEqual(r_empty.json()["data"], [])

        # Finished endpoint filters
        r_fin_lim = self.client.get("/api/fixtures/finished?limit=1")
        self.assertEqual(r_fin_lim.status_code, 200)
        self.assertEqual(len(r_fin_lim.json()["data"]), 1)

        past1_str = past1.strftime("%Y-%m-%d")
        r_fin_esp = self.client.get(f"/api/fixtures/finished?league=Spanish%20LALIGA&date={past1_str}")
        self.assertEqual(r_fin_esp.status_code, 200)
        self.assertEqual([item["id"] for item in r_fin_esp.json()["data"]], [p3.id])

        r_fin_empty = self.client.get("/api/fixtures/finished?league=NonExistentLeagueXYZ")
        self.assertEqual(r_fin_empty.status_code, 200)
        self.assertEqual(r_fin_empty.json()["data"], [])


if __name__ == "__main__":
    unittest.main()
