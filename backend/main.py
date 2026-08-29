import os
import sys
import json
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import inspect
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

logger = logging.getLogger(__name__)

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
            DataIngestionService.auto_resolve_expired_live_fixtures(calc_db)
        except Exception as e:
            logger.error(f"Error in scheduled live score refresh: {e}")
        finally:
            calc_db.close()

    await asyncio.to_thread(run_live_refresh)


async def scheduled_telegram_daily_digest(bot_token: Optional[str] = None, chat_id: Optional[str] = None, is_night_digest: bool = False):
    """
    Automated background worker job executing twice daily:
    - Night Digest (10:00 PM GMT / 22:00 UTC): Gathers picks for early morning fixtures (1:00 AM – 6:50 AM GMT) for next day.
    - Morning Digest (07:00 AM GMT / 07:00 UTC): Gathers picks for the rest of today's fixtures (7:00 AM – 11:59 PM GMT).
    """
    logger.info("Executing scheduled Telegram picks broadcast...")
    calc_db = SessionLocal()
    try:
        utc_now = datetime.now(timezone.utc)
        current_hour = utc_now.hour
        is_night = is_night_digest or (current_hour >= 20 or current_hour < 3)

        if is_night:
            title_cat = "EARLY MORNING PICKS"
            time_win_str = "1:00 AM – 6:50 AM GMT"
            target_date = (utc_now + timedelta(days=1)).date() if current_hour >= 20 else utc_now.date()
            start_dt_utc = datetime(target_date.year, target_date.month, target_date.day, 0, 50, 0)
            end_dt_utc = datetime(target_date.year, target_date.month, target_date.day, 6, 50, 0)
        else:
            title_cat = "DAILY TOP PICKS"
            target_date = utc_now.date()
            # If broadcast runs late (e.g. 10:20 AM / 11:00 AM), dynamically filter from current broadcast time onwards (e.g. 11:00 AM - 11:59 PM)
            start_hour_gmt = max(7, (utc_now + timedelta(hours=1)).hour)
            time_win_str = f"{start_hour_gmt}:00 AM – 11:59 PM GMT" if start_hour_gmt < 12 else f"{start_hour_gmt - 12 if start_hour_gmt > 12 else 12}:00 PM – 11:59 PM GMT"
            naive_now_cutoff = (utc_now - timedelta(minutes=15)).replace(tzinfo=None)
            start_dt_utc = max(datetime(target_date.year, target_date.month, target_date.day, 6, 50, 0), naive_now_cutoff)
            end_dt_utc = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59)

        fixtures = (
            calc_db.query(models.Fixture)
            .options(
                joinedload(models.Fixture.league),
                joinedload(models.Fixture.home_team),
                joinedload(models.Fixture.away_team)
            )
            .filter(
                models.Fixture.status.notin_(["FINISHED", "FT", "AET", "PEN"]),
                models.Fixture.match_date >= start_dt_utc,
                models.Fixture.match_date <= end_dt_utc
            )
            .order_by(models.Fixture.match_date.asc())
            .all()
        )

        if not fixtures:
            logger.info("No fixtures found for target window. Triggering automatic API ingestion fallback...")
            try:
                await DataIngestionService.fetch_and_ingest_from_api(calc_db, api_key=FOOTBALL_API_KEY)
                PoissonPredictionEngine.predict_all_upcoming_fixtures(calc_db)
                fixtures = (
                    calc_db.query(models.Fixture)
                    .options(
                        joinedload(models.Fixture.league),
                        joinedload(models.Fixture.home_team),
                        joinedload(models.Fixture.away_team)
                    )
                    .filter(
                        models.Fixture.status.notin_(["FINISHED", "FT", "AET", "PEN"]),
                        models.Fixture.match_date >= start_dt_utc,
                        models.Fixture.match_date <= end_dt_utc
                    )
                    .order_by(models.Fixture.match_date.asc())
                    .all()
                )
            except Exception as ing_err:
                logger.error(f"Error during scheduled digest ingestion fallback: {ing_err}")

        # STEP 1: Process and broadcast Outcome Recap for the PREVIOUS broadcast window
        all_preds = {p.fixture_id: p for p in calc_db.query(models.Prediction).all()}

        if is_night:
            # Previous window for 10 PM night broadcast is Today's Daytime/Evening window (7 AM - 9:59 PM GMT)
            prev_win_title = "Daily Picks"
            prev_date_str = utc_now.strftime("%A, %b %d, %Y")
            prev_start_utc = datetime(utc_now.year, utc_now.month, utc_now.day, 6, 50, 0)
            prev_end_utc = datetime(utc_now.year, utc_now.month, utc_now.day, 21, 59, 59)
        else:
            # Previous window for 7 AM morning broadcast is Today's Early Morning window (1 AM - 6:50 AM GMT)
            prev_win_title = "Early Morning Picks"
            prev_date_str = utc_now.strftime("%A, %b %d, %Y")
            prev_start_utc = datetime(utc_now.year, utc_now.month, utc_now.day, 0, 50, 0)
            prev_end_utc = datetime(utc_now.year, utc_now.month, utc_now.day, 6, 50, 0)

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
            await TelegramNotificationService.broadcast_outcome_recap(
                recap_items,
                window_title=prev_win_title,
                date_str=prev_date_str,
                bot_token=bot_token,
                chat_id=chat_id
            )

        # STEP 2: Process and broadcast NEW upcoming picks for the upcoming window
        picks = []
        for fix in fixtures:
            pred = all_preds.get(fix.id)
            if not pred:
                pred = PoissonPredictionEngine.predict_fixture(calc_db, fix.id)
            if not pred:
                continue
            match_date_str = fix.match_date.isoformat() + "Z" if fix.match_date else ""
            picks.append({
                "home_team": {"name": fix.home_team.name if fix.home_team else "Home"},
                "away_team": {"name": fix.away_team.name if fix.away_team else "Away"},
                "league": {"name": fix.league.name if fix.league else "League"},
                "match_date": match_date_str,
                "prediction": {
                    "over_1_5_probability": float(pred.over_1_5_probability or 0.75),
                    "most_likely_score": pred.most_likely_score or "2-1"
                }
            })
        picks.sort(key=lambda x: x["prediction"]["over_1_5_probability"], reverse=True)
        top_7_picks = picks[:7]

        # FALLBACK WINDOW: If window returned 0 picks, expand to next 24h upcoming fixtures
        if not top_7_picks:
            logger.info(f"No prediction picks available in primary window ({title_cat}). Executing 24-hour fallback search...")
            now_cutoff = (utc_now - timedelta(minutes=15)).replace(tzinfo=None)
            next_24h = (utc_now + timedelta(hours=24)).replace(tzinfo=None)
            fallback_fixtures = (
                calc_db.query(models.Fixture)
                .options(
                    joinedload(models.Fixture.league),
                    joinedload(models.Fixture.home_team),
                    joinedload(models.Fixture.away_team)
                )
                .filter(
                    models.Fixture.status.notin_(["FINISHED", "FT", "AET", "PEN"]),
                    models.Fixture.match_date >= now_cutoff,
                    models.Fixture.match_date <= next_24h
                )
                .order_by(models.Fixture.match_date.asc())
                .all()
            )
            for fix in fallback_fixtures:
                pred = all_preds.get(fix.id)
                if not pred:
                    pred = PoissonPredictionEngine.predict_fixture(calc_db, fix.id)
                if pred:
                    match_date_str = fix.match_date.isoformat() + "Z" if fix.match_date else ""
                    picks.append({
                        "home_team": {"name": fix.home_team.name if fix.home_team else "Home"},
                        "away_team": {"name": fix.away_team.name if fix.away_team else "Away"},
                        "league": {"name": fix.league.name if fix.league else "League"},
                        "match_date": match_date_str,
                        "prediction": {
                            "over_1_5_probability": float(pred.over_1_5_probability or 0.75),
                            "most_likely_score": pred.most_likely_score or "2-1"
                        }
                    })
            picks.sort(key=lambda x: x["prediction"]["over_1_5_probability"], reverse=True)
            top_7_picks = picks[:7]

        if not top_7_picks:
            logger.info(f"No prediction picks available to broadcast for window ({title_cat}). Suppressing dispatch.")
            return

        await TelegramNotificationService.broadcast_daily_top_picks(
            top_7_picks,
            bot_token=bot_token,
            chat_id=chat_id,
            title_category=title_cat,
            time_window_str=time_win_str
        )
        await WhatsAppNotificationService.broadcast_daily_top_picks(top_7_picks)
    except Exception as e:
        logger.error(f"Error executing scheduled Telegram broadcast: {e}")
    finally:
        calc_db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create SQLite database and tables on application startup
    init_db()

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

    # Schedule night 10:00 PM GMT Telegram digest broadcast (22:00 UTC) for Early Morning 1am-6:50am games
    scheduler.add_job(
        scheduled_telegram_daily_digest,
        'cron',
        hour=22,
        minute=0,
        timezone='UTC',
        kwargs={'is_night_digest': True},
        id='daily_telegram_2200_night_digest',
        replace_existing=True
    )

    # Schedule morning 07:00 AM GMT Telegram digest broadcast (07:00 UTC) for Rest of Day games
    scheduler.add_job(
        scheduled_telegram_daily_digest,
        'cron',
        hour=7,
        minute=0,
        timezone='UTC',
        kwargs={'is_night_digest': False},
        id='daily_telegram_0700_morning_digest',
        replace_existing=True
    )

    try:
        scheduler.start()
        logger.info("APScheduler initialized: Midnight cron, 6h refresh, 60s live score refresh, 10:00 PM GMT & 07:00 AM GMT Telegram digest jobs registered.")
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

