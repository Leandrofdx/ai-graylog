from __future__ import annotations

import os
import time
import uuid
import json
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from flask import Flask, g, jsonify, request, send_from_directory
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError, TimeoutError as SATimeoutError

from common import db_session, get_engine, instrument_flask, ping_db, setup_logging, setup_tracing

SERVICE = os.getenv("APP_NAME", "assistente-vendas")
PORT = int(os.getenv("PORT", "8080"))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
JAEGER_UI = os.getenv("JAEGER_UI_URL", "http://127.0.0.1:16686").rstrip("/")
GRAYLOG_UI = os.getenv("GRAYLOG_UI_URL", "http://127.0.0.1:9000").rstrip("/")


def _obs_ui_bases() -> tuple[str, str]:
    """Bases Jaeger/Graylog para deep links no browser (não usar 127.0.0.1 remoto)."""
    host_hdr = (request.headers.get("X-Forwarded-Host") or request.host or "").split(",")[0].strip()
    hostname = host_hdr.split(":")[0] if host_hdr else "127.0.0.1"
    local = hostname in {"127.0.0.1", "localhost", ""}

    jaeger_env = os.getenv("JAEGER_UI_URL", "").rstrip("/")
    graylog_env = os.getenv("GRAYLOG_UI_URL", "").rstrip("/")

    if jaeger_env and "127.0.0.1" not in jaeger_env and "localhost" not in jaeger_env:
        jaeger = jaeger_env
    elif local:
        jaeger = JAEGER_UI
    else:
        jaeger = f"http://{hostname}:16686"

    if graylog_env and "127.0.0.1" not in graylog_env and "localhost" not in graylog_env:
        graylog = graylog_env
    elif local:
        graylog = GRAYLOG_UI
    elif "duckdns" in hostname or hostname.endswith(".nip.io"):
        # Lab EC2: Graylog atrás do Caddy/LE na 443
        graylog = f"https://{hostname}"
    else:
        graylog = f"http://{hostname}:9000"

    return jaeger, graylog


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

PAYMENT_LABELS = {
    "avista": "À vista",
    "cartao": "Cartão",
    "pix": "PIX",
    "cdc": "CDC",
    "cdci": "CDCI",
}

# Seed do lab — estoque e crédito “infinitos” (boot / Restock / ResetCreditLimit).
SEED_STOCK: dict[str, int] = {
    "SKU-7": 999_999,
    "SKU-42": 999_999,
    "SKU-99": 999_999,
    "SKU-15": 999_999,
    "SKU-88": 999_999,
}
SEED_CREDIT: dict[str, dict[str, Any]] = {
    # CPF lab → limite absurdo + todas as linhas
    "52998224725": {"available": 999_999_999.0, "types": ["CDC", "CDCI", "CP"]},
    "39053344705": {"available": 999_999_999.0, "types": ["CDC", "CDCI", "CP"]},
}


def _payment_label(payment_type: str) -> str:
    return PAYMENT_LABELS.get(payment_type, payment_type or "—")


def _restock_products(db: Any, *, force: bool = False) -> list[dict[str, Any]]:
    """Repoe estoque ao seed. force=True aplica seed; senão só top-up se abaixo."""
    out: list[dict[str, Any]] = []
    for item_id, seed in SEED_STOCK.items():
        if force:
            row = db.execute(
                text(
                    "UPDATE products SET stock = :stock WHERE item_id = :id "
                    "RETURNING item_id, name, stock"
                ),
                {"id": item_id, "stock": seed},
            ).mappings().first()
        else:
            row = db.execute(
                text(
                    "UPDATE products SET stock = :stock "
                    "WHERE item_id = :id AND stock < :stock "
                    "RETURNING item_id, name, stock"
                ),
                {"id": item_id, "stock": seed},
            ).mappings().first()
            if not row:
                row = db.execute(
                    text("SELECT item_id, name, stock FROM products WHERE item_id = :id"),
                    {"id": item_id},
                ).mappings().first()
        if row:
            out.append({"itemId": row["item_id"], "name": row["name"], "stock": int(row["stock"])})
    return out


