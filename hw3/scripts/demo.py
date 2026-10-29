import json
import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


BASE_URL = os.getenv('BASE_URL', 'http://localhost:8003')


def call(method, path, body=None, expected=200):
    data = json.dumps(body).encode() if body is not None else None
    request = Request(BASE_URL + path, data=data, method=method,
                      headers={'Content-Type': 'application/json'})
    try:
        response = urlopen(request, timeout=20)
    except HTTPError as error:
        response = error
    with response:
        payload = json.load(response)
        status = response.status
    print(f'{method} {path} -> {status}')
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if status != expected:
        raise SystemExit(f'Expected {expected}, got {status}')
    return payload


def main():
    call('GET', '/health')
    flights = call('GET', '/flights?origin=SVO&destination=LED')['flights']
    flight = next(item for item in flights if item['available_seats'] >= 2)
    flight_id, user_id = flight['id'], str(uuid4())
    initial_stock = call('GET', f'/flights/{flight_id}')['available_seats']
    body = {
        'user_id': user_id, 'flight_id': flight_id,
        'passenger_name': 'Demo Passenger', 'passenger_email': 'demo@example.com', 'seat_count': 2,
    }
    booking = call('POST', '/bookings', body, expected=201)
    assert Decimal(booking['total_price']) == Decimal(flight['price']) * 2
    assert call('GET', f'/flights/{flight_id}')['available_seats'] == initial_stock - 2
    call('GET', f'/bookings/{booking["id"]}')
    assert len(call('GET', f'/bookings?user_id={user_id}')) == 1
    call('POST', '/bookings', {**body, 'seat_count': 1000}, expected=409)
    assert len(call('GET', f'/bookings?user_id={user_id}')) == 1
    assert call('POST', f'/bookings/{booking["id"]}/cancel')['status'] == 'CANCELLED'
    call('POST', f'/bookings/{booking["id"]}/cancel', expected=409)
    assert call('GET', f'/flights/{flight_id}')['available_seats'] == initial_stock
    call('POST', '/bookings', {**body, 'seat_count': 0}, expected=400)
    call('GET', f'/flights/{uuid4()}', expected=404)

    last_seat = next(item for item in flights if item['total_seats'] == 1 and item['available_seats'] == 1)
    def race(_):
        request = Request(BASE_URL + '/bookings', method='POST', headers={'Content-Type': 'application/json'},
                          data=json.dumps({**body, 'flight_id': last_seat['id'], 'seat_count': 1}).encode())
        try:
            response = urlopen(request, timeout=20)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(race, range(2)))
    assert sorted(status for status, _ in results) == [201, 409], results
    winner = next(payload for status, payload in results if status == 201)
    print('Two concurrent requests for the last seat: 201 and 409')
    call('POST', f'/bookings/{winner["id"]}/cancel')
    assert call('GET', f'/flights/{last_seat["id"]}')['available_seats'] == 1
    print(f'Demo completed. user_id={user_id}; booking_id={booking["id"]}')


if __name__ == '__main__':
    main()
