from __future__ import annotations

import logging
import os
import random
import time
import uuid
from typing import Any

import graypy
from flask import Flask, g, jsonify, request

APP_NAME = os.getenv("APP_NAME", "sample-app")
GELF_HOST = os.getenv("GELF_HOST", "graylog")
GELF_PORT = int(os.getenv("GELF_PORT", "12201"))
PORT = int(os.getenv("PORT", "8080"))
ERROR_RATE = float(os.getenv("ERROR_RATE", "0.18"))

app = Flask(APP_NAME)

logger = logging.getLogger(APP_NAME)
logger.setLevel(logging.INFO)
logger.handlers.clear()

gelf = graypy.GELFUDPHandler(GELF_HOST, GELF_PORT)
gelf.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(gelf)

console = logging.StreamHandler()
console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(console)

PRODUCTS = {
    "SKU-7": {"id": "SKU-7", "name": "Notebook Pro", "price": 4599.90, "stock": 12},
    "SKU-42": {"id": "SKU-42", "name": "Mouse Wireless", "price": 129.90, "stock": 84},
    "SKU-99": {"id": "SKU-99", "name": "Monitor 27", "price": 1899.00, "stock": 5},
    "SKU-15": {"id": "SKU-15", "name": "Teclado Mecânico", "price": 499.00, "stock": 31},
}

USERS = {
    "u-100": {"id": "u-100", "name": "Ana Silva", "email": "ana@example.com"},
    "u-200": {"id": "u-200", "name": "Bruno Costa", "email": "bruno@example.com"},
    "u-300": {"id": "u-300", "name": "Carla Dias", "email": "carla@example.com"},
}

ORDERS: list[dict[str, Any]] = [
    {"id": "ord-1001", "sku": "SKU-42", "qty": 2, "status": "paid", "user_id": "u-100"},
    {"id": "ord-1002", "sku": "SKU-7", "qty": 1, "status": "pending", "user_id": "u-200"},
]

RANDOM_FAILURES = [
    ("PaymentGatewayTimeout", "payment gateway timed out after 3000ms"),
    ("InventoryServiceUnavailable", "inventory service returned 503"),
    ("DatabaseConnectionError", "could not obtain connection from pool"),
    ("NullPointerInPricing", "unexpected null in pricing engine"),
    ("CacheMissCascade", "cache miss cascaded into origin timeout"),
]


def _base_extra(**kwargs: Any) -> dict[str, Any]:
    data = {
        "app": APP_NAME,
        "request_id": getattr(g, "request_id", "-"),
        "http_method": request.method,
        "http_path": request.path,
    }
    data.update(kwargs)
    return data


def _maybe_random_error(operation: str) -> tuple[dict[str, Any], int] | None:
    if random.random() >= ERROR_RATE:
        return None

    # Mix of client/server style failures for analysis practice
    roll = random.random()
    if roll < 0.25:
        code = 404
        err = "ResourceNotFound"
        msg = f"{operation}: resource not found"
        level = logging.WARNING
    elif roll < 0.45:
        code = 429
        err = "RateLimited"
        msg = f"{operation}: too many requests"
        level = logging.WARNING
    elif roll < 0.65:
        code = 503
        err = "DependencyUnavailable"
        msg = f"{operation}: upstream dependency unavailable"
        level = logging.ERROR
    else:
        code = 500
        err, detail = random.choice(RANDOM_FAILURES)
        msg = f"{operation}: {detail}"
        level = logging.ERROR
        logger.log(
            level,
            msg,
            extra=_base_extra(
                error_type=err,
                error_code=code,
                operation=operation,
                severity="high" if code >= 500 else "medium",
            ),
        )
        if code == 500 and random.random() < 0.5:
            try:
                raise RuntimeError(detail)
            except RuntimeError:
                logger.exception(
                    "stacktrace for %s",
                    err,
                    extra=_base_extra(error_type=err, error_code=code, operation=operation),
                )
        return {"error": err, "message": msg, "operation": operation}, code

    logger.log(
        level,
        msg,
        extra=_base_extra(
            error_type=err,
            error_code=code,
            operation=operation,
            severity="medium",
        ),
    )
    return {"error": err, "message": msg, "operation": operation}, code


