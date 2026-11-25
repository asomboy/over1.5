import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base, get_db
from main import app
from models import (
    CardPredictionSnapshot,
    CornerPredictionSnapshot,
    EloRating,
    Fixture,
    HistoricalResult,
    League,
    LeagueStatistics,
    ModelEvaluation,
    Team,
    TeamFormStreak,
)
from services.ensemble_service import AdaptiveEnsembleService
from services.prediction_service import DixonColesPredictionEngine, MODEL_VERSION
from services.statistics_service import calculate_team_statistics
from services.weather_service import WeatherService


class TestPhaseABCUpgrades(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )
        Base.metadata.create_all(bind=self.engine)
        self.db = self.SessionLocal()
        WeatherService.clear_cache()

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        WeatherService.clear_cache()
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_low_xg_defensive_matchup_not_clamped_to_1_80_and_under_2_5_signal(self):
        lg = League(external_id="L-DEF", name="Defensive League", country="Italy", season="2026")
        self.db.add(lg)
        self.db.flush()
        self.db.add(LeagueStatistics(league_id=lg.id, total_matches_analyzed=40, avg_home_goals=1.05, avg_away_goals=0.85))

        t1 = Team(external_id="T-DEF-1", name="Catenaccio FC", short_code="CAT", league_id=lg.id)
        t2 = Team(external_id="T-DEF-2", name="Bunker SC", short_code="BUN", league_id=lg.id)
        t3 = Team(external_id="T-AVG-3", name="Midtable A", short_code="MDA", league_id=lg.id)
        t4 = Team(external_id="T-AVG-4", name="Midtable B", short_code="MDB", league_id=lg.id)
        self.db.add_all([t1, t2, t3, t4])
        self.db.flush()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Seed normal league baseline (1.5 home, 1.0 away) across other clubs
        for i in range(6):
            fm = Fixture(
                external_id=f"FM-{i}",
                league_id=lg.id,
                home_team_id=t3.id,
                away_team_id=t4.id,
                match_date=now - timedelta(days=(i + 1) * 6),
                status="FINISHED",
            )
            self.db.add(fm)
            self.db.flush()
            self.db.add(HistoricalResult(fixture_id=fm.id, home_score=2, away_score=1, total_goals=3))

        # Seed ultra-defensive 0-0 / 1-0 / 0-1 records for Catenaccio FC & Bunker SC
        for i in range(6):
            fh = Fixture(
                external_id=f"FH-{i}",
                league_id=lg.id,
                home_team_id=t1.id,
                away_team_id=t2.id,
                match_date=now - timedelta(days=(i + 1) * 7),
                status="FINISHED",
            )
            self.db.add(fh)
            self.db.flush()
            self.db.add(HistoricalResult(fixture_id=fh.id, home_score=(1 if i == 0 else 0), away_score=0, total_goals=(1 if i == 0 else 0)))

        self.db.commit()
        calculate_team_statistics(self.db, t1.id)
        calculate_team_statistics(self.db, t2.id)

        h_xg, a_xg, tot_xg = DixonColesPredictionEngine.calculate_xg(
            self.db, t1.id, t2.id, lg.id, target_date=now + timedelta(days=3)
        )
        self.assertLess(tot_xg, 1.80, f"Defensive matchup should stay below 1.80 xG, got {tot_xg}")
        self.assertGreaterEqual(tot_xg, 1.05)

        matrix, grid, _ = DixonColesPredictionEngine.calculate_score_matrix(0.60, 0.45)
        markets = DixonColesPredictionEngine.derive_markets_from_matrix(matrix, grid)
        sig = DixonColesPredictionEngine.determine_best_market_signal(
            markets, {"overall": 82, "data_quality": 80, "model_stability": 84}
        )
        self.assertEqual(sig["market"], "Under 2.5 Goals")

    def test_rest_days_schedule_congestion_adjusts_xg(self):
        lg = League(external_id="L-REST", name="Premier League", country="England", season="2026")
        self.db.add(lg)
        self.db.flush()
        t_tired = Team(external_id="T-TRD", name="Tired Rovers", short_code="TRD", league_id=lg.id)
        t_fresh = Team(external_id="T-FRS", name="Fresh Wanderers", short_code="FRS", league_id=lg.id)
        self.db.add_all([t_tired, t_fresh])
        self.db.flush()

        target = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=5)
        # Baseline with both teams well-rested (8 days rest)
        f_prev_fresh = Fixture(
            external_id="F-PREV-8D",
            league_id=lg.id,
            home_team_id=t_tired.id,
            away_team_id=t_fresh.id,
            match_date=target - timedelta(days=8),
            status="FINISHED",
        )
        self.db.add(f_prev_fresh)
        self.db.commit()

        hx_rested, ax_rested, _ = DixonColesPredictionEngine.calculate_xg(
            self.db, t_tired.id, t_fresh.id, lg.id, target_date=target
        )

        # Now add a Thursday Europa match 2.5 days before Sunday kickoff for t_tired ONLY
        t_other = Team(external_id="T-OTH", name="Other FC", short_code="OTH", league_id=lg.id)
        self.db.add(t_other)
        self.db.flush()
        f_congested = Fixture(
            external_id="F-CONG-2D",
            league_id=lg.id,
            home_team_id=t_other.id,
            away_team_id=t_tired.id,
            match_date=target - timedelta(days=2, hours=12),
            status="FINISHED",
        )
        self.db.add(f_congested)
        self.db.commit()

        hx_congested, ax_congested, _ = DixonColesPredictionEngine.calculate_xg(
            self.db, t_tired.id, t_fresh.id, lg.id, target_date=target
        )
        self.assertLessEqual(hx_congested, hx_rested)
        self.assertGreaterEqual(ax_congested, ax_rested)

    def test_cross_competition_cup_weighting_and_opponent_elo(self):
        dom_lg = League(external_id="L-DOM", name="LaLiga", country="Spain", season="2026")
        cup_lg = League(external_id="L-CUP", name="Copa del Rey", country="Spain", season="2026")
        self.db.add_all([dom_lg, cup_lg])
        self.db.flush()
        self.db.add(LeagueStatistics(league_id=dom_lg.id, total_matches_analyzed=20, avg_home_goals=1.50, avg_away_goals=1.10))

        club = Team(external_id="T-CLB", name="Real Betis", short_code="BET", league_id=dom_lg.id)
        elite_opp = Team(external_id="T-ELT", name="Elite FC", short_code="ELT", league_id=dom_lg.id)
        cup_minnow = Team(external_id="T-MIN", name="Minnow CF", short_code="MIN", league_id=cup_lg.id)
        self.db.add_all([club, elite_opp, cup_minnow])
        self.db.flush()
        self.db.add(EloRating(team_id=elite_opp.id, rating=1700.0, matches_played=12))
        self.db.add(EloRating(team_id=cup_minnow.id, rating=1300.0, matches_played=12))

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        f_dom = Fixture(league_id=dom_lg.id, home_team_id=club.id, away_team_id=elite_opp.id, match_date=now - timedelta(days=3), status="FINISHED")
        f_cup = Fixture(league_id=cup_lg.id, home_team_id=club.id, away_team_id=cup_minnow.id, match_date=now - timedelta(days=3), status="FINISHED")
        self.db.add_all([f_dom, f_cup])
        self.db.flush()
        # 1 goal in league vs 5 goals in cup: cup 0.65 weight keeps average < unweighted 3.00
        self.db.add(HistoricalResult(fixture_id=f_dom.id, home_score=1, away_score=1, total_goals=2))
        self.db.add(HistoricalResult(fixture_id=f_cup.id, home_score=5, away_score=0, total_goals=5))
        self.db.commit()

        st = calculate_team_statistics(self.db, club.id)
        self.assertLess(st.avg_home_goals_scored, 3.00)

    def test_weather_cache_and_non_blocking_read_mode(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "main": {"temp": 14.2},
            "weather": [{"main": "Rain"}],
            "wind": {"speed": 4.0},
        }
        with patch.dict(os.environ, {"OPENWEATHER_API_KEY": "test-owm-key"}):
            with patch("services.weather_service.httpx.get", return_value=mock_resp) as mock_get:
                # fetch_remote=False when cache is empty must NOT make HTTP call
                res_cold = WeatherService.get_weather_for_venue("Emirates Stadium, London", fetch_remote=False)
                self.assertFalse(res_cold["available"])
                self.assertEqual(mock_get.call_count, 0)

                # fetch_remote=True populates TTL cache once
                res_live = WeatherService.get_weather_for_venue("Emirates Stadium, London", fetch_remote=True)
                self.assertTrue(res_live["available"])
                self.assertEqual(res_live["xg_modifier"], 1.02)
                self.assertEqual(mock_get.call_count, 1)

                # Subsequent fetch_remote=False reads cached value with 0 extra HTTP calls
                res_cached = WeatherService.get_weather_for_venue("Stamford Bridge, London", fetch_remote=False)
                self.assertTrue(res_cached["available"])
                self.assertEqual(mock_get.call_count, 1)

    def test_domain_isolated_ensemble_and_pre_status_snapshots(self):
        g_models = AdaptiveEnsembleService.resolve_domain_models("over_1_5_goals")
        c_models = AdaptiveEnsembleService.resolve_domain_models("over_8_5_corners")
        k_models = AdaptiveEnsembleService.resolve_domain_models("over_3_5_cards")
        self.assertIn("v2_match_intelligence", g_models)
        self.assertNotIn("v1_corners_nb", g_models)
        self.assertIn("v1_corners_nb", c_models)
        self.assertIn("v1_cards_nb", k_models)

        lg = League(external_id="L-PRE", name="Serie A", country="Italy", season="2026")
        ht = Team(external_id="T-PRE-H", name="Napoli", short_code="NAP")
        at = Team(external_id="T-PRE-A", name="Lazio", short_code="LAZ")
        self.db.add_all([lg, ht, at])
        self.db.flush()
        self.db.add(TeamFormStreak(team_id=ht.id, last_5_results="WWDLW", goals_scored_last_5=9, goals_conceded_last_5=4))
        fix = Fixture(
            external_id="ESPN-PRE-99",
            league_id=lg.id,
            home_team_id=ht.id,
            away_team_id=at.id,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=6),
            status="PRE",
        )
        self.db.add(fix)
        self.db.commit()

        DixonColesPredictionEngine.predict_fixture(self.db, fix.id)
        c_snap = self.db.query(CornerPredictionSnapshot).filter(CornerPredictionSnapshot.fixture_id == fix.id).first()
        k_snap = self.db.query(CardPredictionSnapshot).filter(CardPredictionSnapshot.fixture_id == fix.id).first()
        self.assertIsNotNone(c_snap)
        self.assertTrue(c_snap.is_prematch)
        self.assertIsNotNone(k_snap)
        self.assertTrue(k_snap.is_prematch)

        up_resp = self.client.get("/api/fixtures/upcoming")
        self.assertEqual(up_resp.status_code, 200)
        found = next((x for x in up_resp.json()["data"] if x["id"] == fix.id), None)
        self.assertIsNotNone(found)
        self.assertEqual(found["home_team"]["recent_form"], ["W", "W", "D", "L", "W"])

    def test_client_id_betslip_isolation_and_equity_curve(self):
        lg = League(external_id="L-ISO", name="EPL", country="England", season="2026")
        ht = Team(external_id="T-ISO-H", name="Arsenal", short_code="ARS")
        at = Team(external_id="T-ISO-A", name="Everton", short_code="EVE")
        self.db.add_all([lg, ht, at])
        self.db.flush()
        fix = Fixture(
            external_id="ESPN-ISO-1",
            league_id=lg.id,
            home_team_id=ht.id,
            away_team_id=at.id,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3),
            status="FINISHED",
            home_score=2,
            away_score=1,
        )
        self.db.add(fix)
        self.db.commit()

        headers_a = {"X-Client-Id": "client-alpha-9001"}
        headers_b = {"X-Client-Id": "client-bravo-9002"}

        r_create = self.client.post(
            "/api/betslips",
            headers=headers_a,
            json={
                "title": "Alpha Private Ticket",
                "stake": 15.0,
                "bookmaker": "sportybet",
                "legs": [{"fixture_id": fix.id, "match_label": "Arsenal vs Everton", "market": "Over 1.5 Goals", "model_prob": 0.80, "bookmaker_odds": 1.50}],
            },
        )
        self.assertEqual(r_create.status_code, 200)
        slip_id = r_create.json()["slip"]["id"]

        # Bravo cannot see, patch, or delete Alpha's slip
        r_list_b = self.client.get("/api/betslips", headers=headers_b).json()
        self.assertEqual(len(r_list_b["slips"]), 0)
        self.assertEqual(
            self.client.patch(f"/api/betslips/{slip_id}/booking-code", headers=headers_b, json={"booking_code": "HACK"}).status_code,
            404,
        )
        self.assertEqual(self.client.delete(f"/api/betslips/{slip_id}", headers=headers_b).status_code, 404)

        # Alpha sees settled WON slip, equity_curve, and market_breakdown
        r_list_a = self.client.get("/api/betslips", headers=headers_a).json()
        self.assertEqual(len(r_list_a["slips"]), 1)
        self.assertEqual(r_list_a["summary"]["won_slips"], 1)
        self.assertGreaterEqual(len(r_list_a["summary"]["equity_curve"]), 2)
        self.assertEqual(r_list_a["summary"]["market_breakdown"][0]["market"], "Over 1.5 Goals")

        # Verify browser CORS preflight OPTIONS allows X-Client-Id and PATCH/PUT/DELETE
        preflight = self.client.options(
            f"/api/betslips/{slip_id}/booking-code",
            headers={
                "Origin": "https://soccer-goal-predictor-web.onrender.com",
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "content-type, x-client-id, x-admin-key",
            },
        )
        self.assertEqual(preflight.status_code, 200)
        allow_hdrs = preflight.headers.get("access-control-allow-headers", "").lower()
        allow_meths = preflight.headers.get("access-control-allow-methods", "").upper()
        self.assertIn("x-client-id", allow_hdrs)
        self.assertIn("PATCH", allow_meths)
        self.assertIn("DELETE", allow_meths)
        self.assertIn("PUT", allow_meths)


if __name__ == "__main__":
    unittest.main()
