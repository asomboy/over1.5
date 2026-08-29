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
    from models import (
        Fixture, HistoricalResult, MatchStatistics, League, Team,
        Referee, RefereeMatchStatistics, CardPredictionSnapshot
    )
    from services.prediction_service import _negative_binomial_pmf, _shrink_to_prior, _clamp
    from schemas.prediction_schema import CardsPrediction
except ImportError:
    from ..models import (
        Fixture, HistoricalResult, MatchStatistics, League, Team,
        Referee, RefereeMatchStatistics, CardPredictionSnapshot
    )
    from .prediction_service import _negative_binomial_pmf, _shrink_to_prior, _clamp
    from ..schemas.prediction_schema import CardsPrediction

logger = logging.getLogger(__name__)

CARDS_MODEL_VERSION = "v1_cards_nb"
DEFAULT_FALLBACK_CARDS_DISPERSION = 4.0
MIN_CARDS_DISPERSION = 1.2
MAX_CARDS_DISPERSION = 15.0

# League Baseline Thresholds
MIN_COMPETITION_CARDS_SAMPLES = 20
MIN_SHRUNK_CARDS_SAMPLES = 8
MIN_GLOBAL_CARDS_SAMPLES = 30

# Referee Baseline Thresholds
MIN_REFEREE_SPECIFIC_SAMPLES = 20
MIN_SHRUNK_REFEREE_SAMPLES = 5

# Fallback Baselines
FALLBACK_LEAGUE_HOME_CARDS = 2.00
FALLBACK_LEAGUE_AWAY_CARDS = 2.20
FALLBACK_LEAGUE_TOTAL_CARDS = 4.20
FALLBACK_RED_CARD_RATE_PER_TEAM = 0.05

# Production Backtest Validation Threshold
MIN_REAL_CARDS_BACKTEST_VALIDATION_SAMPLE = 100


