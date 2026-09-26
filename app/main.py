from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import requests
from flask import Flask, g, jsonify, request, send_from_directory
from opentelemetry import trace
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, TimeoutError as SATimeoutError

from common import db_session, get_engine, instrument_flask, ping_db, setup_logging, setup_tracing

SERVICE = os.getenv("APP_NAME", "labops-assistant")
PORT = int(os.getenv("PORT", "8080"))
GRAYLOG_URL = os.getenv("GRAYLOG_URL", "http://graylog:9000").rstrip("/")
GRAYLOG_USER = os.getenv("GRAYLOG_USER", "admin")
GRAYLOG_PASSWORD = os.getenv("GRAYLOG_PASSWORD", "admin")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

logger = setup_logging(SERVICE)
tracer = setup_tracing(SERVICE)
app = Flask(SERVICE, static_folder=STATIC_DIR, static_url_path="/static")
instrument_flask(app)
get_engine()


def _trace_ids() -> tuple[str, str]:
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if ctx and ctx.is_valid:
        return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
    return "", ""


def _extra(**kwargs: Any) -> dict[str, Any]:
    trace_id, span_id = _trace_ids()
    data = {
        "service": SERVICE,
        "http_method": request.method,
        "http_path": request.path,
        "request_id": getattr(g, "request_id", ""),
        "test_run_id": getattr(g, "test_run_id", ""),
        "trace_id": trace_id,
        "span_id": span_id,
    }
    data.update(kwargs)
    return data


@app.before_request
def _before() -> None:
    g.started_at = time.perf_counter()
    g.request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
    g.test_run_id = request.headers.get("X-Test-Run-Id", "")


@app.after_request
def _after(response: Any) -> Any:
    duration_ms = round((time.perf_counter() - g.started_at) * 1000, 2)
    response.headers["X-Request-Id"] = g.request_id
    trace_id, _ = _trace_ids()
    if trace_id:
        response.headers["X-Trace-Id"] = trace_id
    span = trace.get_current_span()
    if span and span.is_recording():
        span.set_attribute("http.status_code", response.status_code)
        span.set_attribute("request.id", g.request_id)
        if g.test_run_id:
            span.set_attribute("test.run_id", g.test_run_id)

    if request.path.startswith("/api/") or request.path == "/health":
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
        logger.error("health db down: %s", exc, extra=_extra(error_type="DbDown"))
        return jsonify({"status": "degraded", "service": SERVICE, "db": "down"}), 503


def _save_message(db: Any, conversation_id: str, role: str, content: str, tool_name: str | None = None) -> str:
    msg_id = f"msg-{uuid.uuid4().hex[:10]}"
    trace_id, _ = _trace_ids()
    db.execute(
        text(
            "INSERT INTO messages (id, conversation_id, role, content, tool_name, trace_id) "
            "VALUES (:id, :cid, :role, :content, :tool, :trace)"
        ),
        {
            "id": msg_id,
            "cid": conversation_id,
            "role": role,
            "content": content,
            "tool": tool_name,
            "trace": trace_id or None,
        },
    )
    return msg_id


def _ensure_conversation(db: Any, conversation_id: str | None, title: str) -> str:
    if conversation_id:
        row = db.execute(
            text("SELECT id FROM conversations WHERE id = :id"), {"id": conversation_id}
        ).first()
        if row:
            return conversation_id
    cid = conversation_id or f"conv-{uuid.uuid4().hex[:10]}"
    db.execute(
        text("INSERT INTO conversations (id, title) VALUES (:id, :title) ON CONFLICT (id) DO NOTHING"),
        {"id": cid, "title": title[:120]},
    )
    return cid


@app.get("/api/conversations")
def list_conversations() -> Any:
    try:
        with db_session() as db:
            rows = db.execute(
                text(
                    "SELECT id, title, created_at FROM conversations ORDER BY created_at DESC LIMIT 50"
                )
            ).mappings().all()
        items = []
        for r in rows:
            item = dict(r)
            item["created_at"] = item["created_at"].isoformat() if item.get("created_at") else None
            items.append(item)
        return jsonify({"conversations": items})
    except SATimeoutError:
        logger.error("db pool timeout listing conversations", extra=_extra(error_type="DbPoolTimeout"))
        return jsonify({"error": "DbPoolTimeout"}), 503
    except OperationalError as exc:
        logger.error("db error listing conversations: %s", exc, extra=_extra(error_type="DbOperationalError"))
        return jsonify({"error": "DbOperationalError"}), 503


