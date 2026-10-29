import os

import psycopg
from psycopg.rows import dict_row


def connect():
    return psycopg.connect(
        os.environ['DATABASE_URL'], row_factory=dict_row, connect_timeout=3,
        options='-c statement_timeout=8000 -c lock_timeout=5000',
    )
