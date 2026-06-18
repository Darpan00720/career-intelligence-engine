"""OpenTelemetry adapter (v5.1) for the observability port.

Bridges the in-process metrics/tracing facade (core.metrics, core.tracing) to
OpenTelemetry + a Prometheus exporter, when the OTel SDK is installed. Imports
are lazy so the platform runs without OTel in dev/test; in production set
OTEL_EXPORTER_OTLP_ENDPOINT and call install_otel() at startup.
"""
from __future__ import annotations

import os


def otel_available() -> bool:
    try:
        import opentelemetry  # noqa: F401
        return True
    except Exception:
        return False


def install_otel(service_name: str = "career-engine") -> bool:  # pragma: no cover - needs OTel
    """Wire OTel tracing + metrics exporters. Returns True if installed."""
    if not otel_available():
        return False
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    return True


def export_prometheus() -> str:
    """Return Prometheus exposition text from the in-process registry.

    Serve this from GET /api/v2/metrics/prometheus (already wired). A real
    Prometheus client can replace the registry transparently.
    """
    from core.metrics import registry
    return registry().render_prometheus()