@app.get("/api/conversations/<conversation_id>")
def get_conversation(conversation_id: str) -> Any:
    try:
        with db_session() as db:
            conv = db.execute(
                text("SELECT id, title, created_at FROM conversations WHERE id = :id"),
                {"id": conversation_id},
            ).mappings().first()
            if not conv:
                return jsonify({"error": "NotFound"}), 404
            msgs = db.execute(
                text(
                    "SELECT id, role, content, tool_name, trace_id, created_at "
                    "FROM messages WHERE conversation_id = :id ORDER BY created_at ASC"
                ),
                {"id": conversation_id},
            ).mappings().all()
        out_msgs = []
        for m in msgs:
            item = dict(m)
            item["created_at"] = item["created_at"].isoformat() if item.get("created_at") else None
            out_msgs.append(item)
        c = dict(conv)
        c["created_at"] = c["created_at"].isoformat() if c.get("created_at") else None
        return jsonify({"conversation": c, "messages": out_msgs})
    except SATimeoutError:
        return jsonify({"error": "DbPoolTimeout"}), 503


def _tool_system_status() -> dict[str, Any]:
    with tracer.start_as_current_span("tool.system_status") as span:
        span.set_attribute("tool.name", "system_status")
        result: dict[str, Any] = {"tool": "system_status", "checked_at": datetime.now(timezone.utc).isoformat()}
        try:
            ping_db()
            result["database"] = "up"
        except Exception as exc:  # noqa: BLE001
            result["database"] = f"down: {exc}"
            span.set_attribute("error.type", "DbDown")

        try:
            r = requests.get(
                f"{GRAYLOG_URL}/api/system/lbstatus",
                auth=(GRAYLOG_USER, GRAYLOG_PASSWORD),
                headers={"X-Requested-By": "labops-assistant", "Accept": "text/plain"},
                timeout=5,
            )
            result["graylog"] = r.text.strip() if r.ok else f"http_{r.status_code}"
            span.set_attribute("graylog.status", result["graylog"])
        except requests.RequestException as exc:
            result["graylog"] = f"unreachable: {exc}"
            span.record_exception(exc)
            span.set_attribute("error.type", "GraylogUnreachable")
        return result


def _tool_search_logs(query: str, limit: int = 20) -> dict[str, Any]:
    with tracer.start_as_current_span("tool.search_logs") as span:
        span.set_attribute("tool.name", "search_logs")
        span.set_attribute("graylog.query", query)
        # relative search last 1 hour
        params = {
            "query": query,
            "range": 3600,
            "limit": limit,
            "sort": "timestamp:desc",
        }
        try:
            r = requests.get(
                f"{GRAYLOG_URL}/api/search/universal/relative",
                params=params,
                auth=(GRAYLOG_USER, GRAYLOG_PASSWORD),
                headers={"X-Requested-By": "labops-assistant", "Accept": "application/json"},
                timeout=10,
            )
            if not r.ok:
                span.set_attribute("error.type", "GraylogSearchFailed")
                logger.error(
                    "graylog search failed status=%s body=%s",
                    r.status_code,
                    r.text[:300],
                    extra=_extra(error_type="GraylogSearchFailed", upstream_status=r.status_code),
                )
                return {"tool": "search_logs", "error": f"Graylog HTTP {r.status_code}", "query": query}

            data = r.json()
            total = data.get("total_results", 0)
            messages = []
            for item in data.get("messages", [])[:limit]:
                msg = item.get("message", {})
                messages.append(
                    {
                        "timestamp": msg.get("timestamp"),
                        "message": msg.get("message"),
                        "level": msg.get("level"),
                        "service": msg.get("service") or msg.get("source"),
                        "trace_id": msg.get("trace_id"),
                        "error_type": msg.get("error_type"),
                    }
                )
            span.set_attribute("graylog.total_results", total)
            logger.info(
                "graylog search query=%s total=%s",
                query,
                total,
                extra=_extra(tool="search_logs", graylog_total=total, query=query),
            )
            return {"tool": "search_logs", "query": query, "total": total, "messages": messages}
        except requests.RequestException as exc:
            span.record_exception(exc)
            span.set_attribute("error.type", "GraylogUnreachable")
            logger.error("graylog unreachable: %s", exc, extra=_extra(error_type="GraylogUnreachable"))
            return {"tool": "search_logs", "error": str(exc), "query": query}


