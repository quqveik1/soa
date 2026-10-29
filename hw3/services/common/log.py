import json
import logging
from datetime import datetime, timezone


def setup():
    logging.basicConfig(level=logging.INFO, format='%(message)s')


def event(name, **fields):
    logging.getLogger('flight-booking').info(json.dumps({
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'event': name, **fields,
    }, default=str))
