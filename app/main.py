from __future__ import annotations

import os
import time
import uuid
from decimal import Decimal
from typing import Any

from flask import Flask, g, jsonify, request, send_from_directory
from opentelemetry import trace
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError, TimeoutError as SATimeoutError

from common import db_session, get_engine, instrument_flask, ping_db, setup_logging, setup_tracing

SERVICE = os.getenv("APP_NAME", "assistente-vendas")
PORT = int(os.getenv("PORT", "8080"))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

logger = setup_logging(SERVICE)
tracer = setup_tracing(SERVICE)
app = Flask(SERVICE, static_folder=STATIC_DIR, static_url_path="/static")
instrument_flask(app)
get_engine()


def _ids() -> tuple[str, str]:
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if ctx and ctx.is_valid:
        return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
    return "", ""


def _extra(**kwargs: Any) -> dict[str, Any]:
    trace_id, span_id = _ids()
    data = {
        "service": SERVICE,
        "http_method": request.method,
        "http_path": request.path,
        "request_id": getattr(g, "request_id", ""),
        "test_run_id": getattr(g, "test_run_id", ""),
        "trace_id": trace_id,
        "span_id": span_id,
        "staff_id": getattr(g, "staff_id", ""),
    }
    data.update(kwargs)
    return data


@app.before_request
def _before() -> None:
    g.started_at = time.perf_counter()
    g.request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
    g.test_run_id = request.headers.get("X-Test-Run-Id", "")
    g.staff_id = request.headers.get("X-Staff-Id", "")


@app.after_request
def _after(response: Any) -> Any:
    duration_ms = round((time.perf_counter() - g.started_at) * 1000, 2)
    response.headers["X-Request-Id"] = g.request_id
    tid, _ = _ids()
    if tid:
        response.headers["X-Trace-Id"] = tid
    span = trace.get_current_span()
    if span and span.is_recording():
        span.set_attribute("http.status_code", response.status_code)
        if g.staff_id:
            span.set_attribute("staff.id", g.staff_id)

    if request.path.startswith("/api") or "/api/" in request.path or request.path == "/health":
        level = logger.info
        if response.status_code >= 500:
            level = logger.error
        elif response.status_code >= 400:
            level = logger.warning
        level(
            "%s %s -> %s (%sms)",
            request.method,
            request.path,
            response.status_code,
            duration_ms,
            extra=_extra(http_status=response.status_code, duration_ms=duration_ms),
        )
    return response


@app.get("/")
def index() -> Any:
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/health")
def health() -> Any:
    try:
        ping_db()
        return jsonify({"status": "ok", "service": SERVICE, "db": "up"})
    except Exception as exc:  # noqa: BLE001
        logger.error("db down: %s", exc, extra=_extra(error_type="DbDown"))
        return jsonify({"status": "degraded", "db": "down"}), 503


@app.post("/UserAuthentication/api/Authorize")
def authorize() -> Any:
    payload = request.get_json(silent=True) or {}
    staff_id = payload.get("staffId") or payload.get("staff_id")
    password = payload.get("password")
    if not staff_id or not password:
        return jsonify({"error": "ValidationError", "message": "staffId and password required"}), 400
    try:
        with db_session() as db:
            row = db.execute(
                text("SELECT staff_id, name, store_id, password FROM staff WHERE staff_id = :id"),
                {"id": staff_id},
            ).mappings().first()
        if not row or row["password"] != password:
            logger.warning("login failed staff_id=%s", staff_id, extra=_extra(error_type="AuthFailed", staff_id=staff_id))
            return jsonify({"error": "Unauthorized", "message": "credenciais inválidas"}), 401
        token = f"lab-token-{uuid.uuid4().hex[:16]}"
        logger.info("login ok staff_id=%s", staff_id, extra=_extra(staff_id=staff_id, store_id=row["store_id"]))
        return jsonify(
            {
                "token": token,
                "staffId": row["staff_id"],
                "name": row["name"],
                "storeId": row["store_id"],
                "position": "Vendedor",
            }
        )
    except SATimeoutError:
        logger.error("db pool timeout on login", extra=_extra(error_type="DbPoolTimeout"))
        return jsonify({"error": "DbPoolTimeout"}), 503
    except OperationalError as exc:
        logger.error("db error on login: %s", exc, extra=_extra(error_type="DbOperationalError"))
        return jsonify({"error": "DbOperationalError"}), 503


