from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import settings


_configured = False


def configure_telemetry(app):
    global _configured
    if _configured:
        return
    endpoint = settings().otel_exporter_otlp_endpoint
    if not endpoint:
        return
    provider = TracerProvider(
        resource=Resource.create({
            'service.name': settings().otel_service_name,
            'service.version': '1.0.0',
        })
    )
    exporter = OTLPSpanExporter(endpoint=endpoint.rstrip('/') + '/v1/traces')
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls='/api/v1/health,/api/v1/metrics',
    )
    _configured = True
