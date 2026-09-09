import os
import sys
import json
import math
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, desc

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import (
        Fixture, HistoricalResult, MatchStatistics, League, Team,
        MatchStatisticsPredictionSnapshot, ModelEvaluation
    )
    from services.calibration_service import CalibrationService, MIN_PRODUCTION_VALIDATION_SAMPLE
    from services.data_reconciliation_service import DataReconciliationService
    from services.cards_service import CardsPredictionEngine
    from services.shots_prediction_service import ShotsPredictionEngine
except ImportError:
    from ..models import (
        Fixture, HistoricalResult, MatchStatistics, League, Team,
        MatchStatisticsPredictionSnapshot, ModelEvaluation
    )
    from .calibration_service import CalibrationService, MIN_PRODUCTION_VALIDATION_SAMPLE
    from .data_reconciliation_service import DataReconciliationService
    from .cards_service import CardsPredictionEngine
    from .shots_prediction_service import ShotsPredictionEngine

logger = logging.getLogger(__name__)

MATCH_STATS_MODEL_VERSION = "v1_match_stats_nb"
LIVE_MATCH_STATS_MODEL_VERSION = "v1_live_match_stats"

# Baseline League Priors (Full Match)
DEFAULT_LEAGUE_POSSESSION_HOME = 51.5
DEFAULT_LEAGUE_POSSESSION_AWAY = 48.5

DEFAULT_LEAGUE_FOULS_HOME = 11.8
DEFAULT_LEAGUE_FOULS_AWAY = 12.4
DEFAULT_FOULS_DISPERSION_R = 18.0

DEFAULT_LEAGUE_OFFSIDES_HOME = 1.8
DEFAULT_LEAGUE_OFFSIDES_AWAY = 1.5
DEFAULT_OFFSIDES_DISPERSION_R = 4.5

DEFAULT_LEAGUE_SAVES_HOME = 2.8
DEFAULT_LEAGUE_SAVES_AWAY = 3.2
DEFAULT_SAVES_DISPERSION_R = 7.0

DEFAULT_LEAGUE_BLOCKED_HOME = 3.2
DEFAULT_LEAGUE_BLOCKED_AWAY = 2.6
DEFAULT_BLOCKED_DISPERSION_R = 6.0

DEFAULT_INSIDE_BOX_RATIO = 0.62
DEFAULT_OUTSIDE_BOX_RATIO = 0.38
DEFAULT_LOCATION_DISPERSION_R = 8.0

MAX_COUNT_GRID = 50


def _negative_binomial_pmf(k: int, mu: float, r: float) -> float:
    """Computes Negative Binomial PMF: P(K=k) given mean mu and dispersion r."""
    if k < 0 or mu <= 0.0 or r <= 0.0:
        return 1.0 if k == 0 else 0.0
    p = r / (r + mu)
    try:
        log_comb = math.lgamma(k + r) - math.lgamma(k + 1) - math.lgamma(r)
        log_prob = log_comb + (r * math.log(p)) + (k * math.log(1.0 - p))
        return math.exp(log_prob)
    except (ValueError, OverflowError):
        return 0.0


def _derive_monotonic_markets(pmf: List[float], lines: List[float]) -> Dict[str, float]:
    """Generates strictly monotonic Over/Under probabilities from discrete PMF."""
    markets = {}
    for line in lines:
        cutoff = int(math.ceil(line))
        over_p = sum(pmf[k] for k in range(cutoff, len(pmf)))
        k_over = f"over_{str(line).replace('.', '_')}"
        k_under = f"under_{str(line).replace('.', '_')}"
        markets[k_over] = round(max(0.01, min(0.99, over_p)), 4)
        markets[k_under] = round(max(0.01, min(0.99, 1.0 - over_p)), 4)
    return markets