def _reset_credits(db: Any) -> list[dict[str, Any]]:
    """Lab: available_limit = seed, used_limit = 0, product_types completos."""
    out: list[dict[str, Any]] = []
    for cpf, cfg in SEED_CREDIT.items():
        row = db.execute(
            text(
                "UPDATE credit_limits SET "
                "available_limit = :avail, used_limit = 0, "
                "product_types = ARRAY['CDC','CDCI','CP']::text[] "
                "WHERE cpf = :cpf "
                "RETURNING cpf, available_limit, used_limit, product_types"
            ),
            {"cpf": cpf, "avail": cfg["available"]},
        ).mappings().first()
        if row:
            out.append(
                {
                    "cpf": row["cpf"],
                    "availableLimit": float(row["available_limit"]),
                    "usedLimit": float(row["used_limit"]),
                    "productTypes": list(row["product_types"] or []),
                }
            )
    return out


def _ensure_schema() -> None:
    """Lab: category + estoque no teto + crédito liberado (DBs persistidos)."""
    try:
        with db_session() as db:
            db.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'Geral'"))
            db.execute(
                text(
                    "UPDATE products SET category = CASE item_id "
                    "WHEN 'SKU-7' THEN 'Informática' "
                    "WHEN 'SKU-99' THEN 'Informática' "
                    "WHEN 'SKU-42' THEN 'Acessórios' "
                    "WHEN 'SKU-15' THEN 'Acessórios' "
                    "WHEN 'SKU-88' THEN 'Telefonia' "
                    "ELSE COALESCE(NULLIF(category,''), 'Geral') END"
                )
            )
            restored = _restock_products(db, force=True)
            credits = _reset_credits(db)
            db.commit()
            logger.info(
                "schema/stock/credit ensure ok products=%s credits=%s",
                len(restored),
                len(credits),
                extra={"action": "LabEnsure", "items": len(restored), "credits": len(credits)},
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("schema ensure skipped: %s", exc)


def _ids() -> tuple[str, str]:
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if ctx and ctx.is_valid:
        return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
    return "", ""


def _extra(**kwargs: Any) -> dict[str, Any]:
    trace_id, span_id = _ids()
    domain = _lab_domain()
    started = getattr(g, "started_at", None)
    duration_ms = (
        round((time.perf_counter() - started) * 1000, 2) if started is not None else None
    )
    # http_path = URL real (com ids); http_route = template Flask (estável p/ dashboards)
    route = ""
    try:
        if request.url_rule is not None:
            route = str(request.url_rule.rule or "")
    except RuntimeError:
        route = ""
    data = {
        "service": SERVICE,
        "spm_service": _spm_service(),
        "lab_domain": domain,
        "http_method": request.method,
        "http_path": request.path,
        "http_route": route or request.path,
        "request_id": getattr(g, "request_id", ""),
        "test_run_id": getattr(g, "test_run_id", ""),
        "journey_step": getattr(g, "journey_step", ""),
        "trace_id": trace_id,
        "span_id": span_id,
        "staff_id": getattr(g, "staff_id", ""),
    }
    if duration_ms is not None:
        data["duration_ms"] = duration_ms
    data.update(kwargs)
    # domain/spm always win so callers cannot blank them
    data["lab_domain"] = domain
    data["spm_service"] = f"assistente-{domain}"
    return data


def _tag_span(**attrs: Any) -> None:
    span = trace.get_current_span()
    if not span or not span.is_recording():
        return
    for key, value in attrs.items():
        if value is None or value == "":
            continue
        span.set_attribute(key, value)


def _lab_domain(path: str | None = None) -> str:
    """Espelha otel-collector: cada rota vira um serviço SPM (assistente-{domain})."""
    p = (path or request.path or "").lower()
    if "userauthentication" in p:
        return "auth"
    if "/products/" in p:
        return "catalogo"
    if "/stock/" in p:
        return "estoque"
    if "/customer/" in p:
        return "cliente"
    if "/paymentcondition/" in p or "/installmentsimulator/" in p:
        return "pagamento"
    if "/salesorder/" in p:
        return "venda"
    if "/multifinancial/" in p:
        return "propostas"
    if "/personalcredit/" in p:
        return "cp"
    return "plataforma"


def _spm_service(path: str | None = None) -> str:
    return f"assistente-{_lab_domain(path)}"


def _problem(error_type: str, status: int, body: dict[str, Any] | None = None, **fields: Any) -> Any:
    """Erro tipado: log GELF + atributos de span para cruzar Graylog ↔ Jaeger."""
    g.error_type = error_type
    _tag_span(**{"error.type": error_type, "error": True})
    span = trace.get_current_span()
    if span and span.is_recording():
        span.set_status(Status(StatusCode.ERROR, error_type))
    payload = {"error": error_type, **(body or {})}
    level = logger.error if status >= 500 else logger.warning
    level("%s status=%s", error_type, status, extra=_extra(error_type=error_type, http_status=status, **fields))
    return jsonify(payload), status


def _norm_payment(raw: Any) -> str:
    key = str(raw or "avista").strip().lower()
    return PAYMENT_ALIASES.get(key, "avista")


@app.before_request
def _before() -> None:
    g.started_at = time.perf_counter()
    g.request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
    g.test_run_id = request.headers.get("X-Test-Run-Id", "")
    g.journey_step = request.headers.get("X-Journey-Step", "")
    g.staff_id = request.headers.get("X-Staff-Id", "")
    g.error_type = ""
    _tag_span(
        **{
            "request.id": g.request_id,
            "test_run.id": g.test_run_id,
            "journey.step": g.journey_step,
            "staff.id": g.staff_id,
        }
    )


@app.after_request
def _after(response: Any) -> Any:
    duration_ms = round((time.perf_counter() - g.started_at) * 1000, 2)
    response.headers["X-Request-Id"] = g.request_id
    if g.test_run_id:
        response.headers["X-Test-Run-Id"] = g.test_run_id
    if g.journey_step:
        response.headers["X-Journey-Step"] = g.journey_step
    tid, _ = _ids()
    spm = _spm_service()
    jaeger_ui, graylog_ui = _obs_ui_bases()
    response.headers["X-Obs-Domain"] = _lab_domain()
    response.headers["X-Obs-Spm-Service"] = spm
    response.headers["X-Obs-Jaeger-Spm"] = f"{jaeger_ui}/monitor"
    if tid:
        response.headers["X-Trace-Id"] = tid
        # Deep links for the UI obs dock
        response.headers["X-Obs-Jaeger"] = f"{jaeger_ui}/trace/{tid}"
        if g.test_run_id:
            tags = json.dumps({"test_run.id": g.test_run_id}, separators=(",", ":"))
            response.headers["X-Obs-Jaeger-Journey"] = (
                f"{jaeger_ui}/search?service={quote(spm, safe='')}&tags={quote(tags)}"
            )
            response.headers["X-Obs-Graylog-Journey"] = (
                f"{graylog_ui}/search?q=test_run_id%3A%22{quote(g.test_run_id, safe='')}%22&rangetype=relative&relative=86400"
            )
        response.headers["X-Obs-Graylog-Trace"] = (
            f"{graylog_ui}/search?q=trace_id%3A{tid}&rangetype=relative&relative=86400"
        )

    span = trace.get_current_span()
    if span and span.is_recording():
        span.set_attribute("http.status_code", response.status_code)
        span.set_attribute("duration_ms", duration_ms)
        if g.staff_id:
            span.set_attribute("staff.id", g.staff_id)
        if g.test_run_id:
            span.set_attribute("test_run.id", g.test_run_id)
        if g.request_id:
            span.set_attribute("request.id", g.request_id)
        if g.journey_step:
            span.set_attribute("journey.step", g.journey_step)
        if getattr(g, "error_type", ""):
            span.set_attribute("error.type", g.error_type)

    if request.path.startswith("/api") or "/api/" in request.path or request.path == "/health":
        level = logger.info
        if response.status_code >= 500:
            level = logger.error
        elif response.status_code >= 400:
            level = logger.warning
        level(
            "%s %s -> %s (%sms)%s",
            request.method,
            request.path,
            response.status_code,
            duration_ms,
            f" step={g.journey_step}" if g.journey_step else "",
            extra=_extra(
                http_status=response.status_code,
                duration_ms=duration_ms,
                error_type=getattr(g, "error_type", "") or "",
            ),
        )
    return response


@app.get("/")
def index() -> Any:
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/docs")
@app.get("/swagger")
def swagger_ui() -> Any:
    return send_from_directory(STATIC_DIR, "swagger.html")


@app.get("/openapi.json")
def openapi_spec() -> Any:
    return send_from_directory(STATIC_DIR, "openapi.json")


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
        return _problem("ValidationError", 400, {"message": "staffId and password required"})
    try:
        with db_session() as db:
            row = db.execute(
                text("SELECT staff_id, name, store_id, password FROM staff WHERE staff_id = :id"),
                {"id": staff_id},
            ).mappings().first()
        if not row or row["password"] != password:
            return _problem("AuthFailed", 401, {"message": "credenciais inválidas"}, staff_id=staff_id)
        token = f"lab-token-{uuid.uuid4().hex[:16]}"
        logger.info("login ok staff_id=%s", staff_id, extra=_extra(staff_id=staff_id, store_id=row["store_id"]))
        _tag_span(**{"staff.id": row["staff_id"], "store.id": row["store_id"]})
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
        return _problem("DbPoolTimeout", 503)
    except OperationalError as exc:
        return _problem("DbOperationalError", 503, {"detail": str(exc)})


@app.get("/Products/api/Products/Search")
def search_products() -> Any:
    q = (request.args.get("q") or request.args.get("query") or "").strip()
    try:
        with db_session() as db:
            if q:
                rows = db.execute(
                    text(
                        "SELECT item_id, name, price, stock, category FROM products "
                        "WHERE item_id ILIKE :q OR name ILIKE :q OR category ILIKE :q ORDER BY name LIMIT 50"
                    ),
                    {"q": f"%{q}%"},
                ).mappings().all()
            else:
                rows = db.execute(
                    text("SELECT item_id, name, price, stock, category FROM products ORDER BY name LIMIT 50")
                ).mappings().all()
        products = [
            {
                "itemId": r["item_id"],
                "name": r["name"],
                "price": float(r["price"]),
                "stock": r["stock"],
                "category": r.get("category") or "Geral",
            }
            for r in rows
        ]
        cats = [p["category"] for p in products if p.get("category")]
        extra_fields: dict[str, Any] = {
            "biz_event": "product_search",
            "query": q,
            "hits": len(products),
        }
        if cats:
            # categoria dominante nos resultados (evita Empty Value no Graylog)
            freq: dict[str, int] = {}
            for c in cats:
                freq[c] = freq.get(c, 0) + 1
            extra_fields["product_category"] = max(freq.items(), key=lambda x: x[1])[0]
        logger.info(
            "product search q=%s hits=%s",
            q,
            len(products),
            extra=_extra(**extra_fields),
        )
        _tag_span(**{"search.query": q, "search.hits": len(products)})
        return jsonify({"products": products})
    except SATimeoutError:
        return _problem("DbPoolTimeout", 503, query=q)


@app.post("/Stock/api/Stock/Restock")
def restock() -> Any:
    """Lab: repõe estoque ao seed (esgota com JMeter/UI). body opcional: {\"force\": true}."""
    body = request.get_json(silent=True) or {}
    force = bool(body.get("force", True))
    try:
        with db_session() as db:
            items = _restock_products(db, force=force)
            db.commit()
        logger.info(
            "stock restocked force=%s count=%s",
            force,
            len(items),
            extra=_extra(action="StockRestock", force=force, items=len(items)),
        )
        _tag_span(**{"stock.restock": True, "stock.items": len(items)})
        return jsonify({"status": "restocked", "force": force, "products": items})
    except SATimeoutError:
        return _problem("DbPoolTimeout", 503)
    except OperationalError as exc:
        return _problem("DbOperationalError", 503, {"detail": str(exc)})


@app.get("/Stock/api/Stock/Find")
def find_stock() -> Any:
    item_id = request.args.get("itemId") or request.args.get("sku")
    if not item_id:
        return _problem("ValidationError", 400, {"message": "itemId required"})
    try:
        with db_session() as db:
            row = db.execute(
                text("SELECT item_id, name, stock, price FROM products WHERE item_id = :id FOR UPDATE"),
                {"id": item_id},
            ).mappings().first()
            db.rollback()
        if not row:
            return _problem("ProductNotFound", 404, {"itemId": item_id}, item_id=item_id)
        logger.info(
            "stock find item=%s stock=%s",
            item_id,
            row["stock"],
            extra=_extra(item_id=item_id, stock=row["stock"]),
        )
        _tag_span(**{"item.id": item_id, "stock.physical": int(row["stock"])})
        return jsonify(
            {
                "itemId": row["item_id"],
                "name": row["name"],
                "physicalStock": row["stock"],
                "price": float(row["price"]),
            }
        )
    except SATimeoutError:
        return _problem("DbPoolTimeout", 503, item_id=item_id)
    except OperationalError as exc:
        return _problem("DbOperationalError", 503, {"detail": str(exc)}, item_id=item_id)


@app.get("/Customer/api/Customer/FindByCpfCnpj")
def find_customer() -> Any:
    cpf = (request.args.get("cpf") or request.args.get("cpfCnpj") or "").strip()
    if not cpf:
        return _problem("ValidationError", 400, {"message": "cpf required"})
    try:
        with db_session() as db:
            row = db.execute(
                text("SELECT cpf, name, account_num, wage FROM customers WHERE cpf = :cpf"),
                {"cpf": cpf},
            ).mappings().first()
        if not row:
            return _problem("CustomerNotFound", 404, {"cpf": cpf}, cpf=cpf)
        logger.info("customer found cpf=%s", cpf, extra=_extra(cpf=cpf, customer_name=row["name"]))
        _tag_span(**{"customer.cpf": cpf})
        return jsonify(
            {
                "cpf": row["cpf"],
                "name": row["name"],
                "accountNum": row["account_num"],
                "wage": float(row["wage"]),
            }
        )
    except SATimeoutError:
        return _problem("DbPoolTimeout", 503, cpf=cpf)


@app.get("/Customer/api/CustomerLimits")
def customer_limits() -> Any:
    """Limites de crédito (CDC / CDCI / CP) — espelha CustomerLimits do Assistente."""
    cpf = (request.args.get("cpf") or request.args.get("cpfCnpj") or "").strip()
    product_type = (request.args.get("productType") or "CDC").upper()
    if not cpf:
        return _problem("ValidationError", 400, {"message": "cpf required"})
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
            return _problem("LimitsNotFound", 424, {"cpf": cpf, "isCreditApproved": False}, cpf=cpf)
        allowed = product_type in (row["product_types"] or [])
        available = float(row["available_limit"]) - float(row["used_limit"])
        if not allowed or available <= 0:
            _tag_span(**{"credit.approved": False, "credit.product_type": product_type})
            logger.warning(
                "credit not approved cpf=%s type=%s available=%s",
                cpf,
                product_type,
                available,
                extra=_extra(error_type="CreditNotApproved", cpf=cpf, product_type=product_type, available_limit=available),
            )
            g.error_type = "CreditNotApproved"
        logger.info(
            "customer limits cpf=%s type=%s available=%s",
            cpf,
            product_type,
            available,
            extra=_extra(cpf=cpf, product_type=product_type, available_limit=available),
        )
        _tag_span(
            **{
                "customer.cpf": cpf,
                "credit.product_type": product_type,
                "credit.available": available,
                "credit.approved": bool(allowed and available > 0),
            }
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
                "totalLimit": float(row["available_limit"]),
                "productTypes": list(row["product_types"] or []),
            }
        )
    except SATimeoutError:
        return _problem("DbPoolTimeout", 503, cpf=cpf)


