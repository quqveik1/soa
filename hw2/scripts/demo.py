#!/usr/bin/env python3
import json
import os
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


BASE_URL = os.environ.get('BASE_URL', 'http://localhost:8000').rstrip('/')


def call(method, path, body=None, token=None, expected=200):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = Request(BASE_URL + path, data=json.dumps(body).encode() if body is not None else None,
                      headers=headers, method=method)
    try:
        result = urlopen(request, timeout=15)
    except HTTPError as error:
        result = error
    with result:
        payload = json.loads(result.read())
        visible = {key: '***' if 'token' in key else value for key, value in payload.items()}
        print(f'{method} {path} → {result.status}; X-Request-Id={result.headers.get("X-Request-Id")}')
        print(json.dumps(visible, ensure_ascii=False, indent=2))
        if result.status != expected:
            raise SystemExit(f'Expected HTTP {expected}')
        return payload


def main():
    suffix = uuid4().hex[:8]
    password = 'demo-password-12345'
    call('GET', '/health')
    tokens = {}
    for role, prefix in [('SELLER', 'seller'), ('USER', 'buyer')]:
        username = f'demo_{prefix}_{suffix}'
        call('POST', '/auth/register', {'username': username, 'password': password, 'role': role}, expected=201)
        tokens[role] = call('POST', '/auth/login', {'username': username, 'password': password})['access_token']
    seller, buyer = tokens['SELLER'], tokens['USER']
    product = call('POST', '/products', {
        'name': 'Notebook', 'price': 100, 'stock': 10, 'category': 'Stationery', 'status': 'ACTIVE',
    }, seller, expected=201)
    now = datetime.now(timezone.utc)
    promo = call('POST', '/promo-codes', {
        'code': f'DEMO_{suffix.upper()}', 'discount_type': 'PERCENTAGE', 'discount_value': 10,
        'min_order_amount': 100, 'max_uses': 10, 'valid_from': (now - timedelta(days=1)).isoformat(),
        'valid_until': (now + timedelta(days=1)).isoformat(),
    }, seller, expected=201)
    order = call('POST', '/orders', {
        'items': [{'product_id': product['id'], 'quantity': 2}], 'promo_code': promo['code'],
    }, buyer, expected=201)
    assert order['total_amount'] == 180 and order['discount_amount'] == 20
    call('PUT', f"/orders/{order['id']}", {
        'items': [{'product_id': product['id'], 'quantity': 999}],
    }, buyer, expected=409)
    order = call('PUT', f"/orders/{order['id']}", {
        'items': [{'product_id': product['id'], 'quantity': 3}],
    }, buyer)
    assert order['total_amount'] == 270 and order['discount_amount'] == 30
    call('GET', f"/orders/{order['id']}", token=buyer)
    call('POST', f"/orders/{order['id']}/cancel", token=buyer)
    call('POST', f"/orders/{order['id']}/cancel", token=buyer, expected=409)
    restored = call('GET', f"/products/{product['id']}", token=buyer)
    assert restored['stock'] == 10
    print(f"\nДля SELECT: order_id={order['id']}; product_id={product['id']}; promo_code={promo['code']}")


if __name__ == '__main__':
    main()
