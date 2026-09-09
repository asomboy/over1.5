import os
import sys
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services.live_narrative_service import LiveNarrativeEngine


class TestLiveNarrativeEngine(unittest.TestCase):

    def test_new_goal_detected(self):
        """Test narrative detects new goal when score changes."""
        prev = {
            "minute": 20,
            "score": {"home": 0, "away": 0},
            "statistics": {}
        }
        curr = {
            "minute": 24,
            "display_clock": "24'",
            "score": {"home": 1, "away": 0},
            "statistics": {},
            "retrieved_at": "2026-09-09T18:00:00Z"
        }

        narr = LiveNarrativeEngine.generate_narrative(1, "Barcelona", "Real Madrid", curr, prev)
        self.assertTrue(any("GOAL!" in item["statement"] and "Barcelona scores" in item["statement"] for item in narr))

    def test_shot_increase_detected(self):
        """Test narrative detects increase in shots between intervals."""
        prev = {
            "minute": 30,
            "score": {"home": 1, "away": 0},
            "statistics": {"shots": {"home": 4, "away": 2}}
        }
        curr = {
            "minute": 35,
            "display_clock": "35'",
            "score": {"home": 1, "away": 0},
            "statistics": {"shots": {"home": 7, "away": 2}},
            "retrieved_at": "2026-09-09T18:05:00Z"
        }

        narr = LiveNarrativeEngine.generate_narrative(1, "Barcelona", "Real Madrid", curr, prev)
        self.assertTrue(any("Barcelona has recorded 3 shots" in item["statement"] for item in narr))

    def test_red_card_detected(self):
        """Test narrative detects red card dismissals."""
        prev = {
            "minute": 55,
            "score": {"home": 1, "away": 1},
            "statistics": {"cards": {"home_red": 0, "away_red": 0}}
        }
        curr = {
            "minute": 58,
            "display_clock": "58'",
            "score": {"home": 1, "away": 1},
            "statistics": {"cards": {"home_red": 0, "away_red": 1}},
            "retrieved_at": "2026-09-09T18:15:00Z"
        }

        narr = LiveNarrativeEngine.generate_narrative(1, "Barcelona", "Real Madrid", curr, prev)
        self.assertTrue(any("RED CARD!" in item["statement"] and "Real Madrid is reduced to 10 players" in item["statement"] for item in narr))

    def test_possession_dominance_statement(self):
        """Test narrative highlights possession dominance when team holds >= 60%."""
        curr = {
            "minute": 40,
            "display_clock": "40'",
            "score": {"home": 0, "away": 0},
            "statistics": {
                "possession": {"home": 68.5, "away": 31.5}
            },
            "retrieved_at": "2026-09-09T18:10:00Z"
        }

        narr = LiveNarrativeEngine.generate_narrative(1, "Barcelona", "Real Madrid", curr, None)
        self.assertTrue(any("Barcelona currently dominates possession with 68.5%" in item["statement"] for item in narr))

    def test_no_material_change_fallback(self):
        """Test narrative outputs stable fallback when nothing material changed."""
        prev = {
            "minute": 15,
            "score": {"home": 0, "away": 0},
            "statistics": {}
        }
        curr = {
            "minute": 16,
            "display_clock": "16'",
            "score": {"home": 0, "away": 0},
            "statistics": {},
            "retrieved_at": "2026-09-09T18:01:00Z"
        }

        narr = LiveNarrativeEngine.generate_narrative(1, "Barcelona", "Real Madrid", curr, prev)
        self.assertEqual(len(narr), 1)
        self.assertEqual(narr[0]["statement"], "No material verified change since the previous update.")


if __name__ == "__main__":
    unittest.main()