def _tool_db_stats() -> dict[str, Any]:
    with tracer.start_as_current_span("tool.db_stats") as span:
        span.set_attribute("tool.name", "db_stats")
        try:
            with db_session() as db:
                convs = db.execute(text("SELECT COUNT(*) AS c FROM conversations")).scalar() or 0
                msgs = db.execute(text("SELECT COUNT(*) AS c FROM messages")).scalar() or 0
                tools = db.execute(text("SELECT COUNT(*) AS c FROM tool_runs")).scalar() or 0
                # query um pouco mais pesada sob carga (agg)
                top = db.execute(
                    text(
                        "SELECT tool_name, COUNT(*) AS c FROM tool_runs "
                        "GROUP BY tool_name ORDER BY c DESC LIMIT 5"
                    )
                ).mappings().all()
            out = {
                "tool": "db_stats",
                "conversations": int(convs),
                "messages": int(msgs),
                "tool_runs": int(tools),
                "tools_by_name": [dict(r) for r in top],
            }
            span.set_attribute("db.conversations", int(convs))
            span.set_attribute("db.messages", int(msgs))
            return out
        except SATimeoutError as exc:
            span.record_exception(exc)
            span.set_attribute("error.type", "DbPoolTimeout")
            logger.error("db pool timeout in db_stats", extra=_extra(error_type="DbPoolTimeout"))
            return {"tool": "db_stats", "error": "DbPoolTimeout"}
        except OperationalError as exc:
            span.record_exception(exc)
            span.set_attribute("error.type", "DbOperationalError")
            return {"tool": "db_stats", "error": "DbOperationalError", "detail": str(exc)}


def _run_tool(db: Any, conversation_id: str, name: str, fn: Any) -> dict[str, Any]:
    started = time.perf_counter()
    trace_id, _ = _trace_ids()
    run_id = f"tool-{uuid.uuid4().hex[:10]}"
    try:
        output = fn()
        status = "error" if output.get("error") else "ok"
    except Exception as exc:  # noqa: BLE001
        logger.exception("tool crashed name=%s", name, extra=_extra(error_type="ToolCrash", tool=name))
        output = {"tool": name, "error": str(exc)}
        status = "error"
    duration_ms = int((time.perf_counter() - started) * 1000)
    db.execute(
        text(
            "INSERT INTO tool_runs (id, conversation_id, tool_name, input_json, output_json, status, duration_ms, trace_id) "
            "VALUES (:id, :cid, :name, CAST(:inp AS jsonb), CAST(:out AS jsonb), :status, :ms, :trace)"
        ),
        {
            "id": run_id,
            "cid": conversation_id,
            "name": name,
            "inp": "{}",
            "out": json.dumps(output),
            "status": status,
            "ms": duration_ms,
            "trace": trace_id or None,
        },
    )
    _save_message(db, conversation_id, "tool", json.dumps(output, ensure_ascii=False)[:4000], tool_name=name)
    return output


def _decide_and_reply(user_text: str, tools_out: list[dict[str, Any]]) -> str:
    """Assistente determinístico (free) — parece Agent, sem LLM pago."""
    lines = ["Analisei sua pergunta no lab."]
    for t in tools_out:
        name = t.get("tool")
        if name == "system_status":
            lines.append(
                f"- **Status**: database=`{t.get('database')}`, graylog=`{t.get('graylog')}`"
            )
        elif name == "search_logs":
            if t.get("error"):
                lines.append(f"- **Logs**: falha ao consultar Graylog (`{t['error']}`).")
            else:
                lines.append(f"- **Logs**: query `{t.get('query')}` → **{t.get('total', 0)}** hits na última 1h.")
                for m in (t.get("messages") or [])[:3]:
                    preview = (m.get("message") or "")[:120].replace("\n", " ")
                    tid = m.get("trace_id") or "-"
                    lines.append(f"  - `{preview}` (trace `{tid}`)")
        elif name == "db_stats":
            if t.get("error"):
                lines.append(f"- **DB**: erro `{t['error']}` (sob carga costuma ser pool timeout).")
            else:
                lines.append(
                    f"- **DB**: {t.get('conversations')} conversas, {t.get('messages')} msgs, "
                    f"{t.get('tool_runs')} tool runs."
                )
    lines.append("")
    lines.append("Próximos passos sugeridos: correlacionar `trace_id` no Jaeger e filtrar `service:labops-assistant` no Graylog.")
    return "\n".join(lines)


