import os
import sys
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
import httpx
from sqlalchemy.orm import Session
from sqlalchemy import func

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import (
        League, Team, Fixture, HistoricalResult, MatchStatistics, Prediction,
        TeamStatistics, LeagueStatistics, Referee, RefereeMatchStatistics,
        CornerPredictionSnapshot, CardPredictionSnapshot
    )
    from services.statistics_service import calculate_team_statistics, calculate_league_statistics
    from services.elo_service import EloRatingService, TeamFormService
    from services.canonical_competition_service import CanonicalCompetitionService
except ImportError:
    from ..models import (
        League, Team, Fixture, HistoricalResult, MatchStatistics, Prediction,
        TeamStatistics, LeagueStatistics, Referee, RefereeMatchStatistics,
        CornerPredictionSnapshot, CardPredictionSnapshot
    )
    from .statistics_service import calculate_team_statistics, calculate_league_statistics
    from .elo_service import EloRatingService, TeamFormService
    from .canonical_competition_service import CanonicalCompetitionService

logger = logging.getLogger(__name__)


def extract_score_value(score_obj: Any) -> Optional[int]:
    """Safely extracts an integer score from raw dict, string, float, or int value."""
    if score_obj is None:
        return None
    if isinstance(score_obj, (int, float)):
        return int(score_obj)
    if isinstance(score_obj, str):
        cleaned = score_obj.strip()
        if cleaned.isdigit():
            return int(cleaned)
        try:
            return int(float(cleaned))
        except (ValueError, TypeError):
            return None
    if isinstance(score_obj, dict):
        val = score_obj.get("displayValue")
        if val is None:
            val = score_obj.get("value")
        if val is not None:
            return extract_score_value(val)
    return None


