from decimal import Decimal, ROUND_HALF_UP

from psycopg.errors import UniqueViolation

from marketplace.database import connect
from marketplace.errors import ApiError, require_roles


def create_promo_code(data: dict, actor: dict) -> dict:
    require_roles(actor, "SELLER", "ADMIN")
    if data["valid_from"] > data["valid_until"]:
        raise ApiError(400, "VALIDATION_ERROR", "Начало действия позже окончания", {
            "fields": [{"field": "valid_until", "message": "Должно быть не раньше valid_from"}],
        })
    discount_type = getattr(data["discount_type"], "value", data["discount_type"])
    if discount_type == "PERCENTAGE" and data["discount_value"] > 100:
        raise ApiError(400, "VALIDATION_ERROR", "Процент скидки превышает 100", {
            "fields": [{"field": "discount_value", "message": "Процент должен быть не больше 100"}],
        })
    try:
        with connect() as conn:
            return conn.execute(
                """INSERT INTO promo_codes
                   (code, discount_type, discount_value, min_order_amount, max_uses,
                    valid_from, valid_until, active)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (data["code"], discount_type, data["discount_value"], data.get("min_order_amount", 0),
                 data["max_uses"], data["valid_from"], data["valid_until"], data.get("active", True)),
            ).fetchone()
    except UniqueViolation as error:
        raise ApiError(400, "VALIDATION_ERROR", "Промокод уже существует", {
            "fields": [{"field": "code", "message": "Код должен быть уникальным"}],
        }) from error


def validate_promo(conn, promo: dict | None, own_reservation: int = 0) -> None:
    now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    if (
        promo is None
        or not promo["active"]
        or not promo["valid_from"] <= now <= promo["valid_until"]
        or promo["current_uses"] < own_reservation
        or promo["current_uses"] - own_reservation >= promo["max_uses"]
    ):
        raise ApiError(422, "PROMO_CODE_INVALID", "Промокод недоступен")


def calculate_discount(subtotal: Decimal, promo: dict | None) -> Decimal:
    if promo is None:
        return Decimal("0.00")
    if promo["discount_type"] == "PERCENTAGE":
        if promo["discount_value"] > 70:
            return Decimal("0.00")
        discount = subtotal * promo["discount_value"] / Decimal("100")
    else:
        discount = min(promo["discount_value"], subtotal)
    return discount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
