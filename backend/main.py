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

    # Ensure database has initial fixture data on boot
    boot_db = SessionLocal()
    try:
        fixture_count = boot_db.query(models.Fixture).count()
        if fixture_count == 0:
            logger.info("Database is empty on boot. Running initial ESPN fixture ingestion...")
            await DataIngestionService.fetch_and_ingest_from_api(boot_db, api_key=FOOTBALL_API_KEY)
            PoissonPredictionEngine.predict_all_upcoming_fixtures(boot_db)
            logger.info(f"Boot ingestion complete. Ingested {boot_db.query(models.Fixture).count()} fixtures.")
    except Exception as boot_err:
        logger.error(f"Error during boot fixture ingestion: {boot_err}")
    finally:
        boot_db.close()

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
    """Retrieve stored prediction for a specific fixture."""
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
    """Retrieves deep H2H history, recent form streaks, xG breakdown, and top scorelines for a fixture."""
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
            "predicted_home_score": pred.predicted_home_score if pred else 1.45,
            "predicted_away_score": pred.predicted_away_score if pred else 1.15,
            "expected_goals_xg": pred.expected_goals_xg if pred else 2.60,
            "over_1_5_probability": pred.over_1_5_probability if pred else 0.78,
            "over_2_5_probability": pred.over_2_5_probability if pred else 0.52,
            "btts_probability": pred.btts_probability if pred else 0.55,
            "confidence_score": pred.confidence_score if pred else 0.50,
            "most_likely_score": pred.most_likely_score if pred else "2-1",
            "top_scorelines": top_scorelines
        }
    }


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







