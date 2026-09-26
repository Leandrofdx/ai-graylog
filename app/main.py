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

PAYMENT_ALIASES = {
    "avista": "avista",
    "cash": "avista",
    "1": "avista",
    "cartao": "cartao",
    "card": "cartao",
    "2": "cartao",
    "pix": "pix",
    "5": "pix",
    "cdc": "cdc",
    "3": "cdc",
    "cdci": "cdci",
}


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


def _norm_payment(raw: Any) -> str:
    key = str(raw or "avista").strip().lower()
    return PAYMENT_ALIASES.get(key, "avista")


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
                text("SELECT cpf, name, account_num, wage FROM customers WHERE cpf = :cpf"),
                {"cpf": cpf},
            ).mappings().first()
        if not row:
            return jsonify({"error": "CustomerNotFound", "cpf": cpf}), 404
        return jsonify(
            {
                "cpf": row["cpf"],
                "name": row["name"],
                "accountNum": row["account_num"],
                "wage": float(row["wage"]),
            }
        )
    except SATimeoutError:
        return jsonify({"error": "DbPoolTimeout"}), 503


@app.get("/Customer/api/CustomerLimits")
def customer_limits() -> Any:
    """Limites de crédito (CDC / CDCI / CP) — espelha CustomerLimits do Assistente."""
    cpf = (request.args.get("cpf") or request.args.get("cpfCnpj") or "").strip()
    product_type = (request.args.get("productType") or "CDC").upper()
    if not cpf:
        return jsonify({"error": "ValidationError", "message": "cpf required"}), 400
    try:
        with db_session() as db:
            row = db.execute(
                text(
                    "SELECT c.cpf, c.name, c.account_num, l.available_limit, l.used_limit, l.product_types "
                    "FROM customers c JOIN credit_limits l ON l.cpf = c.cpf WHERE c.cpf = :cpf"
                ),
                {"cpf": cpf},
            ).mappings().first()
        if not row:
            return jsonify({"error": "LimitsNotFound", "cpf": cpf, "isCreditApproved": False}), 424
        allowed = product_type in (row["product_types"] or [])
        available = float(row["available_limit"]) - float(row["used_limit"])
        logger.info(
            "customer limits cpf=%s type=%s available=%s",
            cpf,
            product_type,
            available,
            extra=_extra(cpf=cpf, product_type=product_type, available_limit=available),
        )
        return jsonify(
            {
                "cpf": row["cpf"],
                "name": row["name"],
                "accountNum": row["account_num"],
                "productType": product_type,
                "isCreditApproved": allowed and available > 0,
                "availableLimit": round(available, 2),
                "usedLimit": float(row["used_limit"]),
                "productTypes": list(row["product_types"] or []),
            }
        )
    except SATimeoutError:
        return jsonify({"error": "DbPoolTimeout"}), 503


@app.post("/PaymentCondition/api/FinancialConditions/Find/<store_id>")
def financial_conditions(store_id: str) -> Any:
    """Condições financeiras base (CDC tende a vir Fin25 no real — lab força Crediare no BatchSimulate)."""
    payload = request.get_json(silent=True) or {}
    product_type = _norm_payment(payload.get("productType") or payload.get("paymentType") or "cdc").upper()
    if product_type not in ("CDC", "CDCI"):
        product_type = "CDC"
    try:
        with db_session() as db:
            rows = db.execute(
                text(
                    "SELECT id, product_type, tender_type_id, name, num_of_payment, interest_rate, "
                    "retail_financial_external_approval, financeira FROM financial_plans "
                    "WHERE product_type = :pt ORDER BY num_of_payment"
                ),
                {"pt": product_type},
            ).mappings().all()
        plans = [
            {
                "planId": r["id"],
                "productType": r["product_type"],
                "tenderTypeId": r["tender_type_id"],
                "name": r["name"],
                "numOfPayment": r["num_of_payment"],
                "interestRate": float(r["interest_rate"]),
                "retailFinancialExternalApproval": r["retail_financial_external_approval"],
                "financeira": r["financeira"],
            }
            for r in rows
        ]
        base = plans[0] if plans else None
        # Espelha quirk: CDC basePlan às vezes vem Fin25 — no lab CDC base = Crediare 12x
        if product_type == "CDC":
            base = next((p for p in plans if p["numOfPayment"] == 12), base)
        logger.info(
            "financial conditions store=%s type=%s plans=%s",
            store_id,
            product_type,
            len(plans),
            extra=_extra(store_id=store_id, product_type=product_type, plans=len(plans)),
        )
        return jsonify({"storeId": store_id, "productType": product_type, "basePlan": base, "plans": plans})
    except SATimeoutError:
        return jsonify({"error": "DbPoolTimeout"}), 503