@app.post("/Customer/api/ResetCreditLimit")
def reset_credit_limit() -> Any:
    """Lab: repõe available_limit ao seed e zera used_limit (todos ou um CPF)."""
    body = request.get_json(silent=True) or {}
    cpf = str(body.get("cpf") or request.args.get("cpf") or "").strip()
    try:
        with db_session() as db:
            if cpf:
                cfg = SEED_CREDIT.get(cpf) or {"available": 999_999_999.0, "types": ["CDC", "CDCI", "CP"]}
                row = db.execute(
                    text(
                        "UPDATE credit_limits SET "
                        "available_limit = :avail, used_limit = 0, "
                        "product_types = ARRAY['CDC','CDCI','CP']::text[] "
                        "WHERE cpf = :cpf "
                        "RETURNING cpf, available_limit, used_limit, product_types"
                    ),
                    {"cpf": cpf, "avail": cfg["available"]},
                ).mappings().first()
                if not row:
                    return _problem("LimitsNotFound", 404, {"cpf": cpf}, cpf=cpf)
                db.commit()
                items = [
                    {
                        "cpf": row["cpf"],
                        "availableLimit": float(row["available_limit"]),
                        "usedLimit": float(row["used_limit"]),
                        "productTypes": list(row["product_types"] or []),
                    }
                ]
            else:
                items = _reset_credits(db)
                db.commit()
        logger.info(
            "credit limit reset count=%s",
            len(items),
            extra=_extra(action="CreditLimitReset", cpf=cpf or "*", items=len(items)),
        )
        _tag_span(**{"credit.reset": True, "credit.items": len(items)})
        if cpf and len(items) == 1:
            return jsonify({**items[0], "status": "reset"})
        return jsonify({"status": "reset", "customers": items})
    except SATimeoutError:
        return _problem("DbPoolTimeout", 503, cpf=cpf)


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
            extra=_extra(
                biz_event="payment_plans_listed",
                store_id=store_id,
                product_type=product_type,
                payment_method=_payment_label(product_type.lower()),
                plans=len(plans),
                plan_name=base["name"] if base else "",
                installments=base["numOfPayment"] if base else 0,
                financeira=base["financeira"] if base else "",
            ),
        )
        _tag_span(**{"store.id": store_id, "credit.product_type": product_type, "plans.count": len(plans)})
        return jsonify({"storeId": store_id, "productType": product_type, "basePlan": base, "plans": plans})
    except SATimeoutError:
        return _problem("DbPoolTimeout", 503, store_id=store_id)


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
        logger.info(
            "payment conditions store=%s options=%s",
            store_id,
            len(conditions),
            extra=_extra(store_id=store_id, plans=len(financed)),
        )
        _tag_span(**{"store.id": store_id, "payment.conditions": len(conditions)})
        return jsonify({"storeId": store_id, "paymentConditions": conditions})
    except SATimeoutError:
        return _problem("DbPoolTimeout", 503, store_id=store_id)


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
            return _problem(
                "NoPlans",
                404,
                {"message": f"sem planos {product_type}"},
                product_type=product_type,
                amount=amount,
            )
        logger.info(
            "batch simulate type=%s amount=%s plans=%s",
            product_type,
            amount,
            len(successful),
            extra=_extra(
                biz_event="plan_simulated",
                product_type=product_type,
                payment_method=_payment_label(product_type.lower()),
                payment_type=product_type.lower(),
                amount=amount,
                plans=len(successful),
                plan_name=successful[0]["name"] if successful else "",
                installments=successful[0]["numOfPayment"] if successful else 0,
                financeira=successful[0]["financeira"] if successful else "",
            ),
        )
        _tag_span(**{"credit.product_type": product_type, "sale.amount": amount, "plans.count": len(successful)})
        return jsonify({"productType": product_type, "successful": successful, "failed": []})
    except SATimeoutError:
        return _problem("DbPoolTimeout", 503, product_type=product_type)


