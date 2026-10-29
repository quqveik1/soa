import hmac
import os
import signal
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import grpc
import psycopg

import flight_pb2_grpc as rpc
from common.log import event, setup
from flight.cache import FlightCache
from flight.repository import FlightError, FlightRepository


class AuthInterceptor(grpc.ServerInterceptor):
    def __init__(self, api_key):
        if not api_key:
            raise ValueError('FLIGHT_API_KEY must not be empty')
        self.api_key = api_key.encode()

    def intercept_service(self, continuation, details):
        handler = continuation(details)
        if handler is None:
            return None
        credentials = [value for key, value in details.invocation_metadata if key == 'x-api-key']

        def invoke(request, context):
            if (len(credentials) != 1 or not isinstance(credentials[0], str)
                    or not hmac.compare_digest(credentials[0].encode(), self.api_key)):
                context.abort(grpc.StatusCode.UNAUTHENTICATED, 'Invalid or missing service credentials')
            try:
                return handler.unary_unary(request, context)
            except FlightError as error:
                context.abort(error.code, error.message)
            except psycopg.Error:
                event('database_error', method=details.method)
                context.abort(grpc.StatusCode.UNAVAILABLE, 'Flight database is unavailable')
            except Exception as error:
                event('rpc_error', method=details.method, error_type=type(error).__name__)
                context.abort(grpc.StatusCode.INTERNAL, 'Internal flight error')

        return grpc.unary_unary_rpc_method_handler(
            invoke, request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


class FlightService(rpc.FlightServiceServicer):
    def __init__(self, repository):
        self.repository = repository

    def SearchFlights(self, request, context):
        return self.repository.search(request)

    def GetFlight(self, request, context):
        return self.repository.get(request)

    def ReserveSeats(self, request, context):
        return self.repository.reserve(request)

    def ReleaseReservation(self, request, context):
        return self.repository.release(request)

    def UpdateFlight(self, request, context):
        return self.repository.update(request)


def main():
    setup()
    cache = FlightCache()
    server = grpc.server(ThreadPoolExecutor(max_workers=16),
                         interceptors=[AuthInterceptor(os.environ['FLIGHT_API_KEY'])])
    rpc.add_FlightServiceServicer_to_server(FlightService(FlightRepository(cache)), server)
    if server.add_insecure_port('[::]:50051') == 0:
        raise RuntimeError('Cannot bind gRPC port')
    server.start()
    event('flight_started', port=50051)
    stopped = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    stopped.wait()
    server.stop(grace=5).wait()
    cache.client.close()


if __name__ == '__main__':
    main()
