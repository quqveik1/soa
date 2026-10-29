import os

import grpc

import flight_pb2 as pb
import flight_pb2_grpc as rpc
from booking.resilience import CircuitBreaker, ResilienceInterceptor


class FlightClient:
    def __init__(self):
        self.breaker = CircuitBreaker(
            threshold=int(os.getenv('CB_FAILURE_THRESHOLD', '5')),
            recovery_seconds=float(os.getenv('CB_RECOVERY_SECONDS', '10')),
            window_seconds=float(os.getenv('CB_WINDOW_SECONDS', '30')),
        )
        self.channel = grpc.insecure_channel(
            os.getenv('FLIGHT_ADDRESS', 'flight:50051'),
            options=(('grpc.enable_retries', 0), ('grpc.initial_reconnect_backoff_ms', 100),
                     ('grpc.min_reconnect_backoff_ms', 100), ('grpc.max_reconnect_backoff_ms', 1000)),
        )
        interceptor = ResilienceInterceptor(
            self.breaker, max_attempts=int(os.getenv('GRPC_MAX_ATTEMPTS', '3')),
            backoff=float(os.getenv('GRPC_BACKOFF_SECONDS', '0.1')),
        )
        self.stub = rpc.FlightServiceStub(grpc.intercept_channel(self.channel, interceptor))
        self.metadata = (('x-api-key', os.environ['FLIGHT_API_KEY']),)
        if not self.metadata[0][1]:
            raise ValueError('FLIGHT_API_KEY must not be empty')
        self.timeout = float(os.getenv('GRPC_TIMEOUT_SECONDS', '1'))
        if self.timeout <= 0:
            raise ValueError('GRPC_TIMEOUT_SECONDS must be positive')

    def search(self, origin, destination, date=''):
        return self.stub.SearchFlights(pb.SearchFlightsRequest(origin=origin, destination=destination, date=date),
                                       metadata=self.metadata, timeout=self.timeout)

    def get(self, flight_id):
        return self.stub.GetFlight(pb.GetFlightRequest(flight_id=str(flight_id)),
                                   metadata=self.metadata, timeout=self.timeout)

    def reserve(self, flight_id, seat_count, booking_id):
        return self.stub.ReserveSeats(pb.ReserveSeatsRequest(
            flight_id=str(flight_id), seat_count=seat_count, booking_id=str(booking_id)),
            metadata=self.metadata, timeout=self.timeout)

    def release(self, booking_id):
        return self.stub.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=str(booking_id)),
                                            metadata=self.metadata, timeout=self.timeout)

    def close(self):
        self.channel.close()