class MatchStatisticsFeatureService:
    """
    Extracts team-level historical rates across match statistics domains
    with strict temporal integrity (match_date < kickoff) and Bayesian shrinkage.
    """

    @classmethod
    def get_team_stats_features(
        cls, db: Session, team_id: int, before_date: datetime, is_home: bool
    ) -> Dict[str, Any]:
        """Calculates historical rolling match statistics for a given team."""
        matches = (
            db.query(MatchStatistics, Fixture)
            .join(Fixture, MatchStatistics.fixture_id == Fixture.id)
            .filter(
                or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
                Fixture.match_date < before_date,
                Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"])
            )
            .order_by(Fixture.match_date.desc())
            .limit(15)
            .all()
        )

        n = len(matches)
        if n == 0:
            return {
                "sample_size": 0,
                "avg_possession": DEFAULT_LEAGUE_POSSESSION_HOME if is_home else DEFAULT_LEAGUE_POSSESSION_AWAY,
                "avg_fouls_committed": DEFAULT_LEAGUE_FOULS_HOME if is_home else DEFAULT_LEAGUE_FOULS_AWAY,
                "avg_fouls_drawn": DEFAULT_LEAGUE_FOULS_AWAY if is_home else DEFAULT_LEAGUE_FOULS_HOME,
                "avg_offsides": DEFAULT_LEAGUE_OFFSIDES_HOME if is_home else DEFAULT_LEAGUE_OFFSIDES_AWAY,
                "avg_saves": DEFAULT_LEAGUE_SAVES_HOME if is_home else DEFAULT_LEAGUE_SAVES_AWAY,
                "avg_blocked": DEFAULT_LEAGUE_BLOCKED_HOME if is_home else DEFAULT_LEAGUE_BLOCKED_AWAY
            }

        poss_list = []
        fouls_comm = []
        fouls_drn = []
        offsides_list = []
        saves_list = []
        blocked_list = []

        for stats, f in matches:
            is_match_home = (f.home_team_id == team_id)
            p = stats.home_possession if is_match_home else stats.away_possession
            if p is not None: poss_list.append(p)

            fc = stats.home_fouls if is_match_home else stats.away_fouls
            if fc is not None: fouls_comm.append(fc)

            fd = stats.away_fouls if is_match_home else stats.home_fouls
            if fd is not None: fouls_drn.append(fd)

            off = stats.home_offsides if is_match_home else stats.away_offsides
            if off is not None: offsides_list.append(off)

            sv = stats.home_saves if is_match_home else stats.away_saves
            if sv is not None: saves_list.append(sv)

            blk = stats.home_blocked_shots if is_match_home else stats.away_blocked_shots
            if blk is not None: blocked_list.append(blk)

        # Baseline priors scaled dynamically with team attack / defense strength
        team = db.query(Team).filter(Team.id == team_id).first() if team_id else None
        att_str, def_str = 1.0, 1.0
        if team:
            try:
                from services.prediction_service import PoissonPredictionEngine
                h_att, h_def, a_att, a_def = PoissonPredictionEngine.resolve_team_ratings(team)
                att_str = h_att if is_home else a_att
                def_str = h_def if is_home else a_def
            except Exception:
                pass

        base_poss = (DEFAULT_LEAGUE_POSSESSION_HOME if is_home else DEFAULT_LEAGUE_POSSESSION_AWAY) + ((att_str - 1.0) * 15.0) - ((def_str - 1.0) * 8.0)
        base_poss = round(max(32.0, min(68.0, base_poss)), 1)
        base_fouls_comm = round((DEFAULT_LEAGUE_FOULS_HOME if is_home else DEFAULT_LEAGUE_FOULS_AWAY) * (0.6 + 0.4 * def_str), 2)
        base_fouls_drn = round((DEFAULT_LEAGUE_FOULS_AWAY if is_home else DEFAULT_LEAGUE_FOULS_HOME) * (0.6 + 0.4 * att_str), 2)
        base_offs = round((DEFAULT_LEAGUE_OFFSIDES_HOME if is_home else DEFAULT_LEAGUE_OFFSIDES_AWAY) * att_str, 2)
        base_saves = round((DEFAULT_LEAGUE_SAVES_HOME if is_home else DEFAULT_LEAGUE_SAVES_AWAY) * def_str, 2)
        base_blk = round((DEFAULT_LEAGUE_BLOCKED_HOME if is_home else DEFAULT_LEAGUE_BLOCKED_AWAY) * def_str, 2)

        # Bayesian Shrinkage helper: w = min(1.0, N / 8.0)
        def _shrink(obs: List[float], baseline: float, target_n: float = 8.0) -> float:
            if not obs: return baseline
            w = min(1.0, len(obs) / target_n)
            return (w * (sum(obs) / float(len(obs)))) + ((1.0 - w) * baseline)

        return {
            "sample_size": n,
            "avg_possession": round(_shrink(poss_list, base_poss), 1),
            "avg_fouls_committed": round(_shrink(fouls_comm, base_fouls_comm), 2),
            "avg_fouls_drawn": round(_shrink(fouls_drn, base_fouls_drn), 2),
            "avg_offsides": round(_shrink(offsides_list, base_offs), 2),
            "avg_saves": round(_shrink(saves_list, base_saves), 2),
            "avg_blocked": round(_shrink(blocked_list, base_blk), 2)
        }


