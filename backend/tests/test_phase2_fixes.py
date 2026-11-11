import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base
from models import (
    League, Team, Fixture, HistoricalResult, MatchStatistics,
    TeamStatistics, LeagueStatistics, ModelEvaluation, Prediction
)
from services.prediction_service import DixonColesPredictionEngine
from services.elo_service import EloRatingService
from services.shots_prediction_service import ShotsPredictionEngine
from services.match_statistics_prediction_service import MatchStatisticsPredictionEngine
from services.corners_service import CornersPredictionEngine
from services.cards_service import TeamDisciplineService, CardsPredictionEngine
from services.market_resolution_service import MarketResolutionService
from services.weather_service import WeatherService
from services.ensemble_service import AdaptiveEnsembleService
from services.production_validation_service import ProductionValidationService
from services.decision_intelligence_service import DecisionIntelligenceService
from services.ingestion_service import DataIngestionService


class TestPhase2Fixes(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = SessionLocal()

        self.league = League(name="Premier League", country="England")
        self.db.add(self.league)
        self.db.commit()

        self.home = Team(name="Arsenal", league_id=self.league.id)
        self.away = Team(name="Chelsea", league_id=self.league.id)
        self.db.add_all([self.home, self.away])
        self.db.commit()

        self.target_dt = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
        self.fixture = Fixture(
            league_id=self.league.id,
            home_team_id=self.home.id,
            away_team_id=self.away.id,
            match_date=self.target_dt,
            status="SCHEDULED",
            external_id="ESPN-PHASE2-01",
        )
        self.db.add(self.fixture)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_dixon_coles_tau_marginal_and_normalization(self):
        # Diagonal scores adjust identically while keeping score matrix sum ~= 1.0
        probs = DixonColesPredictionEngine.calculate_poisson_probabilities(lambda_home=1.8, lambda_away=1.1, rho=-0.10)
        tot = probs["home_win_probability"] + probs["draw_probability"] + probs["away_win_probability"]
        self.assertAlmostEqual(tot, 1.0, places=3)
        self.assertAlmostEqual(
            DixonColesPredictionEngine._dixon_coles_tau(1, 0, 1.8, 1.1, -0.10),
            1.0 + 1.1 * -0.10,
            places=6,
        )
        self.assertAlmostEqual(
            DixonColesPredictionEngine._dixon_coles_tau(0, 1, 1.8, 1.1, -0.10),
            1.0 + 1.8 * -0.10,
            places=6,
        )

    def test_calculate_xg_ignores_under_three_empirical_matches(self):
        self.db.add(LeagueStatistics(league_id=self.league.id, total_matches_analyzed=10, avg_home_goals=1.45, avg_away_goals=1.15))
        # Seed only 2 home matches and 2 away matches (< 3 threshold)
        self.db.add_all([
            TeamStatistics(
                team_id=self.home.id, matches_analyzed_home=2, matches_analyzed_away=2,
                avg_home_goals_scored=5.0, avg_home_goals_conceded=0.0,
                home_attack_strength=3.5, home_defense_strength=0.2,
                away_attack_strength=1.0, away_defense_strength=1.0,
            ),
            TeamStatistics(
                team_id=self.away.id, matches_analyzed_home=2, matches_analyzed_away=2,
                avg_away_goals_scored=5.0, avg_away_goals_conceded=0.0,
                home_attack_strength=1.0, home_defense_strength=1.0,
                away_attack_strength=3.5, away_defense_strength=0.2,
            ),
        ])
        self.db.commit()

        xg_h, xg_a, _ = DixonColesPredictionEngine.calculate_xg(
            self.db, self.home.id, self.away.id, self.league.id, target_date=self.target_dt
        )
        # Should fall back to resolve_team_ratings instead of using the 2-match 5.0-goal spike
        self.assertLess(xg_h, 3.0)
        self.assertLess(xg_a, 3.0)

    def test_elo_home_win_does_not_credit_away_wins(self):
        _, away_elo = EloRatingService.update_elo_for_fixture(
            self.db, self.fixture.id, self.home.id, self.away.id, home_score=2, away_score=0
        )
        self.assertEqual(away_elo.away_wins, 0)
        self.assertEqual(away_elo.matches_played, 1)
        self.assertLess(away_elo.rating, 1500.0)
        self.assertIsNone(away_elo.last_updated.tzinfo)

    def test_shots_and_match_stats_pmfs_normalized_and_monotonic(self):
        # Seed 3 completed matches with boxscore stats so predictions run from empirical history
        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=5)
        for i in range(3):
            f = Fixture(
                league_id=self.league.id,
                home_team_id=self.home.id,
                away_team_id=self.away.id,
                match_date=past - timedelta(days=i * 7),
                status="FINISHED",
                home_score=2,
                away_score=1,
            )
            self.db.add(f)
            self.db.commit()
            self.db.add(
                MatchStatistics(
                    fixture_id=f.id,
                    home_shots=16, away_shots=12,
                    home_shots_on_target=6, away_shots_on_target=4,
                    home_corners=6, away_corners=5,
                    home_fouls=11, away_fouls=13,
                    home_yellow_cards=2, away_yellow_cards=3,
                    home_red_cards=0, away_red_cards=0,
                    home_offsides=2, away_offsides=1,
                    home_saves=3, away_saves=4,
                )
            )
        self.db.commit()

        shots_res = ShotsPredictionEngine.predict_shots(self.db, self.fixture.id)
        self.assertNotIn("error", shots_res)
        self.assertAlmostEqual(shots_res["diagnostics"]["captured_probability_mass"], 1.0, places=3)
        tot_s = shots_res["shots"]["probabilities"]
        self.assertGreaterEqual(tot_s["over_15_5"], tot_s["over_21_5"])
        self.assertGreaterEqual(tot_s["over_21_5"], tot_s["over_25_5"])
        self.assertGreater(tot_s["over_15_5"], 0.5)

        ms_res = MatchStatisticsPredictionEngine.predict_match_statistics(self.db, self.fixture.id)
        self.assertNotIn("error", ms_res)
        fouls = ms_res["fouls"]["probabilities"]
        self.assertGreaterEqual(fouls["over_19_5"], fouls["over_23_5"])
        self.assertGreaterEqual(fouls["over_23_5"], fouls["over_27_5"])

    def test_corners_and_cards_sample_size_fallback_and_red_risk_bounded(self):
        # 1 home match + 3 away matches -> fallback triggers all_records (4 matches)
        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)
        matches = [(self.home.id, self.away.id), (self.away.id, self.home.id), (self.away.id, self.home.id), (self.away.id, self.home.id)]
        for idx, (h_id, a_id) in enumerate(matches):
            f = Fixture(
                league_id=self.league.id,
                home_team_id=h_id,
                away_team_id=a_id,
                match_date=past - timedelta(days=idx * 4),
                status="FINISHED",
                home_score=1,
                away_score=1,
            )
            self.db.add(f)
            self.db.commit()
            self.db.add(
                MatchStatistics(
                    fixture_id=f.id,
                    home_corners=5, away_corners=4,
                    home_yellow_cards=2, away_yellow_cards=2,
                    home_red_cards=1 if idx == 0 else 0, away_red_cards=0,
                )
            )
        self.db.commit()

        c_feat = CornersPredictionEngine.get_team_corner_features(self.db, self.home.id, is_home=True)
        self.assertEqual(c_feat["sample_size"], 4)
        self.assertEqual(c_feat["total_sample_size"], 4)

        d_feat = TeamDisciplineService.get_team_discipline_features(self.db, self.home.id, is_home=True)
        self.assertEqual(d_feat["sample_size"], 4)

        risk = CardsPredictionEngine.calculate_red_card_risk(4.5, 4.5, h_reds=1, a_reds=0, h_sample=1, a_sample=1)
        self.assertGreater(risk["any_red_prob"], 0.0)
        self.assertLessEqual(risk["any_red_prob"], 0.45)

    def test_market_resolution_cards_disambiguated_from_goals(self):
        # 5 goals scored, only 2 cards -> over_4_5_cards MUST lose (0.0), not hit goals!
        res = MarketResolutionService.resolve_market_outcome(
            market="over_4_5_cards",
            home_score=3,
            away_score=2,
            home_yellow=1,
            away_yellow=1,
        )
        self.assertEqual(res, 0.0)
        res_under = MarketResolutionService.resolve_market_outcome(
            market="under_2_5_cards",
            home_score=3,
            away_score=2,
            home_yellow=1,
            away_yellow=1,
        )
        self.assertEqual(res_under, 1.0)

    def test_weather_unavailable_does_not_fabricate(self):
        os.environ.pop("OPENWEATHER_API_KEY", None)
        w = WeatherService.get_weather_for_venue("Emirates Stadium, London")
        self.assertFalse(w["available"])
        self.assertIsNone(w["temp_c"])
        self.assertEqual(w["xg_modifier"], 1.0)

    def test_real_model_versions_and_verified_only_filtering(self):
        self.assertEqual(AdaptiveEnsembleService.DEFAULT_MODELS[0], "v2_match_intelligence")
        # Unverified evaluation MUST NOT unlock readiness or leak into leaderboard / decisions
        ev = ModelEvaluation(
            fixture_id=self.fixture.id,
            model_version="v2_match_intelligence",
            prediction_type="goals",
            market="over_1_5_goals",
            prediction_timestamp=self.target_dt - timedelta(hours=1),
            predicted_probability=0.80,
            actual_outcome=1.0,
            brier_component=0.04,
            log_loss_component=0.223,
            verified=False,
        )
        self.db.add(ev)
        self.db.commit()

        readiness = ProductionValidationService.get_market_level_readiness(self.db)
        o15_entry = next(m for m in readiness["markets"] if m["market"] == "over_1_5_goals")
        self.assertEqual(o15_entry["sample_size"], 0)
        lb = ProductionValidationService.get_real_leaderboard(self.db)
        self.assertEqual(lb["leaderboard"][0]["sample_size"], 0)
        dec = DecisionIntelligenceService.get_fixture_decisions(self.db, self.fixture.id)
        for cand in dec:
            self.assertEqual(cand.get("sample_size", 0), 0)

        # Flipping verified=True should populate metrics & exercise compute_calibration_curve cleanly
        ev.verified = True
        self.db.commit()
        readiness_v = ProductionValidationService.get_market_level_readiness(self.db)
        o15_v = next(m for m in readiness_v["markets"] if m["market"] == "over_1_5_goals")
        self.assertEqual(o15_v["sample_size"], 1)
        lb_v = ProductionValidationService.get_real_leaderboard(self.db)
        v2_row = next(r for r in lb_v["leaderboard"] if r["model_key"] == "v2_match_intelligence")
        self.assertEqual(v2_row["sample_size"], 1)

    def test_auto_resolve_expired_live_does_not_fabricate_zero_zero(self):
        stale = Fixture(
            league_id=self.league.id,
            home_team_id=self.home.id,
            away_team_id=self.away.id,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=6),
            status="LIVE",
            home_score=None,
            away_score=None,
            external_id="ESPN-STALE-00",
        )
        self.db.add(stale)
        self.db.commit()

        DataIngestionService.auto_resolve_expired_live_fixtures(self.db)
        self.db.refresh(stale)
        self.assertEqual(stale.status, "POSTPONED")
        self.assertIsNone(stale.home_score)
        self.assertIsNone(stale.away_score)