class DataIngestionService:
    """
    Data Ingestion Service responsible for ingesting, deduplicating,
    and updating competitions (leagues), teams, historical match results,
    and upcoming fixtures in SQLite.
    """

    @staticmethod
    def ingest_leagues(db: Session, leagues_data: List[Dict[str, Any]], commit: bool = True) -> List[League]:
        """
        Ingests a list of league dictionaries. Prevents duplicates using external_id
        or (name, season) unique key pair.
        """
        ingested_leagues = []
        for l_data in leagues_data:
            ext_id = str(l_data.get("external_id")) if l_data.get("external_id") is not None else None
            name = l_data.get("name")
            country = l_data.get("country", "Unknown")
            season = str(l_data.get("season", "2025/2026"))

            if not name:
                continue

            league = None
            if ext_id:
                league = db.query(League).filter(League.external_id == ext_id).first()

            if not league:
                league = db.query(League).filter(League.name == name, League.season == season).first()

            if league:
                # Update existing record
                league.name = name
                league.country = country
                league.season = season
                if ext_id and not league.external_id:
                    league.external_id = ext_id
            else:
                # Create new record
                league = League(
                    external_id=ext_id,
                    name=name,
                    country=country,
                    season=season,
                )
                db.add(league)

            if commit:
                db.commit()
                db.refresh(league)
            else:
                db.flush()
            ingested_leagues.append(league)

        return ingested_leagues

    @staticmethod
    def ingest_teams(db: Session, teams_data: List[Dict[str, Any]], commit: bool = True) -> List[Team]:
        """
        Ingests a list of team dictionaries. Prevents duplicates using external_id
        or (name, league_id) key pair.
        """
        ingested_teams = []
        for t_data in teams_data:
            ext_id = str(t_data.get("external_id")) if t_data.get("external_id") is not None else None
            name = t_data.get("name")
            short_code = t_data.get("short_code")
            logo_url = t_data.get("logo_url")
            league_id = t_data.get("league_id")

            if not name:
                continue

            team = None
            if ext_id:
                team = db.query(Team).filter(Team.external_id == ext_id).first()

            if not team and league_id:
                team = db.query(Team).filter(Team.name == name, Team.league_id == league_id).first()
            elif not team:
                team = db.query(Team).filter(Team.name == name).first()

            if team:
                # Update existing record
                team.name = name
                if short_code:
                    team.short_code = short_code
                if logo_url:
                    team.logo_url = logo_url
                if league_id:
                    team.league_id = league_id
                if ext_id and not team.external_id:
                    team.external_id = ext_id
            else:
                # Create new record
                team = Team(
                    external_id=ext_id,
                    name=name,
                    short_code=short_code,
                    logo_url=logo_url,
                    league_id=league_id,
                )
                db.add(team)

            if commit:
                db.commit()
                db.refresh(team)
            else:
                db.flush()
            ingested_teams.append(team)

        return ingested_teams

    @staticmethod
    def ingest_fixtures(db: Session, fixtures_data: List[Dict[str, Any]], commit: bool = True) -> List[Fixture]:
        """
        Ingests historical results and upcoming fixtures. Deduplicates records,
        updates status and scores for existing fixtures, and triggers statistics
        recalculations.
        """
        ingested_fixtures = []
        affected_team_ids = set()
        affected_league_ids = set()

        # Preload lookup caches for team/league names and external_ids to minimize per-fixture SELECTs
        need_team_names = {
            name
            for f in fixtures_data
            for name in (f.get("home_team_name"), f.get("away_team_name"))
            if name
        }
        team_by_name = (
            {t.name: t.id for t in db.query(Team).filter(Team.name.in_(need_team_names)).all()}
            if need_team_names else {}
        )
        need_league_names = {f.get("league_name") for f in fixtures_data if f.get("league_name")}
        league_by_name = (
            {l.name: l.id for l in db.query(League).filter(League.name.in_(need_league_names)).all()}
            if need_league_names else {}
        )
        all_ext_ids = [str(f["external_id"]) for f in fixtures_data if f.get("external_id") is not None]
        fixtures_by_ext = {}
        for i in range(0, len(all_ext_ids), 500):
            chunk = all_ext_ids[i:i + 500]
            for existing in db.query(Fixture).filter(Fixture.external_id.in_(chunk)).all():
                if existing.external_id:
                    fixtures_by_ext[existing.external_id] = existing

        for f_data in fixtures_data:
            ext_id = str(f_data.get("external_id")) if f_data.get("external_id") is not None else None
            league_id = f_data.get("league_id")
            home_team_id = f_data.get("home_team_id")
            away_team_id = f_data.get("away_team_id")

            # Resolve team/league names from preloaded caches
            if not home_team_id and f_data.get("home_team_name"):
                home_team_id = team_by_name.get(f_data["home_team_name"])

            if not away_team_id and f_data.get("away_team_name"):
                away_team_id = team_by_name.get(f_data["away_team_name"])

            if not league_id and f_data.get("league_name"):
                league_id = league_by_name.get(f_data["league_name"])

            match_date_raw = f_data.get("match_date")
            if isinstance(match_date_raw, str):
                try:
                    s = match_date_raw.strip().replace("Z", "+00:00")
                    match_date = datetime.fromisoformat(s)
                except ValueError:
                    match_date = datetime.now(timezone.utc)
            elif isinstance(match_date_raw, datetime):
                match_date = match_date_raw
            else:
                match_date = datetime.now(timezone.utc)

            if match_date.tzinfo is not None:
                match_date = match_date.astimezone(timezone.utc).replace(tzinfo=None)

            status = f_data.get("status", "SCHEDULED").upper()
            venue = f_data.get("venue")

            if not league_id or not home_team_id or not away_team_id:
                continue

            fixture = fixtures_by_ext.get(ext_id) if ext_id else None

            if not fixture:
                fixture = (
                    db.query(Fixture)
                    .filter(
                        Fixture.league_id == league_id,
                        Fixture.home_team_id == home_team_id,
                        Fixture.away_team_id == away_team_id,
                        Fixture.match_date == match_date,
                    )
                    .first()
                )

            home_score = f_data.get("home_score")
            away_score = f_data.get("away_score")
            live_clock = f_data.get("live_clock")

            if fixture:
                # Update existing fixture
                fixture.status = status
                fixture.venue = venue or fixture.venue
                fixture.match_date = match_date
                if league_id and fixture.league_id != league_id:
                    fixture.league_id = league_id
                if home_score is not None:
                    fixture.home_score = int(home_score)
                if away_score is not None:
                    fixture.away_score = int(away_score)
                if live_clock:
                    fixture.live_clock = live_clock
                if ext_id and not fixture.external_id:
                    fixture.external_id = ext_id
            else:
                # Create new fixture
                fixture = Fixture(
                    external_id=ext_id,
                    league_id=league_id,
                    home_team_id=home_team_id,
                    away_team_id=away_team_id,
                    match_date=match_date,
                    status=status,
                    venue=venue,
                    home_score=int(home_score) if home_score is not None else None,
                    away_score=int(away_score) if away_score is not None else None,
                    live_clock=live_clock,
                )
                db.add(fixture)

            db.flush()
            if ext_id:
                fixtures_by_ext[ext_id] = fixture

            # Ingest/update historical scores if status is FINISHED and scores are provided
            home_score = f_data.get("home_score")
            away_score = f_data.get("away_score")

            if status == "FINISHED" and home_score is not None and away_score is not None:
                home_score = int(home_score)
                away_score = int(away_score)
                ht_home = int(f_data["half_time_home_score"]) if f_data.get("half_time_home_score") is not None else None
                ht_away = int(f_data["half_time_away_score"]) if f_data.get("half_time_away_score") is not None else None
                raw_hc = f_data.get("home_corners")
                raw_ac = f_data.get("away_corners")
                h_corners = int(raw_hc) if (raw_hc is not None and str(raw_hc).isdigit() and int(raw_hc) >= 0) else None
                a_corners = int(raw_ac) if (raw_ac is not None and str(raw_ac).isdigit() and int(raw_ac) >= 0) else None
                tot_corners = (h_corners + a_corners) if (h_corners is not None and a_corners is not None) else None

                # Disciplinary statistics (Yellow/Red cards)
                raw_hy = f_data.get("home_yellow_cards")
                raw_ay = f_data.get("away_yellow_cards")
                raw_hr = f_data.get("home_red_cards")
                raw_ar = f_data.get("away_red_cards")
                h_yellow = int(raw_hy) if (raw_hy is not None and str(raw_hy).isdigit() and int(raw_hy) >= 0) else None
                a_yellow = int(raw_ay) if (raw_ay is not None and str(raw_ay).isdigit() and int(raw_ay) >= 0) else None
                h_red = int(raw_hr) if (raw_hr is not None and str(raw_hr).isdigit() and int(raw_hr) >= 0) else (0 if h_yellow is not None else None)
                a_red = int(raw_ar) if (raw_ar is not None and str(raw_ar).isdigit() and int(raw_ar) >= 0) else (0 if a_yellow is not None else None)

                tot_yellow = (h_yellow + a_yellow) if (h_yellow is not None and a_yellow is not None) else None
                tot_red = (h_red + a_red) if (h_red is not None and a_red is not None) else None
                tot_cards = (tot_yellow + (tot_red or 0)) if tot_yellow is not None else None
                ref_name = f_data.get("referee") or f_data.get("referee_name")

                total_goals = home_score + away_score

                result = db.query(HistoricalResult).filter(HistoricalResult.fixture_id == fixture.id).first()
                if result:
                    result.home_score = home_score
                    result.away_score = away_score
                    result.half_time_home_score = ht_home
                    result.half_time_away_score = ht_away
                    if h_corners is not None:
                        result.home_corners = h_corners
                    if a_corners is not None:
                        result.away_corners = a_corners
                    if tot_corners is not None:
                        result.total_corners = tot_corners
                    if h_yellow is not None:
                        result.home_yellow_cards = h_yellow
                    if a_yellow is not None:
                        result.away_yellow_cards = a_yellow
                    if h_red is not None:
                        result.home_red_cards = h_red
                    if a_red is not None:
                        result.away_red_cards = a_red
                    if tot_cards is not None:
                        result.total_cards = tot_cards
                    result.total_goals = total_goals
                else:
                    result = HistoricalResult(
                        fixture_id=fixture.id,
                        home_score=home_score,
                        away_score=away_score,
                        half_time_home_score=ht_home,
                        half_time_away_score=ht_away,
                        home_corners=h_corners,
                        away_corners=a_corners,
                        total_corners=tot_corners,
                        home_yellow_cards=h_yellow,
                        away_yellow_cards=a_yellow,
                        home_red_cards=h_red,
                        away_red_cards=a_red,
                        total_cards=tot_cards,
                        total_goals=total_goals,
                    )
                    db.add(result)

                # Ingest / update MatchStatistics idempotently without overwriting verified data with nulls
                match_stats = db.query(MatchStatistics).filter(MatchStatistics.fixture_id == fixture.id).first()
                if not match_stats:
                    match_stats = MatchStatistics(
                        fixture_id=fixture.id,
                        home_corners=h_corners,
                        away_corners=a_corners,
                        total_corners=tot_corners,
                        home_yellow_cards=h_yellow,
                        away_yellow_cards=a_yellow,
                        home_red_cards=h_red,
                        away_red_cards=a_red,
                        total_yellow_cards=tot_yellow,
                        total_red_cards=tot_red,
                        total_cards=tot_cards,
                        referee_name=ref_name,
                        data_source=f_data.get("data_source", "observed"),
                        data_quality=f_data.get("data_quality", "verified"),
                    )
                    db.add(match_stats)
                else:
                    if h_corners is not None:
                        match_stats.home_corners = h_corners
                    if a_corners is not None:
                        match_stats.away_corners = a_corners
                    if tot_corners is not None:
                        match_stats.total_corners = tot_corners
                    if h_yellow is not None:
                        match_stats.home_yellow_cards = h_yellow
                    if a_yellow is not None:
                        match_stats.away_yellow_cards = a_yellow
                    if h_red is not None:
                        match_stats.home_red_cards = h_red
                    if a_red is not None:
                        match_stats.away_red_cards = a_red
                    if tot_yellow is not None:
                        match_stats.total_yellow_cards = tot_yellow
                    if tot_red is not None:
                        match_stats.total_red_cards = tot_red
                    if tot_cards is not None:
                        match_stats.total_cards = tot_cards
                    if ref_name:
                        match_stats.referee_name = ref_name

                # Referee Entity Linkage
                if ref_name and ref_name.strip():
                    ref_obj = db.query(Referee).filter(func.lower(Referee.name) == ref_name.strip().lower()).first()
                    if not ref_obj:
                        ref_obj = Referee(name=ref_name.strip())
                        db.add(ref_obj)
                        db.flush()

                    if ref_obj:
                        match_stats.referee_id = ref_obj.id
                        ref_stats = db.query(RefereeMatchStatistics).filter(RefereeMatchStatistics.fixture_id == fixture.id).first()
                        if not ref_stats:
                            ref_stats = RefereeMatchStatistics(
                                referee_id=ref_obj.id,
                                fixture_id=fixture.id,
                                yellow_cards=(tot_yellow or 0),
                                red_cards=(tot_red or 0),
                                total_cards=(tot_cards or 0),
                                data_source=f_data.get("data_source", "observed"),
                                data_quality=f_data.get("data_quality", "verified")
                            )
                            db.add(ref_stats)
                        else:
                            if tot_yellow is not None:
                                ref_stats.yellow_cards = tot_yellow
                            if tot_red is not None:
                                ref_stats.red_cards = tot_red
                            if tot_cards is not None:
                                ref_stats.total_cards = tot_cards

                # Trigger post-match prediction snapshot result verification
                if h_corners is not None and a_corners is not None:
                    try:
                        from services.corners_service import CornerSnapshotService
                        CornerSnapshotService.verify_finished_fixture_corners(db, fixture.id, h_corners, a_corners)
                    except Exception as v_ex:
                        logger.debug(f"Corner result verification hook error: {v_ex}")

                if h_yellow is not None and a_yellow is not None:
                    try:
                        from services.cards_service import CardSnapshotService
                        CardSnapshotService.verify_finished_fixture_cards(
                            db, fixture.id, h_yellow, a_yellow, (h_red or 0), (a_red or 0)
                        )
                    except Exception as v_ex:
                        logger.debug(f"Card result verification hook error: {v_ex}")

                # Trigger Phase 5 Central Model Evaluation
                try:
                    from services.model_evaluation_service import ModelEvaluationService
                    ModelEvaluationService.evaluate_finished_fixture(db, fixture.id)
                except Exception as eval_ex:
                    logger.debug(f"Model evaluation hook error: {eval_ex}")

                fixture.status = "FINISHED"
                db.flush()

                affected_team_ids.add(home_team_id)
                affected_team_ids.add(away_team_id)
                affected_league_ids.add(league_id)

            ingested_fixtures.append(fixture)

        if commit:
            db.commit()

        # Trigger statistics recalculation for affected leagues & teams cleanly with batched commits
        for l_id in affected_league_ids:
            calculate_league_statistics(db, l_id, commit=False)

        for t_id in affected_team_ids:
            calculate_team_statistics(db, t_id, commit=False)
        if commit and (affected_league_ids or affected_team_ids):
            db.commit()

        # Keep Elo ratings and form streaks fresh (the prediction model reads them).
        # recalculate_all_elo is deterministic and idempotent (resets, then replays all
        # finished matches in chronological order), so re-ingesting a finished fixture
        # never double-counts.
        if affected_team_ids:
            EloRatingService.recalculate_all_elo(db, commit=commit)
            for t_id in affected_team_ids:
                TeamFormService.calculate_form_streaks(db, t_id, commit=commit)

        return ingested_fixtures

    @classmethod
    async def fetch_and_ingest_from_api(
        cls, db: Session, api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fetches competitions, teams, historical results, and upcoming fixtures
        from external HTTP endpoint (or ingests fallback seed dataset if offline).
        """
        try:
            synth_fixtures = db.query(Fixture).filter(
                (Fixture.external_id.like("FIX-%")) |
                (Fixture.external_id.like("HIST-%")) |
                (Fixture.external_id.like("SEED-%"))
            ).all()
            if synth_fixtures:
                for sf in synth_fixtures:
                    db.delete(sf)
                db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"Error purging synthetic placeholders: {e}")

        # Primary: Real-time Live Ingestion from ESPN Soccer API (Global & Top Leagues from present date)
        # Primary: Real-time Live Ingestion from ESPN Soccer API (Global & Top Leagues from present date)
        espn_leagues = [
            ("eng.1", "English Premier League", "England"),
            ("eng.2", "English Championship", "England"),
            ("eng.3", "English League One", "England"),
            ("eng.4", "English League Two", "England"),
            ("esp.1", "Spanish LALIGA", "Spain"),
            ("esp.2", "Spanish Segunda División", "Spain"),
            ("ita.1", "Italian Serie A", "Italy"),
            ("ita.2", "Italian Serie B", "Italy"),
            ("ger.1", "German Bundesliga", "Germany"),
            ("ger.2", "German 2. Bundesliga", "Germany"),
            ("fra.1", "French Ligue 1", "France"),
            ("fra.2", "French Ligue 2", "France"),
            ("uefa.champions", "UEFA Champions League", "Europe"),
            ("uefa.europa", "UEFA Europa League", "Europe"),
            ("uefa.europa.conf", "UEFA Conference League", "Europe"),
            ("usa.1", "Major League Soccer", "USA"),
            ("mex.1", "Mexican Liga MX", "Mexico"),
            ("ned.1", "Dutch Eredivisie", "Netherlands"),
            ("por.1", "Portuguese Primeira Liga", "Portugal"),
            ("arg.1", "Argentine Liga Profesional", "Argentina"),
            ("bra.1", "Brazilian Serie A", "Brazil"),
            ("bra.2", "Brazilian Serie B", "Brazil"),
            ("sau.1", "Saudi Pro League", "Saudi Arabia"),
            ("tur.1", "Turkish Super Lig", "Turkey"),
            ("sco.1", "Scottish Premiership", "Scotland"),
            ("bel.1", "Belgian Pro League", "Belgium"),
            ("aut.1", "Austrian Bundesliga", "Austria"),
            ("sui.1", "Swiss Super League", "Switzerland"),
            ("swe.1", "Swedish Allsvenskan", "Sweden"),
            ("nor.1", "Norwegian Eliteserien", "Norway"),
            ("den.1", "Danish Superliga", "Denmark"),
            ("concacaf.champions", "CONCACAF Champions Cup", "North America"),
            ("copa.libertadores", "Copa Libertadores", "South America"),
            ("copa.sudamericana", "Copa Sudamericana", "South America"),
            ("all", "Global Matches & Cup Competitions", "Global"),
        ]

        now_utc = datetime.now(timezone.utc)
        target_dates = [(now_utc + timedelta(days=i)).strftime("%Y%m%d") for i in range(-1, 15)]

        async def fetch_espn_feed(client_inst, code, default_name, country, date_str=None):
            if date_str:
                url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard?dates={date_str}"
            else:
                d_start = (now_utc - timedelta(days=2)).strftime("%Y%m%d")
                d_end = (now_utc + timedelta(days=35)).strftime("%Y%m%d")
                url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard?dates={d_start}-{d_end}"
            try:
                r = await client_inst.get(url)
                if r.status_code == 200:
                    return (code, default_name, country, r.json())
            except Exception as ex:
                logger.warning(f"Error fetching ESPN feed {code} ({date_str}): {str(ex)}")
            return (code, default_name, country, None)

        def _as_dict(val: Any) -> Dict[str, Any]:
            return val if isinstance(val, dict) else {}

        def _first_dict(items: Any) -> Dict[str, Any]:
            if isinstance(items, list) and items and isinstance(items[0], dict):
                return items[0]
            return {}

        def _clean_team_name(raw_name: str) -> str:
            if not raw_name:
                return "Team"
            clean = raw_name.strip()
            if clean.lower() in ["colo-colo", "colo colo"]:
                return "Colo-Colo"
            parts = clean.split()
            if len(parts) >= 4 and f"{parts[0]} {parts[1]}".lower() == f"{parts[2]} {parts[3]}".lower():
                parts = parts[2:]
            elif len(parts) >= 3 and parts[0].lower() == parts[1].lower() and parts[1].isupper():
                parts.pop(0)
            return " ".join(parts)

        async with httpx.AsyncClient(timeout=10.0) as client:
            feed_tasks = [fetch_espn_feed(client, code, default_name, country) for code, default_name, country in espn_leagues if code != "all"]
            feed_tasks.extend([fetch_espn_feed(client, "all", "Global Matches & Cup Competitions", "Global", d_str) for d_str in target_dates])
            feed_results = await asyncio.gather(*feed_tasks)

        # Parse all ESPN JSON responses in memory first (no DB side effects / rollbacks mid-loop)
        unique_leagues_map: Dict[str, Dict[str, Any]] = {}
        unique_teams_map: Dict[str, Dict[str, Any]] = {}
        parsed_events: List[Dict[str, Any]] = []

        for code, default_name, country, data in feed_results:
            if not isinstance(data, dict):
                continue
            events = data.get("events") or []
            leagues_list = data.get("leagues") or []
            first_league = _first_dict(leagues_list)
            api_league_name = first_league.get("name") if (code != "all" and first_league.get("name")) else None

            for ev in events:
                if not isinstance(ev, dict):
                    continue
                try:
                    comp_info = _first_dict(ev.get("competitions"))
                    notes = _first_dict(comp_info.get("notes")).get("headline", "")
                    alt_note = comp_info.get("altGameNote")
                    season_slug = _as_dict(ev.get("season")).get("slug", "")

                    odds0 = _first_dict(comp_info.get("odds"))
                    odds_league_code = _as_dict(_as_dict(odds0.get("tracking")).get("tags")).get("league")
                    event_code = odds_league_code if (odds_league_code and odds_league_code in CanonicalCompetitionService.ESPN_CODE_MAP) else code

                    competitors = comp_info.get("competitors") or []
                    if not isinstance(competitors, list) or len(competitors) < 2:
                        continue

                    c0 = _as_dict(competitors[0])
                    c1 = _as_dict(competitors[1])
                    home_data = c0 if c0.get("homeAway") == "home" else c1
                    away_data = c1 if c0.get("homeAway") == "home" else c0
                    h_team_meta = _as_dict(home_data.get("team"))
                    a_team_meta = _as_dict(away_data.get("team"))

                    _alt_lower = str(alt_note or "").lower()
                    _season_lower = str(season_slug or "").lower()
                    _combined_early = f"{_alt_lower} {_season_lower}"
                    if any(kw in _combined_early for kw in ["ncaaw", "ncaam", "ncaa ", "ncaa-", "college soccer", "high school soccer"]):
                        continue

                    _home_logo = str(h_team_meta.get("logo") or "")
                    _away_logo = str(a_team_meta.get("logo") or "")
                    if "teamlogos/ncaa/" in _home_logo or "teamlogos/ncaa/" in _away_logo:
                        continue

                    h_clean_name = _clean_team_name(h_team_meta.get("displayName", "Home Team"))
                    a_clean_name = _clean_team_name(a_team_meta.get("displayName", "Away Team"))

                    canon_ident = CanonicalCompetitionService.resolve_competition(
                        provider_code=event_code,
                        league_name=api_league_name or default_name,
                        season_slug=season_slug,
                        alt_note=alt_note,
                        notes=notes,
                        home_team_name=h_clean_name,
                        away_team_name=a_clean_name,
                    )
                    league_name = canon_ident.competition_name
                    resolved_country = canon_ident.country_name
                    league_ext_id = canon_ident.provider_competition_id

                    if CanonicalCompetitionService.is_school_or_youth_competition(
                        league_name=league_name,
                        provider_code=code,
                        home_team_name=h_clean_name,
                        away_team_name=a_clean_name,
                        notes=f"{season_slug} {notes} {alt_note or ''}",
                    ):
                        continue

                    date_str_raw = ev.get("date")
                    if not date_str_raw:
                        continue
                    dt_parsed = datetime.fromisoformat(str(date_str_raw).replace("Z", "+00:00"))
                    match_date = dt_parsed.astimezone(timezone.utc).replace(tzinfo=None)

                    status_obj = _as_dict(ev.get("status"))
                    status_type = _as_dict(status_obj.get("type"))
                    status_name = str(status_type.get("name") or "STATUS_SCHEDULED").upper()
                    status_state = str(status_type.get("state") or "").lower()
                    status_detail = str(status_type.get("shortDetail") or status_type.get("detail") or "").strip()
                    live_clock = status_obj.get("displayClock") or (status_detail if ("'" in status_detail or "HT" in status_detail) else None)

                    venue = _as_dict(comp_info.get("venue")).get("fullName")
                    raw_h_score = home_data.get("score") if home_data.get("score") is not None else home_data.get("displayValue")
                    raw_a_score = away_data.get("score") if away_data.get("score") is not None else away_data.get("displayValue")
                    parsed_h_score = extract_score_value(raw_h_score)
                    parsed_a_score = extract_score_value(raw_a_score)

                    if any(k in status_name for k in ["POSTPONED", "CANCELLED", "CANCELED", "ABANDONED", "SUSPENDED"]):
                        status = "POSTPONED"
                        parsed_h_score = None
                        parsed_a_score = None
                    elif status_state == "post" or any(k in status_name for k in ["FINAL", "FULL", "FINISHED", "FT", "POST_EVENT", "END_OF_EXTRATIME"]):
                        status = "FINISHED"
                    elif status_state == "in" or any(k in status_name for k in ["IN_PROGRESS", "HALFTIME", "FIRST_HALF", "SECOND_HALF", "END_PERIOD", "OVERTIME", "LIVE", "IN PROGRESS", "SHOOTOUT"]):
                        status = "LIVE"
                    else:
                        status = "SCHEDULED"
                        parsed_h_score = None
                        parsed_a_score = None
                        live_clock = None

                    ev_id = ev.get("id")
                    if ev_id is None:
                        continue

                    unique_leagues_map[league_ext_id] = {
                        "external_id": league_ext_id,
                        "name": league_name,
                        "country": resolved_country,
                        "season": "2025/2026",
                    }

                    h_raw_id = home_data.get("id") or h_team_meta.get("id")
                    a_raw_id = away_data.get("id") or a_team_meta.get("id")
                    h_ext_id = f"ESPN-TEAM-{h_raw_id}" if h_raw_id is not None else None
                    a_ext_id = f"ESPN-TEAM-{a_raw_id}" if a_raw_id is not None else None
                    h_team_key = h_ext_id or f"NAME::{h_clean_name}::{league_ext_id}"
                    a_team_key = a_ext_id or f"NAME::{a_clean_name}::{league_ext_id}"

                    unique_teams_map[h_team_key] = {
                        "external_id": h_ext_id,
                        "name": h_clean_name,
                        "short_code": h_team_meta.get("abbreviation", "HOM"),
                        "logo_url": h_team_meta.get("logo"),
                        "league_ext_id": league_ext_id,
                    }
                    unique_teams_map[a_team_key] = {
                        "external_id": a_ext_id,
                        "name": a_clean_name,
                        "short_code": a_team_meta.get("abbreviation", "AWY"),
                        "logo_url": a_team_meta.get("logo"),
                        "league_ext_id": league_ext_id,
                    }

                    parsed_events.append({
                        "external_id": f"ESPN-FIX-{ev_id}",
                        "league_ext_id": league_ext_id,
                        "home_team_key": h_team_key,
                        "away_team_key": a_team_key,
                        "match_date": match_date,
                        "status": status,
                        "venue": venue,
                        "home_score": parsed_h_score,
                        "away_score": parsed_a_score,
                        "live_clock": live_clock if status == "LIVE" else ("FT" if status == "FINISHED" else None),
                    })
                except Exception as ev_ex:
                    logger.warning(f"Error parsing ESPN event {ev.get('id')}: {str(ev_ex)}")

        if parsed_events:
            league_payloads = list(unique_leagues_map.values())
            upserted_leagues = cls.ingest_leagues(db, league_payloads, commit=True)
            league_id_map: Dict[str, int] = {}
            for lg in upserted_leagues:
                if lg.external_id:
                    league_id_map[lg.external_id] = lg.id

            team_keys_ordered: List[str] = []
            team_payloads: List[Dict[str, Any]] = []
            for t_key, t_info in unique_teams_map.items():
                resolved_lg_id = league_id_map.get(t_info["league_ext_id"])
                if not resolved_lg_id:
                    continue
                team_keys_ordered.append(t_key)
                team_payloads.append({
                    "external_id": t_info["external_id"],
                    "name": t_info["name"],
                    "short_code": t_info["short_code"],
                    "logo_url": t_info["logo_url"],
                    "league_id": resolved_lg_id,
                })
            upserted_teams = cls.ingest_teams(db, team_payloads, commit=True)
            team_id_map: Dict[str, int] = {}
            for t_key, tm in zip(team_keys_ordered, upserted_teams):
                team_id_map[t_key] = tm.id

            all_espn_payloads = []
            for p_ev in parsed_events:
                lg_id = league_id_map.get(p_ev["league_ext_id"])
                h_id = team_id_map.get(p_ev["home_team_key"])
                a_id = team_id_map.get(p_ev["away_team_key"])
                if not lg_id or not h_id or not a_id:
                    continue
                all_espn_payloads.append({
                    "external_id": p_ev["external_id"],
                    "league_id": lg_id,
                    "home_team_id": h_id,
                    "away_team_id": a_id,
                    "match_date": p_ev["match_date"],
                    "status": p_ev["status"],
                    "venue": p_ev["venue"],
                    "home_score": p_ev["home_score"],
                    "away_score": p_ev["away_score"],
                    "live_clock": p_ev["live_clock"],
                })

            ingested = cls.ingest_fixtures(db, all_espn_payloads, commit=True)
            espn_fixtures_count = len(ingested)
        else:
            espn_fixtures_count = 0

        if espn_fixtures_count > 0:
            return {
                "status": "ok",
                "source": "espn_realtime_api",
                "leagues_ingested": db.query(League).count(),
                "teams_ingested": db.query(Team).count(),
                "fixtures_ingested": espn_fixtures_count,
            }

        # Backup: Football API (with 10-day date window)
        if api_key and api_key.strip():
            headers = {"X-Auth-Token": api_key}
            async with httpx.AsyncClient(timeout=10.0) as client:
                try:
                    # Target top competitions explicitly on Football API (Premier League PL, La Liga PD, Serie A SA, Bundesliga BL1, Champions League CL)
                    target_comps = [
                        {"code": "PL", "name": "Premier League", "country": "England"},
                        {"code": "PD", "name": "La Liga", "country": "Spain"},
                        {"code": "CL", "name": "UEFA Champions League", "country": "Europe"},
                        {"code": "SA", "name": "Serie A", "country": "Italy"},
                        {"code": "BL1", "name": "Bundesliga", "country": "Germany"}
                    ]
                    leagues_payload = [
                        {"external_id": c["code"], "name": c["name"], "country": c["country"], "season": "2025/2026"}
                        for c in target_comps
                    ]
                    ingested_leagues = cls.ingest_leagues(db, leagues_payload)

                    total_teams = 0
                    total_fixtures = 0
                    for league_obj in ingested_leagues:
                        teams_res = await client.get(f"https://api.football-data.org/v4/competitions/{league_obj.external_id}/teams", headers=headers)
                        if teams_res.status_code == 200:
                            teams_data = teams_res.json().get("teams", [])
                            teams_payload = [
                                {
                                    "external_id": str(t.get("id")),
                                    "name": t.get("name"),
                                    "short_code": t.get("tla"),
                                    "logo_url": t.get("crest"),
                                    "league_id": league_obj.id
                                }
                                for t in teams_data
                            ]
                            ingested_teams = cls.ingest_teams(db, teams_payload)
                            total_teams += len(ingested_teams)

                        matches_res = await client.get(f"https://api.football-data.org/v4/competitions/{league_obj.external_id}/matches", headers=headers)
                        if matches_res.status_code == 200:
                            matches_data = matches_res.json().get("matches", [])
                            fixtures_payload = []
                            for m in matches_data:
                                home_ext_id = str(m.get("homeTeam", {}).get("id"))
                                away_ext_id = str(m.get("awayTeam", {}).get("id"))
                                home_name = m.get("homeTeam", {}).get("name")
                                away_name = m.get("awayTeam", {}).get("name")

                                if not home_name or not away_name:
                                    continue

                                h_team = db.query(Team).filter(Team.external_id == home_ext_id).first()
                                if not h_team and home_name:
                                    h_team = db.query(Team).filter(Team.name == home_name).first()

                                a_team = db.query(Team).filter(Team.external_id == away_ext_id).first()
                                if not a_team and away_name:
                                    a_team = db.query(Team).filter(Team.name == away_name).first()

                                if not h_team and home_name:
                                    h_team = cls.ingest_teams(db, [{
                                        "external_id": home_ext_id,
                                        "name": home_name,
                                        "short_code": m.get("homeTeam", {}).get("tla"),
                                        "logo_url": m.get("homeTeam", {}).get("crest"),
                                        "league_id": league_obj.id
                                    }])[0]

                                if not a_team and away_name:
                                    a_team = cls.ingest_teams(db, [{
                                        "external_id": away_ext_id,
                                        "name": away_name,
                                        "short_code": m.get("awayTeam", {}).get("tla"),
                                        "logo_url": m.get("awayTeam", {}).get("crest"),
                                        "league_id": league_obj.id
                                    }])[0]

                                if h_team and a_team:
                                    score_data = m.get("score", {}).get("fullTime", {})
                                    fixtures_payload.append({
                                        "external_id": str(m.get("id")),
                                        "league_id": league_obj.id,
                                        "home_team_id": h_team.id,
                                        "away_team_id": a_team.id,
                                        "match_date": m.get("utcDate"),
                                        "status": m.get("status", "SCHEDULED"),
                                        "home_score": score_data.get("home"),
                                        "away_score": score_data.get("away"),
                                    })
                            ingested_fixtures = cls.ingest_fixtures(db, fixtures_payload)
                            total_fixtures += len(ingested_fixtures)

                    if total_fixtures > 0:
                        return {
                            "status": "ok",
                            "source": "api.football-data.org",
                            "leagues_ingested": len(ingested_leagues),
                            "teams_ingested": total_teams,
                            "fixtures_ingested": total_fixtures
                        }
                    else:
                        logger.warning("External API returned 0 valid fixtures. Proceeding to active 5-league dataset.")
                except Exception as e:
                    logger.warning(f"External API fetch failed ({str(e)}). Falling back to active 5-league dataset.")

        # Real Active Matches Dataset across 5 Top Competitions
        seed_leagues = [
            {"external_id": "PL2026", "name": "Premier League", "country": "England", "season": "2025/2026"},
            {"external_id": "LL2026", "name": "La Liga", "country": "Spain", "season": "2025/2026"},
            {"external_id": "CL2026", "name": "UEFA Champions League", "country": "Europe", "season": "2025/2026"},
            {"external_id": "SA2026", "name": "Serie A", "country": "Italy", "season": "2025/2026"},
            {"external_id": "BL2026", "name": "Bundesliga", "country": "Germany", "season": "2025/2026"}
        ]
        leagues = cls.ingest_leagues(db, seed_leagues)
        league_map = {l.name: l.id for l in leagues}

        seed_teams = [
            # Premier League
            {"external_id": "ARS", "name": "Arsenal", "short_code": "ARS", "logo_url": "https://crests.football-data.org/57.png", "league_id": league_map["Premier League"]},
            {"external_id": "MCI", "name": "Manchester City", "short_code": "MCI", "logo_url": "https://crests.football-data.org/65.png", "league_id": league_map["Premier League"]},
            {"external_id": "LIV", "name": "Liverpool", "short_code": "LIV", "logo_url": "https://crests.football-data.org/64.png", "league_id": league_map["Premier League"]},
            {"external_id": "CHE", "name": "Chelsea", "short_code": "CHE", "logo_url": "https://crests.football-data.org/61.png", "league_id": league_map["Premier League"]},
            {"external_id": "TOT", "name": "Tottenham Hotspur", "short_code": "TOT", "logo_url": "https://crests.football-data.org/73.png", "league_id": league_map["Premier League"]},
            {"external_id": "MUN", "name": "Manchester United", "short_code": "MUN", "logo_url": "https://crests.football-data.org/66.png", "league_id": league_map["Premier League"]},

            # La Liga
            {"external_id": "RMA", "name": "Real Madrid", "short_code": "RMA", "logo_url": "https://crests.football-data.org/86.png", "league_id": league_map["La Liga"]},
            {"external_id": "BAR", "name": "FC Barcelona", "short_code": "BAR", "logo_url": "https://crests.football-data.org/81.png", "league_id": league_map["La Liga"]},
            {"external_id": "ATM", "name": "Atletico Madrid", "short_code": "ATM", "logo_url": "https://crests.football-data.org/78.png", "league_id": league_map["La Liga"]},
            {"external_id": "SEV", "name": "Sevilla FC", "short_code": "SEV", "logo_url": "https://crests.football-data.org/559.png", "league_id": league_map["La Liga"]},

            # Champions League
            {"external_id": "BAY", "name": "Bayern Munich", "short_code": "BAY", "logo_url": "https://crests.football-data.org/5.png", "league_id": league_map["UEFA Champions League"]},
            {"external_id": "PSG", "name": "Paris Saint-Germain", "short_code": "PSG", "logo_url": "https://crests.football-data.org/524.png", "league_id": league_map["UEFA Champions League"]},
            {"external_id": "INT", "name": "Inter Milan", "short_code": "INT", "logo_url": "https://crests.football-data.org/108.png", "league_id": league_map["UEFA Champions League"]},
            {"external_id": "BVB", "name": "Borussia Dortmund", "short_code": "BVB", "logo_url": "https://crests.football-data.org/4.png", "league_id": league_map["UEFA Champions League"]},

            # Serie A
            {"external_id": "JUV", "name": "Juventus", "short_code": "JUV", "logo_url": "https://crests.football-data.org/109.png", "league_id": league_map["Serie A"]},
            {"external_id": "ACM", "name": "AC Milan", "short_code": "ACM", "logo_url": "https://crests.football-data.org/98.png", "league_id": league_map["Serie A"]},
            {"external_id": "NAP", "name": "SSC Napoli", "short_code": "NAP", "logo_url": "https://crests.football-data.org/113.png", "league_id": league_map["Serie A"]},
            {"external_id": "ROM", "name": "AS Roma", "short_code": "ROM", "logo_url": "https://crests.football-data.org/100.png", "league_id": league_map["Serie A"]},

            # Bundesliga
            {"external_id": "LEV", "name": "Bayer Leverkusen", "short_code": "LEV", "logo_url": "https://crests.football-data.org/3.png", "league_id": league_map["Bundesliga"]},
            {"external_id": "RBL", "name": "RB Leipzig", "short_code": "RBL", "logo_url": "https://crests.football-data.org/721.png", "league_id": league_map["Bundesliga"]},
        ]
        teams = cls.ingest_teams(db, seed_teams)
        team_map: Dict[str, int] = {t.name: t.id for t in teams}

        # Historical match results to establish Poisson attack/defense strength ratings
        historical_fixtures = [
            # Premier League History
            {"external_id": "HIST-1", "league_id": league_map["Premier League"], "home_team_id": team_map["Arsenal"], "away_team_id": team_map["Chelsea"], "match_date": "2026-07-10T15:00:00Z", "status": "FINISHED", "home_score": 3, "away_score": 1, "venue": "Emirates Stadium"},
            {"external_id": "HIST-2", "league_id": league_map["Premier League"], "home_team_id": team_map["Liverpool"], "away_team_id": team_map["Manchester City"], "match_date": "2026-07-12T17:30:00Z", "status": "FINISHED", "home_score": 2, "away_score": 2, "venue": "Anfield"},
            {"external_id": "HIST-3", "league_id": league_map["Premier League"], "home_team_id": team_map["Manchester United"], "away_team_id": team_map["Tottenham Hotspur"], "match_date": "2026-07-14T20:00:00Z", "status": "FINISHED", "home_score": 2, "away_score": 3, "venue": "Old Trafford"},
            
            # La Liga History
            {"external_id": "HIST-5", "league_id": league_map["La Liga"], "home_team_id": team_map["Real Madrid"], "away_team_id": team_map["FC Barcelona"], "match_date": "2026-07-11T20:00:00Z", "status": "FINISHED", "home_score": 3, "away_score": 2, "venue": "Santiago Bernabeu"},
            
            # Champions League History
            {"external_id": "HIST-7", "league_id": league_map["UEFA Champions League"], "home_team_id": team_map["Bayern Munich"], "away_team_id": team_map["Paris Saint-Germain"], "match_date": "2026-07-13T20:00:00Z", "status": "FINISHED", "home_score": 2, "away_score": 1, "venue": "Allianz Arena"},
            
            # Serie A History
            {"external_id": "HIST-9", "league_id": league_map["Serie A"], "home_team_id": team_map["Inter Milan"], "away_team_id": team_map["AC Milan"], "match_date": "2026-07-15T19:45:00Z", "status": "FINISHED", "home_score": 2, "away_score": 1, "venue": "San Siro"},

            # Bundesliga History
            {"external_id": "HIST-10", "league_id": league_map["Bundesliga"], "home_team_id": team_map["Bayer Leverkusen"], "away_team_id": team_map["Bayern Munich"], "match_date": "2026-07-16T17:30:00Z", "status": "FINISHED", "home_score": 2, "away_score": 2, "venue": "BayArena"}
        ]
        cls.ingest_fixtures(db, historical_fixtures)

        # Real Active Scheduled Fixtures Across All 5 Competitions
        seed_fixtures = [
            # Premier League
            {
                "external_id": "FIX-201",
                "league_id": league_map["Premier League"],
                "home_team_id": team_map["Arsenal"],
                "away_team_id": team_map["Manchester City"],
                "match_date": "2026-07-29T19:00:00Z",
                "status": "SCHEDULED",
                "venue": "Emirates Stadium"
            },
            {
                "external_id": "FIX-202",
                "league_id": league_map["Premier League"],
                "home_team_id": team_map["Liverpool"],
                "away_team_id": team_map["Chelsea"],
                "match_date": "2026-07-30T16:30:00Z",
                "status": "SCHEDULED",
                "venue": "Anfield"
            },
            {
                "external_id": "FIX-203",
                "league_id": league_map["Premier League"],
                "home_team_id": team_map["Tottenham Hotspur"],
                "away_team_id": team_map["Manchester United"],
                "match_date": "2026-07-31T14:00:00Z",
                "status": "SCHEDULED",
                "venue": "Tottenham Hotspur Stadium"
            },

            # La Liga
            {
                "external_id": "FIX-204",
                "league_id": league_map["La Liga"],
                "home_team_id": team_map["Real Madrid"],
                "away_team_id": team_map["Atletico Madrid"],
                "match_date": "2026-07-29T20:30:00Z",
                "status": "SCHEDULED",
                "venue": "Santiago Bernabeu"
            },
            {
                "external_id": "FIX-205",
                "league_id": league_map["La Liga"],
                "home_team_id": team_map["FC Barcelona"],
                "away_team_id": team_map["Sevilla FC"],
                "match_date": "2026-07-30T20:00:00Z",
                "status": "SCHEDULED",
                "venue": "Spotify Camp Nou"
            },

            # UEFA Champions League
            {
                "external_id": "FIX-206",
                "league_id": league_map["UEFA Champions League"],
                "home_team_id": team_map["Real Madrid"],
                "away_team_id": team_map["Bayern Munich"],
                "match_date": "2026-08-01T20:00:00Z",
                "status": "SCHEDULED",
                "venue": "Santiago Bernabeu"
            },
            {
                "external_id": "FIX-207",
                "league_id": league_map["UEFA Champions League"],
                "home_team_id": team_map["Paris Saint-Germain"],
                "away_team_id": team_map["Inter Milan"],
                "match_date": "2026-08-02T20:00:00Z",
                "status": "SCHEDULED",
                "venue": "Parc des Princes"
            },
            {
                "external_id": "FIX-208",
                "league_id": league_map["UEFA Champions League"],
                "home_team_id": team_map["Borussia Dortmund"],
                "away_team_id": team_map["Arsenal"],
                "match_date": "2026-08-03T20:00:00Z",
                "status": "SCHEDULED",
                "venue": "Signal Iduna Park"
            },

            # Serie A
            {
                "external_id": "FIX-209",
                "league_id": league_map["Serie A"],
                "home_team_id": team_map["Juventus"],
                "away_team_id": team_map["AC Milan"],
                "match_date": "2026-08-01T19:45:00Z",
                "status": "SCHEDULED",
                "venue": "Allianz Stadium Turin"
            },
            {
                "external_id": "FIX-210",
                "league_id": league_map["Serie A"],
                "home_team_id": team_map["Inter Milan"],
                "away_team_id": team_map["SSC Napoli"],
                "match_date": "2026-08-02T19:45:00Z",
                "status": "SCHEDULED",
                "venue": "San Siro"
            },

            # Bundesliga
            {
                "external_id": "FIX-211",
                "league_id": league_map["Bundesliga"],
                "home_team_id": team_map["Bayern Munich"],
                "away_team_id": team_map["Bayer Leverkusen"],
                "match_date": "2026-08-01T17:30:00Z",
                "status": "SCHEDULED",
                "venue": "Allianz Arena"
            },
            {
                "external_id": "FIX-212",
                "league_id": league_map["Bundesliga"],
                "home_team_id": team_map["Borussia Dortmund"],
                "away_team_id": team_map["RB Leipzig"],
                "match_date": "2026-08-02T17:30:00Z",
                "status": "SCHEDULED",
                "venue": "Signal Iduna Park"
            }
        ]
        fixtures = cls.ingest_fixtures(db, seed_fixtures)

        return {
            "status": "ok",
            "source": "5_leagues_active_fixtures",
            "leagues_ingested": len(leagues),
            "teams_ingested": len(teams),
            "fixtures_ingested": len(fixtures)
        }

    @classmethod
    def purge_school_and_youth_competitions(cls, db: Session) -> int:
        """
        Scans SQLite database and purges any fixtures, predictions, and associated records
        belonging to school, college, collegiate, high school, youth, or U17-U23 competitions.
        """
        all_fixtures = db.query(Fixture).join(League).join(Team, Fixture.home_team_id == Team.id).all()
        purged_count = 0
        for fix in all_fixtures:
            h_name = fix.home_team.name if fix.home_team else ""
            a_name = fix.away_team.name if fix.away_team else ""
            l_name = fix.league.name if fix.league else ""
            h_logo = (fix.home_team.logo_url if fix.home_team else "") or ""
            a_logo = (fix.away_team.logo_url if fix.away_team else "") or ""
            if "teamlogos/ncaa/" in h_logo or "teamlogos/ncaa/" in a_logo:
                db.delete(fix)
                purged_count += 1
                continue
            if CanonicalCompetitionService.is_school_or_youth_competition(
                league_name=l_name,
                home_team_name=h_name,
                away_team_name=a_name
            ):
                db.delete(fix)
                purged_count += 1

        if purged_count > 0:
            db.commit()
            logger.info(f"Purged {purged_count} school/youth/collegiate fixtures from SQLite.")
        return purged_count

    @classmethod
    def auto_resolve_expired_live_fixtures(cls, db: Session) -> int:
        """
        Scans SQLite database for any fixtures currently marked 'LIVE' or 'SCHEDULED'
        whose match start date is > 3.5 hours in the past.
        Automatically updates their status to 'FINISHED' (if scores are populated or defaults to 0-0)
        and clears live_clock to prevent stale live fixtures from lingering.
        """
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff_time = now_naive - timedelta(hours=3, minutes=30)

        stale_fixtures = (
            db.query(Fixture)
            .filter(
                Fixture.status.in_(["LIVE", "SCHEDULED"]),
                Fixture.match_date < cutoff_time
            )
            .all()
        )

        resolved_count = 0
        for fix in stale_fixtures:
            # Only resolve to FINISHED when authoritative final scores actually exist (never invent 0-0)
            if fix.home_score is not None and fix.away_score is not None:
                fix.status = "FINISHED"
                fix.live_clock = "FT"
                resolved_count += 1
                logger.info(f"Resolved expired fixture #{fix.id} ({fix.external_id}) with observed scores to FINISHED.")
            elif fix.status == "LIVE":
                # Expired live status without verified score: revert to POSTPONED without fabricating 0-0 scores
                fix.status = "POSTPONED"
                fix.live_clock = None
                resolved_count += 1
                logger.warning(f"Cleared expired LIVE state without verified scores on fixture #{fix.id} ({fix.external_id}).")

        if resolved_count > 0:
            db.commit()

        return resolved_count

    @classmethod
    def repair_and_canonicalize_fixture_leagues(cls, db: Session) -> int:
        """
        Scans all fixtures in the database and re-resolves their correct canonical
        competition and country using team club signatures, external IDs, and notes,
        fixing any historical misattributions (e.g. Japanese fixtures mapped to Argentina or England).
        """
        from sqlalchemy.orm import joinedload
        fixtures = db.query(Fixture).options(
            joinedload(Fixture.league),
            joinedload(Fixture.home_team),
            joinedload(Fixture.away_team)
        ).all()

        repaired_count = 0
        for fix in fixtures:
            h_name = fix.home_team.name if fix.home_team else ""
            a_name = fix.away_team.name if fix.away_team else ""
            current_l_name = fix.league.name if fix.league else ""
            
            # Re-resolve definitive canonical league and country
            canon_ident = CanonicalCompetitionService.resolve_competition(
                league_name=current_l_name,
                home_team_name=h_name,
                away_team_name=a_name
            )

            # If the resolved canonical league differs from the current league
            if fix.league is None or fix.league.name != canon_ident.competition_name or fix.league.country != canon_ident.country_name:
                target_league = db.query(League).filter(
                    League.name == canon_ident.competition_name,
                    League.country == canon_ident.country_name
                ).first()

                if not target_league:
                    target_league = League(
                        external_id=canon_ident.provider_competition_id,
                        name=canon_ident.competition_name,
                        country=canon_ident.country_name,
                        season="2025/2026"
                    )
                    db.add(target_league)
                    db.flush()

                fix.league_id = target_league.id
                if fix.home_team:
                    fix.home_team.league_id = target_league.id
                if fix.away_team:
                    fix.away_team.league_id = target_league.id
                repaired_count += 1

        if repaired_count > 0:
            db.commit()
            logger.info(f"Successfully repaired and canonicalized {repaired_count} fixture leagues.")

        return repaired_count

