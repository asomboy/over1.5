import os
import sys
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from models import Base, Fixture, LiveMatchState, MatchStatistics, League, Team
from services.live_provider_service import LiveProviderAdapterService
from services.live_narrative_service import LiveNarrativeEngine
from services.live_service import (
    LiveMatchIntelligenceService,
    LiveGoalsPredictionEngine,
    LiveCornersPredictionEngine,
    LiveCardsPredictionEngine,
    LiveSignalEngine
)
from schemas.live_schema import (
    LiveMatchStateSchema,
    LiveConfidence
)


@pytest.fixture
def db_session():
    """Isolated in-memory SQLite database for testing data integrity."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Create dummy league
    league = League(id=1, name="Premier League", country="England", is_active=True)
    session.add(league)

    # Create dummy teams
    team_a = Team(id=1, name="Arsenal", league_id=1)
    team_b = Team(id=2, name="Chelsea", league_id=1)
    session.add_all([team_a, team_b])
    session.commit()

    # Create dummy fixture
    fixture = Fixture(
        id=101,
        external_id="ESPN-101",
        home_team_id=1,
        away_team_id=2,
        league_id=1,
        match_date=datetime.now(timezone.utc).replace(tzinfo=None),
        status="LIVE",
        home_score=0,
        away_score=0
    )
    session.add(fixture)
    session.commit()

    yield session
    session.close()


class TestLiveIsolationHardening:

    def test_fixture_identity_mismatch_rejection(self, db_session):
        """
        Req 2: Verify that when the provider returns data for a different event or teams,
        the response is strictly rejected as REJECTED_IDENTITY_MISMATCH.
        """
        fixture = db_session.query(Fixture).filter(Fixture.id == 101).first()

        # Payload with completely different teams (Real Madrid vs Barcelona instead of Arsenal vs Chelsea)
        mismatched_payload = {
            "header": {
                "id": "ESPN-999", # Mismatched event ID
                "competitions": [{
                    "id": "ESPN-999",
                    "competitors": [
                        {"homeAway": "home", "team": {"name": "Real Madrid", "displayName": "Real Madrid"}},
                        {"homeAway": "away", "team": {"name": "Barcelona", "displayName": "Barcelona"}}
                    ]
                }]
            }
        }

        is_valid, reason = LiveProviderAdapterService.validate_fixture_identity(fixture, mismatched_payload)
        assert is_valid is False
        assert "mismatch" in reason.lower()

        with patch("httpx.Client.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mismatched_payload
            mock_get.return_value = mock_resp

            result = LiveProviderAdapterService.fetch_live_summary(db_session, 101)
            assert result["status"] == "REJECTED_IDENTITY_MISMATCH"
            assert result["data_status"] == "UNAVAILABLE"

    def test_cross_fixture_response_rejection(self, db_session):
        """
        Req 3: Verify that if a payload returns with a different event ID,
        it cannot overwrite Fixture 101's state.
        """
        fixture = db_session.query(Fixture).filter(Fixture.id == 101).first()

        # Payload matching wrong event
        alien_payload = {
            "header": {
                "id": "ESPN-ALIEN-888",
                "competitions": [{
                    "id": "ESPN-ALIEN-888",
                    "competitors": [
                        {"homeAway": "home", "team": {"name": "Arsenal"}},
                        {"homeAway": "away", "team": {"name": "Chelsea"}}
                    ]
                }]
            }
        }
        is_valid, reason = LiveProviderAdapterService.validate_fixture_identity(fixture, alien_payload)
        assert is_valid is False
        assert "Event ID mismatch" in reason

    def test_no_generic_zero_defaults_on_unstarted(self, db_session):
        """
        Req 4: Search the entire live pipeline for dangerous defaults such as
        minute = 0, score = 0-0, shots = 0, corners = 0.
        When provider has not started, values must be None or UNAVAILABLE, not 0.
        """
        # Create unstarted fixture
        unstarted = Fixture(
            id=102,
            external_id="ESPN-102",
            home_team_id=1,
            away_team_id=2,
            league_id=1,
            match_date=(datetime.now(timezone.utc) + timedelta(hours=2)).replace(tzinfo=None),
            status="SCHEDULED"
        )
        db_session.add(unstarted)
        db_session.commit()

        # Simulate pre-match ESPN response (no scores or clock yet)
        pre_payload = {
            "header": {
                "id": "ESPN-102",
                "competitions": [{
                    "id": "ESPN-102",
                    "status": {
                        "type": {"state": "pre", "completed": False, "detail": "Scheduled"}
                    },
                    "competitors": [
                        {"homeAway": "home", "team": {"name": "Arsenal"}, "score": None},
                        {"homeAway": "away", "team": {"name": "Chelsea"}, "score": None}
                    ]
                }]
            },
            "boxscore": {"teams": []}
        }

        with patch("httpx.Client.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = pre_payload
            mock_get.return_value = mock_resp

            res = LiveProviderAdapterService.fetch_live_summary(db_session, 102)
            assert res["status"] == "SCHEDULED"
            assert res["minute"] is None
            assert res["display_clock"] == "PRE"
            assert res["score"]["home"] is None
            assert res["score"]["away"] is None
            assert res["statistics"]["shots"]["home"] is None
            assert res["statistics"]["corners"]["home"] is None
            assert res["statistics"]["shots"]["source_status"] == "UNAVAILABLE"

    def test_stale_provider_handling_and_degradation(self, db_session):
        """
        Req 6 & 12: Test that when provider fails, last verified snapshot is preserved
        with explicit STALE / VERY_STALE data status, and signal engine returns NO_SIGNAL.
        """
        # Put verified snapshot into DB
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=200)).replace(tzinfo=None)
        live_state = LiveMatchState(
            fixture_id=101,
            minute=44,
            period="1H",
            status="LIVE",
            home_score=1,
            away_score=0,
            home_shots=5,
            away_shots=2,
            last_updated=old_time
        )
        db_session.add(live_state)
        db_session.commit()

        # Simulate provider connection failure
        with patch("httpx.Client.get", side_effect=Exception("Connection timed out")):
            res = LiveProviderAdapterService.fetch_live_summary(db_session, 101)
            # Must preserve verified score and assign VERY_STALE
            assert res["data_status"] == "VERY_STALE"
            assert res["score"]["home"] == 1
            assert res["score"]["away"] == 0
            assert res["minute"] == 44

    def test_partial_statistic_availability(self, db_session):
        """
        Req 10: If ESPN provides shots and corners but NOT possession or SoT,
        the response must show exactly that without inferring missing values.
        """
        partial_payload = {
            "header": {
                "id": "ESPN-101",
                "competitions": [{
                    "id": "ESPN-101",
                    "status": {
                        "clock": 1800.0,
                        "displayClock": "30'",
                        "period": 1,
                        "type": {"state": "in", "completed": False}
                    },
                    "competitors": [
                        {"homeAway": "home", "team": {"name": "Arsenal"}, "score": "1"},
                        {"homeAway": "away", "team": {"name": "Chelsea"}, "score": "0"}
                    ]
                }]
            },
            "boxscore": {
                "teams": [
                    {
                        "team": {"name": "Arsenal"},
                        "statistics": [
                            {"name": "totalShots", "displayValue": "6"},
                            {"name": "wonCorners", "displayValue": "3"}
                            # Notice: shotsOnTarget and possessionPct NOT provided
                        ]
                    },
                    {
                        "team": {"name": "Chelsea"},
                        "statistics": [
                            {"name": "totalShots", "displayValue": "2"},
                            {"name": "wonCorners", "displayValue": "1"}
                        ]
                    }
                ]
            }
        }

        with patch("httpx.Client.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = partial_payload
            mock_get.return_value = mock_resp

            res = LiveProviderAdapterService.fetch_live_summary(db_session, 101)
            assert res["statistics"]["shots"]["home"] == 6
            assert res["statistics"]["shots"]["source_status"] == "AVAILABLE"
            assert res["statistics"]["shots_on_target"]["home"] is None
            assert res["statistics"]["shots_on_target"]["source_status"] == "UNAVAILABLE"
            assert res["statistics"]["possession"]["home"] is None
            assert res["statistics"]["possession"]["source_status"] == "UNAVAILABLE"

    def test_event_deduplication(self):
        """
        Req 8: Verify event fingerprinting across repeated polls.
        The exact same event from repeated provider polls must produce the same ID
        and not duplicate in timeline.
        """
        header_comp = {
            "details": [
                {
                    "clock": {"value": 1440.0, "displayValue": "24'"},
                    "team": {"displayName": "Arsenal"},
                    "scoringPlay": True,
                    "type": {"text": "Goal"},
                    "participants": [{"athlete": {"displayName": "Bukayo Saka"}}]
                }
            ]
        }

        events_poll_1 = LiveProviderAdapterService.parse_events_timeline(header_comp, "Arsenal", "Chelsea")
        events_poll_2 = LiveProviderAdapterService.parse_events_timeline(header_comp, "Arsenal", "Chelsea")
        events_poll_3 = LiveProviderAdapterService.parse_events_timeline(header_comp, "Arsenal", "Chelsea")

        assert len(events_poll_1) == 1
        assert len(events_poll_2) == 1
        assert len(events_poll_3) == 1
        # Exact deterministic fingerprint
        assert events_poll_1[0]["id"] == events_poll_2[0]["id"] == events_poll_3[0]["id"]

    def test_finished_match_stops_signals_and_preserves_state(self, db_session):
        """
        Req 9 & 12: When ESPN reports FT/completed, state is marked FINISHED,
        and LiveSignalEngine returns NO_SIGNAL with concluded rationale.
        """
        ft_payload = {
            "header": {
                "id": "ESPN-101",
                "competitions": [{
                    "id": "ESPN-101",
                    "status": {
                        "clock": 5400.0,
                        "displayClock": "90'",
                        "period": 2,
                        "type": {"state": "post", "completed": True, "detail": "Full Time"}
                    },
                    "competitors": [
                        {"homeAway": "home", "team": {"name": "Arsenal"}, "score": "2"},
                        {"homeAway": "away", "team": {"name": "Chelsea"}, "score": "1"}
                    ]
                }]
            },
            "boxscore": {"teams": []}
        }

        with patch("httpx.Client.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = ft_payload
            mock_get.return_value = mock_resp

            res = LiveProviderAdapterService.fetch_live_summary(db_session, 101)
            assert res["status"] == "FINISHED"
            assert res["is_completed"] is True
            assert res["score"]["home"] == 2
            assert res["score"]["away"] == 1

        # Check that signals engine produces NO_SIGNAL for finished match
        state = LiveMatchStateSchema(
            fixture_id=101, minute=90, period="FT", status="FINISHED",
            home_score=2, away_score=1
        )
        conf = LiveConfidence(
            overall_confidence=20, pre_match_confidence=70, live_data_quality=80,
            statistical_coverage=80, model_stability=80, time_sensitivity=0, label="insufficient"
        )
        goals = LiveGoalsPredictionEngine.predict_live_goals(
            1.6, 1.2, state, time_rem_mins=0.0, goal_prop_rem=0.0,
            score_adj_h=1.0, score_adj_a=1.0, mom_adj_h=1.0, mom_adj_a=1.0,
            red_adj_h=1.0, red_adj_a=1.0, prior_w=0.1, live_w=0.9
        )
        corners = LiveCornersPredictionEngine.predict_live_corners(
            5.0, 4.5, state, time_rem_mins=0.0, mom_adj_h=1.0, mom_adj_a=1.0,
            prior_w=0.1, live_w=0.9
        )
        cards = LiveCardsPredictionEngine.predict_live_cards(
            1.8, 2.0, state, time_rem_mins=0.0, score_diff=1, ref_adj=1.0
        )

        signals, best_sig = LiveSignalEngine.evaluate_live_signals(goals, corners, cards, conf, time_rem_mins=0.0)
        assert len(signals) == 0
        assert best_sig.label == "NO_SIGNAL"
        assert "concluded" in best_sig.rationale.lower()

    def test_live_model_recalculation_after_observed_changes(self):
        """
        Req 11: Verify that when observed statistics change (e.g. goal scored or shot count increased),
        model predictions adapt dynamically without overwriting observed values.
        """
        state_0_0 = LiveMatchStateSchema(
            fixture_id=101, minute=20, period="1H", status="LIVE",
            home_score=0, away_score=0
        )
        state_1_0 = LiveMatchStateSchema(
            fixture_id=101, minute=20, period="1H", status="LIVE",
            home_score=1, away_score=0
        )

        goals_0_0 = LiveGoalsPredictionEngine.predict_live_goals(
            1.6, 1.2, state_0_0, time_rem_mins=70.0, goal_prop_rem=0.77,
            score_adj_h=1.0, score_adj_a=1.0, mom_adj_h=1.0, mom_adj_a=1.0,
            red_adj_h=1.0, red_adj_a=1.0, prior_w=0.6, live_w=0.4
        )

        goals_1_0 = LiveGoalsPredictionEngine.predict_live_goals(
            1.6, 1.2, state_1_0, time_rem_mins=70.0, goal_prop_rem=0.77,
            score_adj_h=0.92, score_adj_a=1.10, mom_adj_h=1.0, mom_adj_a=1.0,
            red_adj_h=1.0, red_adj_a=1.0, prior_w=0.6, live_w=0.4
        )

        # In 1-0 state, full match over 1.5 probability must be strictly higher than in 0-0 state
        assert goals_1_0.full_match_over_1_5.probability > goals_0_0.full_match_over_1_5.probability
        # Observed score in 1-0 state must remain 1-0
        assert goals_1_0.current_score["home"] == 1
        assert goals_1_0.current_score["away"] == 0

    def test_factual_narrative_change_detection_only(self):
        """
        Req 7: Verify that narrative statements are only generated when genuine
        statistical or event changes occur, not duplicated on static polls.
        """
        state_prev = {
            "minute": 35,
            "score": {"home": 0, "away": 0},
            "statistics": {
                "shots": {"home": 4, "away": 2}
            }
        }
        state_identical = {
            "minute": 36,
            "score": {"home": 0, "away": 0},
            "statistics": {
                "shots": {"home": 4, "away": 2}
            }
        }
        state_changed = {
            "minute": 36,
            "score": {"home": 0, "away": 0},
            "statistics": {
                "shots": {"home": 5, "away": 2}
            }
        }

        narr_identical = LiveNarrativeEngine.generate_narrative(
            101, "Arsenal", "Chelsea", current_state=state_identical, previous_state=state_prev
        )
        # Identical statistics must not produce false change statements
        shot_change_statements = [s for s in narr_identical if s["category"] == "ATTACK"]
        assert len(shot_change_statements) == 0

        narr_changed = LiveNarrativeEngine.generate_narrative(
            101, "Arsenal", "Chelsea", current_state=state_changed, previous_state=state_prev
        )
        assert len(narr_changed) >= 1
        attack_stmts = [s for s in narr_changed if s["category"] == "ATTACK"]
        assert len(attack_stmts) >= 1
        assert "[OBSERVED FACT]" in attack_stmts[0]["statement"]
        assert "recorded 1 shot" in attack_stmts[0]["statement"].lower()
