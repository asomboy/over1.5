import os
import sys
import json
import math
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request, Query
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
import re
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, inspect
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Ensure backend directory is in sys.path for cross-directory imports
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from database import init_db, get_db, engine, SessionLocal
    from config import CORS_ORIGINS, FOOTBALL_API_KEY
    import models
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
    from services.ingestion_service import DataIngestionService
    from services.prediction_service import PoissonPredictionEngine
    from services.accumulator_service import AccumulatorGeneratorService
    from services.telegram_service import TelegramNotificationService
    from services.weather_service import WeatherService
    from services.whatsapp_service import WhatsAppNotificationService
    from services.canonical_competition_service import CanonicalCompetitionService
    from services.calibration_service import CalibrationService
except ImportError:
    from .database import init_db, get_db, engine, SessionLocal
    from .config import CORS_ORIGINS, FOOTBALL_API_KEY
    from . import models
    from .services.statistics_service import (
        calculate_team_statistics,
        calculate_all_team_statistics,
        get_team_statistics,
        get_all_team_statistics,
        calculate_league_statistics,
        calculate_all_league_statistics,
        get_league_statistics,
        get_all_league_statistics,
    )
    from .services.ingestion_service import DataIngestionService
    from .services.prediction_service import PoissonPredictionEngine
    from .services.accumulator_service import AccumulatorGeneratorService
    from .services.telegram_service import TelegramNotificationService
    from .services.weather_service import WeatherService
    from .services.whatsapp_service import WhatsAppNotificationService
    from .services.canonical_competition_service import CanonicalCompetitionService
    from .services.calibration_service import CalibrationService

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Input Validation Models & Dependencies (Pydantic)
# -----------------------------------------------------------------------------
class FixtureQueryParams(BaseModel):
    date: Optional[str] = None
    league: Optional[str] = "ALL"
    limit: Optional[int] = Field(50, ge=1, le=200)

def validate_fixture_query(
    date: Optional[str] = Query(None, description="Match date filter (ISO YYYY-MM-DD)"),
    league: Optional[str] = Query("ALL", description="League name or 'ALL'"),
    limit: Optional[int] = Query(50, ge=1, le=200, description="Max results limit (1-200)")
) -> FixtureQueryParams:
    if date is not None and date != "" and date != "ALL":
        try:
            if re.match(r'^\d{4}-\d{2}-\d{2}$', date):
                datetime.strptime(date, '%Y-%m-%d')
            else:
                datetime.fromisoformat(date.replace('Z', '+00:00'))
        except Exception:
            raise HTTPException(status_code=422, detail=f"Invalid date format '{date}'. Expected ISO format (e.g. YYYY-MM-DD).")
    
    return FixtureQueryParams(date=date if (date and date != "ALL") else None, league=league or "ALL", limit=limit or 50)


# Track the last successful sync time to prevent redundant API calls
# while ensuring daily updates are loaded automatically on request.
LAST_SYNC_TIME: Optional[datetime] = None

scheduler = AsyncIOScheduler(timezone=timezone.utc)


async def scheduled_data_refresh():
    """
    Automated background worker job that executes periodically:
    1. Fetches/ingests latest competitions, teams, match results, and upcoming fixtures.
    2. Recalculates team and league statistics.
    3. Recalculates Poisson goal predictions for all upcoming fixtures.
    """
    logger.info("Executing scheduled data refresh and prediction recalculation cycle...")
    def run_full_refresh():
        calc_db = SessionLocal()
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(DataIngestionService.fetch_and_ingest_from_api(calc_db, api_key=FOOTBALL_API_KEY))
            finally:
                loop.close()

            calculate_all_league_statistics(calc_db)
            calculate_all_team_statistics(calc_db)
            DataIngestionService.purge_school_and_youth_competitions(calc_db)
            DataIngestionService.repair_and_canonicalize_fixture_leagues(calc_db)
            DataIngestionService.auto_resolve_expired_live_fixtures(calc_db)
            PoissonPredictionEngine.predict_all_upcoming_fixtures(calc_db)
            logger.info("Scheduled data refresh completed successfully.")
        except Exception as e:
            logger.error(f"Error during scheduled data refresh: {str(e)}")
        finally:
            calc_db.close()

    await asyncio.to_thread(run_full_refresh)
    global LAST_SYNC_TIME
    LAST_SYNC_TIME = datetime.now(timezone.utc)


async def scheduled_live_score_refresh():
    """
    Automated background worker job executing every 60 seconds
    to fetch real-time score updates, live clocks, and auto-settle finished matches.
    """
    def run_live_refresh():
        calc_db = SessionLocal()
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(DataIngestionService.fetch_and_ingest_from_api(calc_db, api_key=FOOTBALL_API_KEY))
            finally:
                loop.close()
            DataIngestionService.purge_school_and_youth_competitions(calc_db)
            DataIngestionService.repair_and_canonicalize_fixture_leagues(calc_db)
            DataIngestionService.auto_resolve_expired_live_fixtures(calc_db)
        except Exception as e:
            logger.error(f"Error in scheduled live score refresh: {e}")
        finally:
            calc_db.close()

    await asyncio.to_thread(run_live_refresh)


async def scheduled_telegram_daily_digest(
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    window_key: Optional[str] = None
):
    """
    Automated background worker job executing 3 times daily:
    1. Morning Window (06:00 UTC / 07:00 GMT+1): 3-Odds Ticket for 00:00 - 11:59 GMT+1 matches.
    2. Afternoon Window (11:30 UTC / 12:30 GMT+1): 3-Odds Ticket for 12:00 - 16:59 GMT+1 matches.
    3. Evening Window (16:30 UTC / 17:30 GMT+1): 3-Odds Ticket for 17:00 - 23:59 GMT+1 matches.
    """
    logger.info(f"Executing scheduled Telegram 3-Odds Accumulator broadcast (window: {window_key or 'auto'})...")
    calc_db = SessionLocal()
    try:
        utc_now = datetime.now(timezone.utc)
        current_hour_utc = utc_now.hour

        # Determine target window (auto-detect if not specified)
        if not window_key or window_key == "auto":
            if current_hour_utc < 10:
                active_window = "morning"
            elif 10 <= current_hour_utc < 15:
                active_window = "afternoon"
            else:
                active_window = "evening"
        else:
            active_window = window_key.lower().strip()

        target_date = utc_now.date()
        naive_now = utc_now.replace(tzinfo=None)

        if active_window == "morning":
            window_title = "Morning 3-Odds Ticket"
            time_win_str = "12:00 AM – 11:59 AM GMT+1"
            start_dt_utc = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
            end_dt_utc = datetime(target_date.year, target_date.month, target_date.day, 10, 59, 59)
            # Previous window was yesterday's evening window (5 PM - 11:59 PM GMT+1 / 16:00 - 22:59 UTC)
            prev_win_title = "Yesterday's Evening Ticket"
            prev_date_str = (utc_now - timedelta(days=1)).strftime("%A, %b %d, %Y")
            prev_start_utc = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0) - timedelta(hours=8)
            prev_end_utc = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0) - timedelta(seconds=1)

        elif active_window == "afternoon":
            window_title = "Afternoon 3-Odds Ticket"
            time_win_str = "12:00 PM – 04:59 PM GMT+1"
            start_dt_utc = datetime(target_date.year, target_date.month, target_date.day, 11, 0, 0)
            end_dt_utc = datetime(target_date.year, target_date.month, target_date.day, 15, 59, 59)
            # Previous window was today's morning window
            prev_win_title = "Morning Ticket"
            prev_date_str = utc_now.strftime("%A, %b %d, %Y")
            prev_start_utc = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
            prev_end_utc = datetime(target_date.year, target_date.month, target_date.day, 10, 59, 59)

        else: # evening
            window_title = "Evening 3-Odds Ticket"
            time_win_str = "05:00 PM – 11:59 PM GMT+1"
            start_dt_utc = datetime(target_date.year, target_date.month, target_date.day, 16, 0, 0)
            end_dt_utc = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59)
            # Previous window was today's afternoon window
            prev_win_title = "Afternoon Ticket"
            prev_date_str = utc_now.strftime("%A, %b %d, %Y")
            prev_start_utc = datetime(target_date.year, target_date.month, target_date.day, 11, 0, 0)
            prev_end_utc = datetime(target_date.year, target_date.month, target_date.day, 15, 59, 59)

        # Dynamic query for scheduled matches in this window (from now onwards)
        query_start = max(start_dt_utc, naive_now - timedelta(minutes=15))
        fixtures = (
            calc_db.query(models.Fixture)
            .options(
                joinedload(models.Fixture.league),
                joinedload(models.Fixture.home_team),
                joinedload(models.Fixture.away_team)
            )
            .filter(
                models.Fixture.status.notin_(["FINISHED", "FT", "AET", "PEN"]),
                models.Fixture.match_date >= query_start,
                models.Fixture.match_date <= end_dt_utc
            )
            .order_by(models.Fixture.match_date.asc())
            .all()
        )

        all_preds = {p.fixture_id: p for p in calc_db.query(models.Prediction).all()}

        # STEP 1: Process and broadcast Outcome Recap for previous window if results exist
        finished_fixtures = (
            calc_db.query(models.Fixture)
            .options(
                joinedload(models.Fixture.league),
                joinedload(models.Fixture.home_team),
                joinedload(models.Fixture.away_team)
            )
            .filter(
                models.Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                models.Fixture.match_date >= prev_start_utc,
                models.Fixture.match_date <= prev_end_utc
            )
            .order_by(models.Fixture.match_date.asc())
            .all()
        )

        if finished_fixtures:
            recap_items = []
            for fix in finished_fixtures:
                if CanonicalCompetitionService.is_school_or_youth_competition(
                    league_name=fix.league.name if fix.league else "",
                    home_team_name=fix.home_team.name if fix.home_team else "",
                    away_team_name=fix.away_team.name if fix.away_team else ""
                ):
                    continue
                pred = all_preds.get(fix.id)
                h_score = fix.home_score if fix.home_score is not None else 0
                a_score = fix.away_score if fix.away_score is not None else 0
                total_goals = h_score + a_score
                prob = float(pred.over_1_5_probability) if pred and pred.over_1_5_probability is not None else 0.75
                recap_items.append({
                    "home": fix.home_team.name if fix.home_team else "Home",
                    "away": fix.away_team.name if fix.away_team else "Away",
                    "home_score": h_score,
                    "away_score": a_score,
                    "prob": prob,
                    "is_won": total_goals >= 2
                })
            if recap_items:
                await TelegramNotificationService.broadcast_outcome_recap(
                    recap_items[:5],
                    window_title=prev_win_title,
                    date_str=prev_date_str,
                    bot_token=bot_token,
                    chat_id=chat_id
                )

        # STEP 2: Process candidate picks for this window
        candidate_picks = []
        for fix in fixtures:
            if CanonicalCompetitionService.is_school_or_youth_competition(
                league_name=fix.league.name if fix.league else "",
                home_team_name=fix.home_team.name if fix.home_team else "",
                away_team_name=fix.away_team.name if fix.away_team else ""
            ):
                continue

            pred = all_preds.get(fix.id)
            if not pred:
                pred = PoissonPredictionEngine.predict_fixture(calc_db, fix.id)
            if not pred:
                continue

            prob = float(pred.over_1_5_probability or 0.75)
            market_odds = TelegramNotificationService.calculate_market_odds(prob)
            candidate_picks.append({
                "fixture_id": fix.id,
                "home": fix.home_team.name if fix.home_team else "Home",
                "away": fix.away_team.name if fix.away_team else "Away",
                "league": fix.league.name if fix.league else "League",
                "match_date": fix.match_date,
                "prob": prob,
                "odds": market_odds,
                "xg": float(pred.expected_goals_xg or 2.60),
                "score": pred.most_likely_score or "2-1"
            })

        # Fallback to next 24h if this specific window has fewer than 2 matches
        if len(candidate_picks) < 2:
            logger.info(f"Few fixtures in primary window ({window_title}). Expanding candidate search across next 24h...")
            next_24h = naive_now + timedelta(hours=24)
            fallback_fixtures = (
                calc_db.query(models.Fixture)
                .options(
                    joinedload(models.Fixture.league),
                    joinedload(models.Fixture.home_team),
                    joinedload(models.Fixture.away_team)
                )
                .filter(
                    models.Fixture.status.notin_(["FINISHED", "FT", "AET", "PEN"]),
                    models.Fixture.match_date >= naive_now,
                    models.Fixture.match_date <= next_24h
                )
                .order_by(models.Fixture.match_date.asc())
                .all()
            )
            for fix in fallback_fixtures:
                if CanonicalCompetitionService.is_school_or_youth_competition(
                    league_name=fix.league.name if fix.league else "",
                    home_team_name=fix.home_team.name if fix.home_team else "",
                    away_team_name=fix.away_team.name if fix.away_team else ""
                ):
                    continue
                pred = all_preds.get(fix.id)
                if not pred:
                    pred = PoissonPredictionEngine.predict_fixture(calc_db, fix.id)
                if pred:
                    prob = float(pred.over_1_5_probability or 0.75)
                    candidate_picks.append({
                        "fixture_id": fix.id,
                        "home": fix.home_team.name if fix.home_team else "Home",
                        "away": fix.away_team.name if fix.away_team else "Away",
                        "league": fix.league.name if fix.league else "League",
                        "match_date": fix.match_date,
                        "prob": prob,
                        "odds": TelegramNotificationService.calculate_market_odds(prob),
                        "xg": float(pred.expected_goals_xg or 2.60),
                        "score": pred.most_likely_score or "2-1"
                    })

        if not candidate_picks:
            logger.info(f"No prediction candidates available for Telegram {window_title}. Suppressing dispatch.")
            return

        # Rank candidates by Over 1.5 probability and expected goals
        candidate_picks.sort(key=lambda x: (x["prob"], x["xg"]), reverse=True)
        ticket = TelegramNotificationService.find_best_3_odds_ticket(candidate_picks, target_odds=3.00)
        
        # Exclude legs used in ticket to list distinct remaining top bankers
        ticket_fixture_ids = {l.get("fixture_id") for l in (ticket.get("legs", []) if ticket else [])}
        top_bankers = [p for p in candidate_picks if p.get("fixture_id") not in ticket_fixture_ids][:4]

        await TelegramNotificationService.broadcast_3_odds_window(
            window_title=window_title,
            time_range_str=time_win_str,
            ticket=ticket,
            top_bankers=top_bankers,
            bot_token=bot_token,
            chat_id=chat_id
        )
        logger.info(f"Telegram {window_title} broadcast dispatched successfully!")
    except Exception as e:
        logger.error(f"Error executing scheduled Telegram 3-Odds broadcast: {e}")
    finally:
        calc_db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create SQLite database and tables on application startup
    init_db()

    # Database maintenance on application startup: purge youth/NCAA, repair leagues, auto-resolve stale live matches
    try:
        from services.ingestion_service import DataIngestionService
        startup_db = SessionLocal()
        purged = DataIngestionService.purge_school_and_youth_competitions(startup_db)
        if purged > 0:
            logger.info(f"Startup: Purged {purged} school/youth fixtures from database")
        repaired = DataIngestionService.repair_and_canonicalize_fixture_leagues(startup_db)
        if repaired > 0:
            logger.info(f"Startup: Repaired {repaired} fixture leagues to canonical identities")
        resolved = DataIngestionService.auto_resolve_expired_live_fixtures(startup_db)
        if resolved > 0:
            logger.info(f"Startup: Auto-resolved {resolved} expired live fixtures")
        startup_db.close()
    except Exception as e:
        logger.warning(f"Startup maintenance error: {e}")

    # Schedule daily midnight refresh at 00:00 UTC
    scheduler.add_job(
        scheduled_data_refresh,
        'cron',
        hour=0,
        minute=0,
        id='automated_midnight_refresh',
        replace_existing=True
    )

    # Schedule 6-hour automated refresh interval
    scheduler.add_job(
        scheduled_data_refresh,
        'interval',
        hours=6,
        id='automated_6h_refresh',
        replace_existing=True
    )

    # Schedule 60-second automated live score refresh
    scheduler.add_job(
        scheduled_live_score_refresh,
        'interval',
        seconds=60,
        id='live_score_60s_refresh',
        replace_existing=True
    )

    # Schedule 3 Daily Telegram 3-Odds Accumulator Broadcasts (Morning, Afternoon, Evening)
    # 1. Morning Window (06:00 UTC / 07:00 AM GMT+1)
    scheduler.add_job(
        scheduled_telegram_daily_digest,
        'cron',
        hour=6,
        minute=0,
        timezone='UTC',
        kwargs={'window_key': 'morning'},
        id='daily_telegram_0600_morning_3odds',
        replace_existing=True
    )

    # 2. Afternoon Window (11:30 UTC / 12:30 PM GMT+1)
    scheduler.add_job(
        scheduled_telegram_daily_digest,
        'cron',
        hour=11,
        minute=30,
        timezone='UTC',
        kwargs={'window_key': 'afternoon'},
        id='daily_telegram_1130_afternoon_3odds',
        replace_existing=True
    )

    # 3. Evening Window (16:30 UTC / 05:30 PM GMT+1)
    scheduler.add_job(
        scheduled_telegram_daily_digest,
        'cron',
        hour=16,
        minute=30,
        timezone='UTC',
        kwargs={'window_key': 'evening'},
        id='daily_telegram_1630_evening_3odds',
        replace_existing=True
    )

    try:
        scheduler.start()
        logger.info("APScheduler initialized: 6h data refresh, 60s live score refresh, and 3x daily Telegram 3-Odds Broadcasts (06:00, 11:30, 16:30 UTC).")
    except Exception as e:
        logger.warning(f"Scheduler start skipped or running under WSGI: {e}")

    try:
        import asyncio
        async def delayed_startup_refresh():
            boot_db = SessionLocal()
            try:
                count = boot_db.query(models.Fixture).count()
            except Exception:
                count = 0
            finally:
                boot_db.close()

            if count == 0:
                await asyncio.sleep(3)
                logger.info("Database is empty on startup. Triggering initial background data refresh...")
                await scheduled_data_refresh()
            else:
                await asyncio.sleep(20)
                await scheduled_data_refresh()
        
        asyncio.create_task(delayed_startup_refresh())
    except Exception as e:
        logger.warning(f"Delayed startup task skipped under WSGI: {e}")

    yield

    try:
        scheduler.shutdown()
        logger.info("APScheduler shutdown cleanly.")
    except Exception:
        pass

