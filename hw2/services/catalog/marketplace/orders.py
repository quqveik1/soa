from decimal import Decimal
from uuid import UUID

from marketplace.config import get_settings
from marketplace.database import connect
from marketplace.errors import ApiError, require_roles
from marketplace.promos import calculate_discount, validate_promo


MAX_AMOUNT = Decimal("999999999.99")
MAX_STOCK = 2147483647
NEXT_STATUS = {
    "CREATED": "PAYMENT_PENDING",
    "PAYMENT_PENDING": "PAID",
    "PAID": "SHIPPED",
    "SHIPPED": "COMPLETED",
}


def _requested_items(data: dict) -> dict:
    requested = {}
    for index, item in enumerate(data["items"]):
        product_id = UUID(str(item["product_id"]))
        if product_id in requested:
            raise ApiError(400, "VALIDATION_ERROR", "Товар встречается в заказе несколько раз", {
                "fields": [{"field": f"items.{index}.product_id", "message": "Повторный product_id"}],
            })
        requested[product_id] = item["quantity"]
    return requested


def _lock_user(conn, user_id) -> None:
    key = UUID(str(user_id)).int & ((1 << 64) - 1)
    if key >= 1 << 63:
        key -= 1 << 64
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (key,))


def _check_rate(conn, user_id, operation: str) -> None:
    minutes = get_settings().order_limit_minutes
    if minutes == 0:
        return
    limited = conn.execute(
        """SELECT id FROM user_operations WHERE user_id=%s AND operation_type=%s
           AND created_at > clock_timestamp() - %s * interval '1 minute'
           ORDER BY created_at DESC LIMIT 1""",
        (user_id, operation, minutes),
    ).fetchone()
    if limited:
        raise ApiError(429, "ORDER_LIMIT_EXCEEDED", "Слишком частые операции с заказами")


def _check_owner(order: dict, actor: dict) -> None:
    if actor["role"] != "ADMIN" and str(order["user_id"]) != str(actor["user_id"]):
        raise ApiError(403, "ORDER_OWNERSHIP_VIOLATION", "Заказ принадлежит другому пользователю")


def _lock_order(conn, order_id, actor: dict) -> dict:
    owner = conn.execute("SELECT user_id FROM orders WHERE id=%s", (order_id,)).fetchone()
    if owner is None:
        raise ApiError(404, "ORDER_NOT_FOUND", "Заказ не найден")
    _lock_user(conn, owner["user_id"])
    order = conn.execute("SELECT * FROM orders WHERE id=%s FOR UPDATE", (order_id,)).fetchone()
    if order is None:
        raise ApiError(404, "ORDER_NOT_FOUND", "Заказ не найден")
    _check_owner(order, actor)
    return order


def _require_state(order: dict, *states: str) -> None:
    if order["status"] not in states:
        raise ApiError(409, "INVALID_STATE_TRANSITION", "Операция недоступна в текущем состоянии заказа", {
            "current_status": order["status"], "allowed_statuses": list(states),
        })


def _lock_products(conn, product_ids) -> dict:
    products = {}
    for product_id in sorted(product_ids):
        row = conn.execute("SELECT * FROM products WHERE id=%s FOR UPDATE", (product_id,)).fetchone()
        if row:
            products[product_id] = row
    return products


