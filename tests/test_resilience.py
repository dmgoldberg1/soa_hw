import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import grpc

from booking.resilience import CircuitBreaker, CircuitOpen, Resilience


class Failure(grpc.RpcError):
    def __init__(self, status):
        self.status = status

    def code(self):
        return self.status


class ResilienceTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, CB_FAILURE_THRESHOLD="2",
                              CB_RESET_TIMEOUT="10", CB_WINDOW_SECONDS="30")
        self.env.start()
        self.addCleanup(self.env.stop)

    def call(self, status=None):
        call = Mock()
        if status:
            call.result.side_effect = Failure(status)
        return call

    @patch("booking.resilience.time.sleep")
    def test_transient_errors_use_three_attempts_with_exponential_backoff(self, sleep):
        for status in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
            with self.subTest(status=status):
                interceptor = Resilience()
                continuation = Mock(return_value=self.call(status))
                interceptor.intercept_unary_unary(continuation, SimpleNamespace(method="GetFlight"), None)
                self.assertEqual(continuation.call_count, 3)
                self.assertEqual([args.args[0] for args in sleep.call_args_list[-2:]], [0.1, 0.2])

    def test_business_errors_are_not_retried_or_counted_as_outage(self):
        for status in (grpc.StatusCode.INVALID_ARGUMENT, grpc.StatusCode.NOT_FOUND,
                       grpc.StatusCode.RESOURCE_EXHAUSTED, grpc.StatusCode.UNAUTHENTICATED):
            with self.subTest(status=status):
                interceptor = Resilience()
                continuation = Mock(return_value=self.call(status))
                interceptor.intercept_unary_unary(continuation, SimpleNamespace(method="ReserveSeats"), None)
                self.assertEqual(continuation.call_count, 1)
                self.assertEqual(len(interceptor.breaker.failures), 0)

    @patch("booking.resilience.time.sleep")
    def test_retry_recovers_and_open_circuit_makes_no_network_calls(self, sleep):
        interceptor = Resilience()
        continuation = Mock(side_effect=[self.call(grpc.StatusCode.UNAVAILABLE), self.call()])
        interceptor.intercept_unary_unary(continuation, SimpleNamespace(method="GetFlight"), None)
        self.assertEqual(continuation.call_count, 2)
        continuation = Mock(return_value=self.call(grpc.StatusCode.UNAVAILABLE))
        for _ in range(2):
            interceptor.intercept_unary_unary(continuation, SimpleNamespace(method="GetFlight"), None)
        self.assertEqual(interceptor.breaker.state, "OPEN")
        self.assertEqual(continuation.call_count, 6)
        with self.assertRaises(CircuitOpen):
            interceptor.intercept_unary_unary(continuation, SimpleNamespace(method="GetFlight"), None)
        self.assertEqual(continuation.call_count, 6)

    def test_only_one_half_open_probe_then_reopen_or_close(self):
        breaker = CircuitBreaker()
        for _ in range(2):
            breaker.after(breaker.before(), failed=True)
        breaker.opened_at = time.monotonic() - 11

        def attempt(_):
            try:
                return breaker.before()
            except CircuitOpen:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt, range(8))), 1)
        self.assertEqual(breaker.state, "HALF_OPEN")
        breaker.after(True, failed=True)
        self.assertEqual(breaker.state, "OPEN")
        breaker.opened_at -= 11
        self.assertTrue(breaker.before())
        breaker.after(True, failed=False)
        self.assertEqual(breaker.state, "CLOSED")

    @patch("booking.resilience.time.monotonic")
    def test_failures_outside_window_are_discarded(self, clock):
        breaker = CircuitBreaker()
        clock.return_value = 0
        breaker.after(False, failed=True)
        clock.return_value = 31
        breaker.after(False, failed=True)
        self.assertEqual(breaker.state, "CLOSED")


if __name__ == "__main__":
    unittest.main()