def _maybe_slow(operation: str) -> None:
    if random.random() < 0.12:
        delay = round(random.uniform(0.4, 1.8), 3)
        logger.warning(
            "slow operation detected op=%s delay_ms=%s",
            operation,
            int(delay * 1000),
            extra=_base_extra(operation=operation, delay_ms=int(delay * 1000), slow="true"),
        )
        time.sleep(delay)


@app.before_request
def _start_timer() -> None:
    g.request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
    g.started_at = time.perf_counter()


@app.after_request
def _log_request(response: Any) -> Any:
    duration_ms = round((time.perf_counter() - g.started_at) * 1000, 2)
    response.headers["X-Request-Id"] = g.request_id

    level = logging.INFO
    if response.status_code >= 500:
        level = logging.ERROR
    elif response.status_code >= 400:
        level = logging.WARNING

    logger.log(
        level,
        "%s %s -> %s (%sms)",
        request.method,
        request.path,
        response.status_code,
        duration_ms,
        extra=_base_extra(
            http_status=response.status_code,
            duration_ms=duration_ms,
            client_ip=request.headers.get("X-Forwarded-For", request.remote_addr),
            user_agent=request.headers.get("User-Agent", ""),
        ),
    )
    return response


@app.get("/health")
def health() -> Any:
    return jsonify({"status": "ok", "app": APP_NAME})


@app.get("/api/products")
def list_products() -> Any:
    _maybe_slow("list_products")
    failed = _maybe_random_error("list_products")
    if failed:
        body, code = failed
        return jsonify(body), code

    products = list(PRODUCTS.values())
    logger.info(
        "listed products count=%s",
        len(products),
        extra=_base_extra(products_count=len(products)),
    )
    return jsonify({"products": products})


@app.get("/api/products/<sku>")
def get_product(sku: str) -> Any:
    _maybe_slow("get_product")
    failed = _maybe_random_error("get_product")
    if failed:
        body, code = failed
        return jsonify(body), code

    product = PRODUCTS.get(sku)
    if not product:
        logger.warning(
            "product not found sku=%s",
            sku,
            extra=_base_extra(sku=sku, error_type="ProductNotFound", error_code=404),
        )
        return jsonify({"error": "ProductNotFound", "sku": sku}), 404

    logger.info("product fetched sku=%s", sku, extra=_base_extra(sku=sku))
    return jsonify(product)


@app.get("/api/users/<user_id>")
def get_user(user_id: str) -> Any:
    failed = _maybe_random_error("get_user")
    if failed:
        body, code = failed
        return jsonify(body), code

    user = USERS.get(user_id)
    if not user:
        logger.warning(
            "user not found user_id=%s",
            user_id,
            extra=_base_extra(user_id=user_id, error_type="UserNotFound", error_code=404),
        )
        return jsonify({"error": "UserNotFound", "user_id": user_id}), 404

    logger.info("user fetched user_id=%s", user_id, extra=_base_extra(user_id=user_id))
    return jsonify(user)


@app.get("/api/orders")
def list_orders() -> Any:
    _maybe_slow("list_orders")
    failed = _maybe_random_error("list_orders")
    if failed:
        body, code = failed
        return jsonify(body), code

    logger.info(
        "listed orders count=%s",
        len(ORDERS),
        extra=_base_extra(orders_count=len(ORDERS)),
    )
    return jsonify({"orders": ORDERS})


@app.get("/api/orders/<order_id>")
def get_order(order_id: str) -> Any:
    failed = _maybe_random_error("get_order")
    if failed:
        body, code = failed
        return jsonify(body), code

    order = next((o for o in ORDERS if o["id"] == order_id), None)
    if not order:
        logger.warning(
            "order not found order_id=%s",
            order_id,
            extra=_base_extra(order_id=order_id, error_type="OrderNotFound", error_code=404),
        )
        return jsonify({"error": "OrderNotFound", "order_id": order_id}), 404

    logger.info("order fetched order_id=%s", order_id, extra=_base_extra(order_id=order_id))
    return jsonify(order)


