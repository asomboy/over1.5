import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from models import Base, Fixture, LiveMatchState, MatchStatistics, League, Team
from services.live_provider_service import LiveProviderAdapterService


class TestLiveProviderAdapterService(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        # Seed test league, teams, fixture
        self.league = League(id=1, name="English Premier League", country="England")
        self.home_team = Team(id=1, name="Arsenal", league_id=1)
        self.away_team = Team(id=2, name="Chelsea", league_id=1)
        self.db.add_all([self.league, self.home_team, self.away_team])
        self.db.commit()

        self.fixture = Fixture(
            id=101,
            league_id=1,
            home_team_id=1,
            away_team_id=2,
            external_id="ESPN-FIX-401879301",
            match_date=datetime.now(timezone.utc).replace(tzinfo=None),
            status="LIVE"
        )
        self.db.add(self.fixture)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)

    def test_extract_provider_event_id(self):
        """Test extraction of clean numeric event ID from various external_id formats."""
        f1 = Fixture(external_id="ESPN-FIX-401879301")
        self.assertEqual(LiveProviderAdapterService.extract_provider_event_id(f1), "401879301")

        f2 = Fixture(external_id="ESPN-998877")
        self.assertEqual(LiveProviderAdapterService.extract_provider_event_id(f2), "998877")

        f3 = Fixture(external_id="401234567")
        self.assertEqual(LiveProviderAdapterService.extract_provider_event_id(f3), "401234567")

        f4 = Fixture(external_id="")
        self.assertIsNone(LiveProviderAdapterService.extract_provider_event_id(f4))

    def test_validate_fixture_identity_match(self):
        """Test fixture identity validation succeeds when teams match."""
        payload = {
            "header": {
                "competitions": [{
                    "competitors": [
                        {"homeAway": "home", "team": {"name": "Arsenal"}},
                        {"homeAway": "away", "team": {"name": "Chelsea FC"}}
                    ]
                }]
            }
        }
        valid, err = LiveProviderAdapterService.validate_fixture_identity(self.fixture, payload)
        self.assertTrue(valid)
        self.assertIsNone(err)

    def test_validate_fixture_identity_mismatch_rejected(self):
        """Test fixture identity validation rejects payload when teams do not match."""
        payload = {
            "header": {
                "competitions": [{
                    "competitors": [
                        {"homeAway": "home", "team": {"name": "Barcelona"}},
                        {"homeAway": "away", "team": {"name": "Real Madrid"}}
                    ]
                }]
            }
        }
        valid, err = LiveProviderAdapterService.validate_fixture_identity(self.fixture, payload)
        self.assertFalse(valid)
        self.assertIn("Home team mismatch", err)

    def test_parse_events_timeline(self):
        """Test parsing chronological events and deterministic fingerprints."""
        comp = {
            "details": [
                {
                    "clock": {"value": 720.0, "displayValue": "12'"},
                    "scoringPlay": True,
                    "team": {"displayName": "Arsenal"},
                    "participants": [{"athlete": {"displayName": "Bukayo Saka"}}]
                },
                {
                    "clock": {"value": 2100.0, "displayValue": "35'"},
                    "yellowCard": True,
                    "team": {"displayName": "Chelsea"},
                    "participants": [{"athlete": {"displayName": "Enzo Fernandez"}}]
                }
            ]
        }
        events = LiveProviderAdapterService.parse_events_timeline(comp, "Arsenal", "Chelsea")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["type"], "GOAL")
        self.assertEqual(events[0]["player"], "Bukayo Saka")
        self.assertEqual(events[0]["side"], "home")
        self.assertEqual(events[0]["display_clock"], "12'")
        self.assertTrue(len(events[0]["id"]) > 0)

        self.assertEqual(events[1]["type"], "YELLOW_CARD")
        self.assertEqual(events[1]["player"], "Enzo Fernandez")
        self.assertEqual(events[1]["side"], "away")

    def test_fetch_live_summary_mock_success(self):
        """Test full live summary fetch with simulated ESPN payload."""
        mock_payload = {
            "header": {
                "competitions": [{
                    "status": {
                        "clock": 3120.0,
                        "displayClock": "52'",
                        "period": 2,
                        "type": {"name": "STATUS_SECOND_HALF", "state": "in", "completed": False, "detail": "52'"}
                    },
                    "competitors": [
                        {"homeAway": "home", "score": "2", "team": {"name": "Arsenal"}},
                        {"homeAway": "away", "score": "1", "team": {"name": "Chelsea"}}
                    ],
                    "details": [
                        {
                            "clock": {"value": 720.0, "displayValue": "12'"},
                            "scoringPlay": True,
                            "team": {"displayName": "Arsenal"},
                            "participants": [{"athlete": {"displayName": "Bukayo Saka"}}]
                        }
                    ]
                }]
            },
            "boxscore": {
                "teams": [
                    {
                        "team": {"name": "Arsenal"},
                        "statistics": [
                            {"name": "totalShots", "displayValue": "11"},
                            {"name": "shotsOnTarget", "displayValue": "5"},
                            {"name": "wonCorners", "displayValue": "6"},
                            {"name": "possessionPct", "displayValue": "58.4"},
                            {"name": "foulsCommitted", "displayValue": "4"}
                        ]
                    },
                    {
                        "team": {"name": "Chelsea"},
                        "statistics": [
                            {"name": "totalShots", "displayValue": "7"},
                            {"name": "shotsOnTarget", "displayValue": "2"},
                            {"name": "wonCorners", "displayValue": "3"},
                            {"name": "possessionPct", "displayValue": "41.6"},
                            {"name": "foulsCommitted", "displayValue": "9"}
                        ]
                    }
                ]
            }
        }

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_payload
        mock_client.get.return_value = mock_resp

        res = LiveProviderAdapterService.fetch_live_summary(self.db, self.fixture.id, client=mock_client)

        self.assertEqual(res["status"], "LIVE")
        self.assertFalse(res["is_completed"])
        self.assertEqual(res["score"]["home"], 2)
        self.assertEqual(res["score"]["away"], 1)
        self.assertEqual(res["minute"], 52)
        self.assertEqual(res["display_clock"], "52'")
        self.assertEqual(res["period"], "2H")

        # Verified observed statistics
        stats = res["statistics"]
        self.assertEqual(stats["shots"]["home"], 11.0)
        self.assertEqual(stats["shots"]["away"], 7.0)
        self.assertEqual(stats["shots_on_target"]["home"], 5.0)
        self.assertEqual(stats["corners"]["home"], 6.0)
        self.assertEqual(stats["possession"]["home"], 58.4)

        # Database state updated
        live_db = self.db.query(LiveMatchState).filter(LiveMatchState.fixture_id == self.fixture.id).first()
        self.assertIsNotNone(live_db)
        self.assertEqual(live_db.minute, 52)
        self.assertEqual(live_db.home_score, 2)
        self.assertEqual(live_db.away_score, 1)
        self.assertEqual(live_db.home_shots, 11)
        self.assertEqual(live_db.home_corners, 6)

    def test_completed_match_transitions_fixture(self):
        """Test completed match (FT) updates Fixture status to FINISHED and persists MatchStatistics."""
        mock_payload = {
            "header": {
                "competitions": [{
                    "status": {
                        "clock": 5400.0,
                        "displayClock": "90'",
                        "period": 2,
                        "type": {"name": "STATUS_FULL_TIME", "state": "post", "completed": True, "detail": "FT"}
                    },
                    "competitors": [
                        {"homeAway": "home", "score": "3", "team": {"name": "Arsenal"}},
                        {"homeAway": "away", "score": "1", "team": {"name": "Chelsea"}}
                    ],
                    "details": []
                }]
            },
            "boxscore": {
                "teams": [
                    {
                        "team": {"name": "Arsenal"},
                        "statistics": [{"name": "totalShots", "displayValue": "14"}, {"name": "wonCorners", "displayValue": "7"}]
                    },
                    {
                        "team": {"name": "Chelsea"},
                        "statistics": [{"name": "totalShots", "displayValue": "8"}, {"name": "wonCorners", "displayValue": "4"}]
                    }
                ]
            }
        }

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_payload
        mock_client.get.return_value = mock_resp

        res = LiveProviderAdapterService.fetch_live_summary(self.db, self.fixture.id, client=mock_client)

        self.assertEqual(res["status"], "FINISHED")
        self.assertTrue(res["is_completed"])
        self.assertEqual(self.fixture.status, "FINISHED")
        self.assertEqual(self.fixture.home_score, 3)
        self.assertEqual(self.fixture.away_score, 1)

        # MatchStatistics row created
        m_stat = self.db.query(MatchStatistics).filter(MatchStatistics.fixture_id == self.fixture.id).first()
        self.assertIsNotNone(m_stat)
        self.assertEqual(m_stat.home_shots, 14)
        self.assertEqual(m_stat.home_corners, 7)


if __name__ == "__main__":
    unittest.main()