@app.get("/Products/api/Products/Search")
def search_products() -> Any:
    q = (request.args.get("q") or request.args.get("query") or "").strip()
    try:
        with db_session() as db:
            if q:
                rows = db.execute(
                    text(
                        "SELECT item_id, name, price, stock FROM products "
                        "WHERE item_id ILIKE :q OR name ILIKE :q ORDER BY name LIMIT 50"
                    ),
                    {"q": f"%{q}%"},
                ).mappings().all()
            else:
                rows = db.execute(
                    text("SELECT item_id, name, price, stock FROM products ORDER BY name LIMIT 50")
                ).mappings().all()
        products = [
            {
                "itemId": r["item_id"],
                "name": r["name"],
                "price": float(r["price"]),
                "stock": r["stock"],
            }
            for r in rows
        ]
        logger.info("product search q=%s hits=%s", q, len(products), extra=_extra(query=q, hits=len(products)))
        return jsonify({"products": products})
    except SATimeoutError:
        return jsonify({"error": "DbPoolTimeout"}), 503


@app.get("/Stock/api/Stock/Find")
def find_stock() -> Any:
    item_id = request.args.get("itemId") or request.args.get("sku")
    if not item_id:
        return jsonify({"error": "ValidationError", "message": "itemId required"}), 400
    try:
        with db_session() as db:
            # FOR UPDATE sob carga gera contention real de estoque
            row = db.execute(
                text("SELECT item_id, name, stock, price FROM products WHERE item_id = :id FOR UPDATE"),
                {"id": item_id},
            ).mappings().first()
            db.rollback()
        if not row:
            return jsonify({"error": "NotFound", "itemId": item_id}), 404
        return jsonify(
            {
                "itemId": row["item_id"],
                "name": row["name"],
                "physicalStock": row["stock"],
                "price": float(row["price"]),
            }
        )
    except SATimeoutError:
        logger.error("stock find pool timeout item=%s", item_id, extra=_extra(error_type="DbPoolTimeout", item_id=item_id))
        return jsonify({"error": "DbPoolTimeout"}), 503
    except OperationalError as exc:
        logger.error("stock find db error: %s", exc, extra=_extra(error_type="DbOperationalError", item_id=item_id))
        return jsonify({"error": "DbOperationalError"}), 503


@app.get("/Customer/api/Customer/FindByCpfCnpj")
def find_customer() -> Any:
    cpf = (request.args.get("cpf") or request.args.get("cpfCnpj") or "").strip()
    if not cpf:
        return jsonify({"error": "ValidationError", "message": "cpf required"}), 400
    try:
        with db_session() as db:
            row = db.execute(
                text("SELECT cpf, name, account_num FROM customers WHERE cpf = :cpf"),
                {"cpf": cpf},
            ).mappings().first()
        if not row:
            return jsonify({"error": "CustomerNotFound", "cpf": cpf}), 404
        return jsonify({"cpf": row["cpf"], "name": row["name"], "accountNum": row["account_num"]})
    except SATimeoutError:
        return jsonify({"error": "DbPoolTimeout"}), 503