@app.get("/PaymentCondition/api/PaymentCondition/Find/<store_id>")
def payment_condition_find(store_id: str) -> Any:
    """Opções de pagamento da loja (à vista / cartão / PIX / CDC tender 2006 / CDCI tender 2011)."""
    try:
        with db_session() as db:
            rows = db.execute(
                text(
                    "SELECT id, product_type, tender_type_id, name, num_of_payment, interest_rate, "
                    "retail_financial_external_approval, financeira FROM financial_plans "
                    "ORDER BY product_type, num_of_payment"
                )
            ).mappings().all()
        financed = [
            {
                "planId": r["id"],
                "productType": r["product_type"],
                "tenderTypeId": r["tender_type_id"],
                "name": r["name"],
                "numOfPayment": r["num_of_payment"],
                "interestRate": float(r["interest_rate"]),
                "retailFinancialExternalApproval": r["retail_financial_external_approval"],
                "financeira": r["financeira"],
            }
            for r in rows
        ]
        conditions = [
            {"paymentConditionType": 1, "name": "À vista", "tenderTypeId": None},
            {"paymentConditionType": 2, "name": "Cartão", "tenderTypeId": 1},
            {"paymentConditionType": 5, "name": "PIX", "tenderTypeId": 5},
            {"paymentConditionType": 3, "name": "CDC", "tenderTypeId": 2006, "plans": [p for p in financed if p["productType"] == "CDC"]},
            {"paymentConditionType": 3, "name": "CDCI", "tenderTypeId": 2011, "plans": [p for p in financed if p["productType"] == "CDCI"]},
        ]
        return jsonify({"storeId": store_id, "paymentConditions": conditions})
    except SATimeoutError:
        return jsonify({"error": "DbPoolTimeout"}), 503


@app.post("/InstallmentSimulator/api/InstallmentSimulator/BatchSimulate")
def batch_simulate() -> Any:
    """'Ver mais planos' — CDC força Financeira 12; CDCI só approval=3 / tender 2011."""
    payload = request.get_json(silent=True) or {}
    product_type = _norm_payment(payload.get("productType") or payload.get("paymentType") or "cdc").upper()
    if product_type not in ("CDC", "CDCI"):
        product_type = "CDC"
    amount = float(payload.get("amount") or payload.get("total") or 0)
    min_installments = int(payload.get("minInstallments") or (12 if product_type == "CDC" else 20))
    try:
        with db_session() as db:
            rows = db.execute(
                text(
                    "SELECT id, product_type, tender_type_id, name, num_of_payment, interest_rate, "
                    "retail_financial_external_approval, financeira FROM financial_plans "
                    "WHERE product_type = :pt AND num_of_payment >= :min_n ORDER BY num_of_payment"
                ),
                {"pt": product_type, "min_n": min_installments},
            ).mappings().all()
        successful = []
        for r in rows:
            if product_type == "CDC" and r["tender_type_id"] != 2006:
                continue
            if product_type == "CDCI" and (
                r["tender_type_id"] != 2011 or r["retail_financial_external_approval"] != 3
            ):
                continue
            n = r["num_of_payment"]
            rate = float(r["interest_rate"]) / 100
            installment = round(amount * (1 + rate * n / 12) / n, 2) if amount and n else 0
            successful.append(
                {
                    "planId": r["id"],
                    "productType": r["product_type"],
                    "tenderTypeId": r["tender_type_id"],
                    "name": r["name"],
                    "numOfPayment": n,
                    "interestRate": float(r["interest_rate"]),
                    "installmentValue": installment,
                    "totalValue": round(installment * n, 2) if n else 0,
                    "retailFinancialExternalApproval": r["retail_financial_external_approval"],
                    "financeira": r["financeira"],
                }
            )
        if not successful:
            return jsonify({"error": "NoPlans", "message": f"sem planos {product_type}"}), 404
        logger.info(
            "batch simulate type=%s amount=%s plans=%s",
            product_type,
            amount,
            len(successful),
            extra=_extra(product_type=product_type, amount=amount, plans=len(successful)),
        )
        return jsonify({"productType": product_type, "successful": successful, "failed": []})
    except SATimeoutError:
        return jsonify({"error": "DbPoolTimeout"}), 503


