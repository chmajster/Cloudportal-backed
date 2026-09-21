import uuid

from app.database import session
from app.events.service import dispatch_event_broker_once
from app.models import EventRecord, ExtensionDelivery, WebhookDelivery


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

    response = client.post('/api/v1/events', headers=idem(headers), json={
        'type': 'custom.vm.enriched',
        'subject_type': 'managed_vms',
        'subject_id': 'vm-42',
        'correlation_id': 'corr-42',
        'payload': {'vm_id': 42, 'state': 'ready'},
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
        assert delivery.payload['type'] == 'custom.vm.enriched'
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