class CardDataQualityService:
    """
    Data quality and audit service for disciplinary statistics and referee coverage.
    """

    @classmethod
    def get_database_card_data_quality(cls, db: Session) -> Dict[str, Any]:
        """
        Audits database card data completeness and referee coverage across all eligible finished matches.
        """
        finished_query = db.query(Fixture).filter(Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]))
        total_eligible = finished_query.count()

        card_matches_query = (
            db.query(Fixture)
            .join(MatchStatistics, MatchStatistics.fixture_id == Fixture.id)
            .filter(
                Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                MatchStatistics.home_yellow_cards.isnot(None),
                MatchStatistics.away_yellow_cards.isnot(None)
            )
        )
        total_card_matches = card_matches_query.count()
        missing_card_matches = max(0, total_eligible - total_card_matches)
        coverage = round(total_card_matches / float(total_eligible), 4) if total_eligible > 0 else 0.0

        referee_records_count = (
            db.query(MatchStatistics)
            .filter(MatchStatistics.referee_name.isnot(None))
            .count()
        )
        referee_coverage = round(referee_records_count / float(total_eligible), 4) if total_eligible > 0 else 0.0

        # Competition breakdowns
        leagues = db.query(League).all()
        competitions = []

        for league in leagues:
            l_eligible = db.query(Fixture).filter(
                Fixture.league_id == league.id,
                Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"])
            ).count()

            l_card_records = (
                db.query(MatchStatistics)
                .join(Fixture, Fixture.id == MatchStatistics.fixture_id)
                .filter(
                    Fixture.league_id == league.id,
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    MatchStatistics.home_yellow_cards.isnot(None),
                    MatchStatistics.away_yellow_cards.isnot(None)
                )
                .all()
            )
            l_cards_count = len(l_card_records)

            if l_eligible > 0 or l_cards_count > 0:
                l_cov = round(l_cards_count / float(l_eligible), 4) if l_eligible > 0 else 0.0
                if l_cards_count > 0:
                    tot_vals = [(m.total_cards or (m.home_yellow_cards + m.away_yellow_cards + (m.home_red_cards or 0) + (m.away_red_cards or 0))) for m in l_card_records]
                    avg_tot = round(sum(tot_vals) / float(l_cards_count), 2)
                    var_tot = round(sum((x - avg_tot) ** 2 for x in tot_vals) / float(l_cards_count), 2)
                else:
                    avg_tot = None
                    var_tot = None

                competitions.append({
                    "competition_id": league.id,
                    "competition": league.name,
                    "country": league.country,
                    "eligible_matches": l_eligible,
                    "matches_with_card_data": l_cards_count,
                    "coverage": l_cov,
                    "average_total_cards": avg_tot,
                    "variance": var_tot,
                    "sample_size": l_cards_count
                })

        sources = [r[0] for r in db.query(MatchStatistics.data_source).distinct().all() if r[0]]

        return {
            "eligible_matches": total_eligible,
            "matches_with_card_data": total_card_matches,
            "missing_card_data": missing_card_matches,
            "coverage": coverage,
            "referee_data_coverage": referee_coverage,
            "competitions": sorted(competitions, key=lambda x: x["matches_with_card_data"], reverse=True),
            "data_sources": sources or ["observed"]
        }

    @classmethod
    def get_fixture_card_coverage(
        cls, db: Session, home_team_id: int, away_team_id: int, league_id: Optional[int], target_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Calculates exact historical card sample size and true coverage for a matchup.
        Strictly excludes matches on or after target_date for temporal safety.
        """
        naive_target = target_date.astimezone(timezone.utc).replace(tzinfo=None) if (target_date and target_date.tzinfo) else target_date

        def get_team_stats(team_id: int) -> Tuple[int, int]:
            q_elig = db.query(Fixture).filter(
                or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
                Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"])
            )
            if naive_target:
                q_elig = q_elig.filter(Fixture.match_date < naive_target)
            elig = q_elig.count()

            q_obs = (
                db.query(MatchStatistics)
                .join(Fixture, Fixture.id == MatchStatistics.fixture_id)
                .filter(
                    or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    MatchStatistics.home_yellow_cards.isnot(None),
                    MatchStatistics.away_yellow_cards.isnot(None)
                )
            )
            if naive_target:
                q_obs = q_obs.filter(Fixture.match_date < naive_target)
            obs = q_obs.count()
            return elig, obs

        h_elig, h_obs = get_team_stats(home_team_id)
        a_elig, a_obs = get_team_stats(away_team_id)
        tot_elig = h_elig + a_elig
        tot_obs = h_obs + a_obs
        cov = round(tot_obs / float(tot_elig), 4) if tot_elig > 0 else 0.0

        # League stats
        l_elig, l_obs = 0, 0
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
                    MatchStatistics.home_yellow_cards.isnot(None),
                    MatchStatistics.away_yellow_cards.isnot(None)
                )
            )
            if naive_target:
                q_lobs = q_lobs.filter(Fixture.match_date < naive_target)
            l_obs = q_lobs.count()

        l_cov = round(l_obs / float(l_elig), 4) if l_elig > 0 else 0.0

        return {
            "card_data_coverage": cov,
            "card_sample_size": tot_obs,
            "eligible_match_count": tot_elig,
            "observed_card_match_count": tot_obs,
            "missing_card_match_count": max(0, tot_elig - tot_obs),
            "home_sample_size": h_obs,
            "home_eligible_count": h_elig,
            "away_sample_size": a_obs,
            "away_eligible_count": a_elig,
            "league_sample_size": l_obs,
            "league_eligible_count": l_elig,
            "league_card_coverage": l_cov
        }


class RefereeIntelligenceService:
    """
    Service resolving referee-specific disciplinary baselines and adjustments.
    Follows a 5-tier hierarchy:
    Tier 1: Referee-specific (N >= 20)
    Tier 2: Shrunk referee estimate (5 <= N < 20)
    Tier 3: Competition baseline
    Tier 4: Global baseline
    Tier 5: Fallback baseline
    """

    @classmethod
    def resolve_referee_adjustment(
        cls, db: Optional[Session], referee_name: Optional[str], league_id: Optional[int], target_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Resolves referee card tendency adjustment factor (neutral = 1.0).
        """
        if not db or not referee_name or not referee_name.strip():
            return {
                "available": False,
                "referee_name": None,
                "sample_size": 0,
                "average_cards": None,
                "influence_factor": 1.0,
                "influence_label": "Neutral",
                "source": "fallback"
            }

        clean_ref = referee_name.strip()
        naive_target = target_date.astimezone(timezone.utc).replace(tzinfo=None) if (target_date and target_date.tzinfo) else target_date

        try:
            # Query referee matches in MatchStatistics
            q_ref = (
                db.query(MatchStatistics)
                .join(Fixture, Fixture.id == MatchStatistics.fixture_id)
                .filter(
                    func.lower(MatchStatistics.referee_name) == clean_ref.lower(),
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    MatchStatistics.home_yellow_cards.isnot(None),
                    MatchStatistics.away_yellow_cards.isnot(None)
                )
            )
            if naive_target:
                q_ref = q_ref.filter(Fixture.match_date < naive_target)

            ref_matches = q_ref.all()
            n_ref = len(ref_matches)

            # Get league baseline for comparison
            l_home, l_away, l_tot, _, _ = LeagueCardBaselineService.resolve_league_baseline(db, league_id, target_date=target_date)

            if n_ref >= MIN_REFEREE_SPECIFIC_SAMPLES:
                tot_cards = [
                    (m.total_cards or (m.home_yellow_cards + m.away_yellow_cards + (m.home_red_cards or 0) + (m.away_red_cards or 0)))
                    for m in ref_matches
                ]
                avg_ref = sum(tot_cards) / float(n_ref)
                raw_ratio = avg_ref / max(1.0, l_tot)
                adj = _clamp(raw_ratio, 0.70, 1.40)
                diff_pct = int(round((adj - 1.0) * 100))
                label = f"+{diff_pct}% Strict" if diff_pct > 5 else (f"{diff_pct}% Lenient" if diff_pct < -5 else "Neutral")
                return {
                    "available": True,
                    "referee_name": clean_ref,
                    "sample_size": n_ref,
                    "average_cards": round(avg_ref, 2),
                    "influence_factor": round(adj, 3),
                    "influence_label": label,
                    "source": "referee"
                }
            elif n_ref >= MIN_SHRUNK_REFEREE_SAMPLES:
                tot_cards = [
                    (m.total_cards or (m.home_yellow_cards + m.away_yellow_cards + (m.home_red_cards or 0) + (m.away_red_cards or 0)))
                    for m in ref_matches
                ]
                avg_ref = sum(tot_cards) / float(n_ref)
                raw_ratio = avg_ref / max(1.0, l_tot)
                w = n_ref / float(MIN_REFEREE_SPECIFIC_SAMPLES)
                shrunk_adj = _shrink_to_prior(raw_ratio, 1.0, w)
                adj = _clamp(shrunk_adj, 0.75, 1.30)
                diff_pct = int(round((adj - 1.0) * 100))
                label = f"+{diff_pct}% Strict" if diff_pct > 4 else (f"{diff_pct}% Lenient" if diff_pct < -4 else "Neutral")
                return {
                    "available": True,
                    "referee_name": clean_ref,
                    "sample_size": n_ref,
                    "average_cards": round(avg_ref, 2),
                    "influence_factor": round(adj, 3),
                    "influence_label": label,
                    "source": "shrunk_referee"
                }
            else:
                return {
                    "available": False,
                    "referee_name": clean_ref,
                    "sample_size": n_ref,
                    "average_cards": None,
                    "influence_factor": 1.0,
                    "influence_label": "Neutral",
                    "source": "competition" if league_id else "fallback"
                }
        except Exception as e:
            logger.debug(f"Error resolving referee adjustment for {clean_ref}: {e}")
            return {
                "available": False,
                "referee_name": clean_ref,
                "sample_size": 0,
                "average_cards": None,
                "influence_factor": 1.0,
                "influence_label": "Neutral",
                "source": "fallback"
            }


class LeagueCardBaselineService:
    """
    Service resolving hierarchical league disciplinary baselines and dispersion parameters.
    """

    @classmethod
    def resolve_league_baseline(
        cls, db: Optional[Session], league_id: Optional[int], target_date: Optional[datetime] = None
    ) -> Tuple[float, float, float, str, int]:
        """
        Resolves league card baselines (home_avg, away_avg, total_avg, source, sample_size).
        """
        if not db:
            return FALLBACK_LEAGUE_HOME_CARDS, FALLBACK_LEAGUE_AWAY_CARDS, FALLBACK_LEAGUE_TOTAL_CARDS, "fallback", 0

        naive_target = target_date.astimezone(timezone.utc).replace(tzinfo=None) if (target_date and target_date.tzinfo) else target_date

        def compute_global_baseline() -> Tuple[float, float, float, str, int]:
            try:
                g_query = (
                    db.query(MatchStatistics)
                    .join(Fixture, Fixture.id == MatchStatistics.fixture_id)
                    .filter(
                        Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                        MatchStatistics.home_yellow_cards.isnot(None),
                        MatchStatistics.away_yellow_cards.isnot(None)
                    )
                )
                if naive_target:
                    g_query = g_query.filter(Fixture.match_date < naive_target)
                
                g_matches = g_query.limit(300).all()
                n_g = len(g_matches)
                if n_g >= MIN_GLOBAL_CARDS_SAMPLES:
                    gh = sum(m.home_yellow_cards + (m.home_red_cards or 0) for m in g_matches) / float(n_g)
                    ga = sum(m.away_yellow_cards + (m.away_red_cards or 0) for m in g_matches) / float(n_g)
                    return round(gh, 2), round(ga, 2), round(gh + ga, 2), "global", n_g
            except Exception as e:
                logger.debug(f"Global card baseline error: {e}")
            return FALLBACK_LEAGUE_HOME_CARDS, FALLBACK_LEAGUE_AWAY_CARDS, FALLBACK_LEAGUE_TOTAL_CARDS, "fallback", 0

        if not league_id:
            return compute_global_baseline()

        try:
            l_query = (
                db.query(MatchStatistics)
                .join(Fixture, Fixture.id == MatchStatistics.fixture_id)
                .filter(
                    Fixture.league_id == league_id,
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    MatchStatistics.home_yellow_cards.isnot(None),
                    MatchStatistics.away_yellow_cards.isnot(None)
                )
            )
            if naive_target:
                l_query = l_query.filter(Fixture.match_date < naive_target)

            l_matches = l_query.limit(200).all()
            n_l = len(l_matches)

            if n_l >= MIN_COMPETITION_CARDS_SAMPLES:
                lh = sum(m.home_yellow_cards + (m.home_red_cards or 0) for m in l_matches) / float(n_l)
                la = sum(m.away_yellow_cards + (m.away_red_cards or 0) for m in l_matches) / float(n_l)
                return round(lh, 2), round(la, 2), round(lh + la, 2), "competition", n_l
            elif n_l >= MIN_SHRUNK_CARDS_SAMPLES:
                lh_raw = sum(m.home_yellow_cards + (m.home_red_cards or 0) for m in l_matches) / float(n_l)
                la_raw = sum(m.away_yellow_cards + (m.away_red_cards or 0) for m in l_matches) / float(n_l)
                gh, ga, _, _, _ = compute_global_baseline()
                w = n_l / float(MIN_COMPETITION_CARDS_SAMPLES)
                shrunk_h = _shrink_to_prior(lh_raw, gh, w)
                shrunk_a = _shrink_to_prior(la_raw, ga, w)
                return round(shrunk_h, 2), round(shrunk_a, 2), round(shrunk_h + shrunk_a, 2), "shrunk_competition", n_l
            else:
                return compute_global_baseline()
        except Exception as e:
            logger.warning(f"Error calculating card baseline for league {league_id}: {e}")
            return compute_global_baseline()

    @classmethod
    def resolve_dispersion(
        cls, db: Optional[Session], league_id: Optional[int], target_date: Optional[datetime] = None
    ) -> Tuple[float, str]:
        """
        Resolves Negative Binomial dispersion parameter r for cards.
        """
        if not db:
            return DEFAULT_FALLBACK_CARDS_DISPERSION, "fallback"

        naive_target = target_date.astimezone(timezone.utc).replace(tzinfo=None) if (target_date and target_date.tzinfo) else target_date

        def compute_global_dispersion() -> Tuple[float, str]:
            try:
                g_query = (
                    db.query(MatchStatistics)
                    .join(Fixture, Fixture.id == MatchStatistics.fixture_id)
                    .filter(
                        Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                        MatchStatistics.home_yellow_cards.isnot(None),
                        MatchStatistics.away_yellow_cards.isnot(None)
                    )
                )
                if naive_target:
                    g_query = g_query.filter(Fixture.match_date < naive_target)
                
                matches = g_query.limit(300).all()
                if len(matches) >= 30:
                    cards = [
                        (m.total_cards or (m.home_yellow_cards + m.away_yellow_cards + (m.home_red_cards or 0) + (m.away_red_cards or 0)))
                        for m in matches
                    ]
                    n = len(cards)
                    mean_val = sum(cards) / float(n)
                    var_val = sum((c - mean_val) ** 2 for c in cards) / float(n)
                    if var_val > mean_val:
                        r = (mean_val ** 2) / (var_val - mean_val)
                        return round(_clamp(r, MIN_CARDS_DISPERSION, MAX_CARDS_DISPERSION), 2), "global"
                    else:
                        return MAX_CARDS_DISPERSION, "global"
            except Exception as e:
                logger.debug(f"Global card dispersion error: {e}")
            return DEFAULT_FALLBACK_CARDS_DISPERSION, "fallback"

        if not league_id:
            return compute_global_dispersion()

        try:
            l_query = (
                db.query(MatchStatistics)
                .join(Fixture, Fixture.id == MatchStatistics.fixture_id)
                .filter(
                    Fixture.league_id == league_id,
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    MatchStatistics.home_yellow_cards.isnot(None),
                    MatchStatistics.away_yellow_cards.isnot(None)
                )
            )
            if naive_target:
                l_query = l_query.filter(Fixture.match_date < naive_target)

            league_matches = l_query.limit(200).all()
            n_comp = len(league_matches)

            if n_comp >= MIN_COMPETITION_CARDS_SAMPLES:
                cards = [
                    (m.total_cards or (m.home_yellow_cards + m.away_yellow_cards + (m.home_red_cards or 0) + (m.away_red_cards or 0)))
                    for m in league_matches
                ]
                mean_val = sum(cards) / float(n_comp)
                var_val = sum((c - mean_val) ** 2 for c in cards) / float(n_comp)
                if var_val > mean_val:
                    r = (mean_val ** 2) / (var_val - mean_val)
                    return round(_clamp(r, MIN_CARDS_DISPERSION, MAX_CARDS_DISPERSION), 2), "competition"
                else:
                    return MAX_CARDS_DISPERSION, "competition"
            elif n_comp >= MIN_SHRUNK_CARDS_SAMPLES:
                cards = [
                    (m.total_cards or (m.home_yellow_cards + m.away_yellow_cards + (m.home_red_cards or 0) + (m.away_red_cards or 0)))
                    for m in league_matches
                ]
                mean_val = sum(cards) / float(n_comp)
                var_val = sum((c - mean_val) ** 2 for c in cards) / float(n_comp)
                raw_comp = (mean_val ** 2) / max(0.1, (var_val - mean_val)) if var_val > mean_val else MAX_CARDS_DISPERSION
                glob_r, _ = compute_global_dispersion()
                w = n_comp / float(MIN_COMPETITION_CARDS_SAMPLES)
                shrunk_r = _shrink_to_prior(raw_comp, glob_r, w)
                return round(_clamp(shrunk_r, MIN_CARDS_DISPERSION, MAX_CARDS_DISPERSION), 2), "shrunk_competition"
            else:
                return compute_global_dispersion()
        except Exception as e:
            logger.warning(f"Error calculating card dispersion for league {league_id}: {e}")
            return compute_global_dispersion()


class TeamDisciplineService:
    """
    Extracts time-decay weighted disciplinary metrics:
    - Cards received (for)
    - Opponent cards forced (against)
    - Rolling 5 and Rolling 10 records
    - Venue-specific tendencies
    """
    XI_TIME_DECAY = 0.0035

    @classmethod
    def get_team_discipline_features(
        cls, db: Session, team_id: int, is_home: bool, target_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Extracts team discipline features strictly prior to target_date.
        """
        fallback_for = FALLBACK_LEAGUE_HOME_CARDS if is_home else FALLBACK_LEAGUE_AWAY_CARDS
        fallback_forced = FALLBACK_LEAGUE_AWAY_CARDS if is_home else FALLBACK_LEAGUE_HOME_CARDS

        try:
            naive_target = target_date.astimezone(timezone.utc).replace(tzinfo=None) if (target_date and target_date.tzinfo) else target_date

            # Venue matches
            venue_query = (
                db.query(Fixture, MatchStatistics)
                .join(MatchStatistics, MatchStatistics.fixture_id == Fixture.id)
                .filter(
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    MatchStatistics.home_yellow_cards.isnot(None),
                    MatchStatistics.away_yellow_cards.isnot(None)
                )
            )
            if naive_target:
                venue_query = venue_query.filter(Fixture.match_date < naive_target)

            if is_home:
                venue_query = venue_query.filter(Fixture.home_team_id == team_id)
            else:
                venue_query = venue_query.filter(Fixture.away_team_id == team_id)

            venue_records = venue_query.order_by(Fixture.match_date.desc()).limit(25).all()

            # All matches for rolling history
            all_query = (
                db.query(Fixture, MatchStatistics)
                .join(MatchStatistics, MatchStatistics.fixture_id == Fixture.id)
                .filter(
                    or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    MatchStatistics.home_yellow_cards.isnot(None),
                    MatchStatistics.away_yellow_cards.isnot(None)
                )
            )
            if naive_target:
                all_query = all_query.filter(Fixture.match_date < naive_target)

            all_records = all_query.order_by(Fixture.match_date.desc()).limit(20).all()

            if not venue_records and not all_records:
                return {
                    "avg_cards_received": fallback_for,
                    "avg_cards_forced": fallback_forced,
                    "recent_5_received": fallback_for,
                    "recent_5_forced": fallback_forced,
                    "recent_10_received": fallback_for,
                    "recent_10_forced": fallback_forced,
                    "sample_size": 0,
                    "red_card_count": 0
                }

            records_to_use = venue_records if len(venue_records) >= 3 else all_records
            ref_date = naive_target or datetime.now(timezone.utc).replace(tzinfo=None)

            weighted_recv = 0.0
            weighted_forced = 0.0
            weight_sum = 0.0
            raw_recv = []
            raw_forced = []
            tot_reds = 0

            for f, stats in records_to_use:
                days_diff = max(0, (ref_date - f.match_date).total_seconds() / 86400.0) if f.match_date else 0
                w = math.exp(-cls.XI_TIME_DECAY * days_diff)

                if f.home_team_id == team_id:
                    c_recv = (stats.home_yellow_cards or 0) + (stats.home_red_cards or 0)
                    c_forced = (stats.away_yellow_cards or 0) + (stats.away_red_cards or 0)
                    tot_reds += (stats.home_red_cards or 0)
                else:
                    c_recv = (stats.away_yellow_cards or 0) + (stats.away_red_cards or 0)
                    c_forced = (stats.home_yellow_cards or 0) + (stats.home_red_cards or 0)
                    tot_reds += (stats.away_red_cards or 0)

                weighted_recv += c_recv * w
                weighted_forced += c_forced * w
                weight_sum += w
                raw_recv.append(c_recv)
                raw_forced.append(c_forced)

            avg_recv = weighted_recv / weight_sum if weight_sum > 0 else fallback_for
            avg_forced = weighted_forced / weight_sum if weight_sum > 0 else fallback_forced

            r5_recv = sum(raw_recv[:5]) / float(len(raw_recv[:5])) if raw_recv[:5] else fallback_for
            r5_forced = sum(raw_forced[:5]) / float(len(raw_forced[:5])) if raw_forced[:5] else fallback_forced
            r10_recv = sum(raw_recv[:10]) / float(len(raw_recv[:10])) if raw_recv[:10] else fallback_for
            r10_forced = sum(raw_forced[:10]) / float(len(raw_forced[:10])) if raw_forced[:10] else fallback_forced

            return {
                "avg_cards_received": round(avg_recv, 2),
                "avg_cards_forced": round(avg_forced, 2),
                "recent_5_received": round(r5_recv, 2),
                "recent_5_forced": round(r5_forced, 2),
                "recent_10_received": round(r10_recv, 2),
                "recent_10_forced": round(r10_forced, 2),
                "sample_size": len(venue_records),
                "total_sample_size": len(all_records),
                "red_card_count": tot_reds
            }
        except Exception as e:
            logger.debug(f"Error fetching team discipline features for team {team_id}: {e}")
            return {
                "avg_cards_received": fallback_for,
                "avg_cards_forced": fallback_forced,
                "recent_5_received": fallback_for,
                "recent_5_forced": fallback_forced,
                "recent_10_received": fallback_for,
                "recent_10_forced": fallback_forced,
                "sample_size": 0,
                "red_card_count": 0
            }


class CardsPredictionEngine:
    """
    Core Cards Prediction Engine (Model: v1_cards_nb).
    Predicts:
    1. Expected Cards (Home, Away, Total, Yellow splits)
    2. Over / Under Card Markets (1.5, 2.5, 3.5, 4.5, 5.5, 6.5)
    3. Team Card Markets (Home & Away 0.5, 1.5, 2.5, 3.5)
    4. Separate Low-Frequency Red Card Risk Model
    5. Referee Intelligence Adjustment
    6. Multi-Factor Card Confidence
    """

    @classmethod
    def calculate_expected_cards(
        cls, db: Session, home_team_id: int, away_team_id: int, league_id: Optional[int], referee_name: Optional[str] = None, target_date: Optional[datetime] = None
    ) -> Tuple[float, float, float, Dict[str, Any]]:
        """
        Calculates expected cards (lambda_home, lambda_away, lambda_total) with
        hierarchical baselines, team discipline factors, card forcing, and referee adjustments.
        """
        # 1. League Baseline
        l_home, l_away, l_tot, baseline_source, baseline_sample_size = LeagueCardBaselineService.resolve_league_baseline(
            db, league_id, target_date=target_date
        )

        # 2. Team Features
        h_feat = TeamDisciplineService.get_team_discipline_features(db, home_team_id, is_home=True, target_date=target_date)
        a_feat = TeamDisciplineService.get_team_discipline_features(db, away_team_id, is_home=False, target_date=target_date)

        # 3. Disciplinary & Forcing Ratios
        raw_h_disc = h_feat["avg_cards_received"] / max(0.8, l_home)
        raw_h_force = h_feat["avg_cards_forced"] / max(0.8, l_away)
        raw_a_disc = a_feat["avg_cards_received"] / max(0.8, l_away)
        raw_a_force = a_feat["avg_cards_forced"] / max(0.8, l_home)

        # 4. Bayesian Sample Shrinkage toward 1.0
        w_h = min(1.0, h_feat["sample_size"] / 8.0)
        w_a = min(1.0, a_feat["sample_size"] / 8.0)

        h_disc = _shrink_to_prior(raw_h_disc, 1.0, w_h)
        h_force = _shrink_to_prior(raw_h_force, 1.0, w_h)
        a_disc = _shrink_to_prior(raw_a_disc, 1.0, w_a)
        a_force = _shrink_to_prior(raw_a_force, 1.0, w_a)

        # 5. Referee Adjustment
        ref_info = RefereeIntelligenceService.resolve_referee_adjustment(db, referee_name, league_id, target_date=target_date)
        ref_adj = ref_info.get("influence_factor", 1.0)

        # 6. Expected Rates
        lambda_h = _clamp(l_home * h_disc * a_force * ref_adj, 0.50, 6.0)
        lambda_a = _clamp(l_away * a_disc * h_force * ref_adj, 0.60, 6.5)
        lambda_tot = round(lambda_h + lambda_a, 2)

        diagnostics = {
            "baseline_source": baseline_source,
            "baseline_sample_size": baseline_sample_size,
            "league_home_avg": l_home,
            "league_away_avg": l_away,
            "league_total_avg": l_tot,
            "home_discipline_factor": round(h_disc, 3),
            "away_forcing_factor": round(a_force, 3),
            "away_discipline_factor": round(a_disc, 3),
            "home_forcing_factor": round(h_force, 3),
            "referee": ref_info,
            "home_features": h_feat,
            "away_features": a_feat
        }

        return round(lambda_h, 2), round(lambda_a, 2), lambda_tot, diagnostics

    @classmethod
    def calculate_card_probabilities(
        cls, lambda_home: float, lambda_away: float, dispersion: float = DEFAULT_FALLBACK_CARDS_DISPERSION
    ) -> Dict[str, Any]:
        """
        Derives card probability distributions using Negative Binomial models + Discrete 2D Convolution.
        """
        lh = max(0.4, lambda_home)
        la = max(0.4, lambda_away)
        ltot = lh + la
        safe_r = _clamp(dispersion, MIN_CARDS_DISPERSION, MAX_CARDS_DISPERSION)

        rh = max(0.8, safe_r * (lh / ltot))
        ra = max(0.8, safe_r * (la / ltot))

        max_team_cards = 15
        home_pmf = [_negative_binomial_pmf(k, lh, rh) for k in range(max_team_cards)]
        away_pmf = [_negative_binomial_pmf(j, la, ra) for j in range(max_team_cards)]

        # Discrete Convolution for total match cards (0..30)
        max_total_cards = 30
        total_pmf = [0.0 for _ in range(max_total_cards)]
        for i in range(max_team_cards):
            for j in range(max_team_cards):
                tot = i + j
                if tot < max_total_cards:
                    total_pmf[tot] += home_pmf[i] * away_pmf[j]

        sum_tot = sum(total_pmf)
        if sum_tot > 0:
            total_pmf = [p / sum_tot for p in total_pmf]

        # Total Markets: Over / Under 1.5, 2.5, 3.5, 4.5, 5.5, 6.5
        p_o15 = sum(total_pmf[k] for k in range(2, max_total_cards))
        p_o25 = sum(total_pmf[k] for k in range(3, max_total_cards))
        p_o35 = sum(total_pmf[k] for k in range(4, max_total_cards))
        p_o45 = sum(total_pmf[k] for k in range(5, max_total_cards))
        p_o55 = sum(total_pmf[k] for k in range(6, max_total_cards))
        p_o65 = sum(total_pmf[k] for k in range(7, max_total_cards))

        # Home Team Markets: Over 0.5, 1.5, 2.5, 3.5
        sum_h = sum(home_pmf)
        h_norm = [p / sum_h for p in home_pmf] if sum_h > 0 else home_pmf
        p_h_o05 = sum(h_norm[k] for k in range(1, max_team_cards))
        p_h_o15 = sum(h_norm[k] for k in range(2, max_team_cards))
        p_h_o25 = sum(h_norm[k] for k in range(3, max_team_cards))
        p_h_o35 = sum(h_norm[k] for k in range(4, max_team_cards))

        # Away Team Markets: Over 0.5, 1.5, 2.5, 3.5
        sum_a = sum(away_pmf)
        a_norm = [p / sum_a for p in away_pmf] if sum_a > 0 else away_pmf
        p_a_o05 = sum(a_norm[j] for j in range(1, max_team_cards))
        p_a_o15 = sum(a_norm[j] for j in range(2, max_team_cards))
        p_a_o25 = sum(a_norm[j] for j in range(3, max_team_cards))
        p_a_o35 = sum(a_norm[j] for j in range(4, max_team_cards))

        return {
            "total_markets": {
                "over_1_5": round(p_o15, 4),
                "under_1_5": round(max(0.0, 1.0 - p_o15), 4),
                "over_2_5": round(p_o25, 4),
                "under_2_5": round(max(0.0, 1.0 - p_o25), 4),
                "over_3_5": round(p_o35, 4),
                "under_3_5": round(max(0.0, 1.0 - p_o35), 4),
                "over_4_5": round(p_o45, 4),
                "under_4_5": round(max(0.0, 1.0 - p_o45), 4),
                "over_5_5": round(p_o55, 4),
                "under_5_5": round(max(0.0, 1.0 - p_o55), 4),
                "over_6_5": round(p_o65, 4),
                "under_6_5": round(max(0.0, 1.0 - p_o65), 4),
            },
            "home_team": {
                "over_0_5": round(p_h_o05, 4),
                "over_1_5": round(p_h_o15, 4),
                "over_2_5": round(p_h_o25, 4),
                "over_3_5": round(p_h_o35, 4),
            },
            "away_team": {
                "over_0_5": round(p_a_o05, 4),
                "over_1_5": round(p_a_o15, 4),
                "over_2_5": round(p_a_o25, 4),
                "over_3_5": round(p_a_o35, 4),
            }
        }

    @classmethod
    def calculate_red_card_risk(
        cls, lambda_home: float, lambda_away: float, h_reds: int, a_reds: int, h_sample: int, a_sample: int
    ) -> Dict[str, Any]:
        """
        Low-frequency red card risk model (Poisson/Bernoulli process).
        """
        base_h = (h_reds / float(max(1, h_sample))) if h_sample >= 5 else FALLBACK_RED_CARD_RATE_PER_TEAM
        base_a = (a_reds / float(max(1, a_sample))) if a_sample >= 5 else FALLBACK_RED_CARD_RATE_PER_TEAM

        lam_h_red = _clamp(base_h * (lambda_home / 2.0), 0.02, 0.25)
        lam_a_red = _clamp(base_a * (lambda_away / 2.2), 0.02, 0.28)

        p_h_red = round(1.0 - math.exp(-lam_h_red), 4)
        p_a_red = round(1.0 - math.exp(-lam_a_red), 4)
        p_any_red = round(1.0 - ((1.0 - p_h_red) * (1.0 - p_a_red)), 4)

        if p_any_red >= 0.28:
            risk_label = "High"
        elif p_any_red >= 0.15:
            risk_label = "Moderate"
        else:
            risk_label = "Low"

        return {
            "available": True,
            "any_red_prob": p_any_red,
            "home_red_prob": p_h_red,
            "away_red_prob": p_a_red,
            "risk_level": risk_label
        }

    @classmethod
    def calculate_card_confidence(
        cls, coverage_info: Dict[str, Any], h_feat: Dict[str, Any], a_feat: Dict[str, Any], ref_info: Dict[str, Any], baseline_source: str, dispersion_source: str
    ) -> Dict[str, Any]:
        """
        Multi-dimensional card confidence score (0-100).
        """
        h_sample = h_feat["sample_size"]
        a_sample = a_feat["sample_size"]
        tot_sample = h_sample + a_sample
        cov = coverage_info.get("card_data_coverage", 0.0)

        # 1. Data Quality
        if tot_sample >= 20 and cov >= 0.75:
            dq_base = 88
        elif tot_sample >= 12 and cov >= 0.60:
            dq_base = 76
        elif tot_sample >= 6 and cov >= 0.40:
            dq_base = 60
        elif tot_sample >= 2:
            dq_base = 40
        else:
            dq_base = 20

        data_quality = int(round(_clamp(dq_base, 15, 95)))

        # 2. Sample Strength
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

        # 3. Model Stability
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

        # 4. Referee Confidence
        ref_n = ref_info.get("sample_size", 0)
        if ref_n >= 20:
            ref_conf = 88
        elif ref_n >= 5:
            ref_conf = 68
        elif ref_info.get("available"):
            ref_conf = 50
        else:
            ref_conf = 40

        overall = int(round(0.35 * data_quality + 0.30 * sample_strength + 0.20 * model_stability + 0.15 * ref_conf))

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
            "referee_confidence": ref_conf,
            "label": label
        }

    @classmethod
    def predict_cards(
        cls, db: Session, fixture_id: int, target_date: Optional[datetime] = None, save_snapshot: bool = True
    ) -> Dict[str, Any]:
        """
        Executes full Cards Prediction pipeline for a fixture.
        Returns complete CardsPrediction structure and persists snapshot.
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
                "red_card_risk": None,
                "referee": None,
                "confidence": {"overall": 0, "data_quality": 0, "sample_strength": 0, "model_stability": 0, "referee_confidence": 0, "label": "insufficient"},
                "model": {"version": CARDS_MODEL_VERSION, "dispersion": DEFAULT_FALLBACK_CARDS_DISPERSION, "dispersion_source": "fallback", "baseline_source": "fallback", "referee_source": "fallback"}
            }

        # Check coverage
        cov_info = CardDataQualityService.get_fixture_card_coverage(
            db, cast(int, fixture.home_team_id), cast(int, fixture.away_team_id), fixture.league_id, target_date=target_date
        )

        h_sample = cov_info["home_sample_size"]
        a_sample = cov_info["away_sample_size"]
        l_sample = cov_info["league_sample_size"]

        # Minimum data eligibility gate
        if h_sample == 0 and a_sample == 0 and l_sample == 0:
            return {
                "available": False,
                "reason": "Insufficient verified disciplinary history for a reliable card prediction.",
                "expected": None,
                "total_markets": None,
                "home_team": None,
                "away_team": None,
                "red_card_risk": None,
                "referee": None,
                "confidence": {
                    "overall": 20,
                    "data_quality": 20,
                    "sample_strength": 20,
                    "model_stability": 20,
                    "referee_confidence": 20,
                    "label": "insufficient"
                },
                "model": {
                    "version": CARDS_MODEL_VERSION,
                    "dispersion": DEFAULT_FALLBACK_CARDS_DISPERSION,
                    "dispersion_source": "fallback",
                    "baseline_source": "fallback",
                    "referee_source": "fallback"
                },
                "diagnostics": {
                    **cov_info
                }
            }

        # Extract referee name from fixture or match statistics
        ref_name = getattr(fixture, "referee_name", None)
        if not ref_name:
            m_stats = db.query(MatchStatistics).filter(MatchStatistics.fixture_id == fixture_id).first()
            if m_stats:
                ref_name = m_stats.referee_name

        # Calculate expected cards
        xg_h, xg_a, xg_tot, diag = cls.calculate_expected_cards(
            db, cast(int, fixture.home_team_id), cast(int, fixture.away_team_id), fixture.league_id, referee_name=ref_name, target_date=target_date
        )

        # Resolve dispersion
        dispersion_r, dispersion_source = LeagueCardBaselineService.resolve_dispersion(db, fixture.league_id, target_date=target_date)

        # Calculate probabilities
        probs = cls.calculate_card_probabilities(xg_h, xg_a, dispersion=dispersion_r)

        # Calculate red card risk
        h_reds = diag["home_features"].get("red_card_count", 0)
        a_reds = diag["away_features"].get("red_card_count", 0)
        red_risk = cls.calculate_red_card_risk(xg_h, xg_a, h_reds, a_reds, h_sample, a_sample)

        # Calculate confidence
        conf = cls.calculate_card_confidence(
            cov_info, diag["home_features"], diag["away_features"], diag["referee"], diag["baseline_source"], dispersion_source
        )

        res_payload = {
            "available": True,
            "reason": None,
            "expected": {
                "home": xg_h,
                "away": xg_a,
                "total": xg_tot,
                "home_yellow": round(xg_h * 0.95, 2),
                "away_yellow": round(xg_a * 0.95, 2),
                "total_yellow": round(xg_tot * 0.95, 2)
            },
            "total_markets": probs["total_markets"],
            "home_team": probs["home_team"],
            "away_team": probs["away_team"],
            "red_card_risk": red_risk,
            "referee": diag["referee"],
            "confidence": conf,
            "model": {
                "version": CARDS_MODEL_VERSION,
                "dispersion": dispersion_r,
                "dispersion_source": dispersion_source,
                "baseline_source": diag["baseline_source"],
                "referee_source": diag["referee"].get("source", "fallback")
            },
            "diagnostics": {
                **cov_info,
                **diag
            }
        }

        if save_snapshot:
            try:
                CardSnapshotService.save_prediction_snapshot(db, fixture_id, res_payload, is_prematch=(fixture.status == "SCHEDULED"))
            except Exception as snap_ex:
                logger.debug(f"Error saving card snapshot for fixture {fixture_id}: {snap_ex}")

        return res_payload


class CardSnapshotService:
    """
    Service managing immutable card prediction snapshots and post-match result verification.
    """

    @classmethod
    def save_prediction_snapshot(
        cls, db: Session, fixture_id: int, pred_dict: Dict[str, Any], is_prematch: bool = True
    ) -> Optional[CardPredictionSnapshot]:
        """
        Persists a pre-match card prediction snapshot without overwriting earlier pre-match snapshots.
        """
        if not pred_dict.get("available") or not pred_dict.get("expected") or not pred_dict.get("total_markets"):
            return None

        existing = (
            db.query(CardPredictionSnapshot)
            .filter(
                CardPredictionSnapshot.fixture_id == fixture_id,
                CardPredictionSnapshot.model_version == CARDS_MODEL_VERSION
            )
            .first()
        )

        if existing and existing.is_prematch and not is_prematch:
            return existing

        tot_m = pred_dict["total_markets"]
        h_m = pred_dict.get("home_team", {}) or {}
        a_m = pred_dict.get("away_team", {}) or {}
        exp = pred_dict["expected"]
        conf = pred_dict.get("confidence", {}) or {}
        model_meta = pred_dict.get("model", {}) or {}
        red_info = pred_dict.get("red_card_risk", {}) or {}
        ref_info = pred_dict.get("referee", {}) or {}

        if existing:
            existing.expected_home_cards = exp["home"]
            existing.expected_away_cards = exp["away"]
            existing.expected_total_cards = exp["total"]
            existing.over_1_5_prob = tot_m["over_1_5"]
            existing.under_1_5_prob = tot_m["under_1_5"]
            existing.over_2_5_prob = tot_m["over_2_5"]
            existing.under_2_5_prob = tot_m["under_2_5"]
            existing.over_3_5_prob = tot_m["over_3_5"]
            existing.under_3_5_prob = tot_m["under_3_5"]
            existing.over_4_5_prob = tot_m["over_4_5"]
            existing.under_4_5_prob = tot_m["under_4_5"]
            existing.over_5_5_prob = tot_m["over_5_5"]
            existing.under_5_5_prob = tot_m["under_5_5"]
            existing.over_6_5_prob = tot_m["over_6_5"]
            existing.under_6_5_prob = tot_m["under_6_5"]
            existing.home_over_0_5_prob = h_m.get("over_0_5")
            existing.home_over_1_5_prob = h_m.get("over_1_5")
            existing.home_over_2_5_prob = h_m.get("over_2_5")
            existing.home_over_3_5_prob = h_m.get("over_3_5")
            existing.away_over_0_5_prob = a_m.get("over_0_5")
            existing.away_over_1_5_prob = a_m.get("over_1_5")
            existing.away_over_2_5_prob = a_m.get("over_2_5")
            existing.away_over_3_5_prob = a_m.get("over_3_5")
            existing.any_red_card_prob = red_info.get("any_red_prob")
            existing.home_red_card_prob = red_info.get("home_red_prob")
            existing.away_red_card_prob = red_info.get("away_red_prob")
            existing.confidence_score = conf.get("overall", 50)
            existing.data_quality_score = conf.get("data_quality", 50)
            existing.sample_strength_score = conf.get("sample_strength", 50)
            existing.model_stability_score = conf.get("model_stability", 50)
            existing.referee_confidence_score = conf.get("referee_confidence", 50)
            existing.dispersion = model_meta.get("dispersion", DEFAULT_FALLBACK_CARDS_DISPERSION)
            existing.dispersion_source = model_meta.get("dispersion_source", "fallback")
            existing.baseline_source = model_meta.get("baseline_source", "fallback")
            existing.referee_source = model_meta.get("referee_source", "fallback")
            existing.referee_sample_size = ref_info.get("sample_size", 0)
            db.commit()
            return existing

        snapshot = CardPredictionSnapshot(
            fixture_id=fixture_id,
            model_version=CARDS_MODEL_VERSION,
            prediction_timestamp=datetime.now(timezone.utc),
            is_prematch=is_prematch,
            expected_home_cards=exp["home"],
            expected_away_cards=exp["away"],
            expected_total_cards=exp["total"],
            over_1_5_prob=tot_m["over_1_5"],
            under_1_5_prob=tot_m["under_1_5"],
            over_2_5_prob=tot_m["over_2_5"],
            under_2_5_prob=tot_m["under_2_5"],
            over_3_5_prob=tot_m["over_3_5"],
            under_3_5_prob=tot_m["under_3_5"],
            over_4_5_prob=tot_m["over_4_5"],
            under_4_5_prob=tot_m["under_4_5"],
            over_5_5_prob=tot_m["over_5_5"],
            under_5_5_prob=tot_m["under_5_5"],
            over_6_5_prob=tot_m["over_6_5"],
            under_6_5_prob=tot_m["under_6_5"],
            home_over_0_5_prob=h_m.get("over_0_5"),
            home_over_1_5_prob=h_m.get("over_1_5"),
            home_over_2_5_prob=h_m.get("over_2_5"),
            home_over_3_5_prob=h_m.get("over_3_5"),
            away_over_0_5_prob=a_m.get("over_0_5"),
            away_over_1_5_prob=a_m.get("over_1_5"),
            away_over_2_5_prob=a_m.get("over_2_5"),
            away_over_3_5_prob=a_m.get("over_3_5"),
            any_red_card_prob=red_info.get("any_red_prob"),
            home_red_card_prob=red_info.get("home_red_prob"),
            away_red_card_prob=red_info.get("away_red_prob"),
            confidence_score=conf.get("overall", 50),
            data_quality_score=conf.get("data_quality", 50),
            sample_strength_score=conf.get("sample_strength", 50),
            model_stability_score=conf.get("model_stability", 50),
            referee_confidence_score=conf.get("referee_confidence", 50),
            dispersion=model_meta.get("dispersion", DEFAULT_FALLBACK_CARDS_DISPERSION),
            dispersion_source=model_meta.get("dispersion_source", "fallback"),
            baseline_source=model_meta.get("baseline_source", "fallback"),
            referee_source=model_meta.get("referee_source", "fallback"),
            referee_sample_size=ref_info.get("sample_size", 0),
            is_verified=False
        )
        db.add(snapshot)
        db.commit()
        return snapshot

    @classmethod
    def verify_finished_fixture_cards(
        cls, db: Session, fixture_id: int, actual_home_y: int, actual_away_y: int, actual_home_r: int = 0, actual_away_r: int = 0
    ) -> Optional[CardPredictionSnapshot]:
        """
        Links observed match cards to original prediction snapshot and marks verification.
        """
        if actual_home_y < 0 or actual_away_y < 0 or actual_home_r < 0 or actual_away_r < 0:
            logger.warning(f"Invalid negative card counts rejected for fixture {fixture_id}")
            return None

        actual_total = actual_home_y + actual_away_y + actual_home_r + actual_away_r

        snapshot = (
            db.query(CardPredictionSnapshot)
            .filter(
                CardPredictionSnapshot.fixture_id == fixture_id,
                CardPredictionSnapshot.model_version == CARDS_MODEL_VERSION
            )
            .first()
        )

        if snapshot:
            snapshot.actual_home_yellow_cards = actual_home_y
            snapshot.actual_away_yellow_cards = actual_away_y
            snapshot.actual_home_red_cards = actual_home_r
            snapshot.actual_away_red_cards = actual_away_r
            snapshot.actual_total_cards = actual_total
            snapshot.is_verified = True
            snapshot.verified_at = datetime.now(timezone.utc)
            db.commit()

        return snapshot


class CardsModelEvaluationService:
    """
    Chronological benchmarking across 4 card prediction architectures:
    - Model A: League Baseline Model
    - Model B: Independent Poisson Model
    - Model C: Static Negative Binomial Model
    - Model D: Team-Strength & Referee Negative Binomial (Production Model: v1_cards_nb)
    """

    @classmethod
    def evaluate_model_comparison(cls, db: Session, fixtures_with_stats: List[Tuple[Fixture, MatchStatistics]]) -> Dict[str, Any]:
        """
        Performs out-of-sample chronological benchmark evaluation across 4 candidate models.
        """
        n = len(fixtures_with_stats)
        if n == 0:
            return {"status": "insufficient_data", "sample_size": 0}

        model_keys = ["model_a_league_baseline", "model_b_poisson", "model_c_static_nb", "model_d_production_nb"]
        thresholds = [1.5, 2.5, 3.5, 4.5, 5.5, 6.5]

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
            act_hy = stats.home_yellow_cards or 0
            act_ay = stats.away_yellow_cards or 0
            act_hr = stats.home_red_cards or 0
            act_ar = stats.away_red_cards or 0
            act_tot = stats.total_cards or (act_hy + act_ay + act_hr + act_ar)

            # Model D: Production Model (trained on t < match_date)
            pred_d = CardsPredictionEngine.predict_cards(db, f.id, target_date=f.match_date, save_snapshot=False)
            if not pred_d.get("available") or not pred_d.get("expected"):
                continue

            exp_d = pred_d["expected"]
            m_d_tot = exp_d["total"]
            probs_d = pred_d["total_markets"]

            # Model A: League Baseline
            l_h, l_a, l_tot, _, _ = LeagueCardBaselineService.resolve_league_baseline(db, f.league_id, target_date=f.match_date)
            probs_a = CardsPredictionEngine.calculate_card_probabilities(l_h, l_a, dispersion=DEFAULT_FALLBACK_CARDS_DISPERSION)["total_markets"]

            # Model B: Poisson
            probs_b = CardsPredictionEngine.calculate_card_probabilities(exp_d["home"], exp_d["away"], dispersion=15.0)["total_markets"]

            # Model C: Static NegBin (r=4.0)
            probs_c = CardsPredictionEngine.calculate_card_probabilities(exp_d["home"], exp_d["away"], dispersion=4.0)["total_markets"]

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

        res_summary = {}
        for m_key in model_keys:
            c = metrics[m_key]["eval_count"]
            if c > 0:
                brier_avg = sum(metrics[m_key]["brier_scores"].values()) / float(len(thresholds) * c)
                res_summary[m_key] = {
                    "sample_size": c,
                    "overall_brier_score": round(brier_avg, 4),
                    "log_loss": round(metrics[m_key]["log_loss"] / float(c), 4),
                    "mae_total_cards": round(metrics[m_key]["mae_total"] / float(c), 2),
                    "rmse_total_cards": round(math.sqrt(metrics[m_key]["rmse_total"] / float(c)), 2),
                    "brier_by_market": {k: round(v / float(c), 4) for k, v in metrics[m_key]["brier_scores"].items()}
                }

        return res_summary


class CardsBacktestService:
    """
    Chronological Backtesting Engine for the Cards Prediction Model.
    Evaluates real historical data with strict temporal separation.
    """

    @classmethod
    def run_chronological_backtest(cls, db: Session, min_samples: int = MIN_REAL_CARDS_BACKTEST_VALIDATION_SAMPLE) -> Dict[str, Any]:
        """
        Performs a chronological backtest across all historical finished fixtures
        containing verified observed card counts.
        """
        fixtures = (
            db.query(Fixture, MatchStatistics)
            .join(MatchStatistics, MatchStatistics.fixture_id == Fixture.id)
            .filter(
                Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                MatchStatistics.home_yellow_cards.isnot(None),
                MatchStatistics.away_yellow_cards.isnot(None)
            )
            .order_by(Fixture.match_date.asc())
            .all()
        )

        n_matches = len(fixtures)
        if n_matches < min_samples:
            return {
                "status": "insufficient_data",
                "validation_status": "UNVALIDATED (Insufficient Real Data)",
                "message": f"Only {n_matches} completed matches with verified card data found. Minimum {min_samples} required for production validation.",
                "matches_evaluated": n_matches,
                "required_matches": min_samples,
                "model_version": CARDS_MODEL_VERSION,
                "metrics": None
            }

        market_keys = ["over_1_5", "over_2_5", "over_3_5", "over_4_5", "over_5_5", "over_6_5"]
        thresholds = [1.5, 2.5, 3.5, 4.5, 5.5, 6.5]

        brier_accum = {m: 0.0 for m in market_keys}
        log_loss_accum = {m: 0.0 for m in market_keys}
        correct_accum = {m: 0 for m in market_keys}
        mae_tot_accum = 0.0
        rmse_tot_accum = 0.0

        calibration_buckets = {
            "50-60%": {"count": 0, "pred_sum": 0.0, "actual_hits": 0},
            "60-70%": {"count": 0, "pred_sum": 0.0, "actual_hits": 0},
            "70-80%": {"count": 0, "pred_sum": 0.0, "actual_hits": 0},
            "80-90%": {"count": 0, "pred_sum": 0.0, "actual_hits": 0},
            "90-100%": {"count": 0, "pred_sum": 0.0, "actual_hits": 0},
        }

        evaluated_count = 0

        for f, stats in fixtures:
            act_hy = stats.home_yellow_cards or 0
            act_ay = stats.away_yellow_cards or 0
            act_hr = stats.home_red_cards or 0
            act_ar = stats.away_red_cards or 0
            act_total = stats.total_cards or (act_hy + act_ay + act_hr + act_ar)

            pred = CardsPredictionEngine.predict_cards(db, f.id, target_date=f.match_date, save_snapshot=False)
            if not pred["available"] or not pred["total_markets"] or not pred["expected"]:
                continue

            evaluated_count += 1
            tot_markets = pred["total_markets"]
            exp_tot = pred["expected"]["total"]

            mae_tot_accum += abs(exp_tot - act_total)
            rmse_tot_accum += (exp_tot - act_total) ** 2

            for m_key, thresh in zip(market_keys, thresholds):
                p = tot_markets[m_key]
                y = 1.0 if act_total > thresh else 0.0

                brier_accum[m_key] += (p - y) ** 2
                p_safe = _clamp(p, 1e-6, 1.0 - 1e-6)
                ll = -(y * math.log(p_safe) + (1.0 - y) * math.log(1.0 - p_safe))
                log_loss_accum[m_key] += ll

                if (p >= 0.50 and y == 1.0) or (p < 0.50 and y == 0.0):
                    correct_accum[m_key] += 1

                if m_key in ["over_3_5", "over_4_5"]:
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
                "model_version": CARDS_MODEL_VERSION,
                "metrics": None
            }

        model_comparison = CardsModelEvaluationService.evaluate_model_comparison(db, fixtures)

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
            "model_version": CARDS_MODEL_VERSION,
            "sample_size": evaluated_count,
            "overall_brier_score": round(sum(m["brier_score"] for m in market_metrics.values()) / len(market_keys), 4),
            "log_loss": round(sum(m["log_loss"] for m in market_metrics.values()) / len(market_keys), 4),
            "mae_total_cards": round(mae_tot_accum / float(evaluated_count), 2),
            "rmse_total_cards": round(math.sqrt(rmse_tot_accum / float(evaluated_count)), 2),
            "markets": market_metrics,
            "calibration": calibration_summary,
            "model_comparison": model_comparison
        }
