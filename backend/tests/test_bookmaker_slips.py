import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

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
    Fixture,
    League,
    Prediction,
    Team,
)
from services.bet_slip_service import BetSlipService
from services.bookmaker_slip_service import BookmakerSlipService
from services.market_resolution_service import MarketResolutionService


class TestBookmakerSlips(unittest.TestCase):
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

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def _seed_upcoming_fixtures(self):
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        day1_dt = now_naive + timedelta(hours=3)
        day2_dt = now_naive + timedelta(hours=30)

        leagues = []
        for idx, (ext, name, country) in enumerate([
            ("ESPN-ENG.1", "Premier League", "England"),
            ("ESPN-ESP.1", "LaLiga", "Spain"),
            ("ESPN-GER.1", "Bundesliga", "Germany"),
            ("ESPN-ITA.1", "Serie A", "Italy"),
            ("ESPN-FRA.1", "Ligue 1", "France"),
            ("ESPN-YOUTH.1", "England U19 Youth Cup", "England"),
        ]):
            lg = League(external_id=ext, name=name, country=country, season="2026")
            self.db.add(lg)
            leagues.append(lg)
        self.db.flush()

        fixture_ids = []
        teams_pairs = [
            ("Arsenal", "Chelsea", 0, day1_dt, 2.0, 1.0, 0.84, 0.64, 0.61, 0.58, 0.24, 0.18),
            ("Real Madrid", "Valencia", 1, day1_dt, 2.2, 0.9, 0.86, 0.68, 0.56, 0.66, 0.20, 0.14),
            ("Bayern Munich", "Dortmund", 2, day1_dt, 2.4, 1.3, 0.90, 0.74, 0.72, 0.59, 0.22, 0.19),
            ("Inter", "Roma", 3, day2_dt, 1.9, 1.0, 0.82, 0.62, 0.59, 0.55, 0.26, 0.19),
            ("PSG", "Lyon", 4, day2_dt, 2.3, 1.1, 0.88, 0.70, 0.65, 0.64, 0.21, 0.15),
            ("School XI", "Academy U19", 5, day1_dt, 2.5, 1.5, 0.92, 0.78, 0.75, 0.60, 0.20, 0.20),
        ]

        for i, (h_name, a_name, l_idx, dt, hx, ax, o15, o25, btts, hw, dw, aw) in enumerate(teams_pairs):
            ht = Team(external_id=f"ESPN-H-{i}", name=h_name, short_code=h_name[:3].upper())
            at = Team(external_id=f"ESPN-A-{i}", name=a_name, short_code=a_name[:3].upper())
            self.db.add_all([ht, at])
            self.db.flush()

            fix = Fixture(
                external_id=f"ESPN-BM-{i}",
                league_id=leagues[l_idx].id,
                home_team_id=ht.id,
                away_team_id=at.id,
                match_date=dt,
                status="SCHEDULED",
            )
            self.db.add(fix)
            self.db.flush()
            fixture_ids.append((fix, dt))

            pred = Prediction(
                fixture_id=fix.id,
                predicted_home_score=hx,
                predicted_away_score=ax,
                expected_goals_xg=round(hx + ax, 2),
                home_win_probability=hw,
                draw_probability=dw,
                away_win_probability=aw,
                over_0_5_probability=0.95,
                over_1_5_probability=o15,
                over_2_5_probability=o25,
                over_3_5_probability=0.38,
                over_4_5_probability=0.18,
                under_2_5_probability=round(1.0 - o25, 2),
                btts_probability=btts,
                confidence_score=0.81,
                most_likely_score="2-1",
            )
            corner = CornerPredictionSnapshot(
                fixture_id=fix.id,
                expected_home_corners=5.4,
                expected_away_corners=4.4,
                expected_total_corners=9.8,
                over_7_5_prob=0.82,
                under_7_5_prob=0.18,
                over_8_5_prob=0.71,
                under_8_5_prob=0.29,
                over_9_5_prob=0.58,
                under_9_5_prob=0.42,
                over_10_5_prob=0.44,
                under_10_5_prob=0.56,
                over_11_5_prob=0.31,
                under_11_5_prob=0.69,
            )
            cards = CardPredictionSnapshot(
                fixture_id=fix.id,
                expected_home_cards=2.2,
                expected_away_cards=2.3,
                expected_total_cards=4.5,
                over_1_5_prob=0.90,
                under_1_5_prob=0.10,
                over_2_5_prob=0.81,
                under_2_5_prob=0.19,
                over_3_5_prob=0.69,
                under_3_5_prob=0.31,
                over_4_5_prob=0.50,
                under_4_5_prob=0.50,
                over_5_5_prob=0.32,
                under_5_5_prob=0.68,
                over_6_5_prob=0.18,
                under_6_5_prob=0.82,
            )
            self.db.add_all([pred, corner, cards])

        self.db.commit()
        return fixture_ids

    def test_market_translations_sportybet_and_bet9ja(self):
        sb_o15 = BookmakerSlipService.translate_market("sportybet", "Over 1.5 Goals")
        b9_o15 = BookmakerSlipService.translate_market("bet9ja", "Over 1.5 Goals")
        self.assertEqual(sb_o15["section"], "Goals")
        self.assertEqual(sb_o15["market"], "Over/Under")
        self.assertEqual(sb_o15["pick"], "Over 1.5")
        self.assertEqual(b9_o15["market"], "Over/Under 1.5")
        self.assertEqual(b9_o15["pick"], "Over (1.5)")

        sb_btts = BookmakerSlipService.translate_market("sportybet", "BTTS Yes")
        b9_btts = BookmakerSlipService.translate_market("bet9ja", "BTTS Yes")
        self.assertEqual(sb_btts["market"], "GG/NG")
        self.assertEqual(sb_btts["pick"], "Yes (GG)")
        self.assertEqual(b9_btts["market"], "Goal / No Goal")
        self.assertEqual(b9_btts["pick"], "GG (Yes)")

        sb_dc = BookmakerSlipService.translate_market("sportybet", "Double Chance 1X")
        b9_dc = BookmakerSlipService.translate_market("bet9ja", "Double Chance X2")
        self.assertEqual(sb_dc["canonical_market"], "dc_1x")
        self.assertEqual(b9_dc["canonical_market"], "dc_x2")

    def test_double_chance_resolution_and_slip_settlement(self):
        self.assertEqual(MarketResolutionService.resolve_market_outcome("dc_1x", 2, 1), 1.0)
        self.assertEqual(MarketResolutionService.resolve_market_outcome("dc_1x", 1, 1), 1.0)
        self.assertEqual(MarketResolutionService.resolve_market_outcome("dc_1x", 0, 2), 0.0)

        self.assertEqual(MarketResolutionService.resolve_market_outcome("dc_x2", 1, 1), 1.0)
        self.assertEqual(MarketResolutionService.resolve_market_outcome("dc_x2", 0, 1), 1.0)
        self.assertEqual(MarketResolutionService.resolve_market_outcome("dc_x2", 2, 0), 0.0)

        self.assertEqual(MarketResolutionService.resolve_market_outcome("dc_12", 2, 0), 1.0)
        self.assertEqual(MarketResolutionService.resolve_market_outcome("dc_12", 0, 1), 1.0)
        self.assertEqual(MarketResolutionService.resolve_market_outcome("dc_12", 1, 1), 0.0)

        lg = League(external_id="ESPN-DC.1", name="Test League", country="England", season="2026")
        ht = Team(external_id="ESPN-DC-H", name="Home FC", short_code="HOM")
        at = Team(external_id="ESPN-DC-A", name="Away FC", short_code="AWY")
        self.db.add_all([lg, ht, at])
        self.db.flush()

        fix_done = Fixture(
            external_id="ESPN-DC-DONE",
            league_id=lg.id,
            home_team_id=ht.id,
            away_team_id=at.id,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3),
            status="FINISHED",
            home_score=1,
            away_score=1,
        )
        self.db.add(fix_done)
        self.db.commit()
        self.db.refresh(fix_done)

        slip = BetSlipService.create_slip(
            db=self.db,
            title="DC Draw Ticket",
            source="bookmaker_sportybet",
            stake=20.0,
            bookmaker="sportybet",
            booking_code="SB88X1",
            legs=[{
                "fixture_id": fix_done.id,
                "match_label": "Home FC vs Away FC",
                "league_name": "Test League",
                "market": "Double Chance 1X",
                "model_prob": 0.78,
                "bookmaker_odds": 1.35,
            }],
        )
        self.assertEqual(slip["status"], "WON")
        self.assertEqual(slip["bookmaker"], "sportybet")
        self.assertEqual(slip["booking_code"], "SB88X1")
        self.assertEqual(slip["legs"][0]["result"], "WON")

    def test_bookmaker_slip_builder_excludes_youth_and_duplicates(self):
        self._seed_upcoming_fixtures()

        for bm in ("sportybet", "bet9ja"):
            payload = BookmakerSlipService.build_slips(self.db, bookmaker=bm)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["bookmaker"], bm)
            # 6 seeded minus 1 youth fixture = 5 senior fixtures scanned
            self.assertEqual(payload["total_fixtures_scanned"], 5)

            for preset_key in ("safe_2_odds", "balanced_3_odds", "goals_corners_5_odds", "high_yield_10_odds"):
                slip = payload["slips"][preset_key]
                self.assertEqual(slip["status"], "ready")
                fids = [lg["fixture_id"] for lg in slip["legs"]]
                self.assertEqual(len(fids), len(set(fids)), f"Duplicate fixture in {bm} {preset_key}")
                for lg in slip["legs"]:
                    self.assertNotIn("School XI", lg["match"])
                    self.assertNotIn("Academy U19", lg["match"])
                    self.assertTrue(lg["match_date"].endswith("Z"))
                    self.assertIn("bookmaker_section", lg)
                    self.assertIn("bookmaker_market", lg)
                    self.assertIn("bookmaker_pick", lg)
                    self.assertIn("placement_hint", lg)
                    self.assertIn("sportsbook_search_url", lg)
                    if bm == "bet9ja":
                        self.assertIn("bet9ja.com", lg["sportsbook_search_url"])
                    else:
                        self.assertIn("sportybet.com", lg["sportsbook_search_url"])
                self.assertGreater(len(slip["share_text"]), 10)

    def test_bookmaker_endpoints_day_filter_and_booking_code_patch(self):
        seeded = self._seed_upcoming_fixtures()
        _, day1_dt = seeded[0]
        _, day1_key, _ = BookmakerSlipService._format_gmt1_parts(day1_dt)

        resp_sb = self.client.get(f"/api/bookmakers/slips?bookmaker=sportybet&day={day1_key}")
        self.assertEqual(resp_sb.status_code, 200)
        data_sb = resp_sb.json()
        self.assertEqual(data_sb["status"], "ok")
        self.assertEqual(data_sb["bookmaker"], "sportybet")
        # Only day1 senior fixtures (Arsenal, Real Madrid, Bayern Munich) = 3
        self.assertEqual(data_sb["total_fixtures_scanned"], 3)

        resp_b9 = self.client.get("/api/bookmakers/slips?bookmaker=bet9ja")
        self.assertEqual(resp_b9.status_code, 200)
        self.assertEqual(resp_b9.json()["bookmaker"], "bet9ja")

        resp_bad = self.client.get("/api/bookmakers/slips?bookmaker=invalidbook")
        self.assertEqual(resp_bad.status_code, 422)

        fix_first = seeded[0][0]
        create_resp = self.client.post(
            "/api/betslips",
            json={
                "title": "SportyBet Weekend Banker",
                "source": "bookmaker_sportybet",
                "bookmaker": "sportybet",
                "booking_code": "bc7a9x",
                "stake": 25.0,
                "legs": [
                    {
                        "fixture_id": fix_first.id,
                        "match_label": "Arsenal vs Chelsea",
                        "league_name": "Premier League",
                        "market": "Double Chance 1X",
                        "model_prob": 0.82,
                        "bookmaker_odds": 1.32,
                    }
                ],
            },
        )
        self.assertEqual(create_resp.status_code, 200)
        created_slip = create_resp.json()["slip"]
        self.assertEqual(created_slip["bookmaker"], "sportybet")
        self.assertEqual(created_slip["booking_code"], "BC7A9X")

        patch_resp = self.client.patch(
            f"/api/betslips/{created_slip['id']}/booking-code",
            json={"bookmaker": "bet9ja", "booking_code": " b9j442 "},
        )
        self.assertEqual(patch_resp.status_code, 200)
        updated_slip = patch_resp.json()["slip"]
        self.assertEqual(updated_slip["bookmaker"], "bet9ja")
        self.assertEqual(updated_slip["booking_code"], "B9J442")

        patch_missing = self.client.patch(
            "/api/betslips/999999/booking-code",
            json={"booking_code": "NONE"},
        )
        self.assertEqual(patch_missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
