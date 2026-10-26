"""Bounded local telemetry for the API-to-worker user journey.

Only fixed event names and low-cardinality metric attributes are accepted here.
Run and request identifiers belong in restricted logs/traces, never metrics.
"""

import json
import logging
import os
import re
import sys
import time
from contextvars import ContextVar
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from opentelemetry import metrics, trace
from opentelemetry.metrics import Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


_request_id: ContextVar[str | None] = ContextVar("runguard_request_id", default=None)
_runtime_lock = Lock()
_runtime = None
_event_name = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")
_run_kind = frozenset({"website_monitor", "url_summarizer"})
_run_status = frozenset({"succeeded", "failed"})
_operation = frozenset({"register", "login", "worker_create", "enqueue"})
_outcome = frozenset({"success", "client_error", "server_error"})


def request_id() -> str | None:
    return _request_id.get()


def bind_request_id(value: str):
    return _request_id.set(value)


def reset_request_id(token) -> None:
    _request_id.reset(token)


def trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


def traceparent() -> str | None:
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    return carrier.get("traceparent")


def parent_context(value: str | None):
    if not value or not re.fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}", value):
        return None
    return TraceContextTextMapPropagator().extract({"traceparent": value})


def tracer():
    return trace.get_tracer("runguard.journey")


def _db_path(database_url: str | None) -> Path | None:
    if not database_url or not database_url.startswith("sqlite:///"):
        return None
    path = database_url.removeprefix("sqlite:///")
    return None if path == ":memory:" else Path(path)


@lru_cache(maxsize=8)
def _metric_engine(database_url: str):
    postgres = database_url.startswith("postgresql")
    return create_engine(database_url, poolclass=NullPool,
                         connect_args={"connect_timeout": 1} if postgres else {"timeout": 1.0})


def _metric_rows(options, statement: str, params: dict | None = None):
    database_url = _runtime.get("database_url") if _runtime else None
    if not database_url:
        return []
    path = _db_path(database_url)
    if path is not None and not path.exists():
        return []
    try:
        with _metric_engine(database_url).connect() as connection:
            if connection.dialect.name == "postgresql":
                timeout = max(1, min(int(options.timeout_millis), 1000))
                connection.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                                   {"timeout": f"{timeout}ms"})
            return connection.execute(text(statement), params or {}).all()
    except (SQLAlchemyError, OSError, ValueError):
        return []


def _queue_depth(options):
    rows = _metric_rows(options, "SELECT kind, COUNT(*) FROM runs "
                        "WHERE status = 'queued' AND kind IN ('website_monitor', 'url_summarizer') "
                        "GROUP BY kind LIMIT 2")
    counts = dict(rows)
    return [Observation(counts.get(kind, 0), {"kind": kind}) for kind in sorted(_run_kind)]


def _heartbeat_age(options):
    database_url = _runtime.get("database_url") if _runtime else None
    if not database_url:
        return []
    if database_url.startswith("postgresql"):
        rows = _metric_rows(options, "SELECT last_seen_at FROM worker_heartbeat WHERE id = 1")
        if not rows:
            return []
        try:
            seen = datetime.fromisoformat(rows[0][0].replace("Z", "+00:00"))
            age = max(0.0, (datetime.now(timezone.utc) - seen).total_seconds())
        except (TypeError, ValueError):
            return []
    else:
        path = _db_path(database_url)
        if path is None:
            return []
        try:
            age = max(0.0, time.time() - Path(f"{path.resolve()}.worker.lock").stat().st_mtime)
        except OSError:
            return []
    return [Observation(age)]


def _open_alerts(options):
    rows = _metric_rows(options, "SELECT kind, COUNT(*) FROM alerts "
                        "WHERE status = 'open' AND kind IN ('repeated_run_failure', 'worker_queue_stalled') "
                        "GROUP BY kind LIMIT 2")
    counts = dict(rows)
    return [Observation(counts.get(kind, 0), {"kind": kind})
            for kind in ("repeated_run_failure", "worker_queue_stalled")]


def _service_resource(service: str) -> Resource:
    # Keep Prometheus resource labels stable when either process restarts.
    return Resource.create({"service.name": service, "service.instance.id": service})