def _reserve(conn, products: dict, requested: dict, previous: dict | None = None) -> None:
    previous = previous or {}
    for product_id in requested:
        product = products.get(product_id)
        if product is None:
            raise ApiError(404, "PRODUCT_NOT_FOUND", "Товар не найден", {"product_id": str(product_id)})
        if product["status"] != "ACTIVE":
            raise ApiError(409, "PRODUCT_INACTIVE", "Товар недоступен для заказа", {"product_id": str(product_id)})

    shortages = []
    for product_id, quantity in requested.items():
        available = products[product_id]["stock"] + previous.get(product_id, 0)
        if available < quantity:
            shortages.append({
                "product_id": str(product_id), "requested_quantity": quantity, "available_stock": available,
            })
    if shortages:
        raise ApiError(409, "INSUFFICIENT_STOCK", "Недостаточно товара на складе", {"products": shortages})

    for product_id in sorted(previous.keys() | requested.keys()):
        stock = products[product_id]["stock"] + previous.get(product_id, 0) - requested.get(product_id, 0)
        if stock > MAX_STOCK:
            raise ApiError(400, "VALIDATION_ERROR", "Возврат резерва превышает допустимый остаток", {
                "fields": [{"field": f"products.{product_id}.stock", "message": "Остаток превышает 2147483647"}],
            })
        conn.execute("UPDATE products SET stock=%s WHERE id=%s", (stock, product_id))


def _amounts(products: dict, requested: dict, promo: dict | None) -> tuple:
    subtotal = sum((products[product_id]["price"] * quantity for product_id, quantity in requested.items()), Decimal("0.00"))
    discount = calculate_discount(subtotal, promo)
    total = subtotal - discount
    if total > MAX_AMOUNT or discount > MAX_AMOUNT:
        raise ApiError(400, "VALIDATION_ERROR", "Сумма заказа превышает допустимую", {
            "fields": [{"field": "items", "message": "Сумма и скидка не могут превышать 999999999.99"}],
        })
    return subtotal, total, discount


def _save_items(conn, order_id, products: dict, requested: dict) -> None:
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO order_items (order_id,product_id,quantity,price_at_order) VALUES (%s,%s,%s,%s)",
            [(order_id, product_id, quantity, products[product_id]["price"]) for product_id, quantity in requested.items()],
        )


def _result(conn, order: dict) -> dict:
    items = conn.execute(
        "SELECT id,product_id,quantity,price_at_order FROM order_items WHERE order_id=%s ORDER BY product_id",
        (order["id"],),
    ).fetchall()
    return {**order, "items": items}


def _record_operation(conn, user_id, operation: str) -> None:
    conn.execute("INSERT INTO user_operations (user_id,operation_type) VALUES (%s,%s)", (user_id, operation))


def create_order(data: dict, actor: dict) -> dict:
    require_roles(actor, "USER", "ADMIN")
    requested = _requested_items(data)
    with connect() as conn:
        user_id = actor["user_id"]
        _lock_user(conn, user_id)
        _check_rate(conn, user_id, "CREATE_ORDER")
        active = conn.execute(
            "SELECT id FROM orders WHERE user_id=%s AND status IN ('CREATED','PAYMENT_PENDING') LIMIT 1",
            (user_id,),
        ).fetchone()
        if active:
            raise ApiError(409, "ORDER_HAS_ACTIVE", "У пользователя уже есть активный заказ")
        products = _lock_products(conn, requested)
        _reserve(conn, products, requested)

        promo = None
        if data.get("promo_code") is not None:
            promo = conn.execute("SELECT * FROM promo_codes WHERE code=%s FOR UPDATE", (data["promo_code"],)).fetchone()
            validate_promo(conn, promo)
        subtotal, total, discount = _amounts(products, requested, promo)
        if promo and subtotal < promo["min_order_amount"]:
            raise ApiError(422, "PROMO_CODE_MIN_AMOUNT", "Сумма заказа ниже минимума для промокода", {
                "min_order_amount": str(promo["min_order_amount"]), "order_amount": str(subtotal),
            })
        if promo:
            conn.execute("UPDATE promo_codes SET current_uses=current_uses+1 WHERE id=%s", (promo["id"],))
        order = conn.execute(
            """INSERT INTO orders (user_id,promo_code_id,total_amount,discount_amount)
               VALUES (%s,%s,%s,%s) RETURNING *""",
            (user_id, promo["id"] if promo else None, total, discount),
        ).fetchone()
        _save_items(conn, order["id"], products, requested)
        _record_operation(conn, user_id, "CREATE_ORDER")
        return _result(conn, order)