def _pick_tools(user_text: str) -> list[str]:
    text_l = user_text.lower()
    tools: list[str] = []
    if re.search(r"status|saud|health|alive|sistema", text_l):
        tools.append("system_status")
    if re.search(r"erro|error|log|falha|exception|500|503|timeout|trace", text_l):
        tools.append("search_logs")
    if re.search(r"db|banco|postgres|conversa|métrica|metric|stat|carga|pool", text_l):
        tools.append("db_stats")
    if not tools:
        tools = ["system_status", "search_logs"]
    return tools


@app.post("/api/chat")
def chat() -> Any:
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    conversation_id = payload.get("conversation_id")
    if not message:
        return jsonify({"error": "ValidationError", "message": "message is required"}), 400

    title = message if len(message) < 80 else message[:77] + "..."
    query_for_logs = payload.get("log_query") or "service:labops-assistant OR error_type:* OR level:ERROR"

    try:
        with db_session() as db:
            with db.begin():
                cid = _ensure_conversation(db, conversation_id, title)
                _save_message(db, cid, "user", message)

            tools_out: list[dict[str, Any]] = []
            with tracer.start_as_current_span("assistant.plan") as plan_span:
                chosen = _pick_tools(message)
                plan_span.set_attribute("assistant.tools", ",".join(chosen))
                logger.info(
                    "assistant plan tools=%s",
                    chosen,
                    extra=_extra(tools=",".join(chosen), conversation_id=cid),
                )
                for name in chosen:
                    with db_session() as db2:
                        with db2.begin():
                            if name == "system_status":
                                tools_out.append(_run_tool(db2, cid, name, _tool_system_status))
                            elif name == "search_logs":
                                tools_out.append(
                                    _run_tool(db2, cid, name, lambda: _tool_search_logs(query_for_logs))
                                )
                            elif name == "db_stats":
                                tools_out.append(_run_tool(db2, cid, name, _tool_db_stats))

            reply = _decide_and_reply(message, tools_out)
            trace_id, _ = _trace_ids()
            with db_session() as db3:
                with db3.begin():
                    _save_message(db3, cid, "assistant", reply)

            return jsonify(
                {
                    "conversation_id": cid,
                    "reply": reply,
                    "tools": tools_out,
                    "trace_id": trace_id,
                    "request_id": g.request_id,
                }
            )
    except SATimeoutError:
        logger.error("db pool timeout on chat", extra=_extra(error_type="DbPoolTimeout"))
        return jsonify({"error": "DbPoolTimeout", "message": "database connection pool exhausted"}), 503
    except OperationalError as exc:
        logger.error("db operational on chat: %s", exc, extra=_extra(error_type="DbOperationalError"))
        return jsonify({"error": "DbOperationalError"}), 503


@app.post("/api/tools/search-logs")
def api_search_logs() -> Any:
    payload = request.get_json(silent=True) or {}
    query = payload.get("query") or "level:ERROR"
    limit = int(payload.get("limit") or 20)
    return jsonify(_tool_search_logs(query, limit=limit))


@app.get("/api/tools/system-status")
def api_system_status() -> Any:
    return jsonify(_tool_system_status())


@app.get("/api/tools/db-stats")
def api_db_stats() -> Any:
    return jsonify(_tool_db_stats())


if __name__ == "__main__":
    logger.info("starting %s", SERVICE, extra={"service": SERVICE})
    app.run(host="0.0.0.0", port=PORT, threaded=True)