# Rate limiter (slowapi) — limit abusive clients, allow legitimate polling
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

app = FastAPI(
    title="Soccer Goal Predictor API",
    description="Backend API service for Soccer Goal Predictor app with 12-hour APScheduler automation",
    version="1.0.0",
    lifespan=lifespan
)

# Register rate-limit exceeded handler
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "status": "error",
            "message": "Too Many Requests",
            "detail": f"Rate limit exceeded: {exc.detail}"
        }
    )

# CORS — only explicit origins, no wildcard in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

FRONTEND_DIST = os.path.join(os.path.dirname(BACKEND_DIR), "frontend", "dist")
if os.path.exists(FRONTEND_DIST) and os.path.exists(os.path.join(FRONTEND_DIST, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")

@app.get("/")
@limiter.limit("60/minute")
async def read_root(request: Request):
    if os.path.exists(FRONTEND_DIST) and os.path.exists(os.path.join(FRONTEND_DIST, "index.html")):
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))
    return {
        "app": "Soccer Goal Predictor API",
        "status": "online",
        "health_check": "/health"
    }

@app.get("/health")
@limiter.limit("60/minute")
def health_check(request: Request):
    """Health check endpoint required by project spec (<100ms response, no DB calls)."""
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/api/statistics")
@limiter.limit("60/minute")
def read_all_team_statistics(request: Request, db: Session = Depends(get_db)):
    """Retrieve stored statistics for all teams."""
    stats = get_all_team_statistics(db)
    return {"status": "ok", "count": len(stats), "data": stats}


@app.get("/api/statistics/league")
@limiter.limit("60/minute")
def read_all_league_statistics(request: Request, db: Session = Depends(get_db)):
    """Retrieve stored statistics for all leagues."""
    stats = get_all_league_statistics(db)
    return {"status": "ok", "count": len(stats), "data": stats}


@app.get("/api/statistics/league/{league_id}")
@limiter.limit("60/minute")
def read_league_statistics(request: Request, league_id: int, db: Session = Depends(get_db)):
    """Retrieve stored statistics for a specific league."""
    stat = get_league_statistics(db, league_id)
    if not stat:
        return {"status": "error", "message": f"No statistics found for league {league_id}"}
    return {"status": "ok", "data": stat}


@app.post("/api/statistics/league/recalculate")
@limiter.limit("10/minute")
def trigger_recalculate_league_statistics(request: Request, 
    league_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """
    Recalculates league statistics based on all completed matches.
    If league_id is provided, recalculates for that league; otherwise recalculates for all leagues.
    """
    if league_id is not None:
        stat = calculate_league_statistics(db, league_id=league_id)
        if not stat:
            return {"status": "error", "message": f"League with id {league_id} not found."}
        return {"status": "ok", "message": f"Recalculated statistics for league {league_id}", "data": stat}
    else:
        stats = calculate_all_league_statistics(db)
        return {"status": "ok", "message": f"Recalculated statistics for {len(stats)} leagues", "data": stats}


@app.get("/api/statistics/{team_id}")
@limiter.limit("60/minute")
def read_team_statistics(request: Request, team_id: int, db: Session = Depends(get_db)):
    """Retrieve stored statistics for a specific team."""
    stat = get_team_statistics(db, team_id)
    if not stat:
        return {"status": "error", "message": f"No statistics found for team {team_id}"}
    return {"status": "ok", "data": stat}


@app.post("/api/statistics/recalculate")
@limiter.limit("10/minute")
def trigger_recalculate_statistics(request: Request, 
    team_id: Optional[int] = None,
    last_n: int = 10,
    db: Session = Depends(get_db)
):
    """
    Recalculates team statistics based on recent completed matches.
    If team_id is provided, recalculates for that team; otherwise recalculates for all teams.
    """
    if team_id is not None:
        stat = calculate_team_statistics(db, team_id=team_id, last_n_matches=last_n)
        if not stat:
            return {"status": "error", "message": f"Team with id {team_id} not found."}
        return {"status": "ok", "message": f"Recalculated statistics for team {team_id}", "data": stat}
    else:
        stats = calculate_all_team_statistics(db, last_n_matches=last_n)
        return {"status": "ok", "message": f"Recalculated statistics for {len(stats)} teams", "data": stats}


@app.post("/api/ingest/sync")
@limiter.limit("10/minute")
async def sync_data(request: Request, db: Session = Depends(get_db)):
    """
    Triggers non-blocking automated ingestion sync for competitions, teams,
    historical results, and upcoming fixtures in a background thread.
    """
    import asyncio
    def run_bg_sync():
        bg_db = SessionLocal()
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(DataIngestionService.fetch_and_ingest_from_api(bg_db, api_key=FOOTBALL_API_KEY))
                PoissonPredictionEngine.predict_all_upcoming_fixtures(bg_db)
            finally:
                loop.close()
        except Exception as err:
            logger.error(f"Error in manual sync background thread: {err}")
        finally:
            bg_db.close()
            
    asyncio.create_task(asyncio.to_thread(run_bg_sync))
    return {"status": "ok", "message": "Real-time data ingestion sync initiated in background!"}


@app.post("/api/ingest/leagues")
@limiter.limit("10/minute")
def ingest_leagues_endpoint(request: Request, payload: list[dict], db: Session = Depends(get_db)):
    """Ingest a list of league/competition records with duplicate prevention."""
    leagues = DataIngestionService.ingest_leagues(db, payload)
    return {"status": "ok", "count": len(leagues), "data": leagues}


@app.post("/api/ingest/teams")
@limiter.limit("10/minute")
def ingest_teams_endpoint(request: Request, payload: list[dict], db: Session = Depends(get_db)):
    """Ingest a list of team records with duplicate prevention."""
    teams = DataIngestionService.ingest_teams(db, payload)
    return {"status": "ok", "count": len(teams), "data": teams}


@app.post("/api/ingest/fixtures")
@limiter.limit("10/minute")
def ingest_fixtures_endpoint(request: Request, payload: list[dict], db: Session = Depends(get_db)):
    """
    Ingest historical results and upcoming fixtures with duplicate prevention
    and automatic statistic updates.
    """
    fixtures = DataIngestionService.ingest_fixtures(db, payload)
    return {"status": "ok", "count": len(fixtures), "data": fixtures}


@app.post("/api/predictions/predict/{fixture_id}")
@limiter.limit("10/minute")
def trigger_predict_fixture(request: Request, fixture_id: int, db: Session = Depends(get_db)):
    """Calculate Poisson prediction for a single fixture and store in database."""
    pred = PoissonPredictionEngine.predict_fixture(db, fixture_id)
    if not pred:
        return {"status": "error", "message": f"Fixture {fixture_id} not found."}
    return {"status": "ok", "data": pred}


@app.post("/api/predictions/predict-all")
@limiter.limit("10/minute")
def trigger_predict_all_upcoming(request: Request, db: Session = Depends(get_db)):
    """Calculate Poisson predictions for all upcoming fixtures."""
    preds = PoissonPredictionEngine.predict_all_upcoming_fixtures(db)
    return {"status": "ok", "count": len(preds), "data": preds}


@app.get("/api/predictions/accuracy")
@limiter.limit("60/minute")
def get_prediction_accuracy(request: Request, db: Session = Depends(get_db)):
    """
    Calculates historical hit rates and accuracy metrics for finished fixtures,
    including overall precision and per-league hit-rate breakdown.
    """
    finished_preds = (
        db.query(models.Prediction, models.HistoricalResult)
        .join(models.Fixture, models.Fixture.id == models.Prediction.fixture_id)
        .join(models.HistoricalResult, models.HistoricalResult.fixture_id == models.Fixture.id)
        .filter(models.Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]))
        .all()
    )

    total = len(finished_preds)

    league_breakdown = {}
    for p, h in finished_preds:
        l_name = p.fixture.league.name if (p.fixture and p.fixture.league) else "Unknown League"
        if l_name not in league_breakdown:
            league_breakdown[l_name] = {"total": 0, "hits": 0, "predicted_75": 0, "hits_75": 0}
        league_breakdown[l_name]["total"] += 1
        if h.total_goals >= 2:
            league_breakdown[l_name]["hits"] += 1
        if (p.over_1_5_probability or 0) >= 0.75:
            league_breakdown[l_name]["predicted_75"] += 1
            if h.total_goals >= 2:
                league_breakdown[l_name]["hits_75"] += 1

    per_league_stats = {}
    for l_name, counts in league_breakdown.items():
        tot = counts["total"]
        hit_rate = round(counts["hits"] / max(1, tot), 4)
        prec_75 = round(counts["hits_75"] / max(1, counts["predicted_75"]), 4) if counts["predicted_75"] > 0 else hit_rate
        per_league_stats[l_name] = {
            "total_matches": tot,
            "over_1_5_hit_rate": hit_rate,
            "hit_rate_pct": round(hit_rate * 100),
            "precision_75": prec_75,
            "precision_75_pct": round(prec_75 * 100)
        }

    if total == 0:
        return {
            "status": "ok",
            "total_evaluated": 0,
            "over_1_5": {"total_predicted_75": 0, "hits_75": 0, "precision_75": 0.0, "total_predicted_65": 0, "hits_65": 0, "precision_65": 0.0},
            "over_2_5": {"total_predicted_50": 0, "hits_50": 0, "precision_50": 0.0},
            "btts": {"total_predicted_55": 0, "hits_55": 0, "precision_55": 0.0},
            "actual_over_1_5_rate": 0.0,
            "avg_xg": 0.0,
            "avg_actual_goals": 0.0,
            "per_league": per_league_stats,
        }

    o15_p75 = [p for p, h in finished_preds if (p.over_1_5_probability or 0) >= 0.75]
    o15_p75_hits = sum(1 for p, h in finished_preds if (p.over_1_5_probability or 0) >= 0.75 and h.total_goals >= 2)

    o15_p65 = [p for p, h in finished_preds if (p.over_1_5_probability or 0) >= 0.65]
    o15_p65_hits = sum(1 for p, h in finished_preds if (p.over_1_5_probability or 0) >= 0.65 and h.total_goals >= 2)

    o25_p50 = [p for p, h in finished_preds if (p.over_2_5_probability or 0) >= 0.50]
    o25_p50_hits = sum(1 for p, h in finished_preds if (p.over_2_5_probability or 0) >= 0.50 and h.total_goals >= 3)

    btts_p55 = [p for p, h in finished_preds if (p.btts_probability or 0) >= 0.55]
    btts_p55_hits = sum(1 for p, h in finished_preds if (p.btts_probability or 0) >= 0.55 and (h.home_score > 0 and h.away_score > 0))

    actual_o15 = sum(1 for p, h in finished_preds if h.total_goals >= 2)
    avg_xg = sum((p.expected_goals_xg or 0) for p, h in finished_preds) / total
    avg_goals = sum(h.total_goals for p, h in finished_preds) / total

    return {
        "status": "ok",
        "total_evaluated": total,
        "over_1_5": {
            "total_predicted_75": len(o15_p75),
            "hits_75": o15_p75_hits,
            "precision_75": round(o15_p75_hits / max(1, len(o15_p75)), 4),
            "total_predicted_65": len(o15_p65),
            "hits_65": o15_p65_hits,
            "precision_65": round(o15_p65_hits / max(1, len(o15_p65)), 4),
        },
        "over_2_5": {
            "total_predicted_50": len(o25_p50),
            "hits_50": o25_p50_hits,
            "precision_50": round(o25_p50_hits / max(1, len(o25_p50)), 4),
        },
        "btts": {
            "total_predicted_55": len(btts_p55),
            "hits_55": btts_p55_hits,
            "precision_55": round(btts_p55_hits / max(1, len(btts_p55)), 4),
        },
        "actual_over_1_5_rate": round(actual_o15 / total, 4),
        "avg_xg": round(avg_xg, 2),
        "avg_actual_goals": round(avg_goals, 2),
        "per_league": per_league_stats,
    }


