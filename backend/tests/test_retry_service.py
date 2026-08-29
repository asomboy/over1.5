import os
import sys
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services.retry_service import RetryService, ErrorCategory


class TestRetryService(unittest.TestCase):

    def test_classify_error(self):
        """Validates correct error categorization."""
        self.assertEqual(RetryService.classify_error(Exception("401 Unauthorized")), ErrorCategory.AUTHENTICATION)
        self.assertEqual(RetryService.classify_error(Exception("404 Not Found")), ErrorCategory.NOT_FOUND)
        self.assertEqual(RetryService.classify_error(Exception("429 Too Many Requests")), ErrorCategory.RATE_LIMITED)
        self.assertEqual(RetryService.classify_error(Exception("Connection timeout")), ErrorCategory.TRANSIENT)

    def test_retry_transient_success(self):
        """Retries transient failures until success."""
        attempts = 0

        def flaky_func():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ConnectionError("Temporary network reset")
            return "SUCCESS"

        res = RetryService.execute_with_retry(flaky_func, max_attempts=4, initial_backoff=0.01)
        self.assertEqual(res, "SUCCESS")
        self.assertEqual(attempts, 3)

    def test_permanent_error_abort(self):
        """Permanent errors (e.g. 401 unauthorized) abort immediately without retry."""
        attempts = 0

        def auth_fail_func():
            nonlocal attempts
            attempts += 1
            raise ValueError("401 Invalid API Key")

        with self.assertRaises(ValueError):
            RetryService.execute_with_retry(auth_fail_func, max_attempts=4, initial_backoff=0.01)

        self.assertEqual(attempts, 1) # Must only run once!


if __name__ == "__main__":
    unittest.main()