@app.post("/SalesOrder/api/CreatePreSales")
def create_pre_sales() -> Any:
    payload = request.get_json(silent=True) or {}
    staff_id = payload.get("staffId") or g.staff_id
    cpf = payload.get("cpf")
    item_id = payload.get("itemId")
    try:
        qty = int(payload.get("qty", 1))
    except (TypeError, ValueError):
        qty = 0
    payment_type = _norm_payment(payload.get("paymentType") or payload.get("paymentConditionType"))
    installments = payload.get("installments") or payload.get("numOfPayment")
    tender = payload.get("tenderTypeId")

    if not staff_id or not cpf or not item_id or qty < 1:
        return _problem("ValidationError", 400, {"message": "staffId, cpf, itemId, qty required"})

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
    plan_name = ""
    financeira = ""
    product_name = ""
    product_category = "Geral"
    try:
        with db_session() as db:
            with db.begin():
                cust = db.execute(text("SELECT cpf FROM customers WHERE cpf = :cpf"), {"cpf": cpf}).first()
                if not cust:
                    return _problem("CustomerNotFound", 404, {"cpf": cpf}, cpf=cpf)

                product = db.execute(
                    text("SELECT item_id, name, price, stock, category FROM products WHERE item_id = :id FOR UPDATE"),
                    {"id": item_id},
                ).mappings().first()
                if not product:
                    return _problem("ProductNotFound", 404, {"itemId": item_id}, item_id=item_id)

                if product["stock"] < qty:
                    return _problem(
                        "InsufficientStock",
                        409,
                        {"available": product["stock"]},
                        item_id=item_id,
                        qty=qty,
                        stock=product["stock"],
                    )

                amount = Decimal(str(product["price"])) * qty
                product_name = product["name"]
                product_category = product.get("category") or "Geral"
                if payment_type in ("cdc", "cdci") and installments:
                    plan_row = db.execute(
                        text(
                            "SELECT name, financeira FROM financial_plans "
                            "WHERE product_type = :pt AND num_of_payment = :n "
                            "ORDER BY id LIMIT 1"
                        ),
                        {"pt": payment_type.upper(), "n": int(installments)},
                    ).mappings().first()
                    if plan_row:
                        plan_name = plan_row["name"]
                        financeira = plan_row["financeira"]
                if payment_type in ("cdc", "cdci") and not financeira:
                    financeira = "Financeira 12" if payment_type == "cdc" else "Financeira 25"

                if payment_type in ("cdc", "cdci"):
                    lim = db.execute(
                        text(
                            "SELECT available_limit, used_limit, product_types FROM credit_limits "
                            "WHERE cpf = :cpf FOR UPDATE"
                        ),
                        {"cpf": cpf},
                    ).mappings().first()
                    if not lim:
                        return _problem("LimitsNotFound", 424, {"cpf": cpf}, cpf=cpf)
                    available = Decimal(str(lim["available_limit"])) - Decimal(str(lim["used_limit"]))
                    ptype = payment_type.upper()
                    if ptype not in (lim["product_types"] or []) or available < amount:
                        return _problem(
                            "CreditLimitExceeded",
                            409,
                            {"availableLimit": float(available), "amount": float(amount)},
                            cpf=cpf,
                            product_type=ptype,
                            amount=float(amount),
                        )
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
                    if not financeira:
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
            "pre-sale created id=%s pay=%s plan=%s item=%s category=%s amount=%s",
            sale_id,
            payment_type,
            plan_name or _payment_label(payment_type),
            item_id,
            product_category,
            amount,
            extra=_extra(
                biz_event="sale_created",
                sale_id=sale_id,
                payment_type=payment_type,
                payment_method=_payment_label(payment_type),
                plan_name=plan_name or _payment_label(payment_type),
                installments=int(installments or 0),
                financeira=financeira,
                product_type=payment_type.upper() if payment_type in ("cdc", "cdci") else "",
                staff_id=staff_id,
                item_id=item_id,
                product_name=product_name,
                product_category=product_category,
                qty=qty,
                amount=float(amount),
                proposal_id=proposal_id or "",
                proposal_status="pending_integration" if proposal_id else "",
            ),
        )
        if proposal_id:
            logger.info(
                "proposal created id=%s type=%s amount=%s",
                proposal_id,
                payment_type.upper(),
                amount,
                extra=_extra(
                    biz_event="proposal_created",
                    proposal_id=proposal_id,
                    proposal_status="pending_integration",
                    sale_id=sale_id,
                    payment_type=payment_type,
                    payment_method=_payment_label(payment_type),
                    plan_name=plan_name or _payment_label(payment_type),
                    installments=int(installments or 0),
                    financeira=financeira,
                    product_type=payment_type.upper(),
                    staff_id=staff_id,
                    item_id=item_id,
                    product_name=product_name,
                    product_category=product_category,
                    amount=float(amount),
                ),
            )
        _tag_span(
            **{
                "sale.id": sale_id,
                "sale.amount": float(amount),
                "sale.payment_type": payment_type,
                "customer.cpf": cpf,
                "item.id": item_id,
            }
        )
        if proposal_id:
            _tag_span(**{"proposal.id": proposal_id, "proposal.status": "pending_integration"})
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
        return _problem("DbPoolTimeout", 503, item_id=item_id)
    except IntegrityError:
        return _problem("DbIntegrityError", 409, item_id=item_id)
    except OperationalError as exc:
        return _problem("DbOperationalError", 503, {"detail": str(exc)})


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
        pending = sum(1 for p in out if p["status"] == "pending_integration")
        logger.info(
            "proposals listed count=%s pending=%s",
            len(out),
            pending,
            extra=_extra(
                biz_event="proposals_listed",
                proposals=len(out),
                pending=pending,
                product_type=out[0]["productType"] if out else "",
            ),
        )
        _tag_span(**{"proposals.count": len(out), "proposals.pending": pending})
        return jsonify({"proposals": out})
    except SATimeoutError:
        return _problem("DbPoolTimeout", 503)


