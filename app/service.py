from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import os
from uuid import uuid4

from psycopg.errors import UniqueViolation

from app.db import connect
from app.errors import ApiError, invalid

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("9999999999.99")


def product_by_id(conn, product_id, lock=False):
    row = conn.execute(
        "SELECT * FROM products WHERE id = %s" + (" FOR UPDATE" if lock else ""),
        (product_id,),
    ).fetchone()
    if row is None:
        raise ApiError(
            404, "PRODUCT_NOT_FOUND", "Товар не найден", {"product_id": str(product_id)}
        )
    return row


def get_product(request, id):
    with connect() as conn:
        return product_by_id(conn, id)


def list_products(request, page=0, size=20, status=None, category=None):
    conditions, values = [], []
    if status is not None:
        conditions.append("status = %s")
        values.append(str(status))
    if category is not None:
        conditions.append("category = %s")
        values.append(category)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        total = conn.execute(
            "SELECT count(*) AS n FROM products" + where, values
        ).fetchone()["n"]
        content = conn.execute(
            "SELECT * FROM products"
            + where
            + " ORDER BY created_at, id LIMIT %s OFFSET %s",
            [*values, size, page * size],
        ).fetchall()
    return {"content": content, "totalElements": total, "page": page, "size": size}


def create_product(request, body):
    with connect() as conn:
        return conn.execute(
            "INSERT INTO products(id, name, description, price, stock, category, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                uuid4(),
                body.name,
                body.description,
                body.price,
                body.stock,
                body.category,
                str(body.status),
            ),
        ).fetchone()


def update_product(request, id, body):
    with connect() as conn:
        product_by_id(conn, id, lock=True)
        return conn.execute(
            "UPDATE products SET name=%s, description=%s, price=%s, stock=%s, category=%s, status=%s "
            "WHERE id=%s RETURNING *",
            (
                body.name,
                body.description,
                body.price,
                body.stock,
                body.category,
                str(body.status),
                id,
            ),
        ).fetchone()


def delete_product(request, id):
    with connect() as conn:
        product_by_id(conn, id, lock=True)
        conn.execute("UPDATE products SET status='ARCHIVED' WHERE id=%s", (id,))


def create_promo_code(request, body):
    if body.valid_until <= body.valid_from:
        invalid("valid_until", "Должно быть позже valid_from")
    try:
        with connect() as conn:
            return conn.execute(
                "INSERT INTO promo_codes(id, code, discount_type, discount_value, min_order_amount, "
                "max_uses, valid_from, valid_until, active) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    uuid4(),
                    body.code,
                    str(body.discount_type),
                    body.discount_value,
                    body.min_order_amount.root,
                    body.max_uses,
                    body.valid_from,
                    body.valid_until,
                    body.active,
                ),
            ).fetchone()
    except UniqueViolation:
        raise ApiError(409, "PROMO_CODE_EXISTS", "Промокод уже существует") from None


def lock_user(conn, user_id):
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (str(user_id),)
    )


def check_limit(conn, user_id, operation):
    minutes = float(os.getenv("ORDER_INTERVAL_MINUTES", "1"))
    last = conn.execute(
        "SELECT created_at FROM user_operations WHERE user_id=%s AND operation_type=%s "
        "ORDER BY created_at DESC LIMIT 1",
        (user_id, operation),
    ).fetchone()
    if (
        last
        and (datetime.now(timezone.utc) - last["created_at"]).total_seconds()
        < minutes * 60
    ):
        raise ApiError(
            429,
            "ORDER_LIMIT_EXCEEDED",
            "Слишком частые операции с заказами",
            {
                "operation_type": operation,
                "interval_minutes": minutes,
            },
        )


def record_operation(conn, user_id, operation):
    conn.execute(
        "INSERT INTO user_operations(id, user_id, operation_type, created_at) VALUES (%s,%s,%s,clock_timestamp())",
        (uuid4(), user_id, operation),
    )


def order_by_id(conn, order_id, user_id, lock=False):
    row = conn.execute(
        "SELECT * FROM orders WHERE id=%s" + (" FOR UPDATE" if lock else ""),
        (order_id,),
    ).fetchone()
    if row is None:
        raise ApiError(404, "ORDER_NOT_FOUND", "Заказ не найден")
    if row["user_id"] != user_id:
        raise ApiError(
            403, "ORDER_OWNERSHIP_VIOLATION", "Заказ принадлежит другому пользователю"
        )
    return row


