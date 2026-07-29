import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
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

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

async def scheduled_data_refresh():
    """
    Automated 12-hour background job:
    1. Fetches/ingests latest competitions, teams, match results, and upcoming fixtures.
    2. Recalculates team and league statistics.
    3. Recalculates Poisson goal predictions for all upcoming fixtures.
    """
    logger.info("Executing scheduled 12-hour data refresh and prediction recalculation cycle...")
    db = SessionLocal()
    try:
        await DataIngestionService.fetch_and_ingest_from_api(db, api_key=FOOTBALL_API_KEY)
        calculate_all_league_statistics(db)
        calculate_all_team_statistics(db)
        PoissonPredictionEngine.predict_all_upcoming_fixtures(db)
        logger.info("Scheduled 12-hour data refresh completed successfully.")
    except Exception as e:
        logger.error(f"Error during scheduled 12-hour data refresh: {str(e)}")
    finally:
        db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create SQLite database and tables on application startup
    init_db()

    # Schedule 12-hour automated refresh with APScheduler
    scheduler.add_job(
        scheduled_data_refresh,
        'interval',
        hours=12,
        id='automated_12h_refresh',
        replace_existing=True
    )
    scheduler.start()
    logger.info("APScheduler initialized: 12-hour data refresh job registered.")

    yield

    scheduler.shutdown()
    logger.info("APScheduler shutdown cleanly.")

