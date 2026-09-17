"""OpenTelemetry tracing. No-op unless OTEL_ENABLED=true, so local runs need no collector.
Production: OTLP exporter -> OpenTelemetry Collector -> Azure Monitor / Application Insights."""
from __future__ import annotations

from contextlib import contextmanager

from app.core.config import get_settings

_tracer = None


def setup_tracing(service_name: str = "sentinel-aml-api") -> bool:
    global _tracer
    s = get_settings()
    if not s.otel_enabled:
        return False
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    exporter = ConsoleSpanExporter()
    if s.otel_exporter_otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=s.otel_exporter_otlp_endpoint)
        except ImportError:
            pass
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("sentinel-aml")
    return True


@contextmanager
def span(name: str, **attributes):
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(name) as sp:
        for k, v in attributes.items():
            sp.set_attribute(k, v)
        yield sp
