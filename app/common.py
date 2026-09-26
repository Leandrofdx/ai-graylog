from __future__ import annotations

import logging
import os
from typing import Any

import graypy
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def setup_logging(service_name: str) -> logging.Logger:
    logger = logging.getLogger(service_name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    class TraceFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            span = trace.get_current_span()
            ctx = span.get_span_context() if span else None
            if ctx and ctx.is_valid:
                record.trace_id = format(ctx.trace_id, "032x")  # type: ignore[attr-defined]
                record.span_id = format(ctx.span_id, "016x")  # type: ignore[attr-defined]
            else:
                record.trace_id = ""  # type: ignore[attr-defined]
                record.span_id = ""  # type: ignore[attr-defined]
            record.service = service_name  # type: ignore[attr-defined]
            return True

    filt = TraceFilter()
    gelf = graypy.GELFUDPHandler(os.getenv("GELF_HOST", "graylog"), int(os.getenv("GELF_PORT", "12201")))
    gelf.setFormatter(logging.Formatter("%(message)s"))
    gelf.addFilter(filt)
    logger.addHandler(gelf)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(service)s] trace=%(trace_id)s %(message)s"))
    console.addFilter(filt)
    logger.addHandler(console)
    return logger


def setup_tracing(service_name: str) -> trace.Tracer:
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": service_name,
                "service.namespace": "assistente-lab",
                "deployment.environment": os.getenv("ENV", "lab"),
            }
        )
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)


def instrument_flask(app: Any) -> None:
    FlaskInstrumentor().instrument_app(app)
    RequestsInstrumentor().instrument()


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is not None:
        return _engine
    _engine = create_engine(
        os.getenv("DATABASE_URL", "postgresql+psycopg2://lab:lab@postgres:5432/lab"),
        pool_size=int(os.getenv("DB_POOL_SIZE", "3")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "2")),
        pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "3")),
        pool_pre_ping=True,
    )
    SQLAlchemyInstrumentor().instrument(engine=_engine)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

    @event.listens_for(_engine, "checkout")
    def _on_checkout(dbapi_conn, connection_record, connection_proxy):  # noqa: ANN001
        span = trace.get_current_span()
        if span and span.is_recording():
            span.add_event("db.pool.checkout")

    return _engine


def db_session() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def ping_db() -> None:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