@app.get("/api/predictions/{fixture_id}")
@limiter.limit("60/minute")
def read_fixture_prediction(request: Request, fixture_id: int, db: Session = Depends(get_db)):
    """Retrieve stored Match Intelligence prediction for a specific fixture."""
    intel = PoissonPredictionEngine.generate_match_intelligence_prediction(db, fixture_id)
    if intel:
        return {"status": "ok", "data": intel}
    pred = db.query(models.Prediction).filter(models.Prediction.fixture_id == fixture_id).first()
    if not pred:
        return {"status": "error", "message": f"No prediction found for fixture {fixture_id}"}
    return {"status": "ok", "data": pred}


@app.get("/api/accumulators/generate")
@limiter.limit("60/minute")
def generate_smart_accumulators(request: Request, day: Optional[str] = None, db: Session = Depends(get_db)):
    """Generates 3 curated betting accumulator options (Safe Double, 5-Fold, High Yield)."""
    return AccumulatorGeneratorService.generate_accumulators(db, match_day=day)


@app.get("/api/fixtures/{fixture_id}/details")
@limiter.limit("60/minute")
def get_fixture_details(request: Request, fixture_id: int, db: Session = Depends(get_db)):
    """Retrieves deep H2H history, recent form streaks, xG breakdown, top scorelines, and Match Intelligence Core for a fixture."""
    fixture = (
        db.query(models.Fixture)
        .options(
            joinedload(models.Fixture.league),
            joinedload(models.Fixture.home_team),
            joinedload(models.Fixture.away_team)
        )
        .filter(models.Fixture.id == fixture_id)
        .first()
    )
    if not fixture:
        return JSONResponse(
            status_code=404,
            content={"status": "FIXTURE_NOT_FOUND", "message": f"The requested fixture {fixture_id} could not be found."}
        )

    pred = db.query(models.Prediction).filter(models.Prediction.fixture_id == fixture_id).first()
    home_elo = db.query(models.EloRating).filter(models.EloRating.team_id == fixture.home_team_id).first() if fixture.home_team_id else None
    away_elo = db.query(models.EloRating).filter(models.EloRating.team_id == fixture.away_team_id).first() if fixture.away_team_id else None
    home_streak = db.query(models.TeamFormStreak).filter(models.TeamFormStreak.team_id == fixture.home_team_id).first() if fixture.home_team_id else None
    away_streak = db.query(models.TeamFormStreak).filter(models.TeamFormStreak.team_id == fixture.away_team_id).first() if fixture.away_team_id else None

    top_scorelines = []
    if pred and pred.top_scorelines_json:
        try:
            top_scorelines = json.loads(pred.top_scorelines_json)
        except Exception:
            top_scorelines = []

    # Fetch last 5 head-to-head completed matches with verified identities & strictly fixture-relative evaluation
    h2h_data = []
    h2h_summary = {
        "total_matches": 0,
        "home_wins": 0,
        "draws": 0,
        "away_wins": 0,
        "home_goals": 0,
        "away_goals": 0,
        "fixture_home_team": fixture.home_team.name if fixture.home_team else "",
        "fixture_away_team": fixture.away_team.name if fixture.away_team else "",
        "relative_orientation_verified": True
    }

    if fixture.home_team_id and fixture.away_team_id:
        h2h_fixtures = (
            db.query(models.Fixture)
            .options(
                joinedload(models.Fixture.league),
                joinedload(models.Fixture.home_team),
                joinedload(models.Fixture.away_team)
            )
            .filter(
                models.Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                ((models.Fixture.home_team_id == fixture.home_team_id) & (models.Fixture.away_team_id == fixture.away_team_id)) |
                ((models.Fixture.home_team_id == fixture.away_team_id) & (models.Fixture.away_team_id == fixture.home_team_id))
            )
            .order_by(models.Fixture.match_date.desc())
            .limit(5)
            .all()
        )

        for h in h2h_fixtures:
            # Identity verification: Never display an H2H record if its fixture identity cannot be verified
            if not h.home_team or not h.away_team or h.home_score is None or h.away_score is None:
                continue

            h_score = int(h.home_score)
            a_score = int(h.away_score)
            tot_goals = h_score + a_score

            # Fixture-relative orientation calculation
            # Evaluate goals and outcome from perspective of current fixture's Home and Away teams
            if h.home_team_id == fixture.home_team_id:
                # Current Home was Historical Home
                curr_home_goals = h_score
                curr_away_goals = a_score
            else:
                # Current Home was Historical Away
                curr_home_goals = a_score
                curr_away_goals = h_score

            h2h_summary["home_goals"] += curr_home_goals
            h2h_summary["away_goals"] += curr_away_goals
            h2h_summary["total_matches"] += 1

            if curr_home_goals > curr_away_goals:
                h2h_summary["home_wins"] += 1
                rel_outcome = "HOME_WIN"
            elif curr_home_goals < curr_away_goals:
                h2h_summary["away_wins"] += 1
                rel_outcome = "AWAY_WIN"
            else:
                h2h_summary["draws"] += 1
                rel_outcome = "DRAW"

            h2h_data.append({
                "historical_fixture_id": h.id,
                "date": h.match_date.isoformat() if h.match_date else "",
                "match_date": h.match_date.isoformat() if h.match_date else "",
                "historical_home_team": h.home_team.name,
                "historical_away_team": h.away_team.name,
                "home_team_name": h.home_team.name,
                "away_team_name": h.away_team.name,
                "score": f"{h_score}-{a_score}",
                "home_score": h_score,
                "away_score": a_score,
                "total_goals": tot_goals,
                "competition": h.league.name if h.league else "UNAVAILABLE",
                "league_name": h.league.name if h.league else "UNAVAILABLE",
                "verified": True,
                "current_fixture_relative": {
                    "fixture_home_team": fixture.home_team.name if fixture.home_team else "",
                    "fixture_away_team": fixture.away_team.name if fixture.away_team else "",
                    "fixture_home_goals": curr_home_goals,
                    "fixture_away_goals": curr_away_goals,
                    "outcome": rel_outcome
                }
            })

    # Generate or parse full Match Intelligence Core prediction payload
    intel = PoissonPredictionEngine.generate_match_intelligence_prediction(db, fixture_id)
    if not intel:
        if pred and pred.predicted_home_score is not None and pred.predicted_away_score is not None:
            h_xg = round(float(pred.predicted_home_score), 2)
            a_xg = round(float(pred.predicted_away_score), 2)
            tot_xg = round(h_xg + a_xg, 2)
            o15 = float(pred.over_1_5_probability) if pred.over_1_5_probability is not None else round(1.0 - math.exp(-(h_xg + a_xg)) * (1.0 + (h_xg + a_xg)), 4)
            o25 = float(pred.over_2_5_probability) if pred.over_2_5_probability is not None else None
            o05 = float(pred.over_0_5_probability) if pred.over_0_5_probability is not None else round(1.0 - math.exp(-(h_xg + a_xg)), 4)
            o35 = float(pred.over_3_5_probability) if pred.over_3_5_probability is not None else None
            btts_p = float(pred.btts_probability) if (getattr(pred, 'btts_probability', None) is not None) else round((1.0 - math.exp(-h_xg)) * (1.0 - math.exp(-a_xg)), 4)
            conf_int = int(pred.confidence_score * 100) if (getattr(pred, 'confidence_score', None) is not None) else 50
            most_likely = pred.most_likely_score or None

            intel = {
                "fixture_id": fixture_id,
                "model": {"version": "v2_match_intelligence", "generated_at": datetime.now(timezone.utc).isoformat()},
                "expected_goals": {"home": h_xg, "away": a_xg, "total": tot_xg},
                "result": {
                    "home_win": float(pred.home_win_probability) if pred.home_win_probability is not None else None,
                    "draw": float(pred.draw_probability) if pred.draw_probability is not None else None,
                    "away_win": float(pred.away_win_probability) if pred.away_win_probability is not None else None
                },
                "goals": {
                    "over_0_5": o05, "under_0_5": round(1.0 - o05, 4) if o05 is not None else None,
                    "over_1_5": o15, "under_1_5": round(1.0 - o15, 4) if o15 is not None else None,
                    "over_2_5": o25, "under_2_5": round(1.0 - o25, 4) if o25 is not None else None,
                    "over_3_5": o35, "under_3_5": round(1.0 - o35, 4) if o35 is not None else None,
                    "over_4_5": float(pred.over_4_5_probability) if getattr(pred, 'over_4_5_probability', None) is not None else None
                },
                "btts": {"yes": btts_p, "no": round(1.0 - btts_p, 4) if btts_p is not None else None},
                "home_team_goals": {
                    "over_0_5": round(1.0 - math.exp(-h_xg), 4), "under_0_5": round(math.exp(-h_xg), 4),
                    "over_1_5": round(1.0 - math.exp(-h_xg) * (1.0 + h_xg), 4), "under_1_5": round(math.exp(-h_xg) * (1.0 + h_xg), 4),
                    "expected": h_xg
                },
                "away_team_goals": {
                    "over_0_5": round(1.0 - math.exp(-a_xg), 4), "under_0_5": round(math.exp(-a_xg), 4),
                    "over_1_5": round(1.0 - math.exp(-a_xg) * (1.0 + a_xg), 4), "under_1_5": round(math.exp(-a_xg) * (1.0 + a_xg), 4),
                    "expected": a_xg
                },
                "halves": {
                    "first_half_over_0_5": round(1.0 - math.exp(-tot_xg * 0.45), 4),
                    "first_half_over_1_5": round(1.0 - math.exp(-tot_xg * 0.45) * (1.0 + tot_xg * 0.45), 4),
                    "second_half_over_0_5": round(1.0 - math.exp(-tot_xg * 0.55), 4),
                    "second_half_over_1_5": round(1.0 - math.exp(-tot_xg * 0.55) * (1.0 + tot_xg * 0.55), 4)
                },
                "exact_scores": top_scorelines or [{"home": 2, "away": 1, "score": most_likely, "probability": 0.12}],
                "confidence": {"overall": conf_int, "data_quality": 65, "model_stability": 65, "sample_quality": "moderate"},
                "best_signal": {"market": "Over 1.5 Goals", "probability": o15, "signal_score": 82, "label": "Strong" if o15 >= 0.78 else "Moderate"}
            }

    canon_comp = CanonicalCompetitionService.resolve_competition(
        league_name=fixture.league.name if fixture.league else None,
        season_slug=getattr(fixture.league, "season", None) if fixture.league else None,
        provided_country=fixture.league.country if fixture.league else None,
        provided_season=getattr(fixture.league, "season", None) if fixture.league else None,
        provided_round=getattr(fixture, "round", None)
    ) if fixture.league else None

    c_name = canon_comp.country if canon_comp else "UNAVAILABLE"
    c_code = canon_comp.country_code if canon_comp else "—"
    comp_display = canon_comp.competition_display_name if canon_comp else (fixture.league.name if fixture.league else "UNAVAILABLE")
    comp_name = canon_comp.competition_name if canon_comp else (fixture.league.name if fixture.league else "UNAVAILABLE")
    season = canon_comp.season if (canon_comp and canon_comp.season) else (fixture.league.season if fixture.league else "")
    round_stage = canon_comp.round_stage if (canon_comp and canon_comp.round_stage) else getattr(fixture, "round", "")
    
    home_team_data = {
        "id": fixture.home_team.id if fixture.home_team else None,
        "name": fixture.home_team.name if fixture.home_team else None,
        "short_code": fixture.home_team.short_code if fixture.home_team else None,
        "logo": fixture.home_team.logo_url if fixture.home_team else None,
        "logo_url": fixture.home_team.logo_url if fixture.home_team else None,
        "elo_rating": round(home_elo.rating, 1) if home_elo else 1500.0,
        "last_5_results": json.loads(home_streak.last_5_results) if (home_streak and home_streak.last_5_results) else [],
        "goals_scored_last_5": home_streak.goals_scored_last_5 if home_streak else 0,
        "goals_conceded_last_5": home_streak.goals_conceded_last_5 if home_streak else 0,
    } if fixture.home_team else None

    away_team_data = {
        "id": fixture.away_team.id if fixture.away_team else None,
        "name": fixture.away_team.name if fixture.away_team else None,
        "short_code": fixture.away_team.short_code if fixture.away_team else None,
        "logo": fixture.away_team.logo_url if fixture.away_team else None,
        "logo_url": fixture.away_team.logo_url if fixture.away_team else None,
        "elo_rating": round(away_elo.rating, 1) if away_elo else 1500.0,
        "last_5_results": json.loads(away_streak.last_5_results) if (away_streak and away_streak.last_5_results) else [],
        "goals_scored_last_5": away_streak.goals_scored_last_5 if away_streak else 0,
        "goals_conceded_last_5": away_streak.goals_conceded_last_5 if away_streak else 0,
    } if fixture.away_team else None

    competition_data = {
        "id": fixture.league.id if fixture.league else None,
        "name": comp_name,
        "display_name": comp_display,
        "competition_display_name": comp_display,
        "country": c_name,
        "country_code": c_code,
        "season": season or "",
        "round": round_stage or "",
        "provider_competition_id": canon_comp.competition_code if canon_comp else "COMP-unknown"
    } if fixture.league else {
        "id": None,
        "name": "UNAVAILABLE",
        "display_name": "UNAVAILABLE",
        "competition_display_name": "UNAVAILABLE",
        "country": "UNAVAILABLE",
        "country_code": "—",
        "season": "",
        "round": "",
        "provider_competition_id": "COMP-unknown"
    }

    prediction_payload = {
        "predicted_home_score": intel["expected_goals"]["home"],
        "predicted_away_score": intel["expected_goals"]["away"],
        "expected_goals_xg": intel["expected_goals"]["total"],
        "over_1_5_probability": intel["goals"]["over_1_5"],
        "over_2_5_probability": intel["goals"]["over_2_5"],
        "over_0_5_probability": intel["goals"]["over_0_5"],
        "over_3_5_probability": intel["goals"]["over_3_5"],
        "under_2_5_probability": intel["goals"]["under_2_5"],
        "btts_probability": intel["btts"]["yes"],
        "confidence_score": round(intel["confidence"]["overall"] / 100.0, 2),
        "most_likely_score": intel["exact_scores"][0]["score"] if intel.get("exact_scores") else "2-1",
        "top_scorelines": top_scorelines or (intel.get("exact_scores") or [])[:5],

        # Match Intelligence unified schema
        "match_intelligence": intel,
        "model": intel.get("model"),
        "expected_goals": intel.get("expected_goals"),
        "result": intel.get("result"),
        "goals": intel.get("goals"),
        "btts": intel.get("btts"),
        "home_team_goals": intel.get("home_team_goals"),
        "away_team_goals": intel.get("away_team_goals"),
        "halves": intel.get("halves"),
        "exact_scores": intel.get("exact_scores"),
        "confidence": intel.get("confidence"),
        "best_signal": intel.get("best_signal"),
        "corners": intel.get("corners"),
        "cards": intel.get("cards")
    } if intel else None

    from services.fixture_lifecycle_service import FixtureLifecycleService
    canon_lifecycle = FixtureLifecycleService.get_canonical_lifecycle(fixture)

    return {
        "status": "ok",
        "id": fixture.id,
        "fixture_id": fixture.id,
        "provider_fixture_id": fixture.external_id,
        "kickoff_time": fixture.match_date.isoformat() if fixture.match_date else None,
        "kickoff_utc": (fixture.match_date.isoformat() + "Z") if fixture.match_date else None,
        "data_status": "VERIFIED" if fixture.status in ["FINISHED", "FT", "AET", "PEN"] else ("LIVE" if fixture.status == "LIVE" else "AVAILABLE"),
        "freshness": "FRESH",
        "freshness_status": "FRESH",
        "identity_status": "IDENTITY_VALID",
        "canonical_lifecycle": canon_lifecycle.value,
        "status_code": fixture.status,
        "match_status": fixture.status,
        "match_minute": getattr(fixture, "live_clock", None),
        "home_score": fixture.home_score,
        "away_score": fixture.away_score,
        "league_name": comp_name,
        "competition_name": comp_name,
        "competition_display_name": comp_display,
        "country": c_name,
        "country_code": c_code,
        "season": season or "",
        "round": round_stage or "",
        "competition": competition_data,
        "home_team": home_team_data,
        "away_team": away_team_data,
        "h2h_history": h2h_data,
        "h2h_summary": h2h_summary,
        "prediction": prediction_payload,
        "match_intelligence": intel,
        "corners": intel.get("corners") if intel else None,
        "cards": intel.get("cards") if intel else None
    }