def configure(service: str, database_url: str | None = None) -> None:
    """Configure once per process; no exporter or network is used by default."""
    global _runtime
    with _runtime_lock:
        if _runtime is not None:
            if database_url is not None:
                _runtime["database_url"] = database_url
            return
        endpoint = os.environ.get("RUNGUARD_OTEL_ENDPOINT", "").rstrip("/")
        resource = _service_resource(service)
        traces = TracerProvider(resource=resource)
        readers = []
        if endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
            from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
            from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

            traces.add_span_processor(BatchSpanProcessor(
                OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", timeout=3)))
            readers.append(PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", timeout=3),
                export_interval_millis=5000))
            logs = LoggerProvider(resource=resource)
            logs.add_log_record_processor(BatchLogRecordProcessor(
                OTLPLogExporter(endpoint=f"{endpoint}/v1/logs", timeout=3)))
            operator_logger = logging.getLogger("runguard.telemetry")
            operator_logger.propagate = False
            operator_logger.setLevel(logging.INFO)
            operator_logger.addHandler(LoggingHandler(level=logging.INFO, logger_provider=logs))
        trace.set_tracer_provider(traces)
        meter_provider = MeterProvider(metric_readers=readers, resource=resource)
        metrics.set_meter_provider(meter_provider)
        meter = metrics.get_meter("runguard.journey")
        _runtime = {
            "database_url": database_url,
            "runs_enqueued": meter.create_counter("runguard_runs_enqueued", unit="1"),
            "runs_started": meter.create_counter("runguard_runs_started", unit="1"),
            "runs_completed": meter.create_counter("runguard_runs_completed", unit="1"),
            "duration": meter.create_histogram("runguard_run_duration_seconds", unit="s"),
            "queue_wait": meter.create_histogram("runguard_queue_wait_seconds", unit="s"),
            "api_outcomes": meter.create_counter("runguard_api_outcomes", unit="1"),
        }
        if service == "runguard-api":
            _runtime["heartbeat_gauge"] = meter.create_observable_gauge(
                "runguard_worker_heartbeat_age_seconds", callbacks=[_heartbeat_age], unit="s")
            _runtime["queue_depth_gauge"] = meter.create_observable_gauge(
                "runguard_queue_depth", callbacks=[_queue_depth])
            _runtime["open_alerts_gauge"] = meter.create_observable_gauge(
                "runguard_alerts_open", callbacks=[_open_alerts])


def emit(event: str, *, run_id: str | None = None, trace_id_value: str | None = None,
         level: str = "INFO", service: str = "runguard-api") -> None:
    """Emit fixed-name, content-free operational events to stdout and OTLP."""
    if not _event_name.fullmatch(event):
        raise ValueError("Operational event must be a fixed uppercase name")
    if level not in {"INFO", "WARN", "ERROR"}:
        raise ValueError("Invalid operational log level")
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "level": level,
        "severity": level,
        "service": service,
        "event": event,
        "message": event.replace("_", " ").lower(),
        "request_id": request_id(),
        "run_id": run_id,
        "trace_id": trace_id_value or trace_id(),
    }
    sys.stdout.write(json.dumps(record, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    if os.environ.get("RUNGUARD_OTEL_ENDPOINT"):
        logger = logging.getLogger("runguard.telemetry")
        logger.log(getattr(logging, level), record["message"],
                   extra={key: value for key, value in record.items() if key != "message" and value is not None})


def record_enqueue(kind: str) -> None:
    if _runtime and kind in _run_kind:
        _runtime["runs_enqueued"].add(1, {"kind": kind})


def record_start(kind: str, wait_seconds: float) -> None:
    if _runtime and kind in _run_kind:
        attributes = {"kind": kind}
        _runtime["runs_started"].add(1, attributes)
        _runtime["queue_wait"].record(max(0.0, wait_seconds), attributes)


def record_completion(kind: str, status: str, duration_seconds: float) -> None:
    if _runtime and kind in _run_kind and status in _run_status:
        _runtime["runs_completed"].add(1, {"kind": kind, "status": status})
        _runtime["duration"].record(max(0.0, duration_seconds), {"kind": kind})


def record_api_outcome(operation: str, status_code: int) -> None:
    if _runtime and operation in _operation:
        outcome = "success" if status_code < 400 else "client_error" if status_code < 500 else "server_error"
        if outcome in _outcome:
            _runtime["api_outcomes"].add(1, {"operation": operation, "outcome": outcome,
                                             "status_class": f"{status_code // 100}xx"})


def force_flush() -> None:
    if _runtime is not None:
        trace.get_tracer_provider().force_flush(timeout_millis=3000)
        metrics.get_meter_provider().force_flush(timeout_millis=3000)
