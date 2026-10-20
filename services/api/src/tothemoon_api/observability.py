from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any, cast

import structlog
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"


SENSITIVE_FIELD_PATTERN = re.compile(
    r"(token|secret|password|passwd|seed|private[_-]?key|api[_-]?key|acknowledg|authorization|bearer)",
    re.IGNORECASE,
)
SENSITIVE_VALUE_PATTERN = re.compile(
    r"((?:authorization|bearer)\s*[=:]\s*Bearer\s+\S+|Bearer\s+\S+|"
    r"sk[_-](?:test|live)[_-][A-Za-z0-9_\-]+|whsec_[A-Za-z0-9_\-]+|"
    r"(?:api[_-]?key|token|secret|password|passwd|authorization|cookie|signature)\s*[=:]\s*[^\s,;]+)",
    re.IGNORECASE,
)
REDACTED_PLACEHOLDER = "[REDACTED]"


def redact_sensitive_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return SENSITIVE_VALUE_PATTERN.sub(REDACTED_PLACEHOLDER, value)


def redact_sensitive_fields(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor that redacts values for keys matching sensitive patterns.

    Runs before the JSON renderer so no raw secret ever hits stdout.
    Nested dicts are walked recursively; lists/tuples are replaced wholesale
    if they look suspicious (we cannot redact opaque sequences selectively).
    """

    def _walk(value: Any, *, parent_key: str = "") -> Any:
        if isinstance(value, dict):
            return {
                key: (
                    REDACTED_PLACEHOLDER
                    if SENSITIVE_FIELD_PATTERN.search(str(key))
                    else _walk(sub, parent_key=str(key))
                )
                for key, sub in value.items()
            }
        if isinstance(value, (list, tuple)) and SENSITIVE_FIELD_PATTERN.search(parent_key):
            return REDACTED_PLACEHOLDER
        return redact_sensitive_value(value)

    return {
        key: (
            REDACTED_PLACEHOLDER
            if SENSITIVE_FIELD_PATTERN.search(str(key))
            else _walk(value, parent_key=str(key))
        )
        for key, value in event_dict.items()
    }


SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


_CONFIGURED = False
_RUNTIME_OBSERVABILITY_CONFIGURED = False


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(message)s")

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            cast(Any, redact_sensitive_fields),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    configure_logging()
    return structlog.get_logger(name or "tothemoon_api")


def _parse_otlp_headers(raw: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for part in raw.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if key:
            headers[key] = value.strip()
    return headers


def bind_trace_context(**attrs: object) -> None:
    clean_attrs = {key: value for key, value in attrs.items() if value is not None}
    if not clean_attrs:
        return
    structlog.contextvars.bind_contextvars(**clean_attrs)
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span and span.is_recording():
            for key, value in clean_attrs.items():
                attr_value = value if isinstance(value, str | bool | int | float) else str(value)
                span.set_attribute(f"ttm.{key}", attr_value)
    except Exception:
        return


def configure_runtime_observability(app: Any, settings: object) -> None:
    """Wire optional tracing and error reporting.

    The integrations are intentionally opt-in: local/test environments should not
    need an OTLP collector or a Sentry DSN just to import the FastAPI app.
    """

    global _RUNTIME_OBSERVABILITY_CONFIGURED
    if _RUNTIME_OBSERVABILITY_CONFIGURED:
        return
    _RUNTIME_OBSERVABILITY_CONFIGURED = True

    log = get_logger(__name__)
    sentry_dsn = str(getattr(settings, "sentry_dsn", "") or "")
    if sentry_dsn:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.fastapi import FastApiIntegration
        except ImportError:
            log.warning("sentry_unavailable")
        else:
            sentry_sdk.init(
                dsn=sentry_dsn,
                environment=str(getattr(settings, "sentry_environment", "") or "local"),
                traces_sample_rate=float(getattr(settings, "sentry_traces_sample_rate", 0.0)),
                integrations=[FastApiIntegration()],
            )
            log.info("sentry_configured")

    otlp_endpoint = str(getattr(settings, "otel_exporter_otlp_endpoint", "") or "")
    if not otlp_endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("opentelemetry_unavailable")
        return

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": str(
                    getattr(settings, "otel_service_name", "") or "tothemoontokens-api"
                ),
                "deployment.environment": str(getattr(settings, "app_env", "") or "local"),
            }
        )
    )
    exporter = OTLPSpanExporter(
        endpoint=otlp_endpoint,
        headers=_parse_otlp_headers(str(getattr(settings, "otel_exporter_otlp_headers", "") or "")),
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    log.info("opentelemetry_configured", endpoint=otlp_endpoint)


HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests handled by the API.",
    ["method", "path", "status"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

BACKTESTS_RUN_TOTAL = Counter(
    "backtests_run_total",
    "Total backtests executed.",
    ["strategy_id", "edge_status"],
)

GUARDRAIL_EVALUATIONS_TOTAL = Counter(
    "guardrail_evaluations_total",
    "Guardrail evaluations by outcome.",
    ["mode", "can_submit_testnet"],
)

RATE_LIMIT_REJECTIONS_TOTAL = Counter(
    "rate_limit_rejections_total",
    "Requests rejected by the in-memory rate limiter.",
    ["path"],
)


class InMemoryRateLimiter:
    """Thread-safe sliding-window rate limiter.

    We intentionally keep this dependency-free: slowapi would pull in extra
    transitive deps (limits, redis stubs) for a two-endpoint use case. For a
    single-process API the sliding window is sufficient; if the service ever
    scales horizontally, swap the backing store for Redis.
    """

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, *, key: str, scope: str, limit: int, window_seconds: float) -> bool:
        if limit <= 0 or window_seconds <= 0:
            return True
        now = time.monotonic()
        cutoff = now - window_seconds
        bucket_key = (scope, key)
        with self._lock:
            bucket = self._hits[bucket_key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = InMemoryRateLimiter()


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if forwarded:
        return forwarded
    client = request.client
    return client.host if client else "unknown"


def enforce_rate_limit(request: Request, *, limit: int, window_seconds: float) -> Response | None:
    """Returns a 429 response when the caller exceeds the budget, else None."""

    scope = request.url.path
    key = _client_key(request)
    if rate_limiter.allow(key=key, scope=scope, limit=limit, window_seconds=window_seconds):
        return None
    RATE_LIMIT_REJECTIONS_TOTAL.labels(path=scope).inc()
    get_logger().warning(
        "rate_limit_rejected",
        client=key,
        path=scope,
        limit=limit,
        window_seconds=window_seconds,
    )
    retry_after = max(1, int(window_seconds))
    return JSONResponse(
        status_code=429,
        content={
            "ok": False,
            "error": "rate_limited",
            "message": "Too many requests. Slow down and try again shortly.",
            "retry_after_seconds": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )


class PrometheusMiddleware:
    """Pure ASGI middleware: avoids BaseHTTPMiddleware anyio overhead."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_holder = {"code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["code"] = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            route = scope.get("route")
            path = getattr(route, "path", None) or scope.get("path", "")
            method = scope.get("method", "")
            HTTP_REQUESTS_TOTAL.labels(
                method=method, path=path, status=str(status_holder["code"])
            ).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(method=method, path=path).observe(elapsed)


class RequestIdMiddleware:
    """Pure ASGI middleware for request ID binding + echo header."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER, "").strip()
        request_id = incoming if 0 < len(incoming) <= 128 else uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=scope.get("method", ""),
            path=scope.get("path", ""),
        )

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            structlog.contextvars.clear_contextvars()


class SecurityHeadersMiddleware:
    """Pure ASGI middleware: adds security headers without anyio overhead."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_wrapper)


def metrics_response() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def log_event(event: str, **fields: Any) -> None:
    get_logger().info(event, **fields)