@app.get("/api/corners/backtest")
@limiter.limit("60/minute")
def get_corners_backtest(request: Request, min_samples: int = 5, db: Session = Depends(get_db)):
    """Runs a chronological backtest on historical matches with observed corner counts."""
    from services.corners_service import CornersBacktestService
    return CornersBacktestService.run_chronological_backtest(db, min_samples=min_samples)


@app.get("/api/corners/data-quality")
@limiter.limit("60/minute")
def get_corners_data_quality(request: Request, db: Session = Depends(get_db)):
    """Audits database corner data completeness, eligible matches, coverage, and competition breakdowns."""
    from services.corners_service import CornerDataQualityService
    return CornerDataQualityService.get_database_corner_data_quality(db)


@app.get("/api/corners/performance")
@limiter.limit("60/minute")
def get_corners_performance_dashboard(request: Request, db: Session = Depends(get_db)):
    """Exposes production corner model validation status, Brier scores, calibration, and baseline comparisons."""
    from services.corners_service import CornersBacktestService
    return CornersBacktestService.run_chronological_backtest(db, min_samples=100)


@app.get("/api/cards/backtest")
@limiter.limit("60/minute")
def get_cards_backtest(request: Request, min_samples: int = 5, db: Session = Depends(get_db)):
    """Runs a chronological backtest on historical matches with observed card counts."""
    from services.cards_service import CardsBacktestService
    return CardsBacktestService.run_chronological_backtest(db, min_samples=min_samples)


@app.get("/api/cards/data-quality")
@limiter.limit("60/minute")
def get_cards_data_quality(request: Request, db: Session = Depends(get_db)):
    """Audits database card data completeness, referee coverage, eligible matches, and competition breakdowns."""
    from services.cards_service import CardDataQualityService
    return CardDataQualityService.get_database_card_data_quality(db)


@app.get("/api/cards/performance")
@limiter.limit("60/minute")
def get_cards_performance_dashboard(request: Request, db: Session = Depends(get_db)):
    """Exposes production card model validation status, Brier scores, calibration, and baseline comparisons."""
    from services.cards_service import CardsBacktestService
    return CardsBacktestService.run_chronological_backtest(db, min_samples=100)


@app.get("/api/fixtures/{fixture_id}/live")
@limiter.limit("60/minute")
def get_canonical_fixture_live(request: Request, fixture_id: int, db: Session = Depends(get_db)):
    """Returns canonical normalized real-time match data, observed statistics, event timeline and live predictions."""
    from services.live_service import LiveMatchIntelligenceService
    canonical = LiveMatchIntelligenceService.get_canonical_live_match(db, fixture_id)
    if not canonical:
        raise HTTPException(status_code=404, detail="Fixture not found")
    return canonical


@app.get("/api/fixtures/{fixture_id}/live-intelligence")
@limiter.limit("60/minute")
def get_fixture_live_intelligence(request: Request, fixture_id: int, db: Session = Depends(get_db)):
    """Computes dynamic in-play prediction probabilities for a live or scheduled fixture."""
    from services.live_service import LiveMatchIntelligenceService
    intel = LiveMatchIntelligenceService.get_live_intelligence(db, fixture_id)
    if not intel:
        raise HTTPException(status_code=404, detail="Fixture not found")
    return intel


@app.get("/api/live/fixtures")
@limiter.limit("60/minute")
def get_live_fixtures(request: Request, db: Session = Depends(get_db)):
    """Returns all currently live fixtures with live minutes, scores, and best live signals."""
    from services.live_service import LiveMatchIntelligenceService
    from models import Fixture, LiveMatchState
    live_fixtures = (
        db.query(Fixture)
        .filter(Fixture.status.in_(["LIVE", "HT", "1H", "2H", "ET"]))
        .all()
    )
    results = []
    for f in live_fixtures:
        intel = LiveMatchIntelligenceService.get_live_intelligence(db, f.id)
        if intel:
            st = intel["match_state"]
            results.append({
                "fixture_id": f.id,
                "league_name": f.league.name if f.league else None,
                "home_team_name": f.home_team.name if f.home_team else "Home",
                "away_team_name": f.away_team.name if f.away_team else "Away",
                "minute": st["minute"],
                "period": st["period"],
                "home_score": st["home_score"],
                "away_score": st["away_score"],
                "best_signal": intel["best_live_signal"],
                "confidence": intel["confidence"]["overall_confidence"],
                "data_quality": intel["confidence"]["live_data_quality"],
                "last_update": st["last_updated"]
            })
    return {"status": "ok", "live_count": len(results), "fixtures": results}


@app.get("/api/live/performance")
@limiter.limit("60/minute")
def get_live_performance(request: Request, db: Session = Depends(get_db)):
    """Returns dynamic in-play prediction evaluation metrics across 6 match minute intervals."""
    from services.model_evaluation_service import ModelEvaluationService
    return ModelEvaluationService.get_live_minute_performance(db)


