from app.events.contracts import ExtensionSpec
from app.events.registry import event_matches
from app.models import WebhookDelivery, WebhookEndpoint


def webhook_bridge(db, event):
    endpoints = db.query(WebhookEndpoint).filter(WebhookEndpoint.is_active.is_(True)).all()
    envelope = {
        'specversion': '1.0',
        'id': event.id,
        'type': event.type,
        'source': event.source,
        'subject': f'{event.subject_type}/{event.subject_id}' if event.subject_id else event.subject_type,
        'time': event.created_at.isoformat() + 'Z',
        'datacontenttype': 'application/json',
        'schema_version': event.schema_version,
        'sequence': event.sequence,
        'correlation_id': event.correlation_id,
        'causation_id': event.causation_id,
        'request_id': event.request_id,
        'data': event.payload or {},
        # Preserve the legacy webhook shape while adding CloudEvents-compatible metadata.
        'event': event.type,
        **(event.payload or {}),
    }
    for endpoint in endpoints:
        if event_matches(endpoint.events or (), event.type):
            db.add(WebhookDelivery(
                endpoint_id=endpoint.id,
                event_id=event.id,
                event=event.type,
                resource_id=event.subject_id or event.id,
                payload=envelope,
            ))


EXTENSIONS = (
    ExtensionSpec(
        name='core.webhook-bridge',
        version='1.0.0',
        description='Routes durable broker events to signed HTTPS webhook endpoints.',
        event_patterns=('*',),
        handler=webhook_bridge,
        replay_existing_events=True,
    ),
)
