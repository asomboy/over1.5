import os
import sys
import math
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple, cast
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import Fixture, HistoricalResult, MatchStatistics, League, Team, CornerPredictionSnapshot
    from services.prediction_service import _negative_binomial_pmf, _shrink_to_prior, _clamp
    from schemas.prediction_schema import CornersPrediction
except ImportError:
    from ..models import Fixture, HistoricalResult, MatchStatistics, League, Team, CornerPredictionSnapshot
    from .prediction_service import _negative_binomial_pmf, _shrink_to_prior, _clamp
    from ..schemas.prediction_schema import CornersPrediction

logger = logging.getLogger(__name__)

CORNER_MODEL_VERSION = "v1_corners_nb"
DEFAULT_FALLBACK_DISPERSION = 5.5
MIN_DISPERSION = 1.5
MAX_DISPERSION = 15.0

# Baseline Resolution Hierarchy Sample Thresholds
MIN_COMPETITION_BASELINE_SAMPLES = 15
MIN_SHRUNK_BASELINE_SAMPLES = 5
MIN_GLOBAL_BASELINE_SAMPLES = 25

# Fallback Baselines
FALLBACK_LEAGUE_HOME_CORNERS = 5.40
FALLBACK_LEAGUE_AWAY_CORNERS = 4.60
FALLBACK_LEAGUE_TOTAL_CORNERS = 10.00

# Production Backtest Validation Threshold
MIN_REAL_BACKTEST_VALIDATION_SAMPLE = 100