class MatchStatisticsPredictionEngine:
    """
    First-class prediction engine for match-level statistics:
    Possession, Fouls, Offsides, Saves, Blocked Shots, Shot Locations, and Attacking Pressure.
    """

    @classmethod
    def predict_match_statistics(cls, db: Session, fixture_id: int) -> Dict[str, Any]:
        """
        Generates comprehensive pre-match statistical forecasts for all Phase 11 domains.
        """
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            return {"error": f"Fixture {fixture_id} not found"}

        now = fixture.match_date or datetime.now(timezone.utc)
        h_team_id = fixture.home_team_id
        a_team_id = fixture.away_team_id

        h_feat = MatchStatisticsFeatureService.get_team_stats_features(db, h_team_id, now, is_home=True)
        a_feat = MatchStatisticsFeatureService.get_team_stats_features(db, a_team_id, now, is_home=False)

        # 1. POSSESSION ENGINE (Sum = 100.0)
        raw_h_poss = h_feat["avg_possession"]
        raw_a_poss = a_feat["avg_possession"]
        tot_poss_raw = raw_h_poss + raw_a_poss
        exp_h_poss = round((raw_h_poss / tot_poss_raw) * 100.0, 1)
        exp_a_poss = round(100.0 - exp_h_poss, 1)

        possession_payload = {
            "expected_home_possession": exp_h_poss,
            "expected_away_possession": exp_a_poss,
            "possession_differential": round(exp_h_poss - exp_a_poss, 1),
            "projected_range_home": [max(20.0, round(exp_h_poss - 3.5, 1)), min(80.0, round(exp_h_poss + 3.5, 1))],
            "projected_range_away": [max(20.0, round(exp_a_poss - 3.5, 1)), min(80.0, round(exp_a_poss + 3.5, 1))]
        }

        # 2. FOULS ENGINE (Referee-Adjusted Negative Binomial)
        cards_intel = CardsPredictionEngine.predict_cards(db, fixture_id)
        ref_strictness = cards_intel.get("referee_strictness_index", 1.0) if cards_intel else 1.0

        lambda_h_fouls = max(4.0, (h_feat["avg_fouls_committed"] + a_feat["avg_fouls_drawn"]) / 2.0 * ref_strictness)
        lambda_a_fouls = max(4.0, (a_feat["avg_fouls_committed"] + h_feat["avg_fouls_drawn"]) / 2.0 * ref_strictness)
        lambda_tot_fouls = lambda_h_fouls + lambda_a_fouls

        h_fouls_pmf = [_negative_binomial_pmf(k, lambda_h_fouls, DEFAULT_FOULS_DISPERSION_R) for k in range(MAX_COUNT_GRID)]
        a_fouls_pmf = [_negative_binomial_pmf(k, lambda_a_fouls, DEFAULT_FOULS_DISPERSION_R) for k in range(MAX_COUNT_GRID)]

        tot_fouls_pmf = [0.0] * MAX_COUNT_GRID
        for i in range(MAX_COUNT_GRID):
            for j in range(MAX_COUNT_GRID - i):
                tot_fouls_pmf[i + j] += h_fouls_pmf[i] * a_fouls_pmf[j]

        foul_lines = [19.5, 21.5, 23.5, 25.5, 27.5]
        fouls_payload = {
            "expected_home_fouls": round(lambda_h_fouls, 2),
            "expected_away_fouls": round(lambda_a_fouls, 2),
            "expected_total_fouls": round(lambda_tot_fouls, 2),
            "referee_strictness_applied": round(ref_strictness, 2),
            "probabilities": _derive_monotonic_markets(tot_fouls_pmf, foul_lines)
        }

        # 3. OFFSIDES ENGINE
        lambda_h_off = max(0.5, h_feat["avg_offsides"])
        lambda_a_off = max(0.5, a_feat["avg_offsides"])
        lambda_tot_off = lambda_h_off + lambda_a_off

        h_off_pmf = [_negative_binomial_pmf(k, lambda_h_off, DEFAULT_OFFSIDES_DISPERSION_R) for k in range(MAX_COUNT_GRID)]
        a_off_pmf = [_negative_binomial_pmf(k, lambda_a_off, DEFAULT_OFFSIDES_DISPERSION_R) for k in range(MAX_COUNT_GRID)]

        tot_off_pmf = [0.0] * MAX_COUNT_GRID
        for i in range(MAX_COUNT_GRID):
            for j in range(MAX_COUNT_GRID - i):
                tot_off_pmf[i + j] += h_off_pmf[i] * a_off_pmf[j]

        offside_lines = [1.5, 2.5, 3.5, 4.5]
        offsides_payload = {
            "expected_home_offsides": round(lambda_h_off, 2),
            "expected_away_offsides": round(lambda_a_off, 2),
            "expected_total_offsides": round(lambda_tot_off, 2),
            "probabilities": _derive_monotonic_markets(tot_off_pmf, offside_lines)
        }

        # 4. GOALKEEPER SAVES ENGINE (Conditioned on Opponent SoT)
        shots_pred = ShotsPredictionEngine.predict_shots(db, fixture_id)
        exp_h_sot = shots_pred.get("shots_on_target", {}).get("expected_home_sot", 4.8)
        exp_a_sot = shots_pred.get("shots_on_target", {}).get("expected_away_sot", 3.9)

        # Home saves defend against Away SoT; Away saves defend against Home SoT
        lambda_h_saves = max(0.8, exp_a_sot * 0.72)
        lambda_a_saves = max(0.8, exp_h_sot * 0.70)
        lambda_tot_saves = lambda_h_saves + lambda_a_saves

        h_save_pmf = [_negative_binomial_pmf(k, lambda_h_saves, DEFAULT_SAVES_DISPERSION_R) for k in range(MAX_COUNT_GRID)]
        a_save_pmf = [_negative_binomial_pmf(k, lambda_a_saves, DEFAULT_SAVES_DISPERSION_R) for k in range(MAX_COUNT_GRID)]

        tot_save_pmf = [0.0] * MAX_COUNT_GRID
        for i in range(MAX_COUNT_GRID):
            for j in range(MAX_COUNT_GRID - i):
                tot_save_pmf[i + j] += h_save_pmf[i] * a_save_pmf[j]

        save_lines = [2.5, 3.5, 4.5, 5.5]
        saves_payload = {
            "expected_home_saves": round(lambda_h_saves, 2),
            "expected_away_saves": round(lambda_a_saves, 2),
            "expected_total_saves": round(lambda_tot_saves, 2),
            "probabilities": _derive_monotonic_markets(tot_save_pmf, save_lines)
        }

        # 5. BLOCKED SHOTS ENGINE (Bounded by Total Shots)
        exp_h_shots = shots_pred.get("shots", {}).get("expected_home_shots", 13.5)
        exp_a_shots = shots_pred.get("shots", {}).get("expected_away_shots", 11.2)

        # Home blocked shots are shots taken by Home that were blocked by Away defense
        lambda_h_blocked = min(exp_h_shots * 0.40, max(0.5, exp_h_shots * 0.24))
        lambda_a_blocked = min(exp_a_shots * 0.40, max(0.5, exp_a_shots * 0.22))
        lambda_tot_blocked = lambda_h_blocked + lambda_a_blocked

        h_blk_pmf = [_negative_binomial_pmf(k, lambda_h_blocked, DEFAULT_BLOCKED_DISPERSION_R) for k in range(MAX_COUNT_GRID)]
        a_blk_pmf = [_negative_binomial_pmf(k, lambda_a_blocked, DEFAULT_BLOCKED_DISPERSION_R) for k in range(MAX_COUNT_GRID)]

        tot_blk_pmf = [0.0] * MAX_COUNT_GRID
        for i in range(MAX_COUNT_GRID):
            for j in range(MAX_COUNT_GRID - i):
                tot_blk_pmf[i + j] += h_blk_pmf[i] * a_blk_pmf[j]

        block_lines = [1.5, 2.5, 3.5, 4.5, 5.5]
        blocked_payload = {
            "expected_home_blocked_shots": round(lambda_h_blocked, 2),
            "expected_away_blocked_shots": round(lambda_a_blocked, 2),
            "expected_total_blocked_shots": round(lambda_tot_blocked, 2),
            "probabilities": _derive_monotonic_markets(tot_blk_pmf, block_lines)
        }

        # 6. SHOT LOCATION ENGINE (Inside vs Outside Box Decomposition)
        h_inside = round(exp_h_shots * DEFAULT_INSIDE_BOX_RATIO, 2)
        h_outside = round(exp_h_shots * DEFAULT_OUTSIDE_BOX_RATIO, 2)
        a_inside = round(exp_a_shots * DEFAULT_INSIDE_BOX_RATIO, 2)
        a_outside = round(exp_a_shots * DEFAULT_OUTSIDE_BOX_RATIO, 2)
        tot_inside = round(h_inside + a_inside, 2)
        tot_outside = round(h_outside + a_outside, 2)

        tot_in_pmf = [_negative_binomial_pmf(k, tot_inside, DEFAULT_LOCATION_DISPERSION_R) for k in range(MAX_COUNT_GRID)]
        tot_out_pmf = [_negative_binomial_pmf(k, tot_outside, DEFAULT_LOCATION_DISPERSION_R) for k in range(MAX_COUNT_GRID)]

        shot_location_payload = {
            "expected_home_inside_box": h_inside,
            "expected_home_outside_box": h_outside,
            "expected_away_inside_box": a_inside,
            "expected_away_outside_box": a_outside,
            "expected_total_inside_box": tot_inside,
            "expected_total_outside_box": tot_outside,
            "probabilities_inside_box": _derive_monotonic_markets(tot_in_pmf, [11.5, 13.5, 15.5, 17.5]),
            "probabilities_outside_box": _derive_monotonic_markets(tot_out_pmf, [6.5, 8.5, 10.5, 12.5])
        }

        # 7. ATTACKING PRESSURE INDEX (MODEL_DERIVED Analytical Feature)
        # Normalized formula (0-100 scale):
        # 0.30*NormShots + 0.25*NormSoT + 0.20*NormCorners + 0.15*NormPoss + 0.10*NormInsideBox
        h_corners = 5.2
        a_corners = 4.3

        norm_h_shots = min(100.0, (exp_h_shots / 20.0) * 100.0)
        norm_h_sot = min(100.0, (exp_h_sot / 8.0) * 100.0)
        norm_h_corn = min(100.0, (h_corners / 8.0) * 100.0)
        norm_h_poss = exp_h_poss
        norm_h_in = min(100.0, (h_inside / 12.0) * 100.0)

        h_pressure = 0.30 * norm_h_shots + 0.25 * norm_h_sot + 0.20 * norm_h_corn + 0.15 * norm_h_poss + 0.10 * norm_h_in

        norm_a_shots = min(100.0, (exp_a_shots / 20.0) * 100.0)
        norm_a_sot = min(100.0, (exp_a_sot / 8.0) * 100.0)
        norm_a_corn = min(100.0, (a_corners / 8.0) * 100.0)
        norm_a_poss = exp_a_poss
        norm_a_in = min(100.0, (a_inside / 12.0) * 100.0)

        a_pressure = 0.30 * norm_a_shots + 0.25 * norm_a_sot + 0.20 * norm_a_corn + 0.15 * norm_a_poss + 0.10 * norm_a_in

        attacking_pressure = {
            "type": "MODEL_DERIVED",
            "home_pressure_index": round(h_pressure, 1),
            "away_pressure_index": round(a_pressure, 1),
            "dominant_side": "HOME" if h_pressure >= a_pressure + 6.0 else ("AWAY" if a_pressure >= h_pressure + 6.0 else "BALANCED"),
            "components_weighting": {
                "shots": 0.30,
                "shots_on_target": 0.25,
                "corners": 0.20,
                "possession": 0.15,
                "inside_box_shots": 0.10
            }
        }

        # Confidence & Data Quality Score
        sample_sum = h_feat["sample_size"] + a_feat["sample_size"]
        dq = 0.85 if sample_sum >= 16 else (0.65 if sample_sum >= 6 else 0.40)
        confidence = round(0.40 + 0.50 * (min(sample_sum, 20) / 20.0), 2)

        return {
            "fixture_id": fixture.id,
            "status": "AVAILABLE",
            "model_version": MATCH_STATS_MODEL_VERSION,
            "possession": possession_payload,
            "fouls": fouls_payload,
            "offsides": offsides_payload,
            "saves": saves_payload,
            "blocked_shots": blocked_payload,
            "shot_location": shot_location_payload,
            "attacking_pressure": attacking_pressure,
            "confidence": confidence,
            "data_quality": dq,
            "generated_at": datetime.now(timezone.utc).isoformat()
        }

    @classmethod
    def predict_live_match_statistics(cls, db: Session, fixture_id: int) -> Dict[str, Any]:
        """
        Calculates live in-play match statistics projections with early resolution checks.
        """
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            return {"error": f"Fixture {fixture_id} not found"}

        pre_match = cls.predict_match_statistics(db, fixture_id)
        if "error" in pre_match:
            return pre_match

        stats = db.query(MatchStatistics).filter(MatchStatistics.fixture_id == fixture_id).first()

        minute = max(1, min(90, fixture.match_minute if hasattr(fixture, "match_minute") and fixture.match_minute else 45))
        rem_fraction = max(0.0, (90.0 - float(minute)) / 90.0)
        elapsed_fraction = 1.0 - rem_fraction

        # Observed Stats
        obs_h_poss = stats.home_possession if (stats and stats.home_possession is not None) else 50.0
        obs_a_poss = stats.away_possession if (stats and stats.away_possession is not None) else 50.0

        obs_h_fouls = stats.home_fouls if (stats and stats.home_fouls is not None) else 0
        obs_a_fouls = stats.away_fouls if (stats and stats.away_fouls is not None) else 0
        obs_tot_fouls = obs_h_fouls + obs_a_fouls

        obs_h_off = stats.home_offsides if (stats and stats.home_offsides is not None) else 0
        obs_a_off = stats.away_offsides if (stats and stats.away_offsides is not None) else 0
        obs_tot_off = obs_h_off + obs_a_off

        obs_h_saves = stats.home_saves if (stats and stats.home_saves is not None) else 0
        obs_a_saves = stats.away_saves if (stats and stats.away_saves is not None) else 0
        obs_tot_saves = obs_h_saves + obs_a_saves

        # Live Possession Trajectory
        prior_h_poss = pre_match["possession"]["expected_home_possession"]
        proj_h_poss = round((obs_h_poss * elapsed_fraction) + (prior_h_poss * rem_fraction), 1)
        proj_a_poss = round(100.0 - proj_h_poss, 1)

        # Remaining Expected Rates
        rem_tot_fouls = pre_match["fouls"]["expected_total_fouls"] * rem_fraction
        final_tot_fouls = obs_tot_fouls + rem_tot_fouls

        rem_tot_off = pre_match["offsides"]["expected_total_offsides"] * rem_fraction
        final_tot_off = obs_tot_off + rem_tot_off

        rem_tot_saves = pre_match["saves"]["expected_total_saves"] * rem_fraction
        final_tot_saves = obs_tot_saves + rem_tot_saves

        # Live Fouls Early Resolution
        live_fouls_markets = {}
        for line in [19.5, 21.5, 23.5, 25.5, 27.5]:
            k_over = f"over_{str(line).replace('.', '_')}"
            if obs_tot_fouls >= math.ceil(line):
                live_fouls_markets[k_over] = {"probability": 1.0, "status": "already_resolved", "resolved_result": True}
            else:
                needed = math.ceil(line) - obs_tot_fouls
                p = sum(_negative_binomial_pmf(k, rem_tot_fouls, DEFAULT_FOULS_DISPERSION_R) for k in range(needed, MAX_COUNT_GRID))
                live_fouls_markets[k_over] = {"probability": round(max(0.01, min(0.99, p)), 4), "status": "in_play", "resolved_result": None}

        return {
            "fixture_id": fixture.id,
            "status": "LIVE_ACTIVE",
            "model_version": LIVE_MATCH_STATS_MODEL_VERSION,
            "match_minute": minute,
            "possession": {
                "current_observed": {"home": obs_h_poss, "away": obs_a_poss},
                "projected_final": {"home": proj_h_poss, "away": proj_a_poss}
            },
            "fouls": {
                "observed_total": obs_tot_fouls,
                "remaining_expected": round(rem_tot_fouls, 2),
                "final_expected": round(final_tot_fouls, 2),
                "markets": live_fouls_markets
            },
            "offsides": {
                "observed_total": obs_tot_off,
                "remaining_expected": round(rem_tot_off, 2),
                "final_expected": round(final_tot_off, 2)
            },
            "saves": {
                "observed_total": obs_tot_saves,
                "remaining_expected": round(rem_tot_saves, 2),
                "final_expected": round(final_tot_saves, 2)
            },
            "confidence": round(pre_match.get("confidence", 0.50) * 0.85, 2),
            "generated_at": datetime.now(timezone.utc).isoformat()
        }

    @classmethod
    def save_match_statistics_snapshot(cls, db: Session, fixture_id: int) -> MatchStatisticsPredictionSnapshot:
        """Persists immutable snapshot record for Phase 11 match statistics."""
        pred = cls.predict_match_statistics(db, fixture_id)

        snapshot = MatchStatisticsPredictionSnapshot(
            fixture_id=fixture_id,
            model_version=MATCH_STATS_MODEL_VERSION,
            prediction_timestamp=datetime.now(timezone.utc),
            match_minute=0,
            is_live=False,
            expected_home_possession=pred["possession"]["expected_home_possession"],
            expected_away_possession=pred["possession"]["expected_away_possession"],
            expected_home_fouls=pred["fouls"]["expected_home_fouls"],
            expected_away_fouls=pred["fouls"]["expected_away_fouls"],
            expected_total_fouls=pred["fouls"]["expected_total_fouls"],
            expected_home_offsides=pred["offsides"]["expected_home_offsides"],
            expected_away_offsides=pred["offsides"]["expected_away_offsides"],
            expected_total_offsides=pred["offsides"]["expected_total_offsides"],
            expected_home_saves=pred["saves"]["expected_home_saves"],
            expected_away_saves=pred["saves"]["expected_away_saves"],
            expected_total_saves=pred["saves"]["expected_total_saves"],
            expected_home_blocked_shots=pred["blocked_shots"]["expected_home_blocked_shots"],
            expected_away_blocked_shots=pred["blocked_shots"]["expected_away_blocked_shots"],
            expected_total_blocked_shots=pred["blocked_shots"]["expected_total_blocked_shots"],
            expected_home_inside_box_shots=pred["shot_location"]["expected_home_inside_box"],
            expected_away_inside_box_shots=pred["shot_location"]["expected_away_inside_box"],
            expected_total_inside_box_shots=pred["shot_location"]["expected_total_inside_box"],
            expected_home_outside_box_shots=pred["shot_location"]["expected_home_outside_box"],
            expected_away_outside_box_shots=pred["shot_location"]["expected_away_outside_box"],
            expected_total_outside_box_shots=pred["shot_location"]["expected_total_outside_box"],
            fouls_probabilities_json=json.dumps(pred["fouls"]["probabilities"]),
            offsides_probabilities_json=json.dumps(pred["offsides"]["probabilities"]),
            saves_probabilities_json=json.dumps(pred["saves"]["probabilities"]),
            blocked_shots_probabilities_json=json.dumps(pred["blocked_shots"]["probabilities"]),
            shot_location_probabilities_json=json.dumps(pred["shot_location"]),
            confidence=pred["confidence"],
            data_quality=pred["data_quality"],
            diagnostics_json=json.dumps(pred["attacking_pressure"]),
            created_at=datetime.now(timezone.utc)
        )
        db.add(snapshot)
        db.commit()
        return snapshot

    @classmethod
    def get_match_statistics_readiness_status(cls, db: Session) -> Dict[str, Any]:
        """Returns empirical sample validation readiness gates across match statistics markets."""
        foul_evals = db.query(ModelEvaluation).filter(ModelEvaluation.market.contains("fouls")).count()
        off_evals = db.query(ModelEvaluation).filter(ModelEvaluation.market.contains("offsides")).count()
        save_evals = db.query(ModelEvaluation).filter(ModelEvaluation.market.contains("saves")).count()

        def _eval_gate(n: int) -> str:
            if n >= 300: return "VALIDATED"
            if n >= 100: return "VALIDATING"
            return "INSUFFICIENT_DATA"

        return {
            "fouls_model": {"model_version": MATCH_STATS_MODEL_VERSION, "sample_size": foul_evals, "readiness_state": _eval_gate(foul_evals), "required_sample": MIN_PRODUCTION_VALIDATION_SAMPLE},
            "offsides_model": {"model_version": MATCH_STATS_MODEL_VERSION, "sample_size": off_evals, "readiness_state": _eval_gate(off_evals), "required_sample": MIN_PRODUCTION_VALIDATION_SAMPLE},
            "saves_model": {"model_version": MATCH_STATS_MODEL_VERSION, "sample_size": save_evals, "readiness_state": _eval_gate(save_evals), "required_sample": MIN_PRODUCTION_VALIDATION_SAMPLE},
            "ensemble_activation": "GATED (Sample < 100)" if (foul_evals < 100 or off_evals < 100 or save_evals < 100) else "ELIGIBLE",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
