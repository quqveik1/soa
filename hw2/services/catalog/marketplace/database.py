import os

import psycopg
from psycopg.rows import dict_row

from marketplace.config import DEFAULT_DATABASE_URL


def connect() -> psycopg.Connection:
    return psycopg.connect(
        os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        row_factory=dict_row,
    )