@app.post("/MultiFinancial/api/IntegrateProposal")
def integrate_proposal() -> Any:
    """Enviar proposta CDC/CDCI para a financeira."""
    payload = request.get_json(silent=True) or {}
    proposal_id = payload.get("proposalId")
    if not proposal_id:
        return _problem("ValidationError", 400, {"message": "proposalId required"})
    try:
        with db_session() as db:
            with db.begin():
                row = db.execute(
                    text(
                        "SELECT id, status, financeira, product_type, installments, amount, sale_id "
                        "FROM proposals WHERE id = :id FOR UPDATE"
                    ),
                    {"id": proposal_id},
                ).mappings().first()
                if not row:
                    return _problem("ProposalNotFound", 404, {"proposalId": proposal_id}, proposal_id=proposal_id)
                db.execute(
                    text("UPDATE proposals SET status = :st WHERE id = :id"),
                    {"st": "integrated", "id": proposal_id},
                )
        logger.info(
            "proposal integrated id=%s fin=%s type=%s",
            proposal_id,
            row["financeira"],
            row["product_type"],
            extra=_extra(
                biz_event="proposal_integrated",
                proposal_id=proposal_id,
                proposal_status="integrated",
                sale_id=row.get("sale_id") or "",
                financeira=row["financeira"],
                product_type=row["product_type"],
                payment_method=row["product_type"],
                installments=int(row["installments"] or 0),
                amount=float(row["amount"]),
                plan_name=f"{row['product_type']} {row['installments']}x",
            ),
        )
        _tag_span(
            **{
                "proposal.id": proposal_id,
                "proposal.status": "integrated",
                "proposal.financeira": row["financeira"],
                "credit.product_type": row["product_type"],
            }
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
        return _problem("DbPoolTimeout", 503, proposal_id=proposal_id)


@app.post("/PersonalCredit/api/Create")
def create_personal_credit() -> Any:
    """Crédito Pessoal (CP) — fora do carrinho de produto."""
    payload = request.get_json(silent=True) or {}
    staff_id = payload.get("staffId") or g.staff_id
    cpf = payload.get("cpf") or payload.get("cpfCnpj")
    amount = payload.get("amount")
    installments = int(payload.get("installments") or 12)

    if not staff_id or not cpf or amount is None:
        return _problem("ValidationError", 400, {"message": "staffId, cpf, amount required"})
    try:
        amount_d = Decimal(str(amount))
        if amount_d <= 0 or installments < 1:
            return _problem("ValidationError", 400, {"message": "amount/installments inválidos"})

        cp_id = f"CP-{uuid.uuid4().hex[:10].upper()}"
        with db_session() as db:
            with db.begin():
                cust = db.execute(text("SELECT cpf FROM customers WHERE cpf = :cpf"), {"cpf": cpf}).first()
                if not cust:
                    return _problem("CustomerNotFound", 404, {"cpf": cpf}, cpf=cpf)

                lim = db.execute(
                    text(
                        "SELECT available_limit, used_limit, product_types FROM credit_limits "
                        "WHERE cpf = :cpf FOR UPDATE"
                    ),
                    {"cpf": cpf},
                ).mappings().first()
                if not lim or "CP" not in (lim["product_types"] or []):
                    return _problem("CpNotAllowed", 424, {"cpf": cpf}, cpf=cpf)
                available = Decimal(str(lim["available_limit"])) - Decimal(str(lim["used_limit"]))
                if available < amount_d:
                    return _problem(
                        "CreditLimitExceeded",
                        409,
                        {"availableLimit": float(available), "amount": float(amount_d)},
                        cpf=cpf,
                        amount=float(amount_d),
                    )

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
            extra=_extra(
                biz_event="cp_created",
                cp_id=cp_id,
                cpf=cpf,
                amount=float(amount_d),
                installments=installments,
                payment_method="CP",
                payment_type="cp",
                product_type="CP",
                plan_name=f"CP {installments}x",
                financeira="Crediare",
            ),
        )
        _tag_span(
            **{
                "cp.id": cp_id,
                "sale.amount": float(amount_d),
                "sale.payment_type": "cp",
                "customer.cpf": cpf,
                "cp.installments": installments,
            }
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
        return _problem("DbPoolTimeout", 503, cpf=cpf or "")
    except OperationalError as exc:
        return _problem("DbOperationalError", 503, {"detail": str(exc)})


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
        logger.info("cp proposals listed count=%s", len(out), extra=_extra(proposals=len(out)))
        _tag_span(**{"cp.proposals.count": len(out)})
        return jsonify({"proposals": out})
    except SATimeoutError:
        return _problem("DbPoolTimeout", 503)


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
    _ensure_schema()
    logger.info("starting Assistente de Vendas lab", extra={"service": SERVICE})
    app.run(host="0.0.0.0", port=PORT, threaded=True)