app = FastAPI(
    title="Soccer Goal Predictor API",
    description="Backend API service for Soccer Goal Predictor app with 12-hour APScheduler automation",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
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
    Triggers automated ingestion sync for competitions, teams,
    historical results, and upcoming fixtures.
    """
    res = await DataIngestionService.fetch_and_ingest_from_api(db, api_key=FOOTBALL_API_KEY)
    return res


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


@app.get("/api/predictions/{fixture_id}")
def read_fixture_prediction(fixture_id: int, db: Session = Depends(get_db)):
    """Retrieve stored prediction for a specific fixture."""
    pred = db.query(models.Prediction).filter(models.Prediction.fixture_id == fixture_id).first()
    if not pred:
        return {"status": "error", "message": f"No prediction found for fixture {fixture_id}"}
    return {"status": "ok", "data": pred}


@app.get("/api/fixtures/upcoming")
async def get_upcoming_fixtures(db: Session = Depends(get_db)):
    """
    Retrieve all upcoming/scheduled global fixtures starting from present date
    with full team, league, and Poisson goal prediction details.
    """
    now_cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(tzinfo=None)
    fixtures = db.query(models.Fixture).filter(
        models.Fixture.status != "FINISHED",
        models.Fixture.match_date >= now_cutoff
    ).order_by(models.Fixture.match_date.asc()).all()

    if not fixtures or len(fixtures) < 5:
        await DataIngestionService.fetch_and_ingest_from_api(db, api_key=FOOTBALL_API_KEY)
        fixtures = db.query(models.Fixture).filter(
            models.Fixture.status != "FINISHED",
            models.Fixture.match_date >= now_cutoff
        ).order_by(models.Fixture.match_date.asc()).all()

    result_data = []
    for fix in fixtures:
        pred = db.query(models.Prediction).filter(models.Prediction.fixture_id == fix.id).first()
        if not pred:
            pred = PoissonPredictionEngine.predict_fixture(db, fix.id)

        top_scorelines = []
        if pred and pred.top_scorelines_json:
            try:
                top_scorelines = json.loads(pred.top_scorelines_json)
            except Exception:
                top_scorelines = []

        h_xg = round(float(pred.predicted_home_score), 2) if (pred and pred.predicted_home_score is not None) else 1.45
        a_xg = round(float(pred.predicted_away_score), 2) if (pred and pred.predicted_away_score is not None) else 1.15
        pois = PoissonPredictionEngine.calculate_poisson_probabilities(h_xg, a_xg)

        btts_prob = round((1.0 - (2.718281828459045 ** -h_xg)) * (1.0 - (2.718281828459045 ** -a_xg)), 4)

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
            "status": fix.status,
            "venue": fix.venue,
            "home_score": getattr(fix, "home_score", None),
            "away_score": getattr(fix, "away_score", None),
            "live_clock": getattr(fix, "live_clock", None),
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
                "home_win_probability": pois.get("home_win_probability", 0.0),
                "draw_probability": pois.get("draw_probability", 0.0),
                "away_win_probability": pois.get("away_win_probability", 0.0),
                "over_0_5_probability": pois.get("over_0_5_probability", 0.0),
                "over_1_5_probability": pois.get("over_1_5_probability", 0.0),
                "over_2_5_probability": pois.get("over_2_5_probability", 0.0),
                "over_3_5_probability": pois.get("over_3_5_probability", 0.0),
                "under_2_5_probability": pois.get("under_2_5_probability", 0.0),
                "btts_probability": btts_prob,
                
                # Home Team Specific Goal Thresholds
                "home_over_0_5_probability": pois.get("home_over_0_5_probability", 0.0),
                "home_over_1_5_probability": pois.get("home_over_1_5_probability", 0.0),
                "home_over_2_5_probability": pois.get("home_over_2_5_probability", 0.0),

                # Away Team Specific Goal Thresholds
                "away_over_0_5_probability": pois.get("away_over_0_5_probability", 0.0),
                "away_over_1_5_probability": pois.get("away_over_1_5_probability", 0.0),
                "away_over_2_5_probability": pois.get("away_over_2_5_probability", 0.0),

                # Half Breakdown (1st Half / 2nd Half xG & Over 0.5/1.5)
                "first_half_xg": pois.get("first_half_xg", 0.0),
                "first_half_over_0_5_probability": pois.get("first_half_over_0_5_probability", 0.0),
                "first_half_over_1_5_probability": pois.get("first_half_over_1_5_probability", 0.0),

                "second_half_xg": pois.get("second_half_xg", 0.0),
                "second_half_over_0_5_probability": pois.get("second_half_over_0_5_probability", 0.0),
                "second_half_over_1_5_probability": pois.get("second_half_over_1_5_probability", 0.0),

                "most_likely_score": pois.get("most_likely_score", "1-1"),
                "top_scorelines": top_scorelines or pois.get("top_5_scorelines", [])
            }
        })

    return {"status": "ok", "count": len(result_data), "data": result_data}


@app.get("/api/fixtures/finished")
def get_finished_fixtures(db: Session = Depends(get_db)):
    """
    Retrieve completed match results with final scores and Over 1.5 goal prediction outcomes.
    """
    fixtures = db.query(models.Fixture).filter(
        models.Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"])
    ).order_by(models.Fixture.match_date.desc()).limit(150).all()

    result_data = []
    for fix in fixtures:
        pred = db.query(models.Prediction).filter(models.Prediction.fixture_id == fix.id).first()
        if not pred:
            pred = PoissonPredictionEngine.predict_fixture(db, fix.id)

        top_scorelines = []
        if pred and pred.top_scorelines_json:
            try:
                top_scorelines = json.loads(pred.top_scorelines_json)
            except Exception:
                top_scorelines = []

        h_xg = round(float(pred.predicted_home_score), 2) if (pred and pred.predicted_home_score is not None) else 1.45
        a_xg = round(float(pred.predicted_away_score), 2) if (pred and pred.predicted_away_score is not None) else 1.15
        pois = PoissonPredictionEngine.calculate_poisson_probabilities(h_xg, a_xg)

        btts_prob = round((1.0 - (2.718281828459045 ** -h_xg)) * (1.0 - (2.718281828459045 ** -a_xg)), 4)

        match_date_str = None
        if fix.match_date:
            if isinstance(fix.match_date, datetime):
                dt_obj = fix.match_date if fix.match_date.tzinfo else fix.match_date.replace(tzinfo=timezone.utc)
                match_date_str = dt_obj.isoformat()
            else:
                s = str(fix.match_date).replace(" ", "T")
                match_date_str = s if (s.endswith("Z") or "+" in s[10:] or "-" in s[10:]) else s + "Z"

        h_score = fix.home_score if fix.home_score is not None else 0
        a_score = fix.away_score if fix.away_score is not None else 0
        total_actual_goals = h_score + a_score

        result_data.append({
            "id": fix.id,
            "external_id": fix.external_id,
            "match_date": match_date_str,
            "status": fix.status or "FINISHED",
            "venue": fix.venue,
            "home_score": h_score,
            "away_score": a_score,
            "total_goals": total_actual_goals,
            "over_1_5_hit": total_actual_goals >= 2,
            "over_2_5_hit": total_actual_goals >= 3,
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
                "home_win_probability": pois.get("home_win_probability", 0.0),
                "draw_probability": pois.get("draw_probability", 0.0),
                "away_win_probability": pois.get("away_win_probability", 0.0),
                "over_0_5_probability": pois.get("over_0_5_probability", 0.0),
                "over_1_5_probability": pois.get("over_1_5_probability", 0.0),
                "over_2_5_probability": pois.get("over_2_5_probability", 0.0),
                "over_3_5_probability": pois.get("over_3_5_probability", 0.0),
                "under_2_5_probability": pois.get("under_2_5_probability", 0.0),
                "btts_probability": btts_prob,
                "home_over_0_5_probability": pois.get("home_over_0_5_probability", 0.0),
                "home_over_1_5_probability": pois.get("home_over_1_5_probability", 0.0),
                "home_over_2_5_probability": pois.get("home_over_2_5_probability", 0.0),
                "away_over_0_5_probability": pois.get("away_over_0_5_probability", 0.0),
                "away_over_1_5_probability": pois.get("away_over_1_5_probability", 0.0),
                "away_over_2_5_probability": pois.get("away_over_2_5_probability", 0.0),
                "first_half_xg": pois.get("first_half_xg", 0.0),
                "first_half_over_0_5_probability": pois.get("first_half_over_0_5_probability", 0.0),
                "first_half_over_1_5_probability": pois.get("first_half_over_1_5_probability", 0.0),
                "second_half_xg": pois.get("second_half_xg", 0.0),
                "second_half_over_0_5_probability": pois.get("second_half_over_0_5_probability", 0.0),
                "second_half_over_1_5_probability": pois.get("second_half_over_1_5_probability", 0.0),
                "most_likely_score": pois.get("most_likely_score", "1-1"),
                "top_scorelines": top_scorelines or pois.get("top_5_scorelines", [])
            }
        })

    return {"status": "ok", "count": len(result_data), "data": result_data}




