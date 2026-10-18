import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


DEFAULT_DATABASE_URL = "postgresql://marketplace:marketplace@postgres:5432/marketplace"


@dataclass(frozen=True)
class Settings:
    database_url: str
    jwt_secret: str
    access_minutes: int = 20
    refresh_days: int = 14
    order_limit_minutes: Decimal = Decimal("1")


def get_settings() -> Settings:
    jwt_secret = os.environ.get("JWT_SECRET", "")
    if len(jwt_secret) < 32:
        raise ValueError("JWT_SECRET must contain at least 32 characters")

    try:
        order_limit_minutes = Decimal(os.environ.get("ORDER_LIMIT_MINUTES", "1"))
    except InvalidOperation as error:
        raise ValueError("ORDER_LIMIT_MINUTES must be a non-negative number") from error
    if not order_limit_minutes.is_finite() or order_limit_minutes < 0:
        raise ValueError("ORDER_LIMIT_MINUTES must be a non-negative number")

    return Settings(
        database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        jwt_secret=jwt_secret,
        order_limit_minutes=order_limit_minutes,
    )