app = FastAPI(
    title="Soccer Goal Predictor API",
    description="Backend API service for Soccer Goal Predictor app with 12-hour APScheduler automation",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

FRONTEND_DIST = os.path.join(os.path.dirname(BACKEND_DIR), "frontend", "dist")
if os.path.exists(FRONTEND_DIST) and os.path.exists(os.path.join(FRONTEND_DIST, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")

@app.get("/")
async def read_root():
    if os.path.exists(FRONTEND_DIST) and os.path.exists(os.path.join(FRONTEND_DIST, "index.html")):
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))
    return {
        "app": "Soccer Goal Predictor API",
        "status": "online",
        "health_check": "/health"
    }

@app.get("/health")
def health_check():
    """Health check endpoint required by project spec."""
    return {"status": "ok"}


@app.get("/api/statistics")
def read_all_team_statistics(db: Session = Depends(get_db)):
    """Retrieve stored statistics for all teams."""
    stats = get_all_team_statistics(db)
    return {"status": "ok", "count": len(stats), "data": stats}


@app.get("/api/statistics/league")
def read_all_league_statistics(db: Session = Depends(get_db)):
    """Retrieve stored statistics for all leagues."""
    stats = get_all_league_statistics(db)
    return {"status": "ok", "count": len(stats), "data": stats}


@app.get("/api/statistics/league/{league_id}")
def read_league_statistics(league_id: int, db: Session = Depends(get_db)):
    """Retrieve stored statistics for a specific league."""
    stat = get_league_statistics(db, league_id)
    if not stat:
        return {"status": "error", "message": f"No statistics found for league {league_id}"}
    return {"status": "ok", "data": stat}


@app.post("/api/statistics/league/recalculate")
def trigger_recalculate_league_statistics(
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
def read_team_statistics(team_id: int, db: Session = Depends(get_db)):
    """Retrieve stored statistics for a specific team."""
    stat = get_team_statistics(db, team_id)
    if not stat:
        return {"status": "error", "message": f"No statistics found for team {team_id}"}
    return {"status": "ok", "data": stat}


@app.post("/api/statistics/recalculate")
def trigger_recalculate_statistics(
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
async def sync_data(db: Session = Depends(get_db)):
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
def ingest_leagues_endpoint(payload: list[dict], db: Session = Depends(get_db)):
    """Ingest a list of league/competition records with duplicate prevention."""
    leagues = DataIngestionService.ingest_leagues(db, payload)
    return {"status": "ok", "count": len(leagues), "data": leagues}


@app.post("/api/ingest/teams")
def ingest_teams_endpoint(payload: list[dict], db: Session = Depends(get_db)):
    """Ingest a list of team records with duplicate prevention."""
    teams = DataIngestionService.ingest_teams(db, payload)
    return {"status": "ok", "count": len(teams), "data": teams}


@app.post("/api/ingest/fixtures")
def ingest_fixtures_endpoint(payload: list[dict], db: Session = Depends(get_db)):
    """
    Ingest historical results and upcoming fixtures with duplicate prevention
    and automatic statistic updates.
    """
    fixtures = DataIngestionService.ingest_fixtures(db, payload)
    return {"status": "ok", "count": len(fixtures), "data": fixtures}


@app.post("/api/predictions/predict/{fixture_id}")
def trigger_predict_fixture(fixture_id: int, db: Session = Depends(get_db)):
    """Calculate Poisson prediction for a single fixture and store in database."""
    pred = PoissonPredictionEngine.predict_fixture(db, fixture_id)
    if not pred:
        return {"status": "error", "message": f"Fixture {fixture_id} not found."}
    return {"status": "ok", "data": pred}


@app.post("/api/predictions/predict-all")
def trigger_predict_all_upcoming(db: Session = Depends(get_db)):
    """Calculate Poisson predictions for all upcoming fixtures."""
    preds = PoissonPredictionEngine.predict_all_upcoming_fixtures(db)
    return {"status": "ok", "count": len(preds), "data": preds}


@app.get("/api/predictions/accuracy")
def get_prediction_accuracy(db: Session = Depends(get_db)):
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
def read_fixture_prediction(fixture_id: int, db: Session = Depends(get_db)):
    """Retrieve stored Match Intelligence prediction for a specific fixture."""
    intel = PoissonPredictionEngine.generate_match_intelligence_prediction(db, fixture_id)
    if intel:
        return {"status": "ok", "data": intel}
    pred = db.query(models.Prediction).filter(models.Prediction.fixture_id == fixture_id).first()
    if not pred:
        return {"status": "error", "message": f"No prediction found for fixture {fixture_id}"}
    return {"status": "ok", "data": pred}


@app.get("/api/accumulators/generate")
def generate_smart_accumulators(day: Optional[str] = None, db: Session = Depends(get_db)):
    """Generates 3 curated betting accumulator options (Safe Double, 5-Fold, High Yield)."""
    return AccumulatorGeneratorService.generate_accumulators(db, match_day=day)


@app.get("/api/fixtures/{fixture_id}/details")
def get_fixture_details(fixture_id: int, db: Session = Depends(get_db)):
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
        return {"status": "error", "message": f"Fixture {fixture_id} not found."}

    pred = db.query(models.Prediction).filter(models.Prediction.fixture_id == fixture_id).first()
    home_elo = db.query(models.EloRating).filter(models.EloRating.team_id == fixture.home_team_id).first()
    away_elo = db.query(models.EloRating).filter(models.EloRating.team_id == fixture.away_team_id).first()
    home_streak = db.query(models.TeamFormStreak).filter(models.TeamFormStreak.team_id == fixture.home_team_id).first()
    away_streak = db.query(models.TeamFormStreak).filter(models.TeamFormStreak.team_id == fixture.away_team_id).first()

    top_scorelines = []
    if pred and pred.top_scorelines_json:
        try:
            top_scorelines = json.loads(pred.top_scorelines_json)
        except Exception:
            top_scorelines = []

    # Fetch last 5 head-to-head completed matches
    h2h_fixtures = (
        db.query(models.Fixture)
        .filter(
            models.Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
            ((models.Fixture.home_team_id == fixture.home_team_id) & (models.Fixture.away_team_id == fixture.away_team_id)) |
            ((models.Fixture.home_team_id == fixture.away_team_id) & (models.Fixture.away_team_id == fixture.home_team_id))
        )
        .order_by(models.Fixture.match_date.desc())
        .limit(5)
        .all()
    )

    h2h_data = []
    for h in h2h_fixtures:
        h2h_data.append({
            "match_date": h.match_date.isoformat() if h.match_date else "",
            "home_team_name": h.home_team.name if h.home_team else "Home",
            "away_team_name": h.away_team.name if h.away_team else "Away",
            "score": f"{h.home_score if h.home_score is not None else '-'}-{h.away_score if h.away_score is not None else '-'}",
            "total_goals": (h.home_score or 0) + (h.away_score or 0)
        })

    # Generate or parse full Match Intelligence Core prediction payload
    intel = PoissonPredictionEngine.generate_match_intelligence_prediction(db, fixture_id)
    if not intel:
        h_xg = round(float(pred.predicted_home_score), 2) if (pred and pred.predicted_home_score is not None) else 1.45
        a_xg = round(float(pred.predicted_away_score), 2) if (pred and pred.predicted_away_score is not None) else 1.15
        tot_xg = round(h_xg + a_xg, 2)
        o15 = float(pred.over_1_5_probability or 0.78) if pred else 0.78
        o25 = float(pred.over_2_5_probability or 0.52) if pred else 0.52
        o05 = float(pred.over_0_5_probability or 0.90) if pred else 0.90
        o35 = float(pred.over_3_5_probability or 0.28) if pred else 0.28
        btts_p = float(pred.btts_probability or 0.55) if pred else 0.55
        conf_int = int((pred.confidence_score or 0.50) * 100) if pred else 50
        most_likely = (pred.most_likely_score if pred else None) or "2-1"

        intel = {
            "fixture_id": fixture_id,
            "model": {"version": "v2_match_intelligence", "generated_at": datetime.now(timezone.utc).isoformat()},
            "expected_goals": {"home": h_xg, "away": a_xg, "total": tot_xg},
            "result": {
                "home_win": float(pred.home_win_probability or 0.45) if pred else 0.45,
                "draw": float(pred.draw_probability or 0.25) if pred else 0.25,
                "away_win": float(pred.away_win_probability or 0.30) if pred else 0.30
            },
            "goals": {
                "over_0_5": o05, "under_0_5": round(1.0 - o05, 4),
                "over_1_5": o15, "under_1_5": round(1.0 - o15, 4),
                "over_2_5": o25, "under_2_5": round(1.0 - o25, 4),
                "over_3_5": o35, "under_3_5": round(1.0 - o35, 4),
                "over_4_5": float(pred.over_4_5_probability or 0.12) if pred else 0.12
            },
            "btts": {"yes": btts_p, "no": round(1.0 - btts_p, 4)},
            "home_team_goals": {
                "over_0_5": round(1.0 - math.exp(-h_xg), 4), "under_0_5": round(math.exp(-h_xg), 4),
                "over_1_5": round(1.0 - math.exp(-h_xg) * (1.0 + h_xg), 4), "under_1_5": round(math.exp(-h_xg) * (1.0 + h_xg), 4),
                "over_2_5": round(1.0 - math.exp(-h_xg) * (1.0 + h_xg + (h_xg**2)/2.0), 4), "under_2_5": round(math.exp(-h_xg) * (1.0 + h_xg + (h_xg**2)/2.0), 4)
            },
            "away_team_goals": {
                "over_0_5": round(1.0 - math.exp(-a_xg), 4), "under_0_5": round(math.exp(-a_xg), 4),
                "over_1_5": round(1.0 - math.exp(-a_xg) * (1.0 + a_xg), 4), "under_1_5": round(math.exp(-a_xg) * (1.0 + a_xg), 4),
                "over_2_5": round(1.0 - math.exp(-a_xg) * (1.0 + a_xg + (a_xg**2)/2.0), 4), "under_2_5": round(math.exp(-a_xg) * (1.0 + a_xg + (a_xg**2)/2.0), 4)
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

    return {
        "status": "ok",
        "fixture_id": fixture_id,
        "league_name": fixture.league.name if fixture.league else "League",
        "home_team": {
            "name": fixture.home_team.name if fixture.home_team else "Home Team",
            "elo_rating": round(home_elo.rating, 1) if home_elo else 1500.0,
            "last_5_results": json.loads(home_streak.last_5_results) if (home_streak and home_streak.last_5_results) else [],
            "goals_scored_last_5": home_streak.goals_scored_last_5 if home_streak else 0,
            "goals_conceded_last_5": home_streak.goals_conceded_last_5 if home_streak else 0,
        },
        "away_team": {
            "name": fixture.away_team.name if fixture.away_team else "Away Team",
            "elo_rating": round(away_elo.rating, 1) if away_elo else 1500.0,
            "last_5_results": json.loads(away_streak.last_5_results) if (away_streak and away_streak.last_5_results) else [],
            "goals_scored_last_5": away_streak.goals_scored_last_5 if away_streak else 0,
            "goals_conceded_last_5": away_streak.goals_conceded_last_5 if away_streak else 0,
        },
        "h2h_history": h2h_data,
        "prediction": {
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
            "most_likely_score": intel["exact_scores"][0]["score"] if intel["exact_scores"] else "2-1",
            "top_scorelines": top_scorelines or intel["exact_scores"][:5],

            # Match Intelligence unified schema
            "match_intelligence": intel,
            "model": intel["model"],
            "expected_goals": intel["expected_goals"],
            "result": intel["result"],
            "goals": intel["goals"],
            "btts": intel["btts"],
            "home_team_goals": intel["home_team_goals"],
            "away_team_goals": intel["away_team_goals"],
            "halves": intel["halves"],
            "exact_scores": intel["exact_scores"],
            "confidence": intel["confidence"],
            "best_signal": intel["best_signal"],
            "corners": intel.get("corners"),
            "cards": intel.get("cards")
        },
        "match_intelligence": intel,
        "corners": intel.get("corners"),
        "cards": intel.get("cards")
    }


@app.get("/api/corners/backtest")
def get_corners_backtest(min_samples: int = 5, db: Session = Depends(get_db)):
    """Runs a chronological backtest on historical matches with observed corner counts."""
    from services.corners_service import CornersBacktestService
    return CornersBacktestService.run_chronological_backtest(db, min_samples=min_samples)


@app.get("/api/corners/data-quality")
def get_corners_data_quality(db: Session = Depends(get_db)):
    """Audits database corner data completeness, eligible matches, coverage, and competition breakdowns."""
    from services.corners_service import CornerDataQualityService
    return CornerDataQualityService.get_database_corner_data_quality(db)


@app.get("/api/corners/performance")
def get_corners_performance_dashboard(db: Session = Depends(get_db)):
    """Exposes production corner model validation status, Brier scores, calibration, and baseline comparisons."""
    from services.corners_service import CornersBacktestService
    return CornersBacktestService.run_chronological_backtest(db, min_samples=100)


@app.get("/api/cards/backtest")
def get_cards_backtest(min_samples: int = 5, db: Session = Depends(get_db)):
    """Runs a chronological backtest on historical matches with observed card counts."""
    from services.cards_service import CardsBacktestService
    return CardsBacktestService.run_chronological_backtest(db, min_samples=min_samples)


@app.get("/api/cards/data-quality")
def get_cards_data_quality(db: Session = Depends(get_db)):
    """Audits database card data completeness, referee coverage, eligible matches, and competition breakdowns."""
    from services.cards_service import CardDataQualityService
    return CardDataQualityService.get_database_card_data_quality(db)


@app.get("/api/cards/performance")
def get_cards_performance_dashboard(db: Session = Depends(get_db)):
    """Exposes production card model validation status, Brier scores, calibration, and baseline comparisons."""
    from services.cards_service import CardsBacktestService
    return CardsBacktestService.run_chronological_backtest(db, min_samples=100)


@app.get("/api/fixtures/{fixture_id}/live-intelligence")
def get_fixture_live_intelligence(fixture_id: int, db: Session = Depends(get_db)):
    """Computes dynamic in-play prediction probabilities for a live or scheduled fixture."""
    from services.live_service import LiveMatchIntelligenceService
    intel = LiveMatchIntelligenceService.get_live_intelligence(db, fixture_id)
    if not intel:
        raise HTTPException(status_code=404, detail="Fixture not found")
    return intel


@app.get("/api/live/fixtures")
def get_live_fixtures(db: Session = Depends(get_db)):
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
def get_live_performance(db: Session = Depends(get_db)):
    """Returns dynamic in-play prediction evaluation metrics across 6 match minute intervals."""
    from services.model_evaluation_service import ModelEvaluationService
    return ModelEvaluationService.get_live_minute_performance(db)


@app.get("/api/models/performance")
def get_models_performance(
    model_version: Optional[str] = None,
    prediction_type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Returns global probabilistic scoring metrics (Brier, Log Loss, MAE, ECE) for verified predictions."""
    from services.model_evaluation_service import ModelEvaluationService
    return ModelEvaluationService.get_model_performance(db, model_version=model_version, prediction_type=prediction_type)


@app.get("/api/models/leaderboard")
def get_models_leaderboard(db: Session = Depends(get_db)):
    """Returns ranked prediction markets sorted by composite Brier and calibration performance."""
    from services.model_evaluation_service import ModelEvaluationService
    return {"status": "ok", "leaderboard": ModelEvaluationService.get_market_leaderboard(db)}


@app.get("/api/models/calibration")
def get_models_calibration(market: Optional[str] = None, db: Session = Depends(get_db)):
    """Returns 10-decile probability reliability diagram buckets, ECE, and MCE."""
    from services.model_evaluation_service import ModelEvaluationService
    return ModelEvaluationService.get_calibration_dashboard(db, market=market)


@app.get("/api/models/drift")
def get_models_drift(
    window_size: int = 100, baseline_size: int = 300, db: Session = Depends(get_db)
):
    """Monitors model drift by comparing recent window performance against the historical baseline."""
    from services.model_evaluation_service import ModelEvaluationService
    return ModelEvaluationService.get_model_drift_analysis(db, window_size=window_size, baseline_size=baseline_size)


@app.get("/api/models/league-performance")
def get_models_league_performance(competition: Optional[str] = None, db: Session = Depends(get_db)):
    """Returns model performance breakdown segmented by league competition."""
    from services.model_evaluation_service import ModelEvaluationService
    return {"status": "ok", "leagues": ModelEvaluationService.get_league_performance(db, competition=competition)}


@app.get("/api/models/status")
def get_models_status(db: Session = Depends(get_db)):
    """Returns simple production readiness summary across all predictive model families."""
    from services.model_evaluation_service import ModelEvaluationService
    return ModelEvaluationService.get_models_readiness_status(db)


@app.get("/api/data-quality/overview")
def get_data_quality_overview(db: Session = Depends(get_db)):
    """Returns global data coverage metrics across goals, corners, cards, referees, and live snapshots."""
    from services.data_quality_service import DataQualityService
    return DataQualityService.calculate_global_coverage(db)


@app.get("/api/data-quality/competitions")
def get_data_quality_competitions(db: Session = Depends(get_db)):
    """Returns data quality coverage segmented by competition."""
    from services.data_quality_service import DataQualityService
    return {"status": "ok", "competitions": DataQualityService.calculate_competition_coverage(db)}


@app.get("/api/data-quality/backfill-status")
def get_data_quality_backfill_status(db: Session = Depends(get_db)):
    """Returns historical backfill and enrichment progress."""
    from services.historical_data_service import HistoricalDataService
    return HistoricalDataService.get_backfill_status(db)


@app.post("/api/data-quality/backfill-run")
def run_data_quality_backfill(batch_size: int = 50, db: Session = Depends(get_db)):
    """Triggers a controlled batch historical enrichment pass."""
    from services.historical_data_service import HistoricalDataService
    return HistoricalDataService.discover_and_enrich_batch(db, batch_size=batch_size)


@app.get("/api/models/readiness")
def get_models_readiness(db: Session = Depends(get_db)):
    """Returns production readiness status and activation gates for all prediction models."""
    from services.production_validation_service import ProductionValidationService
    return ProductionValidationService.get_all_models_readiness_report(db)


@app.get("/api/fixtures/{fixture_id}/feature-coverage")
def get_fixture_feature_coverage(fixture_id: int, db: Session = Depends(get_db)):
    """Returns feature payload and temporal coverage diagnostics for a specific fixture."""
    from services.feature_store_service import FeatureStoreService
    return FeatureStoreService.build_fixture_features_payload(db, fixture_id)


@app.get("/api/system/providers")
def get_system_providers(db: Session = Depends(get_db)):
    """Returns external data provider operational health and latency logs."""
    from services.provider_health_service import ProviderHealthService
    return {"status": "ok", "providers": ProviderHealthService.get_providers_status(db)}


@app.get("/api/system/intelligence-status")
def get_system_intelligence_status(db: Session = Depends(get_db)):
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
def get_system_health():
    """Liveness probe confirming FastAPI application process is active and responsive."""
    return {"status": "HEALTHY", "service": "Soccer Goal Predictor / Match Intelligence", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/system/readiness")
def get_system_readiness(db: Session = Depends(get_db)):
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
def get_system_operational_status(db: Session = Depends(get_db)):
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


@app.get("/api/system/jobs")
def get_system_jobs(db: Session = Depends(get_db)):
    """Lists available production jobs and recent executions."""
    from services.job_orchestrator_service import JobOrchestratorService
    return {
        "available_jobs": JobOrchestratorService.AVAILABLE_JOBS,
        "recent_executions": JobOrchestratorService.get_job_history(db, limit=10)
    }


@app.get("/api/system/jobs/{job_name}")
def get_system_job_status(job_name: str, db: Session = Depends(get_db)):
    """Returns latest execution status for a specific job."""
    from services.job_orchestrator_service import JobOrchestratorService
    history = JobOrchestratorService.get_job_history(db, job_name=job_name, limit=1)
    if not history:
        return {"job_name": job_name, "last_execution": None, "status": "IDLE"}
    return {"job_name": job_name, "last_execution": history[0]}


@app.get("/api/system/jobs/{job_name}/history")
def get_system_job_history(job_name: str, limit: int = 20, db: Session = Depends(get_db)):
    """Returns execution history logs for a specific job."""
    from services.job_orchestrator_service import JobOrchestratorService
    return {"job_name": job_name, "history": JobOrchestratorService.get_job_history(db, job_name=job_name, limit=limit)}


@app.post("/api/system/jobs/{job_name}/run")
def run_system_job(job_name: str, db: Session = Depends(get_db)):
    """Manually triggers immediate execution of a production job."""
    from services.job_orchestrator_service import JobOrchestratorService
    return JobOrchestratorService.execute_job(db, job_name)


@app.get("/api/system/alerts")
def get_system_alerts(db: Session = Depends(get_db)):
    """Returns all currently active operational system alerts."""
    from services.alert_service import AlertService
    return {"status": "ok", "alerts": AlertService.get_active_alerts(db)}


@app.post("/api/system/alerts/{alert_id}/resolve")
def resolve_system_alert(alert_id: str, db: Session = Depends(get_db)):
    """Marks an active system alert as resolved."""
    from services.alert_service import AlertService
    success = AlertService.resolve_alert(db, alert_id)
    return {"status": "ok" if success else "error", "resolved": success}


@app.get("/api/system/backups")
def get_system_backups():
    """Lists available SQLite backup archives."""
    from services.backup_service import BackupService
    return {"status": "ok", "backups": BackupService.list_backups()}


@app.post("/api/system/backups/run")
def run_system_backup():
    """Executes an online live SQLite backup and integrity verification."""
    from services.backup_service import BackupService
    return BackupService.create_database_backup()


# =============================================================================
# PHASE 8: PRODUCTION DATA ACQUISITION & REAL-WORLD VALIDATION ENDPOINTS
# =============================================================================

@app.get("/api/data-quality/validation")
def get_data_quality_validation(db: Session = Depends(get_db)):
    """Returns dataset completeness and validation eligibility."""
    from services.data_quality_service import DataQualityService
    cov = DataQualityService.calculate_global_coverage(db)
    return {"status": "ok", "validation_dataset": cov, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/data-quality/provenance")
def get_data_provenance(fixture_id: Optional[int] = None, db: Session = Depends(get_db)):
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
def get_data_conflicts(status: Optional[str] = "CONFLICT", db: Session = Depends(get_db)):
    """Returns recorded data conflicts between external providers."""
    from services.data_reconciliation_service import DataReconciliationService
    return {"status": "ok", "conflicts": DataReconciliationService.get_all_conflicts(db, status=status)}


@app.post("/api/data-quality/conflicts/{conflict_id}/resolve")
def resolve_data_conflict(conflict_id: int, resolved_value: str, notes: Optional[str] = None, db: Session = Depends(get_db)):
    """Resolves an open data conflict with operator notes."""
    from services.data_reconciliation_service import DataReconciliationService
    success = DataReconciliationService.resolve_conflict(db, conflict_id, resolved_value, notes)
    return {"status": "ok" if success else "error", "resolved": success}


@app.get("/api/data-quality/backfill-progress")
def get_backfill_progress(db: Session = Depends(get_db)):
    """Returns detailed progress for historical data backfill."""
    from services.historical_data_service import HistoricalDataService
    return HistoricalDataService.get_backfill_status(db)


@app.post("/api/data-quality/backfill/start")
def start_backfill(db: Session = Depends(get_db)):
    """Starts or triggers an automated historical data backfill pass."""
    from services.historical_data_service import HistoricalDataService
    HistoricalDataService.resume_backfill()
    return HistoricalDataService.discover_and_enrich_batch(db, batch_size=30)


@app.post("/api/data-quality/backfill/pause")
def pause_backfill():
    """Pauses historical data backfill."""
    from services.historical_data_service import HistoricalDataService
    return HistoricalDataService.pause_backfill()


@app.post("/api/data-quality/backfill/resume")
def resume_backfill():
    """Resumes historical data backfill."""
    from services.historical_data_service import HistoricalDataService
    return HistoricalDataService.resume_backfill()


@app.post("/api/data-quality/backfill/retry")
def retry_backfill(db: Session = Depends(get_db)):
    """Resets failed backfill items allowing them to be retried."""
    from services.historical_data_service import HistoricalDataService
    return HistoricalDataService.retry_failed_backfills(db)


@app.get("/api/models/real-validation")
def get_models_real_validation(limit: int = 200, db: Session = Depends(get_db)):
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
def get_models_real_calibration(prediction_type: str = "goals", db: Session = Depends(get_db)):
    """Returns 10-decile empirical calibration reliability table from verified historical outcomes."""
    from services.model_evaluation_service import ModelEvaluationService
    return ModelEvaluationService.get_calibration_report(db, prediction_type=prediction_type)


@app.get("/api/models/real-leaderboard")
def get_models_real_leaderboard(db: Session = Depends(get_db)):
    """Returns ranked model comparison leaderboard on real match outcomes."""
    from services.production_validation_service import ProductionValidationService
    return ProductionValidationService.get_real_leaderboard(db)


@app.get("/api/models/market-readiness")
def get_models_market_readiness(db: Session = Depends(get_db)):
    """Returns granular market-level sample gates across all goals, corners, and cards markets."""
    from services.production_validation_service import ProductionValidationService
    return ProductionValidationService.get_market_level_readiness(db)


@app.get("/api/models/ensemble-status")
def get_models_ensemble_status(db: Session = Depends(get_db)):
    """Returns adaptive ensemble activation state, gate, and weights."""
    from services.ensemble_service import AdaptiveEnsembleService
    weights = AdaptiveEnsembleService.calculate_dynamic_ensemble_weights(db, "over_1_5_goals")
    return {"status": "ok", "ensemble": weights}


@app.get("/api/models/live-validation")
def get_models_live_validation(db: Session = Depends(get_db)):
    """Returns live in-play prediction performance across minute buckets and signals."""
    from services.model_evaluation_service import ModelEvaluationService
    return ModelEvaluationService.get_live_performance_report(db)


# =============================================================================
# PHASE 9: UNIFIED MATCH INTELLIGENCE & CROSS-MARKET PREDICTION ENDPOINTS
# =============================================================================

@app.get("/api/fixtures/{fixture_id}/match-intelligence")
def get_fixture_match_intelligence(fixture_id: int, db: Session = Depends(get_db)):
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
def get_upcoming_match_intelligence(limit: int = 50, db: Session = Depends(get_db)):
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




@app.post("/api/notifications/telegram/test")
async def send_telegram_test_notification(bot_token: Optional[str] = None, chat_id: Optional[str] = None):
    """Sends a test Telegram notification message."""
    test_msg = (
        "🟢 <b>SOCCER GOAL PREDICTOR TEST NOTIFICATION</b>\n\n"
        "Your Telegram Bot connection is successfully configured!\n"
        "You will receive daily top predictions at 08:00 UTC."
    )
    success = await TelegramNotificationService.send_message(test_msg, bot_token=bot_token, chat_id=chat_id)
    if success:
        return {"status": "ok", "message": "Test Telegram message sent successfully!"}
    return {"status": "error", "message": "Failed to send Telegram message. Please verify BOT_TOKEN and CHAT_ID."}


@app.post("/api/notifications/whatsapp/test")
async def send_whatsapp_test_notification(phone: Optional[str] = None, api_key: Optional[str] = None):
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
async def trigger_manual_broadcast(bot_token: Optional[str] = None, chat_id: Optional[str] = None):
    """Triggers immediate prediction broadcast to Telegram and WhatsApp."""
    await scheduled_telegram_daily_digest(bot_token=bot_token, chat_id=chat_id)
    return {"status": "ok", "message": "Broadcast triggered successfully to Telegram and WhatsApp!"}


@app.get("/api/fixtures/upcoming")
async def get_upcoming_fixtures(db: Session = Depends(get_db)):
    """
    Retrieve all upcoming/scheduled global fixtures starting from present date
    with full team, league, and Poisson goal prediction details.
    """
    global LAST_SYNC_TIME
    now = datetime.now(timezone.utc)
    now_cutoff = (now - timedelta(hours=2)).replace(tzinfo=None)

    try:
        fixtures = db.query(models.Fixture).options(
            joinedload(models.Fixture.league),
            joinedload(models.Fixture.home_team),
            joinedload(models.Fixture.away_team)
        ).filter(
            models.Fixture.status.notin_(["FINISHED", "FT", "AET", "PEN"]),
            models.Fixture.match_date >= now_cutoff
        ).order_by(models.Fixture.match_date.asc()).all()

        if not fixtures:
            # Fallback query: fetch all non-finished fixtures regardless of match_date cutoff
            fixtures = db.query(models.Fixture).options(
                joinedload(models.Fixture.league),
                joinedload(models.Fixture.home_team),
                joinedload(models.Fixture.away_team)
            ).filter(
                models.Fixture.status.notin_(["FINISHED", "FT", "AET", "PEN"])
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
        pred = all_preds.get(fix.id)
        top_scorelines = []
        if pred and pred.top_scorelines_json:
            try:
                top_scorelines = json.loads(pred.top_scorelines_json)
            except Exception:
                top_scorelines = []

        if pred:
            h_xg = round(float(pred.predicted_home_score), 2) if pred.predicted_home_score is not None else 1.45
            a_xg = round(float(pred.predicted_away_score), 2) if pred.predicted_away_score is not None else 1.15
            home_win = float(pred.home_win_probability or 0.45)
            draw_prob = float(pred.draw_probability or 0.25)
            away_win = float(pred.away_win_probability or 0.30)
            o05 = float(pred.over_0_5_probability or 0.90)
            o15 = float(pred.over_1_5_probability or 0.78)
            o25 = float(pred.over_2_5_probability or 0.52)
            o35 = float(pred.over_3_5_probability or 0.28)
            u25 = float(pred.under_2_5_probability or 0.48)
            btts_prob = float(pred.btts_probability) if (getattr(pred, 'btts_probability', None) is not None) else round((1.0 - (2.718281828459045 ** -h_xg)) * (1.0 - (2.718281828459045 ** -a_xg)), 4)
            confidence_score = float(pred.confidence_score) if (getattr(pred, 'confidence_score', None) is not None) else 0.50
            most_likely = pred.most_likely_score or "2-1"
        else:
            h_xg, a_xg = 1.45, 1.15
            home_win, draw_prob, away_win = 0.45, 0.25, 0.30
            o05, o15, o25, o35, u25 = 0.90, 0.78, 0.52, 0.28, 0.48
            btts_prob = 0.55
            confidence_score = 0.50
            most_likely = "2-1"

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
        model_odds = round(1.0 / max(0.01, o15), 2)
        implied_market_odds = round(model_odds * 1.08, 2)
        implied_market_prob = round(1.0 / max(1.01, implied_market_odds), 4)
        value_edge_pct = round((o15 - implied_market_prob) * 100, 1)
        is_value_bet = (o15 >= 0.78) and (value_edge_pct >= 4.0)

        result_data.append({
            "id": fix.id,
            "external_id": fix.external_id,
            "match_date": match_date_str,
            "status": fix.status,
            "venue": fix.venue,
            "weather": weather_data,
            "home_score": getattr(fix, "home_score", None),
            "away_score": getattr(fix, "away_score", None),
            "live_clock": getattr(fix, "live_clock", None),
            "value_bet": {
                "is_value_bet": is_value_bet,
                "model_odds": model_odds,
                "market_odds": implied_market_odds,
                "value_edge_pct": value_edge_pct
            },
            "league": {
                "id": fix.league.id if fix.league else None,
                "name": fix.league.name if fix.league else "Unknown League",
                "country": fix.league.country if fix.league else "",
                "season": fix.league.season if fix.league else ""
            },
            "home_team": {
                "id": fix.home_team.id if fix.home_team else None,
                "name": fix.home_team.name if fix.home_team else "Home Team",
                "short_code": fix.home_team.short_code if fix.home_team else "HOM",
                "logo_url": fix.home_team.logo_url if fix.home_team else None
            },
            "away_team": {
                "id": fix.away_team.id if fix.away_team else None,
                "name": fix.away_team.name if fix.away_team else "Away Team",
                "short_code": fix.away_team.short_code if fix.away_team else "AWY",
                "logo_url": fix.away_team.logo_url if fix.away_team else None
            },
            "prediction": {
                "predicted_home_score": h_xg,
                "predicted_away_score": a_xg,
                "expected_goals_xg": round(h_xg + a_xg, 2),
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
                "first_half_xg": round((h_xg + a_xg) * 0.45, 2),
                "first_half_over_0_5_probability": round(1.0 - (2.718281828459045 ** -((h_xg + a_xg) * 0.45)), 4),
                "first_half_over_1_5_probability": round(1.0 - (2.718281828459045 ** -((h_xg + a_xg) * 0.45)) * (1.0 + (h_xg + a_xg) * 0.45), 4),

                "second_half_xg": round((h_xg + a_xg) * 0.55, 2),
                "second_half_over_0_5_probability": round(1.0 - (2.718281828459045 ** -((h_xg + a_xg) * 0.55)), 4),
                "second_half_over_1_5_probability": round(1.0 - (2.718281828459045 ** -((h_xg + a_xg) * 0.55)) * (1.0 + (h_xg + a_xg) * 0.55), 4),

                "most_likely_score": most_likely,
                "top_scorelines": top_scorelines or [{"scoreline": most_likely, "probability": home_win}]
            }
        })

    return {"status": "ok", "count": len(result_data), "data": result_data}


@app.get("/api/fixtures/finished")
def get_finished_fixtures(date: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Retrieve completed match results with final scores and Over 1.5 goal prediction outcomes.
    Supports optional `date` filter (YYYY-MM-DD format).
    """
    try:
        query = db.query(models.Fixture).options(
            joinedload(models.Fixture.league),
            joinedload(models.Fixture.home_team),
            joinedload(models.Fixture.away_team)
        ).filter(
            models.Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"])
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
            pred = all_preds.get(fix.id)

            top_scorelines = []
            if pred and pred.top_scorelines_json:
                try:
                    top_scorelines = json.loads(pred.top_scorelines_json)
                except Exception:
                    top_scorelines = []

            h_xg = round(float(pred.predicted_home_score), 2) if (pred and pred.predicted_home_score is not None) else 1.45
            a_xg = round(float(pred.predicted_away_score), 2) if (pred and pred.predicted_away_score is not None) else 1.15

            h_score = fix.home_score
            a_score = fix.away_score
            has_scores = h_score is not None and a_score is not None
            total_actual_goals = (h_score + a_score) if has_scores else None

            home_win = float(pred.home_win_probability or 0.45) if pred else 0.45
            draw_prob = float(pred.draw_probability or 0.25) if pred else 0.25
            away_win = float(pred.away_win_probability or 0.30) if pred else 0.30
            o05 = float(pred.over_0_5_probability or 0.90) if pred else 0.90
            o15 = float(pred.over_1_5_probability or 0.78) if pred else 0.78
            o25 = float(pred.over_2_5_probability or 0.52) if pred else 0.52
            o35 = float(pred.over_3_5_probability or 0.28) if pred else 0.28
            u25 = float(pred.under_2_5_probability or 0.48) if pred else 0.48
            btts_prob = float(pred.btts_probability) if (pred and getattr(pred, 'btts_probability', None) is not None) else round((1.0 - (2.718281828459045 ** -h_xg)) * (1.0 - (2.718281828459045 ** -a_xg)), 4)
            confidence_score = float(pred.confidence_score) if (pred and getattr(pred, 'confidence_score', None) is not None) else 0.50
            most_likely = (pred.most_likely_score if pred else None) or "1-1"

            match_date_str = None
            if fix.match_date:
                if isinstance(fix.match_date, datetime):
                    dt_obj = fix.match_date if fix.match_date.tzinfo else fix.match_date.replace(tzinfo=timezone.utc)
                    match_date_str = dt_obj.isoformat()
                else:
                    s = str(fix.match_date).replace(" ", "T")
                    match_date_str = s if (s.endswith("Z") or "+" in s[10:] or "-" in s[10:]) else s + "Z"

            result_data.append({
                "id": fix.id,
                "external_id": fix.external_id,
                "match_date": match_date_str,
                "status": "FINISHED",
                "venue": fix.venue,
                "home_score": h_score,
                "away_score": a_score,
                "total_goals": total_actual_goals,
                "over_1_5_hit": (total_actual_goals >= 2) if total_actual_goals is not None else None,
                "over_2_5_hit": (total_actual_goals >= 3) if total_actual_goals is not None else None,
                "live_clock": "FT",
                "league": {
                    "id": fix.league.id if fix.league else None,
                    "name": fix.league.name if fix.league else "Unknown League",
                    "country": fix.league.country if fix.league else "",
                    "season": fix.league.season if fix.league else ""
                },
                "home_team": {
                    "id": fix.home_team.id if fix.home_team else None,
                    "name": fix.home_team.name if fix.home_team else "Home Team",
                    "short_code": fix.home_team.short_code if fix.home_team else "HOM",
                    "logo_url": fix.home_team.logo_url if fix.home_team else None
                },
                "away_team": {
                    "id": fix.away_team.id if fix.away_team else None,
                    "name": fix.away_team.name if fix.away_team else "Away Team",
                    "short_code": fix.away_team.short_code if fix.away_team else "AWY",
                    "logo_url": fix.away_team.logo_url if fix.away_team else None
                },
                "prediction": {
                    "predicted_home_score": h_xg,
                    "predicted_away_score": a_xg,
                    "expected_goals_xg": round(h_xg + a_xg, 2),
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
                    "first_half_xg": round((h_xg + a_xg) * 0.45, 2),
                    "first_half_over_0_5_probability": round(1.0 - (2.718281828459045 ** -((h_xg + a_xg) * 0.45)), 4),
                    "first_half_over_1_5_probability": round(1.0 - (2.718281828459045 ** -((h_xg + a_xg) * 0.45)) * (1.0 + (h_xg + a_xg) * 0.45), 4),
                    "second_half_xg": round((h_xg + a_xg) * 0.55, 2),
                    "second_half_over_0_5_probability": round(1.0 - (2.718281828459045 ** -((h_xg + a_xg) * 0.55)), 4),
                    "second_half_over_1_5_probability": round(1.0 - (2.718281828459045 ** -((h_xg + a_xg) * 0.55)) * (1.0 + (h_xg + a_xg) * 0.55), 4),
                    "most_likely_score": most_likely,
                    "top_scorelines": top_scorelines or [{"scoreline": most_likely, "probability": home_win}]
                }
            })

        return {"status": "ok", "count": len(result_data), "data": result_data}
    except Exception as exc:
        logger.error(f"Error in get_finished_fixtures: {exc}", exc_info=True)
        return {"status": "error", "message": str(exc), "count": 0, "data": []}







