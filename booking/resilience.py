import logging
import os
import threading
import time
from collections import deque

import grpc

log = logging.getLogger(__name__)
TRANSIENT = {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED}


class CircuitOpen(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.UNAVAILABLE

    def details(self):
        return "Flight Service временно недоступен: circuit breaker открыт"


class CircuitBreaker:
    def __init__(self):
        self.threshold = int(os.environ.get("CB_FAILURE_THRESHOLD", "5"))
        self.cooldown = float(os.environ.get("CB_RESET_TIMEOUT", "10"))
        self.window = float(os.environ.get("CB_WINDOW_SECONDS", "30"))
        if min(self.threshold, self.cooldown, self.window) <= 0:
            raise ValueError("Параметры circuit breaker должны быть положительными")
        self.state = "CLOSED"
        self.failures = deque()
        self.opened_at = 0
        self.lock = threading.Lock()

    def transition(self, state):
        log.warning("circuit breaker %s -> %s", self.state, state)
        self.state = state

    def before(self):
        with self.lock:
            if self.state == "OPEN" and time.monotonic() - self.opened_at >= self.cooldown:
                self.transition("HALF_OPEN")
                return True
            if self.state != "CLOSED":
                raise CircuitOpen()
            return False

    def after(self, probe, failed):
        with self.lock:
            now = time.monotonic()
            if probe or self.state == "CLOSED":
                if not failed:
                    self.failures.clear()
                    if probe:
                        self.transition("CLOSED")
                else:
                    self.failures.append(now)
                    while self.failures and now - self.failures[0] > self.window:
                        self.failures.popleft()
                    if probe or len(self.failures) >= self.threshold:
                        self.opened_at = now
                        self.transition("OPEN")


class Resilience(grpc.UnaryUnaryClientInterceptor):
    def __init__(self):
        self.breaker = CircuitBreaker()

    def intercept_unary_unary(self, continuation, details, request):
        probe = self.breaker.before()
        attempts = 1 if probe else 3
        for attempt in range(attempts):
            call = continuation(details, request)
            try:
                call.result()
            except grpc.RpcError as error:
                transient = error.code() in TRANSIENT
                if transient and attempt + 1 < attempts:
                    delay = 0.1 * 2 ** attempt
                    log.warning("gRPC %s: %s; retry %s/%s in %.1fs",
                                details.method, error.code().name, attempt + 2, attempts, delay)
                    time.sleep(delay)
                    continue
                self.breaker.after(probe, failed=transient)
            else:
                self.breaker.after(probe, failed=False)
            return call
