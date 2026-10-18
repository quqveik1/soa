from decimal import Decimal
from uuid import UUID

from marketplace.database import connect
from marketplace.errors import ApiError, require_roles


PRODUCT_FIELDS = "id, name, description, price, stock, category, status, seller_id, created_at, updated_at"


def _owner_id(data: dict, actor: dict, previous_owner=None) -> UUID:
    owner_id = data.get("seller_id") or previous_owner or actor["user_id"]
    owner_id = UUID(str(owner_id))
    if actor["role"] == "SELLER" and owner_id != UUID(str(actor["user_id"])):
        raise ApiError(403, "ACCESS_DENIED", "Продавец может управлять только своими товарами")
    return owner_id


def _validate_seller(connection, seller_id: UUID) -> None:
    seller = connection.execute("SELECT role FROM users WHERE id = %s", (seller_id,)).fetchone()
    if seller is None or seller["role"] not in ("SELLER", "ADMIN"):
        raise ApiError(400, "VALIDATION_ERROR", "Владелец товара должен быть продавцом или администратором", {"fields": [{"field": "seller_id", "message": "Продавец не найден"}]})


def _locked_product(connection, product_id, actor: dict) -> dict:
    row = connection.execute(f"SELECT {PRODUCT_FIELDS} FROM products WHERE id = %s FOR UPDATE", (product_id,)).fetchone()
    if row is None:
        raise ApiError(404, "PRODUCT_NOT_FOUND", "Товар не найден")
    if actor["role"] == "SELLER" and row["seller_id"] != UUID(str(actor["user_id"])):
        raise ApiError(403, "ACCESS_DENIED", "Продавец может управлять только своими товарами")
    return row


def create_product(data: dict, actor: dict) -> dict:
    require_roles(actor, "SELLER", "ADMIN")
    seller_id = _owner_id(data, actor)
    with connect() as connection:
        _validate_seller(connection, seller_id)
        return connection.execute(
            f"INSERT INTO products (name, description, price, stock, category, status, seller_id) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {PRODUCT_FIELDS}",
            (data["name"], data.get("description"), Decimal(str(data["price"])), data["stock"], data["category"], data["status"], seller_id),
        ).fetchone()


def get_product(product_id, actor: dict) -> dict:
    require_roles(actor, "USER", "SELLER", "ADMIN")
    with connect() as connection:
        row = connection.execute(f"SELECT {PRODUCT_FIELDS} FROM products WHERE id = %s", (product_id,)).fetchone()
    if row is None:
        raise ApiError(404, "PRODUCT_NOT_FOUND", "Товар не найден")
    return row


def list_products(page: int, size: int, status: str | None, category: str | None, actor: dict) -> dict:
    require_roles(actor, "USER", "SELLER", "ADMIN")
    conditions, parameters = [], []
    if status is not None:
        conditions.append("status = %s")
        parameters.append(status)
    if category is not None:
        conditions.append("category = %s")
        parameters.append(category)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with connect() as connection:
        total = connection.execute("SELECT count(*) AS total FROM products" + where, parameters).fetchone()["total"]
        items = connection.execute(f"SELECT {PRODUCT_FIELDS} FROM products" + where + " ORDER BY created_at, id LIMIT %s OFFSET %s", [*parameters, size, page * size]).fetchall()
    return {"items": items, "totalElements": total, "page": page, "size": size}


def update_product(product_id, data: dict, actor: dict) -> dict:
    require_roles(actor, "SELLER", "ADMIN")
    with connect() as connection:
        previous = _locked_product(connection, product_id, actor)
        seller_id = _owner_id(data, actor, previous["seller_id"])
        _validate_seller(connection, seller_id)
        return connection.execute(
            f"UPDATE products SET name = %s, description = %s, price = %s, stock = %s, category = %s, status = %s, seller_id = %s WHERE id = %s RETURNING {PRODUCT_FIELDS}",
            (data["name"], data.get("description"), Decimal(str(data["price"])), data["stock"], data["category"], data["status"], seller_id, product_id),
        ).fetchone()


def archive_product(product_id, actor: dict) -> None:
    require_roles(actor, "SELLER", "ADMIN")
    with connect() as connection:
        _locked_product(connection, product_id, actor)
        connection.execute("UPDATE products SET status = 'ARCHIVED' WHERE id = %s", (product_id,))