@app.post("/api/orders")
def create_order() -> Any:
    _maybe_slow("create_order")
    payload = request.get_json(silent=True) or {}
    sku = payload.get("sku")
    qty = payload.get("qty")
    user_id = payload.get("user_id", "u-100")

    if not sku or not isinstance(qty, int) or qty < 1:
        logger.warning(
            "invalid create order payload",
            extra=_base_extra(payload=payload, error_type="ValidationError", error_code=400),
        )
        return jsonify({"error": "ValidationError", "message": "sku and qty (int >= 1) required"}), 400

    failed = _maybe_random_error("create_order")
    if failed:
        body, code = failed
        return jsonify(body), code

    if sku not in PRODUCTS:
        logger.warning(
            "cannot create order unknown sku=%s",
            sku,
            extra=_base_extra(sku=sku, error_type="UnknownSku", error_code=400),
        )
        return jsonify({"error": "UnknownSku", "sku": sku}), 400

    if PRODUCTS[sku]["stock"] < qty:
        logger.error(
            "insufficient stock sku=%s requested=%s available=%s",
            sku,
            qty,
            PRODUCTS[sku]["stock"],
            extra=_base_extra(
                sku=sku,
                qty=qty,
                stock=PRODUCTS[sku]["stock"],
                error_type="InsufficientStock",
                error_code=409,
            ),
        )
        return jsonify({"error": "InsufficientStock", "sku": sku, "available": PRODUCTS[sku]["stock"]}), 409

    order = {
        "id": f"ord-{uuid.uuid4().hex[:8]}",
        "sku": sku,
        "qty": qty,
        "status": "created",
        "user_id": user_id,
    }
    ORDERS.append(order)
    PRODUCTS[sku]["stock"] -= qty
    logger.info(
        "order created id=%s sku=%s qty=%s user_id=%s",
        order["id"],
        sku,
        qty,
        user_id,
        extra=_base_extra(order_id=order["id"], sku=sku, qty=qty, user_id=user_id),
    )
    return jsonify(order), 201


@app.post("/api/payments")
def create_payment() -> Any:
    _maybe_slow("create_payment")
    payload = request.get_json(silent=True) or {}
    order_id = payload.get("order_id")
    amount = payload.get("amount")

    if not order_id or amount is None:
        logger.warning(
            "invalid payment payload",
            extra=_base_extra(payload=payload, error_type="ValidationError", error_code=400),
        )
        return jsonify({"error": "ValidationError", "message": "order_id and amount required"}), 400

    failed = _maybe_random_error("create_payment")
    if failed:
        body, code = failed
        return jsonify(body), code

    payment = {
        "id": f"pay-{uuid.uuid4().hex[:8]}",
        "order_id": order_id,
        "amount": amount,
        "status": "approved" if random.random() > 0.1 else "declined",
    }
    if payment["status"] == "declined":
        logger.error(
            "payment declined payment_id=%s order_id=%s amount=%s",
            payment["id"],
            order_id,
            amount,
            extra=_base_extra(
                payment_id=payment["id"],
                order_id=order_id,
                amount=amount,
                error_type="PaymentDeclined",
                error_code=402,
            ),
        )
        return jsonify({"error": "PaymentDeclined", "payment": payment}), 402

    logger.info(
        "payment approved payment_id=%s order_id=%s amount=%s",
        payment["id"],
        order_id,
        amount,
        extra=_base_extra(payment_id=payment["id"], order_id=order_id, amount=amount),
    )
    return jsonify(payment), 201


@app.get("/api/inventory/<sku>")
def get_inventory(sku: str) -> Any:
    failed = _maybe_random_error("get_inventory")
    if failed:
        body, code = failed
        return jsonify(body), code

    product = PRODUCTS.get(sku)
    if not product:
        logger.warning(
            "inventory miss sku=%s",
            sku,
            extra=_base_extra(sku=sku, error_type="InventoryNotFound", error_code=404),
        )
        return jsonify({"error": "InventoryNotFound", "sku": sku}), 404

    logger.info(
        "inventory check sku=%s stock=%s",
        sku,
        product["stock"],
        extra=_base_extra(sku=sku, stock=product["stock"]),
    )
    return jsonify({"sku": sku, "stock": product["stock"]})


if __name__ == "__main__":
    logger.info(
        "starting %s gelf=%s:%s error_rate=%s",
        APP_NAME,
        GELF_HOST,
        GELF_PORT,
        ERROR_RATE,
        extra={"app": APP_NAME, "error_rate": ERROR_RATE},
    )
    app.run(host="0.0.0.0", port=PORT)