@app.post("/SalesOrder/api/CreatePreSales")
def create_pre_sales() -> Any:
    payload = request.get_json(silent=True) or {}
    staff_id = payload.get("staffId") or g.staff_id
    cpf = payload.get("cpf")
    item_id = payload.get("itemId")
    qty = payload.get("qty", 1)

    if not staff_id or not cpf or not item_id or not isinstance(qty, int) or qty < 1:
        return jsonify({"error": "ValidationError", "message": "staffId, cpf, itemId, qty required"}), 400

    sale_id = f"ps-{uuid.uuid4().hex[:10]}"
    span = trace.get_current_span()

    try:
        with db_session() as db:
            with db.begin():
                cust = db.execute(text("SELECT cpf FROM customers WHERE cpf = :cpf"), {"cpf": cpf}).first()
                if not cust:
                    return jsonify({"error": "CustomerNotFound", "cpf": cpf}), 404

                product = db.execute(
                    text("SELECT item_id, price, stock FROM products WHERE item_id = :id FOR UPDATE"),
                    {"id": item_id},
                ).mappings().first()
                if not product:
                    return jsonify({"error": "ProductNotFound", "itemId": item_id}), 404

                if product["stock"] < qty:
                    logger.warning(
                        "insufficient stock item=%s qty=%s available=%s",
                        item_id,
                        qty,
                        product["stock"],
                        extra=_extra(error_type="InsufficientStock", item_id=item_id, qty=qty, stock=product["stock"]),
                    )
                    if span and span.is_recording():
                        span.set_attribute("error.type", "InsufficientStock")
                    return jsonify({"error": "InsufficientStock", "available": product["stock"]}), 409

                amount = Decimal(str(product["price"])) * qty
                db.execute(
                    text("UPDATE products SET stock = stock - :qty WHERE item_id = :id"),
                    {"qty": qty, "id": item_id},
                )
                db.execute(
                    text(
                        "INSERT INTO pre_sales (id, staff_id, cpf, item_id, qty, amount, status) "
                        "VALUES (:id, :staff, :cpf, :item, :qty, :amount, :status)"
                    ),
                    {
                        "id": sale_id,
                        "staff": staff_id,
                        "cpf": cpf,
                        "item": item_id,
                        "qty": qty,
                        "amount": amount,
                        "status": "created",
                    },
                )

        logger.info(
            "pre-sale created id=%s staff=%s item=%s qty=%s amount=%s",
            sale_id,
            staff_id,
            item_id,
            qty,
            amount,
            extra=_extra(sale_id=sale_id, staff_id=staff_id, item_id=item_id, qty=qty, amount=float(amount)),
        )
        if span and span.is_recording():
            span.set_attribute("sale.id", sale_id)
            span.set_attribute("sale.amount", float(amount))
        return jsonify(
            {
                "saleId": sale_id,
                "status": "created",
                "staffId": staff_id,
                "cpf": cpf,
                "itemId": item_id,
                "qty": qty,
                "amount": float(amount),
            }
        ), 201
    except SATimeoutError:
        logger.error("create pre-sale pool timeout", extra=_extra(error_type="DbPoolTimeout", item_id=item_id))
        return jsonify({"error": "DbPoolTimeout"}), 503
    except IntegrityError:
        logger.error("create pre-sale integrity", extra=_extra(error_type="DbIntegrityError", item_id=item_id))
        return jsonify({"error": "DbIntegrityError"}), 409
    except OperationalError as exc:
        logger.error("create pre-sale db: %s", exc, extra=_extra(error_type="DbOperationalError"))
        return jsonify({"error": "DbOperationalError"}), 503


@app.post("/chat/api/chat")
def chat_lia() -> Any:
    """Chat simplificado da Lia (lab) — sem LLM pago."""
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"error": "ValidationError"}), 400
    lower = message.lower()
    if "estoque" in lower or "stock" in lower:
        reply = "Posso ajudar com estoque. Use a busca de produtos ou consulte Stock/Find com o código do item."
    elif "preço" in lower or "preco" in lower:
        reply = "Os preços aparecem na busca de produtos. Quer que eu sugira um item em promoção do lab?"
    elif "cliente" in lower:
        reply = "Informe o CPF na aba Cliente. Massa lab: 52998224725 ou 39053344705."
    else:
        reply = "Olá! Sou a Lia (lab). Posso orientar sobre produtos, estoque e pré-venda neste Assistente."
    logger.info("lia chat", extra=_extra(chat_preview=message[:80]))
    return jsonify({"message": reply, "direction": "incoming"})


@app.get("/api/pre-sales")
def list_pre_sales() -> Any:
    try:
        with db_session() as db:
            rows = db.execute(
                text(
                    "SELECT id, staff_id, cpf, item_id, qty, amount, status, created_at "
                    "FROM pre_sales ORDER BY created_at DESC LIMIT 30"
                )
            ).mappings().all()
        out = []
        for r in rows:
            item = dict(r)
            item["amount"] = float(item["amount"])
            item["created_at"] = item["created_at"].isoformat() if item.get("created_at") else None
            out.append(item)
        return jsonify({"preSales": out})
    except SATimeoutError:
        return jsonify({"error": "DbPoolTimeout"}), 503


if __name__ == "__main__":
    logger.info("starting Assistente de Vendas lab", extra={"service": SERVICE})
    app.run(host="0.0.0.0", port=PORT, threaded=True)