def get_order(order_id, actor: dict) -> dict:
    require_roles(actor, "USER", "ADMIN")
    with connect() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id=%s FOR SHARE", (order_id,)).fetchone()
        if order is None:
            raise ApiError(404, "ORDER_NOT_FOUND", "Заказ не найден")
        _check_owner(order, actor)
        return _result(conn, order)


def update_order(order_id, data: dict, actor: dict) -> dict:
    require_roles(actor, "USER", "ADMIN")
    requested = _requested_items(data)
    with connect() as conn:
        order = _lock_order(conn, order_id, actor)
        _require_state(order, "CREATED")
        _check_rate(conn, order["user_id"], "UPDATE_ORDER")
        previous = {
            row["product_id"]: row["quantity"] for row in conn.execute(
                "SELECT product_id,quantity FROM order_items WHERE order_id=%s", (order_id,),
            ).fetchall()
        }
        products = _lock_products(conn, previous.keys() | requested.keys())
        _reserve(conn, products, requested, previous)

        promo = None
        if order["promo_code_id"]:
            promo = conn.execute("SELECT * FROM promo_codes WHERE id=%s FOR UPDATE", (order["promo_code_id"],)).fetchone()
            validate_promo(conn, promo, own_reservation=1)
        subtotal = sum((products[product_id]["price"] * quantity for product_id, quantity in requested.items()), Decimal("0.00"))
        if promo and subtotal < promo["min_order_amount"]:
            conn.execute("UPDATE promo_codes SET current_uses=current_uses-1 WHERE id=%s", (promo["id"],))
            promo = None
        _, total, discount = _amounts(products, requested, promo)
        conn.execute("DELETE FROM order_items WHERE order_id=%s", (order_id,))
        _save_items(conn, order_id, products, requested)
        order = conn.execute(
            "UPDATE orders SET promo_code_id=%s,total_amount=%s,discount_amount=%s WHERE id=%s RETURNING *",
            (promo["id"] if promo else None, total, discount, order_id),
        ).fetchone()
        _record_operation(conn, order["user_id"], "UPDATE_ORDER")
        return _result(conn, order)


def cancel_order(order_id, actor: dict) -> dict:
    require_roles(actor, "USER", "ADMIN")
    with connect() as conn:
        order = _lock_order(conn, order_id, actor)
        _require_state(order, "CREATED", "PAYMENT_PENDING")
        previous = {
            row["product_id"]: row["quantity"] for row in conn.execute(
                "SELECT product_id,quantity FROM order_items WHERE order_id=%s", (order_id,),
            ).fetchall()
        }
        products = _lock_products(conn, previous)
        _reserve(conn, products, {}, previous)
        if order["promo_code_id"]:
            conn.execute("SELECT id FROM promo_codes WHERE id=%s FOR UPDATE", (order["promo_code_id"],))
            conn.execute("UPDATE promo_codes SET current_uses=current_uses-1 WHERE id=%s", (order["promo_code_id"],))
        order = conn.execute("UPDATE orders SET status='CANCELED' WHERE id=%s RETURNING *", (order_id,)).fetchone()
        return _result(conn, order)


def change_order_status(order_id, data: dict, actor: dict) -> dict:
    require_roles(actor, "ADMIN")
    status = getattr(data["status"], "value", data["status"])
    with connect() as conn:
        order = _lock_order(conn, order_id, actor)
        if NEXT_STATUS.get(order["status"]) != status:
            raise ApiError(409, "INVALID_STATE_TRANSITION", "Недопустимый переход состояния заказа", {
                "current_status": order["status"], "requested_status": status,
            })
        order = conn.execute("UPDATE orders SET status=%s WHERE id=%s RETURNING *", (status, order_id)).fetchone()
        return _result(conn, order)
