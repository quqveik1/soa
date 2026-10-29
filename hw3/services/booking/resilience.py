import threading
import time
from collections import deque

import grpc

from common.log import event


TRANSIENT = {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED}


class CircuitOpen(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.UNAVAILABLE

    def details(self):
        return 'Flight Service circuit is open; try again later'


class CircuitBreaker:
    def __init__(self, threshold, recovery_seconds, window_seconds, clock=time.monotonic):
        if threshold < 1 or recovery_seconds <= 0 or window_seconds <= 0:
            raise ValueError('Circuit breaker parameters must be positive')
        self.threshold = threshold
        self.recovery_seconds = recovery_seconds
        self.window_seconds = window_seconds
        self.clock = clock
        self.state = 'CLOSED'
        self.generation = 0
        self.failures = deque()
        self.opened_at = 0
        self.lock = threading.Lock()

    def transition(self, target):
        previous, self.state = self.state, target
        self.generation += 1
        event('circuit_transition', previous=previous, current=target)

    def before_call(self):
        with self.lock:
            if self.state == 'OPEN':
                if self.clock() - self.opened_at < self.recovery_seconds:
                    event('circuit_rejected', state=self.state)
                    raise CircuitOpen()
                self.transition('HALF_OPEN')
                return self.generation, True
            if self.state == 'HALF_OPEN':
                event('circuit_rejected', state=self.state)
                raise CircuitOpen()
            return self.generation, False

    def after_call(self, ticket, status):
        with self.lock:
            # An old in-flight request cannot close a circuit opened by newer calls.
            if ticket[0] != self.generation:
                return
            now = self.clock()
            if status not in TRANSIENT:
                self.failures.clear()
                if self.state == 'HALF_OPEN':
                    self.transition('CLOSED')
                return
            while self.failures and self.failures[0] < now - self.window_seconds:
                self.failures.popleft()
            self.failures.append(now)
            if self.state == 'HALF_OPEN' or len(self.failures) >= self.threshold:
                self.opened_at = now
                self.transition('OPEN')


class ResilienceInterceptor(grpc.UnaryUnaryClientInterceptor):
    def __init__(self, breaker, max_attempts=3, backoff=0.1, sleeper=time.sleep):
        if not 1 <= max_attempts <= 3 or backoff < 0:
            raise ValueError('Use 1..3 attempts and nonnegative backoff')
        self.breaker, self.max_attempts = breaker, max_attempts
        self.backoff, self.sleeper = backoff, sleeper

    def intercept_unary_unary(self, continuation, details, request):
        ticket = self.breaker.before_call()
        attempts = 1 if ticket[1] else self.max_attempts
        status = grpc.StatusCode.UNKNOWN
        try:
            for attempt in range(1, attempts + 1):
                error = None
                try:
                    outcome = continuation(details, request)
                    status = outcome.code()
                except grpc.RpcError as failure:
                    error, status = failure, failure.code()
                if status not in TRANSIENT or attempt == attempts:
                    if error is not None:
                        raise error
                    return outcome
                delay = self.backoff * 2 ** (attempt - 1)
                event('grpc_retry', method=details.method, attempt=attempt,
                      status=status.name, backoff_seconds=delay)
                self.sleeper(delay)
        finally:
            self.breaker.after_call(ticket, status)