@app.get("/api/models/performance")
@limiter.limit("60/minute")
def get_models_performance(request: Request, 
    model_version: Optional[str] = None,
    prediction_type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Returns global probabilistic scoring metrics (Brier, Log Loss, MAE, ECE) for verified predictions."""
    from services.model_evaluation_service import ModelEvaluationService
    return ModelEvaluationService.get_model_performance(db, model_version=model_version, prediction_type=prediction_type)


@app.get("/api/models/leaderboard")
@limiter.limit("60/minute")
def get_models_leaderboard(request: Request, db: Session = Depends(get_db)):
    """Returns ranked prediction markets sorted by composite Brier and calibration performance."""
    from services.model_evaluation_service import ModelEvaluationService
    return {"status": "ok", "leaderboard": ModelEvaluationService.get_market_leaderboard(db)}


@app.get("/api/models/calibration")
@limiter.limit("60/minute")
def get_models_calibration(request: Request, market: Optional[str] = None, db: Session = Depends(get_db)):
    """Returns 10-decile probability reliability diagram buckets, ECE, and MCE."""
    from services.model_evaluation_service import ModelEvaluationService
    return ModelEvaluationService.get_calibration_dashboard(db, market=market)


@app.get("/api/models/drift")
@limiter.limit("60/minute")
def get_models_drift(request: Request, 
    window_size: int = 100, baseline_size: int = 300, db: Session = Depends(get_db)
):
    """Monitors model drift by comparing recent window performance against the historical baseline."""
    from services.model_evaluation_service import ModelEvaluationService
    return ModelEvaluationService.get_model_drift_analysis(db, window_size=window_size, baseline_size=baseline_size)


@app.get("/api/models/league-performance")
@limiter.limit("60/minute")
def get_models_league_performance(request: Request, competition: Optional[str] = None, db: Session = Depends(get_db)):
    """Returns model performance breakdown segmented by league competition."""
    from services.model_evaluation_service import ModelEvaluationService
    return {"status": "ok", "leagues": ModelEvaluationService.get_league_performance(db, competition=competition)}


@app.get("/api/models/status")
@limiter.limit("60/minute")
def get_models_status(request: Request, db: Session = Depends(get_db)):
    """Returns simple production readiness summary across all predictive model families."""
    from services.model_evaluation_service import ModelEvaluationService
    return ModelEvaluationService.get_models_readiness_status(db)


@app.get("/api/data-quality/overview")
@limiter.limit("60/minute")
def get_data_quality_overview(request: Request, db: Session = Depends(get_db)):
    """Returns global data coverage metrics across goals, corners, cards, referees, and live snapshots."""
    from services.data_quality_service import DataQualityService
    return DataQualityService.calculate_global_coverage(db)


@app.get("/api/data-quality/competitions")
@limiter.limit("60/minute")
def get_data_quality_competitions(request: Request, db: Session = Depends(get_db)):
    """Returns data quality coverage segmented by competition."""
    from services.data_quality_service import DataQualityService
    return {"status": "ok", "competitions": DataQualityService.calculate_competition_coverage(db)}


@app.get("/api/data-quality/backfill-status")
@limiter.limit("60/minute")
def get_data_quality_backfill_status(request: Request, db: Session = Depends(get_db)):
    """Returns historical backfill and enrichment progress."""
    from services.historical_data_service import HistoricalDataService
    return HistoricalDataService.get_backfill_status(db)


@app.post("/api/data-quality/backfill-run")
@limiter.limit("10/minute")
def run_data_quality_backfill(request: Request, batch_size: int = 50, db: Session = Depends(get_db)):
    """Triggers a controlled batch historical enrichment pass."""
    from services.historical_data_service import HistoricalDataService
    return HistoricalDataService.discover_and_enrich_batch(db, batch_size=batch_size)


@app.get("/api/models/readiness")
@limiter.limit("60/minute")
def get_models_readiness(request: Request, db: Session = Depends(get_db)):
    """Returns production readiness status and activation gates for all prediction models."""
    from services.production_validation_service import ProductionValidationService
    return ProductionValidationService.get_all_models_readiness_report(db)


@app.get("/api/fixtures/{fixture_id}/feature-coverage")
@limiter.limit("60/minute")
def get_fixture_feature_coverage(request: Request, fixture_id: int, db: Session = Depends(get_db)):
    """Returns feature payload and temporal coverage diagnostics for a specific fixture."""
    from services.feature_store_service import FeatureStoreService
    return FeatureStoreService.build_fixture_features_payload(db, fixture_id)


@app.get("/api/system/providers")
@limiter.limit("60/minute")
def get_system_providers(request: Request, db: Session = Depends(get_db)):
    """Returns external data provider operational health and latency logs."""
    from services.provider_health_service import ProviderHealthService
    return {"status": "ok", "providers": ProviderHealthService.get_providers_status(db)}


@app.get("/api/system/intelligence-status")
@limiter.limit("60/minute")
def get_system_intelligence_status(request: Request, db: Session = Depends(get_db)):
    """Central production intelligence status report summarizing data coverage, model validation, and provider health."""
    from services.data_quality_service import DataQualityService
    from services.production_validation_service import ProductionValidationService
    from services.provider_health_service import ProviderHealthService
    from services.historical_data_service import HistoricalDataService

    cov = DataQualityService.calculate_global_coverage(db)
    models = ProductionValidationService.get_all_models_readiness_report(db)
    prov = ProviderHealthService.get_providers_status(db)
    bf = HistoricalDataService.get_backfill_status(db)

    return {
        "status": "ok",
        "system_status": "OPERATIONAL",
        "coverage": cov,
        "models_readiness": models,
        "providers": prov,
        "backfill": bf,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# =============================================================================
# PHASE 7: PRODUCTION AUTOMATION & OBSERVABILITY ENDPOINTS
# =============================================================================

@app.get("/api/system/health")
@limiter.limit("60/minute")
def get_system_health(request: Request):
    """Liveness probe confirming FastAPI application process is active and responsive."""
    return {"status": "HEALTHY", "service": "Soccer Goal Predictor / Match Intelligence", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/system/readiness")
@limiter.limit("60/minute")
def get_system_readiness(request: Request, db: Session = Depends(get_db)):
    """Readiness probe validating database connectivity, migrations, and core services."""
    try:
        # Test DB query
        db.query(models.Fixture).count()
        db_status = "CONNECTED"
    except Exception as ex:
        db_status = f"FAILED: {ex}"

    is_ready = (db_status == "CONNECTED")
    return {
        "status": "READY" if is_ready else "NOT_READY",
        "database": db_status,
        "environment": os.getenv("ENVIRONMENT", "development"),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/api/system/status")
@limiter.limit("60/minute")
def get_system_operational_status(request: Request, db: Session = Depends(get_db)):
    """Comprehensive production health status summarizing database, providers, jobs, and alerts."""
    from services.provider_health_service import ProviderHealthService
    from services.alert_service import AlertService
    from services.job_orchestrator_service import JobOrchestratorService

    try:
        db.query(models.Fixture).count()
        db_ok = True
    except Exception:
        db_ok = False

    providers = ProviderHealthService.get_providers_status(db)
    active_alerts = AlertService.get_active_alerts(db)
    recent_jobs = JobOrchestratorService.get_job_history(db, limit=5)

    has_critical_alerts = any(a["severity"] == "CRITICAL" for a in active_alerts)
    any_provider_down = any(p["status"] == "UNAVAILABLE" for p in providers)

    if not db_ok:
        overall = "UNAVAILABLE"
    elif has_critical_alerts or any_provider_down:
        overall = "DEGRADED"
    else:
        overall = "HEALTHY"

    return {
        "overall_status": overall,
        "database": "HEALTHY" if db_ok else "UNAVAILABLE",
        "providers": providers,
        "active_alerts_count": len(active_alerts),
        "recent_jobs_count": len(recent_jobs),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/api/system/data-integrity")
@limiter.limit("60/minute")
def get_system_data_integrity(request: Request, db: Session = Depends(get_db)):
    """
    Developer / system observability endpoint for Phase 14 data integrity.
    Provides real-time health across providers, live fixtures, freshness,
    mappings, duplicates, snapshots, and data quality metrics.
    """
    from services.provider_health_service import ProviderHealthService
    from services.circuit_breaker_service import CircuitBreakerService
    from services.fixture_duplicate_detection_service import FixtureDuplicateDetectionService
    from services.data_quality_service import DataQualityService
    import models

    now_utc = datetime.now(timezone.utc)
    now_naive = now_utc.replace(tzinfo=None)

    # 1. Provider health and circuit states
    provider_statuses = ProviderHealthService.get_providers_status(db)
    circuits = CircuitBreakerService.get_all_circuits_status()
    espn_circuit = circuits.get("ESPN", {})

    # 2. Live fixture counts
    live_count = db.query(models.Fixture).filter(models.Fixture.status == "LIVE").count()

    # 3. Stale fixture count (Live fixtures where last_updated > 180s ago)
    cutoff_stale = now_naive - timedelta(seconds=180)
    stale_count = db.query(models.LiveMatchState).join(
        models.Fixture, models.LiveMatchState.fixture_id == models.Fixture.id
    ).filter(
        models.Fixture.status == "LIVE",
        models.LiveMatchState.last_updated < cutoff_stale
    ).count()

    # 4. Identity mismatch count
    identity_mismatch_count = espn_circuit.get("identity_mismatch_count", 0)

    # 5. Provider mapping count
    provider_mapping_count = db.query(models.FixtureProviderMapping).count()

    # 6. Duplicate candidate count
    dup_summary = FixtureDuplicateDetectionService.get_system_duplicate_summary(db)

    # 7. Last successful provider retrieval
    last_retrieval = espn_circuit.get("last_successful_retrieval")

    # 8. Snapshot counts
    snapshot_count = db.query(models.LiveObservedSnapshot).count()

    # 9. API health & global data quality summary
    coverage_summary = DataQualityService.calculate_global_coverage(db)

    return {
        "status": "HEALTHY" if espn_circuit.get("state", "CLOSED") != "OPEN" else "DEGRADED",
        "provider_health": {
            "providers": provider_statuses,
            "circuits": circuits
        },
        "live_fixture_count": live_count,
        "stale_fixture_count": stale_count,
        "identity_mismatch_count": identity_mismatch_count,
        "provider_mapping_count": provider_mapping_count,
        "duplicate_candidate_count": dup_summary.get("duplicate_confirmed_count", 0) + dup_summary.get("duplicate_possible_count", 0),
        "duplicate_summary": dup_summary,
        "last_successful_provider_retrieval": last_retrieval,
        "snapshot_counts": {
            "live_observed_snapshots": snapshot_count,
            "prediction_decision_snapshots": db.query(models.PredictionDecisionSnapshot).count()
        },
        "api_health": "HEALTHY",
        "data_quality_summary": coverage_summary,
        "timestamp": now_utc.isoformat()
    }


@app.get("/api/system/jobs")
@limiter.limit("60/minute")
def get_system_jobs(request: Request, db: Session = Depends(get_db)):
    """Lists available production jobs and recent executions."""
    from services.job_orchestrator_service import JobOrchestratorService
    return {
        "available_jobs": JobOrchestratorService.AVAILABLE_JOBS,
        "recent_executions": JobOrchestratorService.get_job_history(db, limit=10)
    }


@app.get("/api/system/jobs/{job_name}")
@limiter.limit("60/minute")
def get_system_job_status(request: Request, job_name: str, db: Session = Depends(get_db)):
    """Returns latest execution status for a specific job."""
    from services.job_orchestrator_service import JobOrchestratorService
    history = JobOrchestratorService.get_job_history(db, job_name=job_name, limit=1)
    if not history:
        return {"job_name": job_name, "last_execution": None, "status": "IDLE"}
    return {"job_name": job_name, "last_execution": history[0]}


@app.get("/api/system/jobs/{job_name}/history")
@limiter.limit("60/minute")
def get_system_job_history(request: Request, job_name: str, limit: int = 20, db: Session = Depends(get_db)):
    """Returns execution history logs for a specific job."""
    from services.job_orchestrator_service import JobOrchestratorService
    return {"job_name": job_name, "history": JobOrchestratorService.get_job_history(db, job_name=job_name, limit=limit)}


@app.post("/api/system/jobs/{job_name}/run")
@limiter.limit("10/minute")
def run_system_job(request: Request, job_name: str, db: Session = Depends(get_db)):
    """Manually triggers immediate execution of a production job."""
    from services.job_orchestrator_service import JobOrchestratorService
    return JobOrchestratorService.execute_job(db, job_name)


@app.get("/api/system/alerts")
@limiter.limit("60/minute")
def get_system_alerts(request: Request, db: Session = Depends(get_db)):
    """Returns all currently active operational system alerts."""
    from services.alert_service import AlertService
    return {"status": "ok", "alerts": AlertService.get_active_alerts(db)}


@app.post("/api/system/alerts/{alert_id}/resolve")
@limiter.limit("10/minute")
def resolve_system_alert(request: Request, alert_id: str, db: Session = Depends(get_db)):
    """Marks an active system alert as resolved."""
    from services.alert_service import AlertService
    success = AlertService.resolve_alert(db, alert_id)
    return {"status": "ok" if success else "error", "resolved": success}


@app.get("/api/system/backups")
@limiter.limit("60/minute")
def get_system_backups(request: Request):
    """Lists available SQLite backup archives."""
    from services.backup_service import BackupService
    return {"status": "ok", "backups": BackupService.list_backups()}


@app.post("/api/system/backups/run")
@limiter.limit("10/minute")
def run_system_backup(request: Request):
    """Executes an online live SQLite backup and integrity verification."""
    from services.backup_service import BackupService
    return BackupService.create_database_backup()


# =============================================================================
# PHASE 8: PRODUCTION DATA ACQUISITION & REAL-WORLD VALIDATION ENDPOINTS
# =============================================================================

@app.get("/api/data-quality/validation")
@limiter.limit("60/minute")
def get_data_quality_validation(request: Request, db: Session = Depends(get_db)):
    """Returns dataset completeness and validation eligibility."""
    from services.data_quality_service import DataQualityService
    cov = DataQualityService.calculate_global_coverage(db)
    return {"status": "ok", "validation_dataset": cov, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/data-quality/provenance")
@limiter.limit("60/minute")
def get_data_provenance(request: Request, fixture_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Returns field-level data provenance and audit trail."""
    from services.data_reconciliation_service import DataReconciliationService
    if fixture_id:
        prov = DataReconciliationService.get_fixture_provenance(db, fixture_id)
        return {"status": "ok", "fixture_id": fixture_id, "provenance": prov}
    
    recs = db.query(models.DataProvenance).order_by(models.DataProvenance.created_at.desc()).limit(50).all()
    return {
        "status": "ok",
        "provenance": [
            {
                "fixture_id": r.fixture_id,
                "field_name": r.field_name,
                "value": r.value,
                "provider": r.provider,
                "source_type": r.source_type,
                "retrieved_at": r.retrieved_at.isoformat() if r.retrieved_at else None
            }
            for r in recs
        ]
    }


@app.get("/api/data-quality/conflicts")
@limiter.limit("60/minute")
def get_data_conflicts(request: Request, status: Optional[str] = "CONFLICT", db: Session = Depends(get_db)):
    """Returns recorded data conflicts between external providers."""
    from services.data_reconciliation_service import DataReconciliationService
    return {"status": "ok", "conflicts": DataReconciliationService.get_all_conflicts(db, status=status)}


@app.post("/api/data-quality/conflicts/{conflict_id}/resolve")
@limiter.limit("10/minute")
def resolve_data_conflict(request: Request, conflict_id: int, resolved_value: str, notes: Optional[str] = None, db: Session = Depends(get_db)):
    """Resolves an open data conflict with operator notes."""
    from services.data_reconciliation_service import DataReconciliationService
    success = DataReconciliationService.resolve_conflict(db, conflict_id, resolved_value, notes)
    return {"status": "ok" if success else "error", "resolved": success}


@app.get("/api/data-quality/backfill-progress")
@limiter.limit("60/minute")
def get_backfill_progress(request: Request, db: Session = Depends(get_db)):
    """Returns detailed progress for historical data backfill."""
    from services.historical_data_service import HistoricalDataService
    return HistoricalDataService.get_backfill_status(db)


@app.post("/api/data-quality/backfill/start")
@limiter.limit("10/minute")
def start_backfill(request: Request, db: Session = Depends(get_db)):
    """Starts or triggers an automated historical data backfill pass."""
    from services.historical_data_service import HistoricalDataService
    HistoricalDataService.resume_backfill()
    return HistoricalDataService.discover_and_enrich_batch(db, batch_size=30)


@app.post("/api/data-quality/backfill/pause")
@limiter.limit("10/minute")
def pause_backfill(request: Request):
    """Pauses historical data backfill."""
    from services.historical_data_service import HistoricalDataService
    return HistoricalDataService.pause_backfill()


@app.post("/api/data-quality/backfill/resume")
@limiter.limit("10/minute")
def resume_backfill(request: Request):
    """Resumes historical data backfill."""
    from services.historical_data_service import HistoricalDataService
    return HistoricalDataService.resume_backfill()


@app.post("/api/data-quality/backfill/retry")
@limiter.limit("10/minute")
def retry_backfill(request: Request, db: Session = Depends(get_db)):
    """Resets failed backfill items allowing them to be retried."""
    from services.historical_data_service import HistoricalDataService
    return HistoricalDataService.retry_failed_backfills(db)


@app.get("/api/models/real-validation")
@limiter.limit("60/minute")
def get_models_real_validation(request: Request, limit: int = 200, db: Session = Depends(get_db)):
    """Returns chronological walk-forward validation dataset and readiness."""
    from services.validation_dataset_service import ValidationDatasetService
    from services.production_validation_service import ProductionValidationService

    ds = ValidationDatasetService.build_chronological_dataset(db, limit=limit)
    readiness = ProductionValidationService.get_all_models_readiness_report(db)

    return {
        "status": "ok",
        "dataset_size": len(ds),
        "validation_samples": ds[:50], # Sample preview
        "models_readiness": readiness,
        "evaluated_at": datetime.now(timezone.utc).isoformat()
    }


@app.get("/api/models/real-calibration")
@limiter.limit("60/minute")
def get_models_real_calibration(request: Request, prediction_type: str = "goals", db: Session = Depends(get_db)):
    """Returns 10-decile empirical calibration reliability table from verified historical outcomes."""
    from services.model_evaluation_service import ModelEvaluationService
    return ModelEvaluationService.get_calibration_report(db, prediction_type=prediction_type)


@app.get("/api/models/real-leaderboard")
@limiter.limit("60/minute")
def get_models_real_leaderboard(request: Request, db: Session = Depends(get_db)):
    """Returns ranked model comparison leaderboard on real match outcomes."""
    from services.production_validation_service import ProductionValidationService
    return ProductionValidationService.get_real_leaderboard(db)


@app.get("/api/models/market-readiness")
@limiter.limit("60/minute")
def get_models_market_readiness(request: Request, db: Session = Depends(get_db)):
    """Returns granular market-level sample gates across all goals, corners, and cards markets."""
    from services.production_validation_service import ProductionValidationService
    return ProductionValidationService.get_market_level_readiness(db)


@app.get("/api/models/ensemble-status")
@limiter.limit("60/minute")
def get_models_ensemble_status(request: Request, db: Session = Depends(get_db)):
    """Returns adaptive ensemble activation state, gate, and weights."""
    from services.ensemble_service import AdaptiveEnsembleService
    weights = AdaptiveEnsembleService.calculate_dynamic_ensemble_weights(db, "over_1_5_goals")
    return {"status": "ok", "ensemble": weights}


@app.get("/api/models/live-validation")
@limiter.limit("60/minute")
def get_models_live_validation(request: Request, db: Session = Depends(get_db)):
    """Returns live in-play prediction performance across minute buckets and signals."""
    from services.model_evaluation_service import ModelEvaluationService
    return ModelEvaluationService.get_live_performance_report(db)


# =============================================================================
# PHASE 9: UNIFIED MATCH INTELLIGENCE & CROSS-MARKET PREDICTION ENDPOINTS
# =============================================================================

@app.get("/api/fixtures/{fixture_id}/match-intelligence")
@limiter.limit("60/minute")
def get_fixture_match_intelligence(request: Request, fixture_id: int, db: Session = Depends(get_db)):
    """
    Returns complete unified Match Intelligence object combining Goals, Corners, Cards, 
    Referee signals, Live dynamics, Cross-Market consistency diagnostics, and ranked opportunities.
    """
    from services.unified_match_intelligence_service import UnifiedMatchIntelligenceService
    intel = UnifiedMatchIntelligenceService.get_unified_match_intelligence(db, fixture_id)
    if "error" in intel:
        raise HTTPException(status_code=404, detail=intel["error"])
    return intel


@app.get("/api/match-intelligence/fixtures")
@limiter.limit("60/minute")
def get_upcoming_match_intelligence(request: Request, limit: int = 50, db: Session = Depends(get_db)):
    """
    Returns list of upcoming scheduled fixtures with synthesized Match Intelligence summaries.
    """
    from services.unified_match_intelligence_service import UnifiedMatchIntelligenceService

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    fixtures = (
        db.query(models.Fixture)
        .filter(models.Fixture.status.in_(["SCHEDULED", "LIVE"]))
        .order_by(models.Fixture.match_date.asc())
        .limit(limit)
        .all()
    )

    items = []
    for f in fixtures:
        if CanonicalCompetitionService.is_school_or_youth_competition(
            league_name=f.league.name if f.league else "",
            home_team_name=f.home_team.name if f.home_team else "",
            away_team_name=f.away_team.name if f.away_team else ""
        ):
            continue
        try:
            intel = UnifiedMatchIntelligenceService.get_unified_match_intelligence(db, f.id)
            items.append({
                "fixture_id": f.id,
                "home_team": f.home_team.name if f.home_team else "Home",
                "away_team": f.away_team.name if f.away_team else "Away",
                "competition": f.league.name if f.league else "League",
                "kickoff": f.match_date.isoformat() if f.match_date else None,
                "status": f.status,
                "unified_confidence": intel.get("unified_confidence", 0.50),
                "match_state_tags": intel.get("match_state_classification", []),
                "top_signal": intel.get("ranked_signals", [{}])[0] if intel.get("ranked_signals") else None,
                "consistency_score": intel.get("cross_market_consistency", {}).get("consistency_score", 1.0)
            })
        except Exception as ex:
            logger.debug(f"Error extracting intelligence summary for fixture {f.id}: {ex}")

    return {"status": "ok", "fixtures_count": len(items), "fixtures": items}


# =============================================================================
# PHASE 10: SHOTS & SHOTS-ON-TARGET PREDICTION ENDPOINTS
# =============================================================================

@app.get("/api/fixtures/{fixture_id}/shots")
@limiter.limit("60/minute")
def get_fixture_shots_prediction(request: Request, fixture_id: int, db: Session = Depends(get_db)):
    """
    Returns pre-match Total Shots and Shots-on-Target predictions and discrete PMF distributions.
    """
    from services.shots_prediction_service import ShotsPredictionEngine
    res = ShotsPredictionEngine.predict_shots(db, fixture_id)
    if "error" in res:
        raise HTTPException(status_code=404, detail=res["error"])
    return res


@app.get("/api/fixtures/{fixture_id}/shots/live")
@limiter.limit("60/minute")
def get_fixture_live_shots_prediction(request: Request, fixture_id: int, db: Session = Depends(get_db)):
    """
    Returns live in-play dynamic remaining Shots and SoT expectations.
    """
    from services.shots_prediction_service import ShotsPredictionEngine
    res = ShotsPredictionEngine.predict_live_shots(db, fixture_id)
    if "error" in res:
        raise HTTPException(status_code=404, detail=res["error"])
    return res


@app.get("/api/models/shots/readiness")
@limiter.limit("60/minute")
def get_shots_model_readiness(request: Request, db: Session = Depends(get_db)):
    """
    Returns empirical sample readiness gates for Shots and SoT prediction engines.
    """
    from services.shots_prediction_service import ShotsPredictionEngine
    return ShotsPredictionEngine.get_shots_readiness_status(db)


@app.get("/api/models/shots/calibration")
@limiter.limit("60/minute")
def get_shots_model_calibration(request: Request, db: Session = Depends(get_db)):
    """
    Returns empirical calibration and reliability curves for verified shot markets.
    """
    from services.shots_prediction_service import ShotsPredictionEngine, SHOTS_MODEL_VERSION
    evals = db.query(models.ModelEvaluation).filter(models.ModelEvaluation.model_version.contains(SHOTS_MODEL_VERSION)).all()
    pairs = [(e.predicted_probability, e.actual_outcome) for e in evals]
    return {
        "status": "ok",
        "model_version": SHOTS_MODEL_VERSION,
        "sample_size": len(pairs),
        "calibration": CalibrationService.compute_calibration_curve(pairs)
    }


@app.get("/api/models/shots/performance")
@limiter.limit("60/minute")
def get_shots_model_performance(request: Request, db: Session = Depends(get_db)):
    """
    Returns aggregate probabilistic performance metrics for Shots and SoT prediction engines.
    """
    from services.shots_prediction_service import ShotsPredictionEngine, SHOTS_MODEL_VERSION
    evals = db.query(models.ModelEvaluation).filter(models.ModelEvaluation.model_version.contains(SHOTS_MODEL_VERSION)).all()
    pairs = [(e.predicted_probability, e.actual_outcome) for e in evals]
    return {
        "status": "ok",
        "model_version": SHOTS_MODEL_VERSION,
        "metrics": CalibrationService.calculate_aggregate_metrics(pairs)
    }


# =============================================================================
# PHASE 11: MATCH STATISTICS INTELLIGENCE ENDPOINTS
# =============================================================================

@app.get("/api/fixtures/{fixture_id}/match-statistics")
@limiter.limit("60/minute")
def get_fixture_match_statistics_prediction(request: Request, fixture_id: int, db: Session = Depends(get_db)):
    """
    Returns pre-match predictions across all match statistics domains:
    Possession, Fouls, Offsides, Saves, Blocked Shots, Shot Location, and Attacking Pressure.
    """
    from services.match_statistics_prediction_service import MatchStatisticsPredictionEngine
    res = MatchStatisticsPredictionEngine.predict_match_statistics(db, fixture_id)
    if "error" in res:
        raise HTTPException(status_code=404, detail=res["error"])
    return res


@app.get("/api/fixtures/{fixture_id}/match-statistics/live")
@limiter.limit("60/minute")
def get_fixture_live_match_statistics_prediction(request: Request, fixture_id: int, db: Session = Depends(get_db)):
    """
    Returns live in-play dynamic remaining Match Statistics expectations and early-resolved markets.
    """
    from services.match_statistics_prediction_service import MatchStatisticsPredictionEngine
    res = MatchStatisticsPredictionEngine.predict_live_match_statistics(db, fixture_id)
    if "error" in res:
        raise HTTPException(status_code=404, detail=res["error"])
    return res


@app.get("/api/models/match-statistics/readiness")
@limiter.limit("60/minute")
def get_match_statistics_model_readiness(request: Request, db: Session = Depends(get_db)):
    """
    Returns empirical sample readiness gates for Match Statistics prediction engines.
    """
    from services.match_statistics_prediction_service import MatchStatisticsPredictionEngine
    return MatchStatisticsPredictionEngine.get_match_statistics_readiness_status(db)


@app.get("/api/models/match-statistics/calibration")
@limiter.limit("60/minute")
def get_match_statistics_model_calibration(request: Request, db: Session = Depends(get_db)):
    """
    Returns empirical calibration curves for verified match statistics markets.
    """
    from services.match_statistics_prediction_service import MATCH_STATS_MODEL_VERSION
    evals = db.query(models.ModelEvaluation).filter(models.ModelEvaluation.model_version.contains(MATCH_STATS_MODEL_VERSION)).all()
    pairs = [(e.predicted_probability, e.actual_outcome) for e in evals]
    return {
        "status": "ok",
        "model_version": MATCH_STATS_MODEL_VERSION,
        "sample_size": len(pairs),
        "calibration": CalibrationService.compute_calibration_curve(pairs)
    }


@app.get("/api/models/match-statistics/performance")
@limiter.limit("60/minute")
def get_match_statistics_model_performance(request: Request, db: Session = Depends(get_db)):
    """
    Returns aggregate probabilistic performance metrics for Match Statistics prediction engines.
    """
    from services.match_statistics_prediction_service import MATCH_STATS_MODEL_VERSION
    evals = db.query(models.ModelEvaluation).filter(models.ModelEvaluation.model_version.contains(MATCH_STATS_MODEL_VERSION)).all()
    pairs = [(e.predicted_probability, e.actual_outcome) for e in evals]
    return {
        "status": "ok",
        "model_version": MATCH_STATS_MODEL_VERSION,
        "metrics": CalibrationService.calculate_aggregate_metrics(pairs)
    }


# =============================================================================
# PHASE 12: PRODUCTION INTELLIGENCE, EXPLAINABILITY & DECISION ENGINE ENDPOINTS
# =============================================================================

@app.get("/api/fixtures/{fixture_id}/decision")
@limiter.limit("60/minute")
def get_fixture_match_decision(request: Request, fixture_id: int, db: Session = Depends(get_db)):
    """
    Returns authoritative match decision summary consumed by MatchDetailModal and operational UI.
    """
    from services.decision_intelligence_service import DecisionIntelligenceService
    res = DecisionIntelligenceService.get_match_decision_summary(db, fixture_id)
    if "error" in res:
        raise HTTPException(status_code=404, detail=res["error"])
    return res


@app.get("/api/fixtures/{fixture_id}/signals")
@limiter.limit("60/minute")
def get_fixture_top_signals(request: Request, fixture_id: int, db: Session = Depends(get_db)):
    """
    Returns top qualifying production/shadow signals for a fixture with reason codes and risk tiers.
    """
    from services.decision_intelligence_service import DecisionIntelligenceService
    return DecisionIntelligenceService.get_top_signals(db, fixture_id)


@app.get("/api/fixtures/{fixture_id}/decision/explanation")
@limiter.limit("60/minute")
def get_fixture_decision_explanations(request: Request, fixture_id: int, db: Session = Depends(get_db)):
    """
    Returns detailed machine-readable explainability reason codes and factors across all fixture markets.
    """
    from services.decision_intelligence_service import DecisionIntelligenceService
    decisions = DecisionIntelligenceService.get_fixture_decisions(db, fixture_id)
    return {
        "fixture_id": fixture_id,
        "total_markets_explained": len(decisions),
        "decisions": decisions
    }


@app.get("/api/fixtures/{fixture_id}/decision/history")
@limiter.limit("60/minute")
def get_fixture_decision_history(request: Request, fixture_id: int, db: Session = Depends(get_db)):
    """
    Returns historical immutable decision snapshot audit trail for a fixture.
    """
    snaps = (
        db.query(models.PredictionDecisionSnapshot)
        .filter(models.PredictionDecisionSnapshot.fixture_id == fixture_id)
        .order_by(models.PredictionDecisionSnapshot.created_at.desc())
        .all()
    )
    return [
        {
            "id": s.id,
            "fixture_id": s.fixture_id,
            "market": s.market,
            "selection": s.selection,
            "model_version": s.model_version,
            "probability": s.probability,
            "confidence": s.confidence,
            "decision_score": s.decision_score,
            "risk_tier": s.risk_tier,
            "signal_status": s.signal_status,
            "sample_size": s.sample_size,
            "brier_score": s.brier_score,
            "ece": s.ece,
            "data_quality": s.data_quality,
            "drift_status": s.drift_status,
            "readiness_status": s.readiness_status,
            "is_live": s.is_live,
            "match_minute": s.match_minute,
            "created_at": s.created_at.isoformat() if s.created_at else None
        }
        for s in snaps
    ]


@app.get("/api/models/decision-readiness")
@limiter.limit("60/minute")
def get_decision_engine_readiness(request: Request, db: Session = Depends(get_db)):
    """
    Returns empirical sample readiness gates across all decision engine markets.
    """
    from services.decision_intelligence_service import DecisionIntelligenceService
    return DecisionIntelligenceService.get_decision_readiness(db)


@app.get("/api/models/decision-performance")
@limiter.limit("60/minute")
def get_decision_engine_performance(request: Request, db: Session = Depends(get_db)):
    """
    Returns aggregate probabilistic performance metrics for the unified decision engine.
    """
    from services.decision_intelligence_service import DECISION_ENGINE_VERSION
    evals = db.query(models.ModelEvaluation).all()
    pairs = [(e.predicted_probability, e.actual_outcome) for e in evals]
    return {
        "status": "ok",
        "model_version": DECISION_ENGINE_VERSION,
        "sample_size": len(pairs),
        "metrics": CalibrationService.calculate_aggregate_metrics(pairs)
    }


@app.get("/api/decision/signals")
@limiter.limit("60/minute")
def get_all_active_decision_signals(request: Request, 
    status: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """
    Returns recent decision snapshots with optional status filter (e.g. PRODUCTION_SIGNAL, SHADOW_SIGNAL).
    """
    q = db.query(models.PredictionDecisionSnapshot)
    if status:
        q = q.filter(models.PredictionDecisionSnapshot.signal_status == status)
    snaps = q.order_by(models.PredictionDecisionSnapshot.created_at.desc()).limit(limit).all()
    return [
        {
            "id": s.id,
            "fixture_id": s.fixture_id,
            "market": s.market,
            "selection": s.selection,
            "probability": s.probability,
            "confidence": s.confidence,
            "decision_score": s.decision_score,
            "risk_tier": s.risk_tier,
            "signal_status": s.signal_status,
            "sample_size": s.sample_size,
            "is_live": s.is_live,
            "created_at": s.created_at.isoformat() if s.created_at else None
        }
        for s in snaps
    ]


@app.get("/api/decision/status")
@limiter.limit("60/minute")
def get_decision_engine_status(request: Request, db: Session = Depends(get_db)):
    """
    Returns real-time operating metrics and snapshot counts for the decision intelligence service.
    """
    from services.decision_intelligence_service import DecisionIntelligenceService
    return DecisionIntelligenceService.get_decision_status(db)







@app.post("/api/notifications/telegram/test")
@limiter.limit("10/minute")
async def send_telegram_test_notification(request: Request, bot_token: Optional[str] = None, chat_id: Optional[str] = None, window: Optional[str] = "auto"):
    """Sends a live 3-Odds Accumulator Ticket test notification directly to Telegram."""
    try:
        await scheduled_telegram_daily_digest(bot_token=bot_token, chat_id=chat_id, window_key=window)
        return {
            "status": "ok",
            "message": f"Live 3-Odds Accumulator ticket dispatched to Telegram successfully! (Window: {window})"
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to dispatch Telegram message: {str(e)}"}


@app.post("/api/notifications/whatsapp/test")
@limiter.limit("10/minute")
async def send_whatsapp_test_notification(request: Request, phone: Optional[str] = None, api_key: Optional[str] = None):
    """Sends a test WhatsApp notification message via CallMeBot API."""
    test_msg = (
        "⚽ *SOCCER GOAL PREDICTOR TEST NOTIFICATION*\n\n"
        "Your WhatsApp Bot connection is successfully configured!\n"
        "You will receive daily top prediction broadcasts on WhatsApp."
    )
    success = await WhatsAppNotificationService.send_message(test_msg, phone=phone, api_key=api_key)
    if success:
        return {"status": "ok", "message": "Test WhatsApp message sent successfully!"}
    return {"status": "error", "message": "Failed to send WhatsApp message. Please verify WHATSAPP_PHONE_NUMBER and WHATSAPP_API_KEY."}


@app.post("/api/notifications/broadcast")
@limiter.limit("10/minute")
async def trigger_manual_broadcast(request: Request, bot_token: Optional[str] = None, chat_id: Optional[str] = None, window: Optional[str] = "auto"):
    """Triggers immediate 3-Odds Accumulator broadcast to Telegram across Morning, Afternoon, or Evening window."""
    await scheduled_telegram_daily_digest(bot_token=bot_token, chat_id=chat_id, window_key=window)
    return {"status": "ok", "message": f"3-Odds Accumulator broadcast triggered successfully to Telegram (Window: {window})!"}


@app.get("/api/fixtures/upcoming")
@limiter.limit("60/minute")
async def get_upcoming_fixtures(request: Request, params: FixtureQueryParams = Depends(validate_fixture_query), db: Session = Depends(get_db)):
    """
    Retrieve all upcoming/scheduled global fixtures starting from present date
    with full team, league, and Poisson goal prediction details.
    """
    global LAST_SYNC_TIME
    now = datetime.now(timezone.utc)
    now_cutoff = (now - timedelta(hours=2)).replace(tzinfo=None)

    try:
        fixtures = db.query(models.Fixture).join(models.League).options(
            joinedload(models.Fixture.league),
            joinedload(models.Fixture.home_team),
            joinedload(models.Fixture.away_team)
        ).filter(
            models.Fixture.status.notin_(["FINISHED", "FT", "AET", "PEN"]),
            models.Fixture.match_date >= now_cutoff,
            ~func.lower(models.League.name).like("%ncaa%"),
            ~func.lower(models.League.name).like("%college%"),
            ~func.lower(models.League.name).like("%high school%"),
            ~func.lower(models.League.name).like("%varsity%"),
            ~func.lower(models.League.name).like("%university%"),
        ).order_by(models.Fixture.match_date.asc()).all()

        if not fixtures:
            # Fallback query: fetch all non-finished fixtures regardless of match_date cutoff
            fixtures = db.query(models.Fixture).join(models.League).options(
                joinedload(models.Fixture.league),
                joinedload(models.Fixture.home_team),
                joinedload(models.Fixture.away_team)
            ).filter(
                models.Fixture.status.notin_(["FINISHED", "FT", "AET", "PEN"]),
                ~func.lower(models.League.name).like("%ncaa%"),
                ~func.lower(models.League.name).like("%college%"),
                ~func.lower(models.League.name).like("%high school%"),
                ~func.lower(models.League.name).like("%varsity%"),
                ~func.lower(models.League.name).like("%university%"),
            ).order_by(models.Fixture.match_date.asc()).all()
    except Exception as query_err:
        logger.error(f"Error querying upcoming fixtures: {query_err}")
        db.rollback()
        fixtures = []

    if not fixtures:
        logger.info("No upcoming fixtures found in DB. Triggering non-blocking background ingestion fallback...")
        def run_bg_sync():
            bg_db = SessionLocal()
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(DataIngestionService.fetch_and_ingest_from_api(bg_db, api_key=FOOTBALL_API_KEY))
                    PoissonPredictionEngine.predict_all_upcoming_fixtures(bg_db)
                finally:
                    loop.close()
            except Exception as ing_err:
                logger.error(f"Error during background ingestion fallback: {ing_err}")
            finally:
                bg_db.close()
        asyncio.create_task(asyncio.to_thread(run_bg_sync))

    try:
        all_preds = {p.fixture_id: p for p in db.query(models.Prediction).all()}
    except Exception as pred_err:
        logger.error(f"Error loading predictions: {pred_err}")
        db.rollback()
        all_preds = {}

    result_data = []
    for fix in fixtures:
        # Comprehensive Python-level school/youth filter
        l_name_lower = (fix.league.name if fix.league else "").lower()
        h_name_lower = (fix.home_team.name if fix.home_team else "").lower()
        a_name_lower = (fix.away_team.name if fix.away_team else "").lower()

        # Check league name
        if any(kw in l_name_lower for kw in ['ncaa', 'ncaam', 'ncaaw', 'college', 'high school', 'varsity', 'university soccer']):
            continue

        # Check team logo URLs for NCAA pattern (if available)
        h_logo = (fix.home_team.logo_url if fix.home_team else "") or ""
        a_logo = (fix.away_team.logo_url if fix.away_team else "") or ""
        if "teamlogos/ncaa/" in h_logo or "teamlogos/ncaa/" in a_logo:
            continue

        # Existing is_school_or_youth_competition check (keep it)
        if CanonicalCompetitionService.is_school_or_youth_competition(
            league_name=fix.league.name if fix.league else "",
            home_team_name=fix.home_team.name if fix.home_team else "",
            away_team_name=fix.away_team.name if fix.away_team else ""
        ):
            continue

        pred = all_preds.get(fix.id)
        top_scorelines = []
        if pred and pred.top_scorelines_json:
            try:
                top_scorelines = json.loads(pred.top_scorelines_json)
            except Exception:
                top_scorelines = []

        if pred:
            h_xg = round(float(pred.predicted_home_score), 2) if pred.predicted_home_score is not None else None
            a_xg = round(float(pred.predicted_away_score), 2) if pred.predicted_away_score is not None else None
            home_win = float(pred.home_win_probability) if pred.home_win_probability is not None else None
            draw_prob = float(pred.draw_probability) if pred.draw_probability is not None else None
            away_win = float(pred.away_win_probability) if pred.away_win_probability is not None else None
            o05 = float(pred.over_0_5_probability) if pred.over_0_5_probability is not None else None
            o15 = float(pred.over_1_5_probability) if pred.over_1_5_probability is not None else None
            o25 = float(pred.over_2_5_probability) if pred.over_2_5_probability is not None else None
            o35 = float(pred.over_3_5_probability) if pred.over_3_5_probability is not None else None
            u25 = float(pred.under_2_5_probability) if pred.under_2_5_probability is not None else None
            btts_prob = float(pred.btts_probability) if (getattr(pred, 'btts_probability', None) is not None) else (round((1.0 - (2.718281828459045 ** -h_xg)) * (1.0 - (2.718281828459045 ** -a_xg)), 4) if (h_xg is not None and a_xg is not None) else None)
            confidence_score = float(pred.confidence_score) if (getattr(pred, 'confidence_score', None) is not None) else None
            most_likely = pred.most_likely_score or None
        else:
            h_xg, a_xg = None, None
            home_win, draw_prob, away_win = None, None, None
            o05, o15, o25, o35, u25 = None, None, None, None, None
            btts_prob = None
            confidence_score = None
            most_likely = None

        match_date_str = None
        if fix.match_date:
            if isinstance(fix.match_date, datetime):
                dt_obj = fix.match_date if fix.match_date.tzinfo else fix.match_date.replace(tzinfo=timezone.utc)
                match_date_str = dt_obj.isoformat()
            else:
                s = str(fix.match_date).replace(" ", "T")
                match_date_str = s if (s.endswith("Z") or "+" in s[10:] or "-" in s[10:]) else s + "Z"

        # Weather Context (Upgrade 5)
        weather_data = WeatherService.get_weather_for_venue(fix.venue)

        # Value Bet Finder (Upgrade 9)
        if o15 is not None and o15 > 0:
            model_odds = round(1.0 / max(0.01, o15), 2)
            implied_market_odds = round(model_odds * 1.08, 2)
            implied_market_prob = round(1.0 / max(1.01, implied_market_odds), 4)
            value_edge_pct = round((o15 - implied_market_prob) * 100, 1)
            is_value_bet = (o15 >= 0.78) and (value_edge_pct >= 4.0)
        else:
            model_odds = None
            implied_market_odds = None
            implied_market_prob = None
            value_edge_pct = None
            is_value_bet = False

        # Dynamic Status & Live Clock Evaluation:
        # Guarantee matches that have kicked off never stay stuck on SCHEDULED
        eff_status = str(fix.status or "SCHEDULED").upper()
        eff_home_score = getattr(fix, "home_score", None)
        eff_away_score = getattr(fix, "away_score", None)
        eff_live_clock = getattr(fix, "live_clock", None)

        if fix.match_date:
            naive_m_date = fix.match_date.replace(tzinfo=None) if fix.match_date.tzinfo else fix.match_date
            now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
            if eff_status in ["SCHEDULED", "PRE_EVENT", "PRE"] and naive_m_date <= (now_utc_naive - timedelta(minutes=1)):
                if naive_m_date >= (now_utc_naive - timedelta(hours=3)):
                    eff_status = "LIVE"
                    mins_elapsed = max(1, int((now_utc_naive - naive_m_date).total_seconds() / 60))
                    if not eff_live_clock or eff_live_clock == "0'":
                        if mins_elapsed <= 45:
                            eff_live_clock = f"{mins_elapsed}'"
                        elif mins_elapsed <= 60:
                            eff_live_clock = "HT"
                        elif mins_elapsed <= 105:
                            eff_live_clock = f"{mins_elapsed - 15}'"
                        else:
                            eff_live_clock = "90+'"
                elif naive_m_date < (now_utc_naive - timedelta(hours=3)):
                    eff_status = "FINISHED"
                    if not eff_live_clock:
                        eff_live_clock = "FT"

        # Dynamic Canonical Competition & Country Resolution (Phase 13: provider-verified, zero team-name guessing)
        canon_ident = CanonicalCompetitionService.resolve_competition(
            league_name=fix.league.name if fix.league else None,
            season_slug=getattr(fix.league, "season", None) if fix.league else None,
            provided_country=fix.league.country if fix.league else None,
            provided_season=getattr(fix.league, "season", None) if fix.league else None,
            provided_round=getattr(fix, "round", None)
        )
        resolved_league_name = canon_ident.competition_name
        resolved_display_name = canon_ident.competition_display_name
        resolved_country = canon_ident.country
        resolved_country_code = canon_ident.country_code
        resolved_round = canon_ident.round_stage or getattr(fix, "round", None)
        resolved_season = canon_ident.season or (fix.league.season if fix.league else "")

        pred_dict = None
        if h_xg is not None and a_xg is not None:
            tot_xg = round(h_xg + a_xg, 2)
            pred_dict = {
                "predicted_home_score": h_xg,
                "predicted_away_score": a_xg,
                "expected_goals_xg": tot_xg,
                "home_win_probability": home_win,
                "draw_probability": draw_prob,
                "away_win_probability": away_win,
                "over_0_5_probability": o05,
                "over_1_5_probability": o15,
                "over_2_5_probability": o25,
                "over_3_5_probability": o35,
                "under_2_5_probability": u25,
                "btts_probability": btts_prob,
                "confidence_score": confidence_score,
                
                # Home Team Specific Goal Thresholds
                "home_over_0_5_probability": round(1.0 - (2.718281828459045 ** -h_xg), 4),
                "home_over_1_5_probability": round(1.0 - (2.718281828459045 ** -h_xg) * (1.0 + h_xg), 4),
                "home_over_2_5_probability": round(1.0 - (2.718281828459045 ** -h_xg) * (1.0 + h_xg + (h_xg ** 2) / 2.0), 4),

                # Away Team Specific Goal Thresholds
                "away_over_0_5_probability": round(1.0 - (2.718281828459045 ** -a_xg), 4),
                "away_over_1_5_probability": round(1.0 - (2.718281828459045 ** -a_xg) * (1.0 + a_xg), 4),
                "away_over_2_5_probability": round(1.0 - (2.718281828459045 ** -a_xg) * (1.0 + a_xg + (a_xg ** 2) / 2.0), 4),

                # Half Breakdown
                "first_half_xg": round(tot_xg * 0.45, 2),
                "first_half_over_0_5_probability": round(1.0 - (2.718281828459045 ** -(tot_xg * 0.45)), 4),
                "first_half_over_1_5_probability": round(1.0 - (2.718281828459045 ** -(tot_xg * 0.45)) * (1.0 + tot_xg * 0.45), 4),

                "second_half_xg": round(tot_xg * 0.55, 2),
                "second_half_over_0_5_probability": round(1.0 - (2.718281828459045 ** -(tot_xg * 0.55)), 4),
                "second_half_over_1_5_probability": round(1.0 - (2.718281828459045 ** -(tot_xg * 0.55)) * (1.0 + tot_xg * 0.55), 4),

                "most_likely_score": most_likely,
                "top_scorelines": top_scorelines or ([{"scoreline": most_likely, "probability": home_win}] if most_likely else [])
            }

        result_data.append({
            "id": fix.id,
            "fixture_id": fix.id,
            "external_id": fix.external_id,
            "match_date": match_date_str,
            "kickoff_utc": match_date_str,
            "data_status": "LIVE" if eff_status == "LIVE" else "AVAILABLE",
            "freshness": "FRESH",
            "freshness_status": "FRESH",
            "identity_status": "IDENTITY_VALID",
            "status": eff_status,
            "venue": fix.venue,
            "weather": weather_data,
            "home_score": eff_home_score,
            "away_score": eff_away_score,
            "live_clock": eff_live_clock,
            "country": resolved_country,
            "country_code": resolved_country_code,
            "competition_name": resolved_league_name,
            "competition_display_name": resolved_display_name,
            "round": resolved_round,
            "season": resolved_season,
            "value_bet": {
                "is_value_bet": is_value_bet,
                "model_odds": model_odds,
                "market_odds": implied_market_odds,
                "value_edge_pct": value_edge_pct
            },
            "competition": {
                "id": fix.league.id if fix.league else None,
                "name": resolved_league_name,
                "display_name": resolved_display_name,
                "competition_display_name": resolved_display_name,
                "country": resolved_country,
                "country_code": resolved_country_code,
                "season": resolved_season,
                "round": resolved_round,
                "provider_competition_id": canon_ident.competition_code
            },
            "league": {
                "id": fix.league.id if fix.league else None,
                "name": resolved_display_name,
                "country": resolved_country,
                "country_code": resolved_country_code,
                "season": resolved_season
            },
            "home_team": {
                "id": fix.home_team.id if fix.home_team else None,
                "name": fix.home_team.name if fix.home_team else None,
                "short_code": fix.home_team.short_code if fix.home_team else None,
                "logo_url": fix.home_team.logo_url if fix.home_team else None
            },
            "away_team": {
                "id": fix.away_team.id if fix.away_team else None,
                "name": fix.away_team.name if fix.away_team else None,
                "short_code": fix.away_team.short_code if fix.away_team else None,
                "logo_url": fix.away_team.logo_url if fix.away_team else None
            },
            "prediction": pred_dict
        })

    return {"status": "ok", "count": len(result_data), "data": result_data}


@app.get("/api/fixtures/finished")
@limiter.limit("60/minute")
def get_finished_fixtures(request: Request, params: FixtureQueryParams = Depends(validate_fixture_query), db: Session = Depends(get_db)):
    """
    Retrieve completed match results with final scores and Over 1.5 goal prediction outcomes.
    Supports optional `date` filter (YYYY-MM-DD format).
    """
    date = params.date
    try:
        query = db.query(models.Fixture).join(models.League).options(
            joinedload(models.Fixture.league),
            joinedload(models.Fixture.home_team),
            joinedload(models.Fixture.away_team)
        ).filter(
            models.Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
            ~func.lower(models.League.name).like("%ncaa%"),
            ~func.lower(models.League.name).like("%college%"),
            ~func.lower(models.League.name).like("%high school%"),
            ~func.lower(models.League.name).like("%varsity%"),
            ~func.lower(models.League.name).like("%university%"),
        )

        if date:
            try:
                parsed_dt = datetime.fromisoformat(date)
                s_dt = datetime(parsed_dt.year, parsed_dt.month, parsed_dt.day, 0, 0, 0)
                e_dt = datetime(parsed_dt.year, parsed_dt.month, parsed_dt.day, 23, 59, 59)
                query = query.filter(models.Fixture.match_date >= s_dt, models.Fixture.match_date <= e_dt)
            except Exception as e:
                logger.warning(f"Invalid date filter parameter '{date}': {e}")

        fixtures = query.order_by(models.Fixture.match_date.desc()).all()

        try:
            all_preds = {p.fixture_id: p for p in db.query(models.Prediction).all()}
        except Exception:
            all_preds = {}

        result_data = []

        for fix in fixtures:
            # Comprehensive Python-level school/youth filter
            l_name_lower = (fix.league.name if fix.league else "").lower()
            h_name_lower = (fix.home_team.name if fix.home_team else "").lower()
            a_name_lower = (fix.away_team.name if fix.away_team else "").lower()

            # Check league name
            if any(kw in l_name_lower for kw in ['ncaa', 'ncaam', 'ncaaw', 'college', 'high school', 'varsity', 'university soccer']):
                continue

            # Check team logo URLs for NCAA pattern (if available)
            h_logo = (fix.home_team.logo_url if fix.home_team else "") or ""
            a_logo = (fix.away_team.logo_url if fix.away_team else "") or ""
            if "teamlogos/ncaa/" in h_logo or "teamlogos/ncaa/" in a_logo:
                continue

            if CanonicalCompetitionService.is_school_or_youth_competition(
                league_name=fix.league.name if fix.league else "",
                home_team_name=fix.home_team.name if fix.home_team else "",
                away_team_name=fix.away_team.name if fix.away_team else ""
            ):
                continue

            pred = all_preds.get(fix.id)

            top_scorelines = []
            if pred and pred.top_scorelines_json:
                try:
                    top_scorelines = json.loads(pred.top_scorelines_json)
                except Exception:
                    top_scorelines = []

            if pred and pred.predicted_home_score is not None and pred.predicted_away_score is not None:
                h_xg = round(float(pred.predicted_home_score), 2)
                a_xg = round(float(pred.predicted_away_score), 2)
                home_win = float(pred.home_win_probability) if pred.home_win_probability is not None else None
                draw_prob = float(pred.draw_probability) if pred.draw_probability is not None else None
                away_win = float(pred.away_win_probability) if pred.away_win_probability is not None else None
                o05 = float(pred.over_0_5_probability) if pred.over_0_5_probability is not None else None
                o15 = float(pred.over_1_5_probability) if pred.over_1_5_probability is not None else None
                o25 = float(pred.over_2_5_probability) if pred.over_2_5_probability is not None else None
                o35 = float(pred.over_3_5_probability) if pred.over_3_5_probability is not None else None
                u25 = float(pred.under_2_5_probability) if pred.under_2_5_probability is not None else None
                btts_prob = float(pred.btts_probability) if getattr(pred, 'btts_probability', None) is not None else round((1.0 - (2.718281828459045 ** -h_xg)) * (1.0 - (2.718281828459045 ** -a_xg)), 4)
                confidence_score = float(pred.confidence_score) if getattr(pred, 'confidence_score', None) is not None else None
                most_likely = pred.most_likely_score
            else:
                h_xg, a_xg = None, None
                home_win, draw_prob, away_win = None, None, None
                o05, o15, o25, o35, u25 = None, None, None, None, None
                btts_prob = None
                confidence_score = None
                most_likely = None

            h_score = fix.home_score
            a_score = fix.away_score
            has_scores = h_score is not None and a_score is not None
            total_actual_goals = (h_score + a_score) if has_scores else None

            match_date_str = None
            if fix.match_date:
                if isinstance(fix.match_date, datetime):
                    dt_obj = fix.match_date if fix.match_date.tzinfo else fix.match_date.replace(tzinfo=timezone.utc)
                    match_date_str = dt_obj.isoformat()
                else:
                    s = str(fix.match_date).replace(" ", "T")
                    match_date_str = s if (s.endswith("Z") or "+" in s[10:] or "-" in s[10:]) else s + "Z"

            # Dynamic Canonical Competition & Country Resolution (Phase 13: provider-verified, zero team-name guessing)
            canon_ident = CanonicalCompetitionService.resolve_competition(
                league_name=fix.league.name if fix.league else None,
                season_slug=getattr(fix.league, "season", None) if fix.league else None,
                provided_country=fix.league.country if fix.league else None,
                provided_season=getattr(fix.league, "season", None) if fix.league else None,
                provided_round=getattr(fix, "round", None)
            )
            resolved_league_name = canon_ident.competition_name
            resolved_display_name = canon_ident.competition_display_name
            resolved_country = canon_ident.country
            resolved_country_code = canon_ident.country_code
            resolved_round = canon_ident.round_stage or getattr(fix, "round", None)
            resolved_season = canon_ident.season or (fix.league.season if fix.league else "")

            pred_dict = None
            if h_xg is not None and a_xg is not None:
                tot_xg = round(h_xg + a_xg, 2)
                pred_dict = {
                    "predicted_home_score": h_xg,
                    "predicted_away_score": a_xg,
                    "expected_goals_xg": tot_xg,
                    "home_win_probability": home_win,
                    "draw_probability": draw_prob,
                    "away_win_probability": away_win,
                    "over_0_5_probability": o05,
                    "over_1_5_probability": o15,
                    "over_2_5_probability": o25,
                    "over_3_5_probability": o35,
                    "under_2_5_probability": u25,
                    "btts_probability": btts_prob,
                    "confidence_score": confidence_score,
                    "home_over_0_5_probability": round(1.0 - (2.718281828459045 ** -h_xg), 4),
                    "home_over_1_5_probability": round(1.0 - (2.718281828459045 ** -h_xg) * (1.0 + h_xg), 4),
                    "home_over_2_5_probability": round(1.0 - (2.718281828459045 ** -h_xg) * (1.0 + h_xg + (h_xg ** 2) / 2.0), 4),
                    "away_over_0_5_probability": round(1.0 - (2.718281828459045 ** -a_xg), 4),
                    "away_over_1_5_probability": round(1.0 - (2.718281828459045 ** -a_xg) * (1.0 + a_xg), 4),
                    "away_over_2_5_probability": round(1.0 - (2.718281828459045 ** -a_xg) * (1.0 + a_xg + (a_xg ** 2) / 2.0), 4),
                    "first_half_xg": round(tot_xg * 0.45, 2),
                    "first_half_over_0_5_probability": round(1.0 - (2.718281828459045 ** -(tot_xg * 0.45)), 4),
                    "first_half_over_1_5_probability": round(1.0 - (2.718281828459045 ** -(tot_xg * 0.45)) * (1.0 + tot_xg * 0.45), 4),
                    "second_half_xg": round(tot_xg * 0.55, 2),
                    "second_half_over_0_5_probability": round(1.0 - (2.718281828459045 ** -(tot_xg * 0.55)), 4),
                    "second_half_over_1_5_probability": round(1.0 - (2.718281828459045 ** -(tot_xg * 0.55)) * (1.0 + tot_xg * 0.55), 4),
                    "most_likely_score": most_likely,
                    "top_scorelines": top_scorelines or ([{"scoreline": most_likely, "probability": home_win}] if most_likely else [])
                }

            result_data.append({
                "id": fix.id,
                "fixture_id": fix.id,
                "external_id": fix.external_id,
                "match_date": match_date_str,
                "kickoff_utc": match_date_str,
                "data_status": "VERIFIED",
                "freshness": "FRESH",
                "freshness_status": "FRESH",
                "identity_status": "IDENTITY_VALID",
                "status": "FINISHED",
                "venue": fix.venue,
                "home_score": h_score,
                "away_score": a_score,
                "total_goals": total_actual_goals,
                "over_1_5_hit": (total_actual_goals >= 2) if total_actual_goals is not None else None,
                "over_2_5_hit": (total_actual_goals >= 3) if total_actual_goals is not None else None,
                "live_clock": "FT",
                "country": resolved_country,
                "country_code": resolved_country_code,
                "competition_name": resolved_league_name,
                "competition_display_name": resolved_display_name,
                "round": resolved_round,
                "season": resolved_season,
                "competition": {
                    "id": fix.league.id if fix.league else None,
                    "name": resolved_league_name,
                    "display_name": resolved_display_name,
                    "competition_display_name": resolved_display_name,
                    "country": resolved_country,
                    "country_code": resolved_country_code,
                    "season": resolved_season,
                    "round": resolved_round,
                    "provider_competition_id": canon_ident.competition_code
                },
                "league": {
                    "id": fix.league.id if fix.league else None,
                    "name": resolved_display_name,
                    "country": resolved_country,
                    "country_code": resolved_country_code,
                    "season": resolved_season
                },
                "home_team": {
                    "id": fix.home_team.id if fix.home_team else None,
                    "name": fix.home_team.name if fix.home_team else None,
                    "short_code": fix.home_team.short_code if fix.home_team else None,
                    "logo_url": fix.home_team.logo_url if fix.home_team else None
                },
                "away_team": {
                    "id": fix.away_team.id if fix.away_team else None,
                    "name": fix.away_team.name if fix.away_team else None,
                    "short_code": fix.away_team.short_code if fix.away_team else None,
                    "logo_url": fix.away_team.logo_url if fix.away_team else None
                },
                "prediction": pred_dict
            })

        return {"status": "ok", "count": len(result_data), "data": result_data}
    except Exception as exc:
        logger.error(f"Error in get_finished_fixtures: {exc}", exc_info=True)
        return {"status": "error", "message": str(exc), "count": 0, "data": []}







