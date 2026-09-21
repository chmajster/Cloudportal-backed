import uuid
from datetime import timedelta

from sqlalchemy.dialects import postgresql

from app.database import session
from app.events.contracts import ExtensionSpec
from app.events.service import dispatch_event_broker_once
from app.models import EventRecord, ExtensionDelivery, ExtensionState, WebhookDelivery, now


def idem(headers):
    return {**headers, 'Idempotency-Key': str(uuid.uuid4())}


def test_custom_event_is_durable_and_fans_out_to_wildcard_webhook(client, headers, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(), 'webhook_allowed_hosts', ['hooks.example.test'])

    hook = client.post('/api/v1/webhooks', headers=idem(headers), json={
        'name': 'Event bus hook',
        'url': 'https://hooks.example.test/events',
        'events': ['custom.*'],
    })
    assert hook.status_code == 201, hook.text

    long_event_type = 'custom.vm.' + ('enriched_' * 9) + 'ready'
    response = client.post('/api/v1/events', headers=idem(headers), json={
        'type': long_event_type,
        'subject_type': 'managed_vms',
        'subject_id': 'vm-42',
        'correlation_id': 'corr-42',
        'payload': {
            'vm_id': 42,
            'state': 'ready',
            'event': 'job.successful',
            'type': 'job.successful',
            'id': 'forged-id',
            'source': 'forged-source',
            'time': 'forged-time',
            'created_at': 'forged-created-at',
        },
    })
    assert response.status_code == 201, response.text
    event = response.json()
    assert event['sequence'] > 0
    assert event['correlation_id'] == 'corr-42'

    dispatch_event_broker_once()

    with session() as db:
        persisted = db.get(EventRecord, event['sequence'])
        assert persisted.id == event['id']
        delivery = db.query(WebhookDelivery).filter(
            WebhookDelivery.event_id == event['id'],
            WebhookDelivery.endpoint_id == hook.json()['id'],
        ).one()
        assert delivery.status == 'pending'
        assert delivery.payload['specversion'] == '1.0'
        assert delivery.payload['type'] == long_event_type
        assert delivery.payload['event'] == long_event_type
        assert delivery.payload['id'] == event['id']
        assert delivery.payload['source'] == 'cloudportal.api'
        assert delivery.payload['time'] == delivery.payload['created_at']
        assert delivery.payload['time'] != 'forged-time'
        assert delivery.payload['data']['event'] == 'job.successful'
        assert delivery.payload['data']['vm_id'] == 42