def order_response(conn, order_id):
    order = conn.execute("SELECT * FROM orders WHERE id=%s", (order_id,)).fetchone()
    order["items"] = conn.execute(
        "SELECT id, product_id, quantity, price_at_order FROM order_items WHERE order_id=%s ORDER BY product_id",
        (order_id,),
    ).fetchall()
    return order


def get_order(request, id, user_id):
    with connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        order_by_id(conn, id, user_id)
        return order_response(conn, id)


def lock_products(conn, ids):
    rows = conn.execute(
        "SELECT * FROM products WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
        (list(set(ids)),),
    ).fetchall()
    return {row["id"]: row for row in rows}


def reserve(conn, items, products):
    for item in items:
        product = products.get(item.product_id)
        if product is None:
            raise ApiError(
                404,
                "PRODUCT_NOT_FOUND",
                "Товар не найден",
                {"product_id": str(item.product_id)},
            )
        if product["status"] != "ACTIVE":
            raise ApiError(
                409,
                "PRODUCT_INACTIVE",
                "Товар неактивен",
                {"product_id": str(item.product_id)},
            )
    insufficient = [
        {
            "product_id": str(item.product_id),
            "requested_quantity": item.quantity,
            "available_stock": products[item.product_id]["stock"],
        }
        for item in items
        if products[item.product_id]["stock"] < item.quantity
    ]
    if insufficient:
        raise ApiError(
            409,
            "INSUFFICIENT_STOCK",
            "Недостаточно товара на складе",
            {"products": insufficient},
        )
    for item in items:
        conn.execute(
            "UPDATE products SET stock=stock-%s WHERE id=%s",
            (item.quantity, item.product_id),
        )
    return sum(
        (products[i.product_id]["price"] * i.quantity for i in items), Decimal("0")
    )


def restore(conn, items, products):
    for item in items:
        conn.execute(
            "UPDATE products SET stock=stock+%s WHERE id=%s",
            (item["quantity"], item["product_id"]),
        )
        products[item["product_id"]]["stock"] += item["quantity"]


def promo_discount(promo, subtotal, already_used=False):
    now = datetime.now(timezone.utc)
    other_uses = promo["current_uses"] - int(already_used)
    if (
        not promo["active"]
        or not promo["valid_from"] <= now <= promo["valid_until"]
        or other_uses >= promo["max_uses"]
    ):
        raise ApiError(422, "PROMO_CODE_INVALID", "Промокод недействителен")
    if subtotal < promo["min_order_amount"]:
        raise ApiError(
            422, "PROMO_CODE_MIN_AMOUNT", "Сумма ниже минимальной для промокода"
        )
    if promo["discount_type"] == "PERCENTAGE":
        discount = subtotal * promo["discount_value"] / 100
        if discount > subtotal * Decimal("0.70"):
            discount = Decimal("0")
    else:
        discount = min(promo["discount_value"], subtotal)
    return discount.quantize(CENT, rounding=ROUND_HALF_UP)


def check_amount(subtotal):
    if subtotal > MAX_AMOUNT:
        invalid("items", "Стоимость заказа превышает DECIMAL(12,2)")


def save_items(conn, order_id, items, products):
    for item in items:
        conn.execute(
            "INSERT INTO order_items(id, order_id, product_id, quantity, price_at_order) VALUES (%s,%s,%s,%s,%s)",
            (
                uuid4(),
                order_id,
                item.product_id,
                item.quantity,
                products[item.product_id]["price"],
            ),
        )