class CornerDataQualityService:
    """
    Service responsible for calculating true data coverage and sample size metrics
    without pseudo-coverage approximations.
    """

    @classmethod
    def get_database_corner_data_quality(cls, db: Session) -> Dict[str, Any]:
        """
        Audits overall database corner data completeness across all eligible finished matches
        and provides per-competition breakdowns.
        """
        finished_query = db.query(Fixture).filter(Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]))
        total_eligible = finished_query.count()

        corner_matches_query = (
            db.query(Fixture)
            .join(MatchStatistics, MatchStatistics.fixture_id == Fixture.id)
            .filter(
                Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                MatchStatistics.home_corners.isnot(None),
                MatchStatistics.away_corners.isnot(None)
            )
        )
        total_corner_matches = corner_matches_query.count()
        missing_corner_matches = max(0, total_eligible - total_corner_matches)
        coverage = round(total_corner_matches / float(total_eligible), 4) if total_eligible > 0 else 0.0

        # Competition breakdowns
        leagues = db.query(League).all()
        competitions = []

        for league in leagues:
            l_eligible = db.query(Fixture).filter(
                Fixture.league_id == league.id,
                Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"])
            ).count()

            l_corner_records = (
                db.query(MatchStatistics)
                .join(Fixture, Fixture.id == MatchStatistics.fixture_id)
                .filter(
                    Fixture.league_id == league.id,
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    MatchStatistics.home_corners.isnot(None),
                    MatchStatistics.away_corners.isnot(None)
                )
                .all()
            )
            l_corners_count = len(l_corner_records)

            if l_eligible > 0 or l_corners_count > 0:
                l_cov = round(l_corners_count / float(l_eligible), 4) if l_eligible > 0 else 0.0
                if l_corners_count > 0:
                    avg_tot = round(sum(m.total_corners or (m.home_corners + m.away_corners) for m in l_corner_records) / float(l_corners_count), 2)
                else:
                    avg_tot = None

                competitions.append({
                    "competition_id": league.id,
                    "competition": league.name,
                    "country": league.country,
                    "eligible_matches": l_eligible,
                    "corner_matches": l_corners_count,
                    "coverage": l_cov,
                    "average_total_corners": avg_tot
                })

        # Distinct data sources
        sources = [r[0] for r in db.query(MatchStatistics.data_source).distinct().all() if r[0]]

        return {
            "eligible_matches": total_eligible,
            "matches_with_corner_data": total_corner_matches,
            "missing_corner_data": missing_corner_matches,
            "coverage": coverage,
            "competitions": sorted(competitions, key=lambda x: x["corner_matches"], reverse=True),
            "data_sources": sources or ["observed"]
        }

    @classmethod
    def get_fixture_corner_coverage(
        cls, db: Session, home_team_id: int, away_team_id: int, league_id: Optional[int], target_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Calculates exact historical sample size and true coverage for a specific fixture matchup.
        Excludes matches occurring on or after target_date to maintain temporal safety.
        """
        naive_target = target_date.astimezone(timezone.utc).replace(tzinfo=None) if (target_date and target_date.tzinfo) else target_date

        def get_team_stats(team_id: int) -> Tuple[int, int]:
            q_elig = db.query(Fixture).filter(
                or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
                Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"])
            )
            if naive_target:
                q_elig = q_elig.filter(Fixture.match_date < naive_target)
            elig_count = q_elig.count()

            q_obs = (
                db.query(MatchStatistics)
                .join(Fixture, Fixture.id == MatchStatistics.fixture_id)
                .filter(
                    or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    MatchStatistics.home_corners.isnot(None),
                    MatchStatistics.away_corners.isnot(None)
                )
            )
            if naive_target:
                q_obs = q_obs.filter(Fixture.match_date < naive_target)
            obs_count = q_obs.count()

            return elig_count, obs_count

        h_elig, h_obs = get_team_stats(home_team_id)
        a_elig, a_obs = get_team_stats(away_team_id)

        tot_elig = h_elig + a_elig
        tot_obs = h_obs + a_obs
        coverage = round(tot_obs / float(tot_elig), 4) if tot_elig > 0 else 0.0

        # League sample & coverage
        l_elig = 0
        l_obs = 0
        if league_id:
            q_lelig = db.query(Fixture).filter(
                Fixture.league_id == league_id,
                Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"])
            )
            if naive_target:
                q_lelig = q_lelig.filter(Fixture.match_date < naive_target)
            l_elig = q_lelig.count()

            q_lobs = (
                db.query(MatchStatistics)
                .join(Fixture, Fixture.id == MatchStatistics.fixture_id)
                .filter(
                    Fixture.league_id == league_id,
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    MatchStatistics.home_corners.isnot(None),
                    MatchStatistics.away_corners.isnot(None)
                )
            )
            if naive_target:
                q_lobs = q_lobs.filter(Fixture.match_date < naive_target)
            l_obs = q_lobs.count()

        l_cov = round(l_obs / float(l_elig), 4) if l_elig > 0 else 0.0

        return {
            "corner_data_coverage": coverage,
            "corner_sample_size": tot_obs,
            "eligible_match_count": tot_elig,
            "observed_corner_match_count": tot_obs,
            "missing_corner_match_count": max(0, tot_elig - tot_obs),
            "home_sample_size": h_obs,
            "home_eligible_count": h_elig,
            "away_sample_size": a_obs,
            "away_eligible_count": a_elig,
            "league_sample_size": l_obs,
            "league_eligible_count": l_elig,
            "league_corner_coverage": l_cov
        }


class CornersPredictionEngine:
    """
    Negative Binomial Corners Prediction Engine V1 (Model: v1_corners_nb).
    
    Predicts:
    1. Expected Home Corners (lambda_home)
    2. Expected Away Corners (lambda_away)
    3. Expected Total Corners (lambda_total = lambda_home + lambda_away)
    4. Total Corners Probability Markets (Over/Under 7.5, 8.5, 9.5, 10.5, 11.5)
    5. Team Corners Probability Markets (Home & Away Over 3.5, 4.5, 5.5)
    6. Multi-Factor Corner Confidence based on verified sample size, true coverage, and dispersion stability
    7. Hierarchical League Baseline Resolution (competition -> shrunk_competition -> global -> fallback)
    8. Hierarchical Dispersion Estimation (competition -> shrunk_competition -> global -> fallback)
    """

    XI_TIME_DECAY = 0.0035

    @classmethod
    def resolve_league_baseline(
        cls, db: Optional[Session], league_id: Optional[int], target_date: Optional[datetime] = None
    ) -> Tuple[float, float, float, str, int]:
        """
        Hierarchical League Baseline Resolution.
        Priority:
        1. Competition-specific (N >= 15 verified corner matches in league)
        2. Shrunk competition (5 <= N < 15, shrunk toward global baseline)
        3. Global baseline (N >= 25 corner matches across DB)
        4. Fallback baseline (Home: 5.40, Away: 4.60, Total: 10.00)

        Returns (home_avg, away_avg, total_avg, baseline_source, baseline_sample_size).
        """
        if not db:
            return FALLBACK_LEAGUE_HOME_CORNERS, FALLBACK_LEAGUE_AWAY_CORNERS, FALLBACK_LEAGUE_TOTAL_CORNERS, "fallback", 0

        naive_target = target_date.astimezone(timezone.utc).replace(tzinfo=None) if (target_date and target_date.tzinfo) else target_date

        def compute_global_baseline() -> Tuple[float, float, float, str, int]:
            try:
                g_query = (
                    db.query(MatchStatistics)
                    .join(Fixture, Fixture.id == MatchStatistics.fixture_id)
                    .filter(
                        Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                        MatchStatistics.home_corners.isnot(None),
                        MatchStatistics.away_corners.isnot(None)
                    )
                )
                if naive_target:
                    g_query = g_query.filter(Fixture.match_date < naive_target)
                
                g_matches = g_query.limit(300).all()
                n_g = len(g_matches)
                if n_g >= MIN_GLOBAL_BASELINE_SAMPLES:
                    gh = sum(m.home_corners for m in g_matches if m.home_corners is not None) / float(n_g)
                    ga = sum(m.away_corners for m in g_matches if m.away_corners is not None) / float(n_g)
                    return round(gh, 2), round(ga, 2), round(gh + ga, 2), "global", n_g
            except Exception as e:
                logger.debug(f"Global corner baseline error: {e}")
            return FALLBACK_LEAGUE_HOME_CORNERS, FALLBACK_LEAGUE_AWAY_CORNERS, FALLBACK_LEAGUE_TOTAL_CORNERS, "fallback", 0

        if not league_id:
            return compute_global_baseline()

        try:
            l_query = (
                db.query(MatchStatistics)
                .join(Fixture, Fixture.id == MatchStatistics.fixture_id)
                .filter(
                    Fixture.league_id == league_id,
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    MatchStatistics.home_corners.isnot(None),
                    MatchStatistics.away_corners.isnot(None)
                )
            )
            if naive_target:
                l_query = l_query.filter(Fixture.match_date < naive_target)

            l_matches = l_query.limit(200).all()
            n_l = len(l_matches)

            if n_l >= MIN_COMPETITION_BASELINE_SAMPLES:
                lh = sum(m.home_corners for m in l_matches if m.home_corners is not None) / float(n_l)
                la = sum(m.away_corners for m in l_matches if m.away_corners is not None) / float(n_l)
                return round(lh, 2), round(la, 2), round(lh + la, 2), "competition", n_l
            elif n_l >= MIN_SHRUNK_BASELINE_SAMPLES:
                lh_raw = sum(m.home_corners for m in l_matches if m.home_corners is not None) / float(n_l)
                la_raw = sum(m.away_corners for m in l_matches if m.away_corners is not None) / float(n_l)
                gh, ga, _, _, _ = compute_global_baseline()
                w = n_l / float(MIN_COMPETITION_BASELINE_SAMPLES)
                shrunk_h = _shrink_to_prior(lh_raw, gh, w)
                shrunk_a = _shrink_to_prior(la_raw, ga, w)
                return round(shrunk_h, 2), round(shrunk_a, 2), round(shrunk_h + shrunk_a, 2), "shrunk_competition", n_l
            else:
                return compute_global_baseline()
        except Exception as e:
            logger.warning(f"Error calculating corner baseline for league {league_id}: {e}")
            return compute_global_baseline()

    @classmethod
    def resolve_dispersion(
        cls, db: Optional[Session], league_id: Optional[int], target_date: Optional[datetime] = None
    ) -> Tuple[float, str]:
        """
        Estimates the Negative Binomial dispersion parameter r from historical corner data.
        Hierarchy:
        A. Competition-specific (N >= 20 corner matches in league)
        B. Shrunk competition (8 <= N < 20 matches, shrunk toward global)
        C. Global dispersion (N >= 30 matches across all leagues in DB)
        D. Fallback dispersion (5.5)

        Returns (dispersion_r, dispersion_source).
        """
        if not db:
            return DEFAULT_FALLBACK_DISPERSION, "fallback"

        naive_target = target_date.astimezone(timezone.utc).replace(tzinfo=None) if (target_date and target_date.tzinfo) else target_date

        def compute_global_dispersion() -> Tuple[float, str]:
            try:
                g_query = (
                    db.query(MatchStatistics)
                    .join(Fixture, Fixture.id == MatchStatistics.fixture_id)
                    .filter(
                        Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                        MatchStatistics.home_corners.isnot(None),
                        MatchStatistics.away_corners.isnot(None)
                    )
                )
                if naive_target:
                    g_query = g_query.filter(Fixture.match_date < naive_target)
                
                matches = g_query.limit(300).all()
                if len(matches) >= 30:
                    corners = [(m.total_corners or (m.home_corners + m.away_corners)) for m in matches if m.home_corners is not None and m.away_corners is not None]
                    n = len(corners)
                    mean_val = sum(corners) / float(n)
                    var_val = sum((c - mean_val) ** 2 for c in corners) / float(n)
                    if var_val > mean_val:
                        r = (mean_val ** 2) / (var_val - mean_val)
                        return round(_clamp(r, MIN_DISPERSION, MAX_DISPERSION), 2), "global"
                    else:
                        return MAX_DISPERSION, "global"
            except Exception as e:
                logger.debug(f"Global corner dispersion estimation error: {e}")
            return DEFAULT_FALLBACK_DISPERSION, "fallback"

        if not league_id:
            return compute_global_dispersion()

        try:
            l_query = (
                db.query(MatchStatistics)
                .join(Fixture, Fixture.id == MatchStatistics.fixture_id)
                .filter(
                    Fixture.league_id == league_id,
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    MatchStatistics.home_corners.isnot(None),
                    MatchStatistics.away_corners.isnot(None)
                )
            )
            if naive_target:
                l_query = l_query.filter(Fixture.match_date < naive_target)

            league_matches = l_query.limit(200).all()
            n_comp = len(league_matches)

            if n_comp >= 20:
                corners = [(m.total_corners or (m.home_corners + m.away_corners)) for m in league_matches if m.home_corners is not None and m.away_corners is not None]
                mean_val = sum(corners) / float(n_comp)
                var_val = sum((c - mean_val) ** 2 for c in corners) / float(n_comp)
                if var_val > mean_val:
                    r = (mean_val ** 2) / (var_val - mean_val)
                    return round(_clamp(r, MIN_DISPERSION, MAX_DISPERSION), 2), "competition"
                else:
                    return MAX_DISPERSION, "competition"
            elif n_comp >= 8:
                corners = [(m.total_corners or (m.home_corners + m.away_corners)) for m in league_matches if m.home_corners is not None and m.away_corners is not None]
                mean_val = sum(corners) / float(n_comp)
                var_val = sum((c - mean_val) ** 2 for c in corners) / float(n_comp)
                raw_comp = (mean_val ** 2) / max(0.1, (var_val - mean_val)) if var_val > mean_val else MAX_DISPERSION
                glob_r, _ = compute_global_dispersion()
                w = n_comp / 20.0
                shrunk_r = _shrink_to_prior(raw_comp, glob_r, w)
                return round(_clamp(shrunk_r, MIN_DISPERSION, MAX_DISPERSION), 2), "shrunk_competition"
            else:
                return compute_global_dispersion()
        except Exception as e:
            logger.warning(f"Error calculating corner dispersion for league {league_id}: {e}")
            return compute_global_dispersion()

    @classmethod
    def get_team_corner_features(
        cls, db: Session, team_id: int, is_home: bool, target_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Extracts comprehensive, chronologically safe team corner features:
        - average corners for/against (venue-specific and overall)
        - recent 5 matches corners for/against
        - recent 10 matches corners for/against
        - time-decay weighted averages
        - sample variance
        - true sample size and eligible match count
        """
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

        fallback_for = round((FALLBACK_LEAGUE_HOME_CORNERS if is_home else FALLBACK_LEAGUE_AWAY_CORNERS) * att_str, 2)
        fallback_against = round((FALLBACK_LEAGUE_AWAY_CORNERS if is_home else FALLBACK_LEAGUE_HOME_CORNERS) * def_str, 2)

        try:
            naive_target = target_date.astimezone(timezone.utc).replace(tzinfo=None) if (target_date and target_date.tzinfo) else target_date

            # Query venue-specific matches
            venue_query = (
                db.query(Fixture, MatchStatistics)
                .join(MatchStatistics, MatchStatistics.fixture_id == Fixture.id)
                .filter(
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    MatchStatistics.home_corners.isnot(None),
                    MatchStatistics.away_corners.isnot(None)
                )
            )
            if naive_target:
                venue_query = venue_query.filter(Fixture.match_date < naive_target)

            if is_home:
                venue_query = venue_query.filter(Fixture.home_team_id == team_id)
            else:
                venue_query = venue_query.filter(Fixture.away_team_id == team_id)

            venue_records = venue_query.order_by(Fixture.match_date.desc()).limit(25).all()

            # Query all matches (home + away) for rolling form
            all_query = (
                db.query(Fixture, MatchStatistics)
                .join(MatchStatistics, MatchStatistics.fixture_id == Fixture.id)
                .filter(
                    or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    MatchStatistics.home_corners.isnot(None),
                    MatchStatistics.away_corners.isnot(None)
                )
            )
            if naive_target:
                all_query = all_query.filter(Fixture.match_date < naive_target)

            all_records = all_query.order_by(Fixture.match_date.desc()).limit(20).all()

            if not venue_records and not all_records:
                return {
                    "avg_corners_for": fallback_for,
                    "avg_corners_against": fallback_against,
                    "recent_5_for": fallback_for,
                    "recent_5_against": fallback_against,
                    "recent_10_for": fallback_for,
                    "recent_10_against": fallback_against,
                    "sample_size": 0,
                    "variance_for": 1.0,
                    "variance_against": 1.0
                }

            # Time-decay weighting on venue matches (or all matches if venue sample is small)
            records_to_use = venue_records if len(venue_records) >= 3 else all_records
            ref_date = naive_target or datetime.now(timezone.utc).replace(tzinfo=None)

            weighted_for = 0.0
            weighted_against = 0.0
            weight_sum = 0.0
            raw_for_list = []
            raw_against_list = []

            for f, stats in records_to_use:
                days_diff = max(0, (ref_date - f.match_date).total_seconds() / 86400.0) if f.match_date else 0
                w = math.exp(-cls.XI_TIME_DECAY * days_diff)

                if f.home_team_id == team_id:
                    c_for = stats.home_corners or 0
                    c_against = stats.away_corners or 0
                else:
                    c_for = stats.away_corners or 0
                    c_against = stats.home_corners or 0

                weighted_for += c_for * w
                weighted_against += c_against * w
                weight_sum += w
                raw_for_list.append(c_for)
                raw_against_list.append(c_against)

            avg_for = weighted_for / weight_sum if weight_sum > 0 else fallback_for
            avg_against = weighted_against / weight_sum if weight_sum > 0 else fallback_against

            # Recent 5 and Recent 10 on all matches
            r5_for = sum(raw_for_list[:5]) / float(len(raw_for_list[:5])) if raw_for_list[:5] else fallback_for
            r5_against = sum(raw_against_list[:5]) / float(len(raw_against_list[:5])) if raw_against_list[:5] else fallback_against
            r10_for = sum(raw_for_list[:10]) / float(len(raw_for_list[:10])) if raw_for_list[:10] else fallback_for
            r10_against = sum(raw_against_list[:10]) / float(len(raw_against_list[:10])) if raw_against_list[:10] else fallback_against

            n_raw = len(raw_for_list)
            var_for = sum((x - avg_for) ** 2 for x in raw_for_list) / float(n_raw) if n_raw > 1 else 1.0
            var_against = sum((x - avg_against) ** 2 for x in raw_against_list) / float(n_raw) if n_raw > 1 else 1.0

            return {
                "avg_corners_for": round(avg_for, 2),
                "avg_corners_against": round(avg_against, 2),
                "recent_5_for": round(r5_for, 2),
                "recent_5_against": round(r5_against, 2),
                "recent_10_for": round(r10_for, 2),
                "recent_10_against": round(r10_against, 2),
                "sample_size": len(venue_records),
                "total_sample_size": len(all_records),
                "variance_for": round(var_for, 2),
                "variance_against": round(var_against, 2)
            }
        except Exception as e:
            logger.debug(f"Error fetching team corner features for team {team_id}: {e}")
            return {
                "avg_corners_for": fallback_for,
                "avg_corners_against": fallback_against,
                "recent_5_for": fallback_for,
                "recent_5_against": fallback_against,
                "recent_10_for": fallback_for,
                "recent_10_against": fallback_against,
                "sample_size": 0,
                "variance_for": 1.0,
                "variance_against": 1.0
            }

    @classmethod
    def calculate_expected_corners(
        cls, db: Session, home_team_id: int, away_team_id: int, league_id: Optional[int], target_date: Optional[datetime] = None
    ) -> Tuple[float, float, float, Dict[str, Any]]:
        """
        Calculates expected corners (lambda_home, lambda_away, lambda_total) using
        hierarchical league baselines, time-decay features, concession rates, and Bayesian sample shrinkage.
        """
        # 1. Resolve League baseline hierarchically
        l_home_avg, l_away_avg, l_tot_avg, baseline_source, baseline_sample_size = cls.resolve_league_baseline(
            db, league_id, target_date=target_date
        )

        # 2. Extract Team features
        h_feat = cls.get_team_corner_features(db, home_team_id, is_home=True, target_date=target_date)
        a_feat = cls.get_team_corner_features(db, away_team_id, is_home=False, target_date=target_date)

        # 3. Attacking and defensive concession strengths
        raw_h_att = h_feat["avg_corners_for"] / max(1.0, l_home_avg)
        raw_h_def = h_feat["avg_corners_against"] / max(1.0, l_away_avg)
        raw_a_att = a_feat["avg_corners_for"] / max(1.0, l_away_avg)
        raw_a_def = a_feat["avg_corners_against"] / max(1.0, l_home_avg)

        # 4. Bayesian shrinkage toward 1.0 based on verified sample size
        w_h = min(1.0, h_feat["sample_size"] / 8.0)
        w_a = min(1.0, a_feat["sample_size"] / 8.0)

        h_att = _shrink_to_prior(raw_h_att, 1.0, w_h)
        h_def = _shrink_to_prior(raw_h_def, 1.0, w_h)
        a_att = _shrink_to_prior(raw_a_att, 1.0, w_a)
        a_def = _shrink_to_prior(raw_a_def, 1.0, w_a)

        # 5. Expected corner rates (clamped to realistic football bounds)
        lambda_h = _clamp(l_home_avg * h_att * a_def, 1.5, 12.0)
        lambda_a = _clamp(l_away_avg * a_att * h_def, 1.2, 11.0)
        lambda_tot = round(lambda_h + lambda_a, 2)

        diagnostics = {
            "baseline_source": baseline_source,
            "baseline_sample_size": baseline_sample_size,
            "league_home_avg": l_home_avg,
            "league_away_avg": l_away_avg,
            "league_total_avg": l_tot_avg,
            "home_attack_strength": round(h_att, 3),
            "away_concession_strength": round(a_def, 3),
            "away_attack_strength": round(a_att, 3),
            "home_concession_strength": round(h_def, 3),
            "home_features": h_feat,
            "away_features": a_feat
        }

        return round(lambda_h, 2), round(lambda_a, 2), lambda_tot, diagnostics

    @classmethod
    def calculate_corner_probabilities(
        cls, lambda_home: float, lambda_away: float, dispersion: float = DEFAULT_FALLBACK_DISPERSION
    ) -> Dict[str, Any]:
        """
        Derives comprehensive corner probability markets using separate home/away
        Negative Binomial distributions combined via discrete 2D convolution.
        """
        lambda_h = max(0.5, lambda_home)
        lambda_a = max(0.5, lambda_away)
        lambda_tot = lambda_h + lambda_a
        safe_r = _clamp(dispersion, MIN_DISPERSION, MAX_DISPERSION)

        # Team dispersion parameters proportional to mean
        r_h = max(1.0, safe_r * (lambda_h / lambda_tot))
        r_a = max(1.0, safe_r * (lambda_a / lambda_tot))

        max_team_corners = 25
        home_pmf = [_negative_binomial_pmf(k, lambda_h, r_h) for k in range(max_team_corners)]
        away_pmf = [_negative_binomial_pmf(j, lambda_a, r_a) for j in range(max_team_corners)]

        # Discrete Convolution for total corners (0..50)
        max_total_corners = 50
        total_pmf = [0.0 for _ in range(max_total_corners)]
        for i in range(max_team_corners):
            for j in range(max_team_corners):
                tot = i + j
                if tot < max_total_corners:
                    total_pmf[tot] += home_pmf[i] * away_pmf[j]

        # Normalize total PMF
        sum_total = sum(total_pmf)
        if sum_total > 0:
            total_pmf = [p / sum_total for p in total_pmf]

        # Total Markets (Over / Under 7.5, 8.5, 9.5, 10.5, 11.5)
        p_o75 = sum(total_pmf[k] for k in range(8, max_total_corners))
        p_o85 = sum(total_pmf[k] for k in range(9, max_total_corners))
        p_o95 = sum(total_pmf[k] for k in range(10, max_total_corners))
        p_o105 = sum(total_pmf[k] for k in range(11, max_total_corners))
        p_o115 = sum(total_pmf[k] for k in range(12, max_total_corners))

        # Home Team Markets (Over 3.5, 4.5, 5.5)
        sum_h = sum(home_pmf)
        h_pmf_norm = [p / sum_h for p in home_pmf] if sum_h > 0 else home_pmf
        p_h_o35 = sum(h_pmf_norm[k] for k in range(4, max_team_corners))
        p_h_o45 = sum(h_pmf_norm[k] for k in range(5, max_team_corners))
        p_h_o55 = sum(h_pmf_norm[k] for k in range(6, max_team_corners))

        # Away Team Markets (Over 3.5, 4.5, 5.5)
        sum_a = sum(away_pmf)
        a_pmf_norm = [p / sum_a for p in away_pmf] if sum_a > 0 else away_pmf
        p_a_o35 = sum(a_pmf_norm[j] for j in range(4, max_team_corners))
        p_a_o45 = sum(a_pmf_norm[j] for j in range(5, max_team_corners))
        p_a_o55 = sum(a_pmf_norm[j] for j in range(6, max_team_corners))

        return {
            "total_markets": {
                "over_7_5": round(p_o75, 4),
                "under_7_5": round(max(0.0, 1.0 - p_o75), 4),
                "over_8_5": round(p_o85, 4),
                "under_8_5": round(max(0.0, 1.0 - p_o85), 4),
                "over_9_5": round(p_o95, 4),
                "under_9_5": round(max(0.0, 1.0 - p_o95), 4),
                "over_10_5": round(p_o105, 4),
                "under_10_5": round(max(0.0, 1.0 - p_o105), 4),
                "over_11_5": round(p_o115, 4),
                "under_11_5": round(max(0.0, 1.0 - p_o115), 4),
            },
            "home_team": {
                "over_3_5": round(p_h_o35, 4),
                "over_4_5": round(p_h_o45, 4),
                "over_5_5": round(p_h_o55, 4),
            },
            "away_team": {
                "over_3_5": round(p_a_o35, 4),
                "over_4_5": round(p_a_o45, 4),
                "over_5_5": round(p_a_o55, 4),
            }
        }

    @classmethod
    def calculate_corner_confidence(
        cls, coverage_info: Dict[str, Any], h_feat: Dict[str, Any], a_feat: Dict[str, Any], dispersion_source: str, baseline_source: str
    ) -> Dict[str, Any]:
        """
        Calculates multi-dimensional corner confidence score (0-100) incorporating:
        - true verified sample size (home + away + league)
        - true corner data coverage percentage
        - home/away sample balance
        - baseline and dispersion stability
        """
        h_sample = h_feat["sample_size"]
        a_sample = a_feat["sample_size"]
        tot_sample = h_sample + a_sample
        coverage = coverage_info.get("corner_data_coverage", 0.0)

        # 1. Data Quality Score
        if tot_sample >= 20 and coverage >= 0.75:
            dq_base = 88
        elif tot_sample >= 12 and coverage >= 0.60:
            dq_base = 76
        elif tot_sample >= 6 and coverage >= 0.40:
            dq_base = 60
        elif tot_sample >= 2:
            dq_base = 40
        else:
            dq_base = 20

        data_quality = int(round(_clamp(dq_base, 15, 95)))

        # 2. Sample Strength Score
        min_team_s = min(h_sample, a_sample)
        if min_team_s >= 8:
            ss_base = 88
        elif min_team_s >= 4:
            ss_base = 72
        elif min_team_s >= 2:
            ss_base = 50
        elif min_team_s >= 1:
            ss_base = 35
        else:
            ss_base = 20
        sample_strength = int(round(_clamp(ss_base, 15, 95)))

        # 3. Model Stability Score
        stab_base = 65
        if baseline_source == "competition":
            stab_base += 15
        elif baseline_source == "shrunk_competition":
            stab_base += 8
        elif baseline_source == "fallback":
            stab_base -= 12

        if dispersion_source == "competition":
            stab_base += 10
        elif dispersion_source == "fallback":
            stab_base -= 8

        model_stability = int(round(_clamp(stab_base, 15, 95)))

        overall = int(round(0.40 * data_quality + 0.35 * sample_strength + 0.25 * model_stability))

        if overall >= 80:
            label = "strong"
        elif overall >= 65:
            label = "good"
        elif overall >= 50:
            label = "moderate"
        elif overall >= 35:
            label = "low"
        else:
            label = "insufficient"

        return {
            "overall": overall,
            "data_quality": data_quality,
            "sample_strength": sample_strength,
            "model_stability": model_stability,
            "label": label
        }

    @classmethod
    def predict_corners(
        cls, db: Session, fixture_id: int, target_date: Optional[datetime] = None, save_snapshot: bool = True
    ) -> Dict[str, Any]:
        """
        Executes full Corner Prediction pipeline for a fixture.
        Returns complete CornersPrediction structure and automatically persists snapshot.
        """
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            return {
                "available": False,
                "reason": f"Fixture {fixture_id} not found in database.",
                "expected": None,
                "total_markets": None,
                "home_team": None,
                "away_team": None,
                "confidence": {"overall": 0, "data_quality": 0, "sample_strength": 0, "model_stability": 0, "label": "insufficient"},
                "model": {"version": CORNER_MODEL_VERSION, "dispersion": DEFAULT_FALLBACK_DISPERSION, "dispersion_source": "fallback", "baseline_source": "fallback"}
            }

        # Calculate exact coverage and sample metrics
        cov_info = CornerDataQualityService.get_fixture_corner_coverage(
            db, cast(int, fixture.home_team_id), cast(int, fixture.away_team_id), fixture.league_id, target_date=target_date
        )

        h_sample = cov_info["home_sample_size"]
        a_sample = cov_info["away_sample_size"]
        l_sample = cov_info["league_sample_size"]

        is_low_sample = (h_sample == 0 and a_sample == 0 and l_sample == 0)

        # Calculate expected corners
        xg_h, xg_a, xg_tot, diag = cls.calculate_expected_corners(
            db, cast(int, fixture.home_team_id), cast(int, fixture.away_team_id), fixture.league_id, target_date=target_date
        )

        # Resolve dispersion
        dispersion_r, dispersion_source = cls.resolve_dispersion(db, fixture.league_id, target_date=target_date)

        # Calculate probabilities
        probs = cls.calculate_corner_probabilities(xg_h, xg_a, dispersion=dispersion_r)

        # Calculate confidence
        conf = cls.calculate_corner_confidence(
            cov_info, diag["home_features"], diag["away_features"], dispersion_source, diag["baseline_source"]
        )

        res_payload = {
            "available": True,
            "reason": None,
            "expected": {
                "home": xg_h,
                "away": xg_a,
                "total": xg_tot
            },
            "total_markets": probs["total_markets"],
            "home_team": probs["home_team"],
            "away_team": probs["away_team"],
            "confidence": conf,
            "model": {
                "version": CORNER_MODEL_VERSION,
                "dispersion": dispersion_r,
                "dispersion_source": dispersion_source,
                "baseline_source": diag["baseline_source"]
            },
            "diagnostics": {
                **cov_info,
                **diag
            }
        }

        # Persist prediction snapshot if requested
        if save_snapshot:
            try:
                CornerSnapshotService.save_prediction_snapshot(db, fixture_id, res_payload, is_prematch=(fixture.status == "SCHEDULED"))
            except Exception as snap_ex:
                logger.debug(f"Error persisting corner snapshot for fixture {fixture_id}: {snap_ex}")

        return res_payload


class CornerSnapshotService:
    """
    Service responsible for storing immutable prediction snapshots and verifying
    finished match results against original predictions.
    """

    @classmethod
    def save_prediction_snapshot(
        cls, db: Session, fixture_id: int, pred_dict: Dict[str, Any], is_prematch: bool = True
    ) -> Optional[CornerPredictionSnapshot]:
        """
        Persists a prediction snapshot. If a pre-match snapshot already exists,
        it does NOT overwrite it with a later live prediction.
        """
        if not pred_dict.get("available") or not pred_dict.get("expected") or not pred_dict.get("total_markets"):
            return None

        existing = (
            db.query(CornerPredictionSnapshot)
            .filter(
                CornerPredictionSnapshot.fixture_id == fixture_id,
                CornerPredictionSnapshot.model_version == CORNER_MODEL_VERSION
            )
            .first()
        )

        # Do not overwrite existing pre-match snapshot
        if existing and existing.is_prematch and not is_prematch:
            return existing

        tot_m = pred_dict["total_markets"]
        h_m = pred_dict.get("home_team", {}) or {}
        a_m = pred_dict.get("away_team", {}) or {}
        exp = pred_dict["expected"]
        conf = pred_dict.get("confidence", {}) or {}
        model_meta = pred_dict.get("model", {}) or {}

        if existing:
            existing.expected_home_corners = exp["home"]
            existing.expected_away_corners = exp["away"]
            existing.expected_total_corners = exp["total"]
            existing.over_7_5_prob = tot_m["over_7_5"]
            existing.under_7_5_prob = tot_m["under_7_5"]
            existing.over_8_5_prob = tot_m["over_8_5"]
            existing.under_8_5_prob = tot_m["under_8_5"]
            existing.over_9_5_prob = tot_m["over_9_5"]
            existing.under_9_5_prob = tot_m["under_9_5"]
            existing.over_10_5_prob = tot_m["over_10_5"]
            existing.under_10_5_prob = tot_m["under_10_5"]
            existing.over_11_5_prob = tot_m["over_11_5"]
            existing.under_11_5_prob = tot_m["under_11_5"]
            existing.home_over_3_5_prob = h_m.get("over_3_5")
            existing.home_over_4_5_prob = h_m.get("over_4_5")
            existing.home_over_5_5_prob = h_m.get("over_5_5")
            existing.away_over_3_5_prob = a_m.get("over_3_5")
            existing.away_over_4_5_prob = a_m.get("over_4_5")
            existing.away_over_5_5_prob = a_m.get("over_5_5")
            existing.confidence_score = conf.get("overall", 50)
            existing.data_quality_score = conf.get("data_quality", 50)
            existing.sample_strength_score = conf.get("sample_strength", 50)
            existing.dispersion = model_meta.get("dispersion", DEFAULT_FALLBACK_DISPERSION)
            existing.dispersion_source = model_meta.get("dispersion_source", "fallback")
            existing.baseline_source = model_meta.get("baseline_source", "fallback")
            db.commit()
            return existing

        snapshot = CornerPredictionSnapshot(
            fixture_id=fixture_id,
            model_version=CORNER_MODEL_VERSION,
            prediction_timestamp=datetime.now(timezone.utc),
            is_prematch=is_prematch,
            expected_home_corners=exp["home"],
            expected_away_corners=exp["away"],
            expected_total_corners=exp["total"],
            over_7_5_prob=tot_m["over_7_5"],
            under_7_5_prob=tot_m["under_7_5"],
            over_8_5_prob=tot_m["over_8_5"],
            under_8_5_prob=tot_m["under_8_5"],
            over_9_5_prob=tot_m["over_9_5"],
            under_9_5_prob=tot_m["under_9_5"],
            over_10_5_prob=tot_m["over_10_5"],
            under_10_5_prob=tot_m["under_10_5"],
            over_11_5_prob=tot_m["over_11_5"],
            under_11_5_prob=tot_m["under_11_5"],
            home_over_3_5_prob=h_m.get("over_3_5"),
            home_over_4_5_prob=h_m.get("over_4_5"),
            home_over_5_5_prob=h_m.get("over_5_5"),
            away_over_3_5_prob=a_m.get("over_3_5"),
            away_over_4_5_prob=a_m.get("over_4_5"),
            away_over_5_5_prob=a_m.get("over_5_5"),
            confidence_score=conf.get("overall", 50),
            data_quality_score=conf.get("data_quality", 50),
            sample_strength_score=conf.get("sample_strength", 50),
            dispersion=model_meta.get("dispersion", DEFAULT_FALLBACK_DISPERSION),
            dispersion_source=model_meta.get("dispersion_source", "fallback"),
            baseline_source=model_meta.get("baseline_source", "fallback"),
            is_verified=False
        )
        db.add(snapshot)
        db.commit()
        return snapshot

    @classmethod
    def verify_finished_fixture_corners(
        cls, db: Session, fixture_id: int, actual_home: int, actual_away: int
    ) -> Optional[CornerPredictionSnapshot]:
        """
        Validates finished match corner result against the original snapshot.
        Enforces non-negative constraints (home >= 0, away >= 0, total = home + away)
        and records verification status.
        """
        if actual_home < 0 or actual_away < 0:
            logger.warning(f"Invalid negative corner counts rejected for fixture {fixture_id}: ({actual_home}, {actual_away})")
            return None

        actual_total = actual_home + actual_away

        snapshot = (
            db.query(CornerPredictionSnapshot)
            .filter(
                CornerPredictionSnapshot.fixture_id == fixture_id,
                CornerPredictionSnapshot.model_version == CORNER_MODEL_VERSION
            )
            .first()
        )

        if snapshot:
            snapshot.actual_home_corners = actual_home
            snapshot.actual_away_corners = actual_away
            snapshot.actual_total_corners = actual_total
            snapshot.is_verified = True
            snapshot.verified_at = datetime.now(timezone.utc)
            db.commit()

        return snapshot


class CornersModelEvaluationService:
    """
    Evaluation engine comparing 4 corner model architectures on chronological historical test sets:
    - Model A: League Baseline Model
    - Model B: Independent Poisson Model
    - Model C: Static Negative Binomial Model
    - Model D: Team-Strength Negative Binomial (Production Model)
    """

    @classmethod
    def evaluate_model_comparison(cls, db: Session, fixtures_with_stats: List[Tuple[Fixture, MatchStatistics]]) -> Dict[str, Any]:
        """
        Performs out-of-sample chronological evaluation across all 4 candidate models.
        """
        n = len(fixtures_with_stats)
        if n == 0:
            return {"status": "insufficient_data", "sample_size": 0}

        model_keys = ["model_a_league_baseline", "model_b_poisson", "model_c_static_nb", "model_d_production_nb"]
        thresholds = [7.5, 8.5, 9.5, 10.5, 11.5]

        metrics = {
            m: {
                "brier_scores": {f"over_{int(t)}_{int((t%1)*10)}": 0.0 for t in thresholds},
                "log_loss": 0.0,
                "mae_total": 0.0,
                "rmse_total": 0.0,
                "eval_count": 0
            } for m in model_keys
        }

        for f, stats in fixtures_with_stats:
            act_h = stats.home_corners or 0
            act_a = stats.away_corners or 0
            act_tot = act_h + act_a

            # Model D: Production Team-Strength Negative Binomial (trained on t < match_date)
            pred_d = CornersPredictionEngine.predict_corners(db, f.id, target_date=f.match_date, save_snapshot=False)
            if not pred_d.get("available") or not pred_d.get("expected"):
                continue

            exp_d = pred_d["expected"]
            m_d_tot = exp_d["total"]
            probs_d = pred_d["total_markets"]

            # Model A: League Baseline
            l_h, l_a, l_tot, _, _ = CornersPredictionEngine.resolve_league_baseline(db, f.league_id, target_date=f.match_date)
            probs_a = CornersPredictionEngine.calculate_corner_probabilities(l_h, l_a, dispersion=DEFAULT_FALLBACK_DISPERSION)["total_markets"]

            # Model B: Independent Poisson (dispersion -> infinity)
            probs_b = CornersPredictionEngine.calculate_corner_probabilities(exp_d["home"], exp_d["away"], dispersion=15.0)["total_markets"]

            # Model C: Static Negative Binomial (static r=5.5)
            probs_c = CornersPredictionEngine.calculate_corner_probabilities(exp_d["home"], exp_d["away"], dispersion=5.5)["total_markets"]

            eval_pack = [
                ("model_a_league_baseline", l_tot, probs_a),
                ("model_b_poisson", m_d_tot, probs_b),
                ("model_c_static_nb", m_d_tot, probs_c),
                ("model_d_production_nb", m_d_tot, probs_d),
            ]

            for m_key, exp_tot_val, probs_dict in eval_pack:
                m_rec = metrics[m_key]
                m_rec["eval_count"] += 1
                m_rec["mae_total"] += abs(exp_tot_val - act_tot)
                m_rec["rmse_total"] += (exp_tot_val - act_tot) ** 2

                ll_accum = 0.0
                for t in thresholds:
                    t_key = f"over_{int(t)}_{int((t%1)*10)}"
                    p = probs_dict.get(t_key, 0.5)
                    y = 1.0 if act_tot > t else 0.0
                    m_rec["brier_scores"][t_key] += (p - y) ** 2
                    p_safe = _clamp(p, 1e-6, 1.0 - 1e-6)
                    ll_accum += -(y * math.log(p_safe) + (1.0 - y) * math.log(1.0 - p_safe))
                m_rec["log_loss"] += (ll_accum / len(thresholds))

        # Normalize results
        res_summary = {}
        for m_key in model_keys:
            c = metrics[m_key]["eval_count"]
            if c > 0:
                brier_avg = sum(metrics[m_key]["brier_scores"].values()) / float(len(thresholds) * c)
                res_summary[m_key] = {
                    "sample_size": c,
                    "overall_brier_score": round(brier_avg, 4),
                    "log_loss": round(metrics[m_key]["log_loss"] / float(c), 4),
                    "mae_total_corners": round(metrics[m_key]["mae_total"] / float(c), 2),
                    "rmse_total_corners": round(math.sqrt(metrics[m_key]["rmse_total"] / float(c)), 2),
                    "brier_by_market": {k: round(v / float(c), 4) for k, v in metrics[m_key]["brier_scores"].items()}
                }

        return res_summary


class CornersBacktestService:
    """
    Chronological Backtesting Engine for the Corners Prediction Model.
    Evaluates real historical data with strict temporal separation and distinguishes
    between 'insufficient_data' and 'validated' states.
    """

    @classmethod
    def run_chronological_backtest(cls, db: Session, min_samples: int = MIN_REAL_BACKTEST_VALIDATION_SAMPLE) -> Dict[str, Any]:
        """
        Performs a chronological backtest across all historical finished fixtures
        containing verified observed corner counts.
        """
        fixtures = (
            db.query(Fixture, MatchStatistics)
            .join(MatchStatistics, MatchStatistics.fixture_id == Fixture.id)
            .filter(
                Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                MatchStatistics.home_corners.isnot(None),
                MatchStatistics.away_corners.isnot(None)
            )
            .order_by(Fixture.match_date.asc())
            .all()
        )

        n_matches = len(fixtures)
        if n_matches < min_samples:
            return {
                "status": "insufficient_data",
                "validation_status": "UNVALIDATED (Insufficient Real Data)",
                "message": f"Only {n_matches} completed matches with verified corner data found. Minimum {min_samples} required for production validation.",
                "matches_evaluated": n_matches,
                "required_matches": min_samples,
                "model_version": CORNER_MODEL_VERSION,
                "metrics": None
            }

        market_keys = ["over_7_5", "over_8_5", "over_9_5", "over_10_5", "over_11_5"]
        thresholds = [7.5, 8.5, 9.5, 10.5, 11.5]

        brier_accum = {m: 0.0 for m in market_keys}
        log_loss_accum = {m: 0.0 for m in market_keys}
        correct_accum = {m: 0 for m in market_keys}
        mae_tot_accum = 0.0
        rmse_tot_accum = 0.0

        # Calibration buckets: 50-60%, 60-70%, 70-80%, 80-90%, 90-100%
        calibration_buckets = {
            "50-60%": {"count": 0, "pred_sum": 0.0, "actual_hits": 0},
            "60-70%": {"count": 0, "pred_sum": 0.0, "actual_hits": 0},
            "70-80%": {"count": 0, "pred_sum": 0.0, "actual_hits": 0},
            "80-90%": {"count": 0, "pred_sum": 0.0, "actual_hits": 0},
            "90-100%": {"count": 0, "pred_sum": 0.0, "actual_hits": 0},
        }

        evaluated_count = 0

        for f, stats in fixtures:
            actual_home = stats.home_corners or 0
            actual_away = stats.away_corners or 0
            actual_total = actual_home + actual_away

            # Predict corners using ONLY matches strictly before f.match_date
            pred = CornersPredictionEngine.predict_corners(db, f.id, target_date=f.match_date, save_snapshot=False)
            if not pred["available"] or not pred["total_markets"] or not pred["expected"]:
                continue

            evaluated_count += 1
            tot_markets = pred["total_markets"]
            exp_tot = pred["expected"]["total"]

            mae_tot_accum += abs(exp_tot - actual_total)
            rmse_tot_accum += (exp_tot - actual_total) ** 2

            for m_key, thresh in zip(market_keys, thresholds):
                p = tot_markets[m_key]
                y = 1.0 if actual_total > thresh else 0.0

                # Brier score contribution: (p - y)^2
                brier_accum[m_key] += (p - y) ** 2

                # Log loss contribution
                p_safe = _clamp(p, 1e-6, 1.0 - 1e-6)
                ll = -(y * math.log(p_safe) + (1.0 - y) * math.log(1.0 - p_safe))
                log_loss_accum[m_key] += ll

                # Binary classification accuracy at threshold 0.50
                if (p >= 0.50 and y == 1.0) or (p < 0.50 and y == 0.0):
                    correct_accum[m_key] += 1

                # Calibration tracking for Over 8.5 and 9.5
                if m_key in ["over_8_5", "over_9_5"]:
                    if 0.50 <= p < 0.60:
                        b = calibration_buckets["50-60%"]
                    elif 0.60 <= p < 0.70:
                        b = calibration_buckets["60-70%"]
                    elif 0.70 <= p < 0.80:
                        b = calibration_buckets["70-80%"]
                    elif 0.80 <= p < 0.90:
                        b = calibration_buckets["80-90%"]
                    elif p >= 0.90:
                        b = calibration_buckets["90-100%"]
                    else:
                        b = None

                    if b:
                        b["count"] += 1
                        b["pred_sum"] += p
                        b["actual_hits"] += int(y)

        if evaluated_count < min_samples:
            return {
                "status": "insufficient_data",
                "validation_status": "UNVALIDATED (Insufficient Real Data)",
                "message": f"Evaluated {evaluated_count} fixtures with training history (< {min_samples} required).",
                "matches_evaluated": evaluated_count,
                "model_version": CORNER_MODEL_VERSION,
                "metrics": None
            }

        # Multi-model baseline comparison
        model_comparison = CornersModelEvaluationService.evaluate_model_comparison(db, fixtures)

        # Summarize market metrics
        market_metrics = {}
        for m_key in market_keys:
            market_metrics[m_key] = {
                "brier_score": round(brier_accum[m_key] / float(evaluated_count), 4),
                "log_loss": round(log_loss_accum[m_key] / float(evaluated_count), 4),
                "accuracy": round(correct_accum[m_key] / float(evaluated_count), 4),
                "sample_size": evaluated_count
            }

        calibration_summary = []
        for b_name, b_data in calibration_buckets.items():
            c = b_data["count"]
            avg_p = round(b_data["pred_sum"] / float(c), 4) if c > 0 else 0.0
            act_rate = round(b_data["actual_hits"] / float(c), 4) if c > 0 else 0.0
            calibration_summary.append({
                "bucket": b_name,
                "sample_size": c,
                "avg_predicted_prob": avg_p,
                "actual_hit_rate": act_rate
            })

        return {
            "status": "validated",
            "validation_status": "VALIDATED",
            "model_version": CORNER_MODEL_VERSION,
            "sample_size": evaluated_count,
            "overall_brier_score": round(sum(m["brier_score"] for m in market_metrics.values()) / len(market_keys), 4),
            "log_loss": round(sum(m["log_loss"] for m in market_metrics.values()) / len(market_keys), 4),
            "mae_total_corners": round(mae_tot_accum / float(evaluated_count), 2),
            "rmse_total_corners": round(math.sqrt(rmse_tot_accum / float(evaluated_count)), 2),
            "markets": market_metrics,
            "calibration": calibration_summary,
            "model_comparison": model_comparison
        }
