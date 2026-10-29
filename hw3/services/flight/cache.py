import base64
import json
import os

from redis.exceptions import RedisError
from redis.sentinel import Sentinel

from common.log import event


def flight_scope(flight_id):
    return f'flight:{flight_id}'


def route_scope(origin, destination):
    return f'search:{origin}:{destination}'


def advance_versions(conn, flight):
    scopes = sorted({flight_scope(flight['id']), route_scope(flight['origin'], flight['destination'])})
    for scope in scopes:
        conn.execute('''
            INSERT INTO cache_versions(scope, version) VALUES (%s, 1)
            ON CONFLICT (scope) DO UPDATE SET version = cache_versions.version + 1
        ''', (scope,))


class FlightCache:
    def __init__(self):
        addresses = [node.rsplit(':', 1) for node in os.environ['REDIS_SENTINELS'].split(',')]
        self.sentinel = Sentinel(
            [(host, int(port)) for host, port in addresses],
            socket_timeout=0.25, socket_connect_timeout=0.25,
        )
        self.client = self.sentinel.master_for(os.getenv('REDIS_MASTER_NAME', 'flights-cache'))
        self.ttl = int(os.getenv('CACHE_TTL_SECONDS', '300'))
        if not 300 <= self.ttl <= 600:
            raise ValueError('CACHE_TTL_SECONDS must be between 300 and 600')

    def load(self, conn, key, scope, message_type, loader):
        row = conn.execute('SELECT version FROM cache_versions WHERE scope = %s', (scope,)).fetchone()
        version = row['version'] if row else 0
        try:
            raw = self.client.get(key)
            if raw:
                envelope = json.loads(raw)
                if envelope['version'] == version:
                    result = message_type.FromString(base64.b64decode(envelope['payload']))
                    event('cache_hit', key=key)
                    return result
        except (RedisError, ValueError, KeyError, TypeError):
            event('cache_unavailable', operation='get', key=key)
        event('cache_miss', key=key)
        result = loader()
        try:
            self.client.set(key, json.dumps({
                'version': version,
                'payload': base64.b64encode(result.SerializeToString()).decode('ascii'),
            }), ex=self.ttl)
        except RedisError:
            event('cache_unavailable', operation='set', key=key)
        return result

    def invalidate(self, flight):
        key = flight_scope(flight['id'])
        prefix = route_scope(flight['origin'], flight['destination'])
        try:
            self.client.delete(key)
            for search_key in self.client.scan_iter(match=f'{prefix}:*', count=100):
                self.client.delete(search_key)
            event('cache_invalidated', flight_id=flight['id'], route=prefix)
        except RedisError:
            # PostgreSQL revisions also reject entries restored by a stale replica.
            event('cache_unavailable', operation='invalidate', flight_id=flight['id'])