def create_order(request, user_id, body):
    with connect() as conn:
        lock_user(conn, user_id)
        check_limit(conn, user_id, "CREATE_ORDER")
        if conn.execute(
            "SELECT id FROM orders WHERE user_id=%s AND status IN ('CREATED','PAYMENT_PENDING')",
            (user_id,),
        ).fetchone():
            raise ApiError(
                409, "ORDER_HAS_ACTIVE", "У пользователя уже есть активный заказ"
            )
        products = lock_products(conn, [i.product_id for i in body.items])
        subtotal = reserve(conn, body.items, products)
        check_amount(subtotal)
        promo, discount = None, Decimal("0")
        if body.promo_code is not None:
            promo = conn.execute(
                "SELECT * FROM promo_codes WHERE code=%s FOR UPDATE", (body.promo_code,)
            ).fetchone()
            if promo is None:
                raise ApiError(422, "PROMO_CODE_INVALID", "Промокод не найден")
            discount = promo_discount(promo, subtotal)
            conn.execute(
                "UPDATE promo_codes SET current_uses=current_uses+1 WHERE id=%s",
                (promo["id"],),
            )
        order_id = uuid4()
        conn.execute(
            "INSERT INTO orders(id, user_id, status, promo_code_id, total_amount, discount_amount) "
            "VALUES (%s,%s,'CREATED',%s,%s,%s)",
            (
                order_id,
                user_id,
                promo["id"] if promo else None,
                subtotal - discount,
                discount,
            ),
        )
        save_items(conn, order_id, body.items, products)
        record_operation(conn, user_id, "CREATE_ORDER")
        return order_response(conn, order_id)


def update_order(request, id, user_id, body):
    with connect() as conn:
        lock_user(conn, user_id)
        order = order_by_id(conn, id, user_id, lock=True)
        if order["status"] != "CREATED":
            raise ApiError(
                409, "INVALID_STATE_TRANSITION", "Обновление возможно только в CREATED"
            )
        check_limit(conn, user_id, "UPDATE_ORDER")
        old_items = conn.execute(
            "SELECT * FROM order_items WHERE order_id=%s", (id,)
        ).fetchall()
        products = lock_products(
            conn,
            [i["product_id"] for i in old_items] + [i.product_id for i in body.items],
        )
        restore(conn, old_items, products)
        subtotal = reserve(conn, body.items, products)
        check_amount(subtotal)
        promo_id, discount = order["promo_code_id"], Decimal("0")
        if promo_id is not None:
            promo = conn.execute(
                "SELECT * FROM promo_codes WHERE id=%s FOR UPDATE", (promo_id,)
            ).fetchone()
            try:
                discount = promo_discount(promo, subtotal, already_used=True)
            except ApiError as error:
                if error.code != "PROMO_CODE_MIN_AMOUNT":
                    raise
                conn.execute(
                    "UPDATE promo_codes SET current_uses=current_uses-1 WHERE id=%s",
                    (promo_id,),
                )
                promo_id = None
        conn.execute("DELETE FROM order_items WHERE order_id=%s", (id,))
        save_items(conn, id, body.items, products)
        conn.execute(
            "UPDATE orders SET promo_code_id=%s, total_amount=%s, discount_amount=%s WHERE id=%s",
            (promo_id, subtotal - discount, discount, id),
        )
        record_operation(conn, user_id, "UPDATE_ORDER")
        return order_response(conn, id)


def cancel_order(request, id, user_id):
    with connect() as conn:
        lock_user(conn, user_id)
        order = order_by_id(conn, id, user_id, lock=True)
        if order["status"] not in ("CREATED", "PAYMENT_PENDING"):
            raise ApiError(
                409,
                "INVALID_STATE_TRANSITION",
                "Отмена возможна только из CREATED/PAYMENT_PENDING",
            )
        items = conn.execute(
            "SELECT * FROM order_items WHERE order_id=%s", (id,)
        ).fetchall()
        products = lock_products(conn, [i["product_id"] for i in items])
        restore(conn, items, products)
        if order["promo_code_id"] is not None:
            conn.execute(
                "UPDATE promo_codes SET current_uses=current_uses-1 WHERE id=%s",
                (order["promo_code_id"],),
            )
        conn.execute("UPDATE orders SET status='CANCELED' WHERE id=%s", (id,))
        return order_response(conn, id)


def change_order_status(request, id, user_id, body):
    transitions = {
        "CREATED": "PAYMENT_PENDING",
        "PAYMENT_PENDING": "PAID",
        "PAID": "SHIPPED",
        "SHIPPED": "COMPLETED",
    }
    with connect() as conn:
        order = order_by_id(conn, id, user_id, lock=True)
        if transitions.get(order["status"]) != body.status:
            raise ApiError(
                409, "INVALID_STATE_TRANSITION", "Недопустимый переход состояния"
            )
        conn.execute("UPDATE orders SET status=%s WHERE id=%s", (str(body.status), id))
        return order_response(conn, id)