@app.post("/SalesOrder/api/CreatePreSales")
def create_pre_sales() -> Any:
    payload = request.get_json(silent=True) or {}
    staff_id = payload.get("staffId") or g.staff_id
    cpf = payload.get("cpf")
    item_id = payload.get("itemId")
    qty = payload.get("qty", 1)
    payment_type = _norm_payment(payload.get("paymentType") or payload.get("paymentConditionType"))
    installments = payload.get("installments") or payload.get("numOfPayment")
    tender = payload.get("tenderTypeId")

    if not staff_id or not cpf or not item_id or not isinstance(qty, int) or qty < 1:
        return jsonify({"error": "ValidationError", "message": "staffId, cpf, itemId, qty required"}), 400

    if payment_type == "cdc":
        tender = int(tender or 2006)
        installments = int(installments or 12)
    elif payment_type == "cdci":
        tender = int(tender or 2011)
        installments = int(installments or 20)
    else:
        tender = int(tender) if tender is not None else None
        installments = int(installments) if installments is not None else None

    sale_id = f"ASS-{uuid.uuid4().hex[:10].upper()}"
    proposal_id = None
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

                if payment_type in ("cdc", "cdci"):
                    lim = db.execute(
                        text(
                            "SELECT available_limit, used_limit, product_types FROM credit_limits "
                            "WHERE cpf = :cpf FOR UPDATE"
                        ),
                        {"cpf": cpf},
                    ).mappings().first()
                    if not lim:
                        return jsonify({"error": "LimitsNotFound", "cpf": cpf}), 424
                    available = Decimal(str(lim["available_limit"])) - Decimal(str(lim["used_limit"]))
                    ptype = payment_type.upper()
                    if ptype not in (lim["product_types"] or []) or available < amount:
                        logger.warning(
                            "credit limit exceeded cpf=%s type=%s amount=%s available=%s",
                            cpf,
                            ptype,
                            amount,
                            available,
                            extra=_extra(error_type="CreditLimitExceeded", cpf=cpf, product_type=ptype),
                        )
                        return jsonify(
                            {
                                "error": "CreditLimitExceeded",
                                "availableLimit": float(available),
                                "amount": float(amount),
                            }
                        ), 409
                    db.execute(
                        text("UPDATE credit_limits SET used_limit = used_limit + :amt WHERE cpf = :cpf"),
                        {"amt": amount, "cpf": cpf},
                    )

                db.execute(
                    text("UPDATE products SET stock = stock - :qty WHERE item_id = :id"),
                    {"qty": qty, "id": item_id},
                )
                db.execute(
                    text(
                        "INSERT INTO pre_sales "
                        "(id, staff_id, cpf, item_id, qty, amount, payment_type, tender_type_id, installments, status) "
                        "VALUES (:id, :staff, :cpf, :item, :qty, :amount, :pay, :tender, :inst, :status)"
                    ),
                    {
                        "id": sale_id,
                        "staff": staff_id,
                        "cpf": cpf,
                        "item": item_id,
                        "qty": qty,
                        "amount": amount,
                        "pay": payment_type,
                        "tender": tender,
                        "inst": installments,
                        "status": "created",
                    },
                )

                if payment_type in ("cdc", "cdci"):
                    proposal_id = f"PROP-{uuid.uuid4().hex[:8].upper()}"
                    financeira = "Financeira 12" if payment_type == "cdc" else "Financeira 25"
                    db.execute(
                        text(
                            "INSERT INTO proposals "
                            "(id, sale_id, staff_id, cpf, product_type, tender_type_id, installments, amount, status, financeira) "
                            "VALUES (:id, :sale, :staff, :cpf, :pt, :tender, :inst, :amount, :status, :fin)"
                        ),
                        {
                            "id": proposal_id,
                            "sale": sale_id,
                            "staff": staff_id,
                            "cpf": cpf,
                            "pt": payment_type.upper(),
                            "tender": tender,
                            "inst": installments,
                            "amount": amount,
                            "status": "pending_integration",
                            "fin": financeira,
                        },
                    )

        logger.info(
            "pre-sale created id=%s pay=%s staff=%s item=%s qty=%s amount=%s",
            sale_id,
            payment_type,
            staff_id,
            item_id,
            qty,
            amount,
            extra=_extra(
                sale_id=sale_id,
                payment_type=payment_type,
                staff_id=staff_id,
                item_id=item_id,
                qty=qty,
                amount=float(amount),
                proposal_id=proposal_id or "",
            ),
        )
        if span and span.is_recording():
            span.set_attribute("sale.id", sale_id)
            span.set_attribute("sale.amount", float(amount))
            span.set_attribute("sale.payment_type", payment_type)
        return jsonify(
            {
                "saleId": sale_id,
                "status": "created",
                "staffId": staff_id,
                "cpf": cpf,
                "itemId": item_id,
                "qty": qty,
                "amount": float(amount),
                "paymentType": payment_type,
                "tenderTypeId": tender,
                "installments": installments,
                "proposalId": proposal_id,
                "serializedPreSales": {
                    "sale": {
                        "installmentSimulator": {
                            "paymentCondition": {
                                "tenderTypeId": tender,
                                "numOfPayment": installments,
                                "paymentType": payment_type,
                            }
                        }
                    }
                },
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


@app.get("/MultiFinancial/api/Proposals")
def list_proposals() -> Any:
    """Monitor de Propostas (CDC / CDCI)."""
    try:
        with db_session() as db:
            rows = db.execute(
                text(
                    "SELECT p.id, p.sale_id, p.staff_id, p.cpf, c.name AS customer_name, "
                    "p.product_type, p.tender_type_id, p.installments, p.amount, p.status, "
                    "p.financeira, p.created_at "
                    "FROM proposals p JOIN customers c ON c.cpf = p.cpf "
                    "ORDER BY p.created_at DESC LIMIT 50"
                )
            ).mappings().all()
        out = []
        for r in rows:
            out.append(
                {
                    "proposalId": r["id"],
                    "saleId": r["sale_id"],
                    "staffId": r["staff_id"],
                    "cpf": r["cpf"],
                    "customerName": r["customer_name"],
                    "productType": r["product_type"],
                    "tenderTypeId": r["tender_type_id"],
                    "installments": r["installments"],
                    "amount": float(r["amount"]),
                    "status": r["status"],
                    "financeira": r["financeira"],
                    "createdAt": r["created_at"].isoformat() if r.get("created_at") else None,
                }
            )
        return jsonify({"proposals": out})
    except SATimeoutError:
        return jsonify({"error": "DbPoolTimeout"}), 503


@app.post("/MultiFinancial/api/IntegrateProposal")
def integrate_proposal() -> Any:
    """Enviar proposta CDC/CDCI para a financeira."""
    payload = request.get_json(silent=True) or {}
    proposal_id = payload.get("proposalId")
    if not proposal_id:
        return jsonify({"error": "ValidationError", "message": "proposalId required"}), 400
    try:
        with db_session() as db:
            with db.begin():
                row = db.execute(
                    text("SELECT id, status, financeira, product_type FROM proposals WHERE id = :id FOR UPDATE"),
                    {"id": proposal_id},
                ).mappings().first()
                if not row:
                    return jsonify({"error": "ProposalNotFound"}), 404
                db.execute(
                    text("UPDATE proposals SET status = :st WHERE id = :id"),
                    {"st": "integrated", "id": proposal_id},
                )
        logger.info(
            "proposal integrated id=%s fin=%s",
            proposal_id,
            row["financeira"],
            extra=_extra(proposal_id=proposal_id, financeira=row["financeira"], product_type=row["product_type"]),
        )
        return jsonify(
            {
                "proposalId": proposal_id,
                "status": "integrated",
                "statusLabel": "Proposta integrada na financeira!",
                "financeira": row["financeira"],
            }
        )
    except SATimeoutError:
        return jsonify({"error": "DbPoolTimeout"}), 503


@app.post("/PersonalCredit/api/Create")
def create_personal_credit() -> Any:
    """Crédito Pessoal (CP) — fora do carrinho de produto."""
    payload = request.get_json(silent=True) or {}
    staff_id = payload.get("staffId") or g.staff_id
    cpf = payload.get("cpf") or payload.get("cpfCnpj")
    amount = payload.get("amount")
    installments = int(payload.get("installments") or 12)

    if not staff_id or not cpf or amount is None:
        return jsonify({"error": "ValidationError", "message": "staffId, cpf, amount required"}), 400
    try:
        amount_d = Decimal(str(amount))
        if amount_d <= 0 or installments < 1:
            return jsonify({"error": "ValidationError", "message": "amount/installments inválidos"}), 400

        cp_id = f"CP-{uuid.uuid4().hex[:10].upper()}"
        with db_session() as db:
            with db.begin():
                cust = db.execute(text("SELECT cpf FROM customers WHERE cpf = :cpf"), {"cpf": cpf}).first()
                if not cust:
                    return jsonify({"error": "CustomerNotFound", "cpf": cpf}), 404

                lim = db.execute(
                    text(
                        "SELECT available_limit, used_limit, product_types FROM credit_limits "
                        "WHERE cpf = :cpf FOR UPDATE"
                    ),
                    {"cpf": cpf},
                ).mappings().first()
                if not lim or "CP" not in (lim["product_types"] or []):
                    return jsonify({"error": "CpNotAllowed", "cpf": cpf}), 424
                available = Decimal(str(lim["available_limit"])) - Decimal(str(lim["used_limit"]))
                if available < amount_d:
                    return jsonify(
                        {"error": "CreditLimitExceeded", "availableLimit": float(available), "amount": float(amount_d)}
                    ), 409

                db.execute(
                    text("UPDATE credit_limits SET used_limit = used_limit + :amt WHERE cpf = :cpf"),
                    {"amt": amount_d, "cpf": cpf},
                )
                db.execute(
                    text(
                        "INSERT INTO personal_credits "
                        "(id, staff_id, cpf, amount, installments, status, financeira) "
                        "VALUES (:id, :staff, :cpf, :amount, :inst, :status, :fin)"
                    ),
                    {
                        "id": cp_id,
                        "staff": staff_id,
                        "cpf": cpf,
                        "amount": amount_d,
                        "inst": installments,
                        "status": "pending",
                        "fin": "Crediare",
                    },
                )

        logger.info(
            "personal credit created id=%s cpf=%s amount=%s",
            cp_id,
            cpf,
            amount_d,
            extra=_extra(cp_id=cp_id, cpf=cpf, amount=float(amount_d), installments=installments),
        )
        return jsonify(
            {
                "proposalId": cp_id,
                "cpf": cpf,
                "amount": float(amount_d),
                "installments": installments,
                "status": "pending",
                "financeira": "Crediare",
                "productType": "CP",
            }
        ), 201
    except SATimeoutError:
        return jsonify({"error": "DbPoolTimeout"}), 503
    except OperationalError as exc:
        logger.error("cp create db: %s", exc, extra=_extra(error_type="DbOperationalError"))
        return jsonify({"error": "DbOperationalError"}), 503


@app.get("/PersonalCredit/api/Proposals")
def list_personal_credits() -> Any:
    """Monitor de Crédito Pessoal."""
    try:
        with db_session() as db:
            rows = db.execute(
                text(
                    "SELECT p.id, p.staff_id, p.cpf, c.name AS customer_name, p.amount, "
                    "p.installments, p.status, p.financeira, p.created_at "
                    "FROM personal_credits p JOIN customers c ON c.cpf = p.cpf "
                    "ORDER BY p.created_at DESC LIMIT 50"
                )
            ).mappings().all()
        out = [
            {
                "proposalId": r["id"],
                "staffId": r["staff_id"],
                "cpf": r["cpf"],
                "customerName": r["customer_name"],
                "amount": float(r["amount"]),
                "installments": r["installments"],
                "status": r["status"],
                "financeira": r["financeira"],
                "productType": "CP",
                "createdAt": r["created_at"].isoformat() if r.get("created_at") else None,
            }
            for r in rows
        ]
        return jsonify({"proposals": out})
    except SATimeoutError:
        return jsonify({"error": "DbPoolTimeout"}), 503


@app.post("/chat/api/chat")
def chat_lia() -> Any:
    """Chat simplificado da Lia (lab) — sem LLM pago."""
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"error": "ValidationError"}), 400
    lower = message.lower()
    if "cdc" in lower or "cdci" in lower:
        reply = "CDC é Crediare (Financeira 12). CDCI é Fin25 (Financeira 25). Use o carrinho → forma de pagamento e o Monitor de Propostas."
    elif "crédito pessoal" in lower or "credito pessoal" in lower or lower.strip() == "cp":
        reply = "Crédito Pessoal (CP) fica no menu Monitor de Crédito Pessoal — é fora do carrinho de produto."
    elif "estoque" in lower or "stock" in lower:
        reply = "Posso ajudar com estoque. Use a busca de produtos ou consulte Stock/Find com o código do item."
    elif "preço" in lower or "preco" in lower:
        reply = "Os preços aparecem na busca de produtos. Quer que eu sugira um item em promoção do lab?"
    elif "cliente" in lower:
        reply = "Informe o CPF na aba Cliente. Massa lab: 52998224725 ou 39053344705."
    else:
        reply = "Olá! Sou a Lia (lab). Posso orientar sobre produtos, estoque, CDC, CDCI, CP e pré-venda."
    logger.info("lia chat", extra=_extra(chat_preview=message[:80]))
    return jsonify({"message": reply, "direction": "incoming"})


@app.get("/api/pre-sales")
def list_pre_sales() -> Any:
    try:
        with db_session() as db:
            rows = db.execute(
                text(
                    "SELECT id, staff_id, cpf, item_id, qty, amount, payment_type, tender_type_id, "
                    "installments, status, created_at "
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