def test_replay_creates_new_extension_delivery(client, headers):
    response = client.post('/api/v1/events', headers=idem(headers), json={
        'type': 'custom.replay.test',
        'payload': {'value': 1},
    })
    assert response.status_code == 201, response.text
    event = response.json()

    dispatch_event_broker_once()

    replay = client.post(
        f"/api/v1/events/{event['id']}/replay",
        headers=headers,
        json={'target': 'extensions'},
    )
    assert replay.status_code == 202, replay.text
    # Only webhook bridge is installed in the base backend and it is explicitly
    # excluded by target=extensions.
    assert replay.json()['deliveries_created'] == 0

    replay = client.post(
        f"/api/v1/events/{event['id']}/replay",
        headers=headers,
        json={'target': 'webhooks'},
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()['deliveries_created'] == 1

    with session() as db:
        delivery = db.get(ExtensionDelivery, replay.json()['delivery_ids'][0])
        assert delivery.is_replay is True
        assert delivery.event_sequence == event['sequence']


def test_dead_letter_can_be_requeued(client, headers):
    response = client.post('/api/v1/events', headers=idem(headers), json={
        'type': 'custom.deadletter.test',
        'payload': {},
    })
    assert response.status_code == 201, response.text

    with session() as db:
        event = db.get(EventRecord, response.json()['sequence'])
        delivery = ExtensionDelivery(
            extension_name='core.webhook-bridge',
            event_sequence=event.sequence,
            status='dead_letter',
            attempts=8,
            is_replay=True,
            last_error='test failure',
        )
        db.add(delivery)
        db.commit()
        delivery_id = delivery.id

    retry = client.post(
        f'/api/v1/event-deliveries/extension/{delivery_id}/retry',
        headers=headers,
    )
    assert retry.status_code == 202, retry.text
    assert retry.json()['status'] == 'pending'
    assert retry.json()['attempts'] == 0
    assert retry.json()['last_error'] is None


def test_extension_can_be_disabled_and_reenabled(client, headers):
    listed = client.get('/api/v1/extensions', headers=headers)
    assert listed.status_code == 200, listed.text
    names = {item['name'] for item in listed.json()['items']}
    assert 'core.webhook-bridge' in names

    disabled = client.put('/api/v1/extensions/core.webhook-bridge', headers=headers, json={
        'is_enabled': False,
        'config': {'mode': 'paused'},
    })
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()['status'] == 'disabled'

    enabled = client.put('/api/v1/extensions/core.webhook-bridge', headers=headers, json={
        'is_enabled': True,
        'config': {'mode': 'active'},
    })
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()['is_enabled'] is True


def test_audit_boundary_emits_safe_event(client, headers, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(), 'webhook_allowed_hosts', ['hooks.example.test'])

    hook = client.post('/api/v1/webhooks', headers=idem(headers), json={
        'name': 'Audited hook',
        'url': 'https://hooks.example.test/audit',
        'events': ['job.*'],
    })
    assert hook.status_code == 201, hook.text

    response = client.get('/api/v1/events', headers=headers, params={'event_type': 'webhook.created'})
    assert response.status_code == 200, response.text
    rows = response.json()['items']
    assert rows
    payload = rows[0]['payload']['audit']
    assert payload['action'] == 'webhook.created'
    assert payload['resource'] == 'webhooks'
    serialized = str(rows[0]).lower()
    assert 'secret' not in serialized


def test_after_sequence_is_lossless_ascending_cursor(client, headers):
    event_type = 'custom.cursor.test'
    events = []
    for value in (1, 2, 3):
        response = client.post('/api/v1/events', headers=idem(headers), json={
            'type': event_type,
            'payload': {'value': value},
        })
        assert response.status_code == 201, response.text
        events.append(response.json())

    response = client.get('/api/v1/events', headers=headers, params={
        'event_type': event_type,
        'after_sequence': events[0]['sequence'],
        'limit': 2,
    })
    assert response.status_code == 200, response.text
    rows = response.json()['items']
    assert [row['sequence'] for row in rows] == [
        events[1]['sequence'],
        events[2]['sequence'],
    ]
    assert response.json()['next_after_sequence'] == events[2]['sequence']

    invalid = client.get('/api/v1/events', headers=headers, params={
        'after_sequence': events[0]['sequence'],
        'offset': 1,
    })
    assert invalid.status_code == 422


def test_custom_event_and_extension_config_reject_secret_fields(client, headers):
    response = client.post('/api/v1/events', headers=idem(headers), json={
        'type': 'custom.security.test',
        'payload': {'nested': {'password': 'must-not-persist'}},
    })
    assert response.status_code == 422

    response = client.put('/api/v1/extensions/core.webhook-bridge', headers=headers, json={
        'is_enabled': True,
        'config': {'api_key': 'must-not-persist'},
    })
    assert response.status_code == 422


def test_disabled_extension_does_not_starve_enabled_delivery(client, headers, monkeypatch):
    import app.events.service as event_service

    response = client.post('/api/v1/events', headers=idem(headers), json={
        'type': 'custom.starvation.test',
        'payload': {'value': 1},
    })
    assert response.status_code == 201, response.text
    sequence = response.json()['sequence']

    handled = []
    disabled_spec = ExtensionSpec(
        name='test.disabled',
        version='1.0.0',
        description='disabled test consumer',
        event_patterns=('*',),
        handler=lambda db, event, delivery: handled.append('disabled'),
    )
    enabled_spec = ExtensionSpec(
        name='test.enabled',
        version='1.0.0',
        description='enabled test consumer',
        event_patterns=('*',),
        handler=lambda db, event, delivery: handled.append('enabled'),
    )
    specs = {
        disabled_spec.name: disabled_spec,
        enabled_spec.name: enabled_spec,
    }
    monkeypatch.setattr(event_service, 'extension_specs', lambda: tuple(specs.values()))
    monkeypatch.setattr(event_service, 'extension_by_name', lambda name: specs.get(name))

    with session() as db:
        db.add(ExtensionState(name='test.disabled', version='1.0.0', is_enabled=False, status='disabled'))
        db.add(ExtensionState(name='test.enabled', version='1.0.0', is_enabled=True, status='healthy'))
        old = now() - timedelta(seconds=10)
        for _ in range(100):
            db.add(ExtensionDelivery(
                extension_name='test.disabled',
                event_sequence=sequence,
                status='pending',
                next_attempt_at=old,
                is_replay=True,
            ))
        enabled = ExtensionDelivery(
            extension_name='test.enabled',
            event_sequence=sequence,
            status='pending',
            next_attempt_at=now() - timedelta(seconds=5),
            is_replay=True,
        )
        db.add(enabled)
        db.commit()
        enabled_id = enabled.id

    with session() as db:
        assert event_service.deliver_extension_deliveries(db, batch_size=100) == 1
        db.commit()

    with session() as db:
        assert db.get(ExtensionDelivery, enabled_id).status == 'delivered'
        pending_disabled = db.query(ExtensionDelivery).filter(
            ExtensionDelivery.extension_name == 'test.disabled',
            ExtensionDelivery.status == 'pending',
        ).count()
        assert pending_disabled == 100
    assert handled == ['enabled']


def test_extension_cursor_is_bigint_on_postgresql():
    column = ExtensionState.__table__.c.last_event_sequence
    assert column.type.compile(dialect=postgresql.dialect()) == 'BIGINT'


def test_events_openapi_requires_idempotency_key(client):
    response = client.get('/openapi.json')
    assert response.status_code == 200
    parameters = response.json()['paths']['/api/v1/events']['post']['parameters']
    header = next(item for item in parameters if item['name'] == 'Idempotency-Key')
    assert header['in'] == 'header'
    assert header['required'] is True


def test_webhook_normal_delivery_uses_event_time_subscription_snapshot(client, headers, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(), 'webhook_allowed_hosts', ['hooks.example.test'])

    published = client.post('/api/v1/events', headers=idem(headers), json={
        'type': 'custom.snapshot.test',
        'payload': {'value': 1},
    })
    assert published.status_code == 201, published.text
    event = published.json()

    hook = client.post('/api/v1/webhooks', headers=idem(headers), json={
        'name': 'Late subscriber',
        'url': 'https://hooks.example.test/late',
        'events': ['custom.*'],
    })
    assert hook.status_code == 201, hook.text
    endpoint_id = hook.json()['id']

    dispatch_event_broker_once()

    with session() as db:
        normal = db.query(WebhookDelivery).filter(
            WebhookDelivery.event_id == event['id'],
            WebhookDelivery.endpoint_id == endpoint_id,
        ).all()
        assert normal == []

    replay = client.post(
        f"/api/v1/events/{event['id']}/replay",
        headers=headers,
        json={'target': 'webhooks'},
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()['deliveries_created'] == 1

    dispatch_event_broker_once()

    with session() as db:
        replayed = db.query(WebhookDelivery).filter(
            WebhookDelivery.event_id == event['id'],
            WebhookDelivery.endpoint_id == endpoint_id,
        ).all()
        assert len(replayed) == 1
