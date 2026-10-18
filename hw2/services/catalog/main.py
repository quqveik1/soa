import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import yaml
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from marketplace import handlers
from marketplace.auth import authenticate
from marketplace.config import get_settings
from marketplace.context import current_actor
from marketplace.errors import ApiError
from marketplace_api.apis.auth_api import router as auth_router
from marketplace_api.apis.health_api import router as health_router
from marketplace_api.apis.orders_api import router as orders_router
from marketplace_api.apis.products_api import router as products_router
from marketplace_api.apis.promo_codes_api import router as promos_router
from marketplace_api.models.error_response import ErrorResponse
from marketplace_api.security_api import get_token_bearerAuth


request_logger = logging.getLogger('marketplace.requests')
request_logger.setLevel(logging.INFO)
if not request_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(message)s'))
    request_logger.addHandler(handler)


@asynccontextmanager
async def lifespan(app):
    get_settings()
    yield


app = FastAPI(title='Marketplace', lifespan=lifespan)
for router in (health_router, auth_router, products_router, orders_router, promos_router):
    app.include_router(router)

contract_path = Path(__file__).resolve().parents[2] / 'openapi' / 'marketplace.yaml'
contract = yaml.safe_load(contract_path.read_text(encoding='utf-8'))
app.openapi = lambda: contract


async def authorized_actor(request: Request):
    authorization = request.headers.get('Authorization', '').split()
    if len(authorization) != 2 or authorization[0].lower() != 'bearer':
        raise ApiError(401, 'TOKEN_INVALID', 'Требуется access token в Authorization: Bearer')
    actor = await run_in_threadpool(authenticate, authorization[1])
    request.state.actor = actor
    current_actor.set(actor)
    return {'sub': str(actor['user_id'])}


app.dependency_overrides[get_token_bearerAuth] = authorized_actor


def error_response(status, code, message, details=None):
    body = ErrorResponse(error_code=code, message=message, details=details)
    return JSONResponse(status_code=status, content=body.model_dump(mode='json', exclude_none=True))


@app.exception_handler(ApiError)
async def api_error(request, error):
    return error_response(error.status, error.code, error.message, error.details)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, error):
    fields = [{
        'field': '.'.join(str(part) for part in violation['loc']),
        'message': violation['msg'], 'type': violation['type'],
    } for violation in error.errors()]
    return error_response(400, 'VALIDATION_ERROR', 'Некорректные входные данные', {'fields': fields})


@app.exception_handler(HTTPException)
async def http_error(request, error):
    return error_response(error.status_code, 'HTTP_ERROR', str(error.detail))


def masked_body(value):
    if isinstance(value, dict):
        return {key: '***' if any(secret in key.lower() for secret in ('password', 'token', 'secret', 'authorization')) else masked_body(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [masked_body(item) for item in value]
    return value


@app.middleware('http')
async def log_request(request: Request, call_next):
    started = perf_counter()
    request_id = str(uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    body = None
    too_deep = False
    if request.method in ('POST', 'PUT', 'DELETE'):
        raw = await request.body()
        if raw:
            try:
                body = masked_body(json.loads(raw))
            except (ValueError, UnicodeDecodeError):
                body = '<invalid JSON>'
            except RecursionError:
                body = '<invalid JSON>'
                too_deep = True
    try:
        if too_deep:
            result = error_response(400, 'VALIDATION_ERROR', 'Слишком глубокая вложенность JSON', {
                'fields': [{'field': 'body', 'message': 'Слишком глубокая вложенность', 'type': 'json_invalid'}],
            })
        else:
            result = await call_next(request)
    except Exception as error:
        request_logger.error(json.dumps({'event': 'unhandled_error', 'request_id': request_id, 'exception': type(error).__name__}))
        result = error_response(500, 'INTERNAL_ERROR', 'Внутренняя ошибка сервера')
    result.headers['X-Request-Id'] = request_id
    actor = getattr(request.state, 'actor', None)
    entry = {
        'request_id': request_id, 'method': request.method, 'endpoint': request.url.path,
        'status_code': result.status_code, 'duration_ms': round((perf_counter() - started) * 1000, 3),
        'user_id': str(actor['user_id']) if actor else None, 'timestamp': timestamp,
    }
    if request.method in ('POST', 'PUT', 'DELETE'):
        entry['body'] = body
    request_logger.info(json.dumps(entry, ensure_ascii=False))
    return result
