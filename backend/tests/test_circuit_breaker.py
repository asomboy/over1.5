import os
import sys
import unittest
import time

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services.circuit_breaker_service import CircuitBreaker, CircuitState


class TestCircuitBreaker(unittest.TestCase):

    def test_circuit_state_transitions(self):
        """Validates CLOSED -> OPEN -> HALF_OPEN -> CLOSED transitions."""
        cb = CircuitBreaker("test_provider", failure_threshold=3, recovery_timeout_sec=0.1, half_open_success_threshold=2)

        # 1. Starts CLOSED
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertTrue(cb.can_execute())

        # 2. 3 failures -> Transitions to OPEN
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, CircuitState.CLOSED)
        cb.record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertFalse(cb.can_execute())

        # 3. Wait for recovery timeout -> Transitions to HALF_OPEN on probe
        time.sleep(0.12)
        self.assertTrue(cb.can_execute())
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

        # 4. Probe success 1
        cb.record_success()
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

        # 5. Probe success 2 -> Transitions back to CLOSED
        cb.record_success()
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertTrue(cb.can_execute())


if __name__ == "__main__":
    unittest.main()
