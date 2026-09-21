import uuid
from datetime import timedelta

from app.database import session
from app.events.service import publish_event, sync_extension_states
from app.models import EventConsumer, EventRecord, ExtensionDelivery, ExtensionState, now
from app.operations.service import cleanup_retention_once
from conftest import new_user


def idem(headers):
    return {**headers, 'Idempotency-Key': str(uuid.uuid4())}


def publish(client, headers, event_type, payload):
    response = client.post('/api/v1/events', headers=idem(headers), json={
        'type': event_type,
        'payload': payload,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_event_schema_registry_enforces_active_contract(client, headers):
    schema = client.post('/api/v1/event-schemas', headers=idem(headers), json={
        'event_type': 'custom.schema.test',
        'version': 1,
        'schema': {
            '$schema': 'https://json-schema.org/draft/2020-12/schema',
            'type': 'object',
            'properties': {'name': {'type': 'string'}},
            'required': ['name'],
            'additionalProperties': False,
        },
    })
    assert schema.status_code == 201, schema.text

    valid = client.post('/api/v1/events', headers=idem(headers), json={
        'type': 'custom.schema.test',
        'schema_version': 1,
        'payload': {'name': 'vm01'},
    })
    assert valid.status_code == 201, valid.text

    invalid = client.post('/api/v1/events', headers=idem(headers), json={
        'type': 'custom.schema.test',
        'schema_version': 1,
        'payload': {'wrong': 'field'},
    })
    assert invalid.status_code == 422
    assert 'does not match custom.schema.test v1' in invalid.text

    check = client.post(
        f"/api/v1/event-schemas/{schema.json()['id']}/validate",
        headers=headers,
        json={'payload': {'name': 'vm02'}},
    )
    assert check.status_code == 200
    assert check.json()['valid'] is True

    disabled = client.put(
        f"/api/v1/event-schemas/{schema.json()['id']}/state",
        headers=headers,
        json={'is_active': False},
    )
    assert disabled.status_code == 200
    assert disabled.json()['is_active'] is False

    no_longer_enforced = client.post('/api/v1/events', headers=idem(headers), json={
        'type': 'custom.schema.test',
        'schema_version': 1,
        'payload': {'wrong': 'field'},
    })
    assert no_longer_enforced.status_code == 201, no_longer_enforced.text


def test_event_schema_rejects_external_ref(client, headers):
    response = client.post('/api/v1/event-schemas', headers=idem(headers), json={
        'event_type': 'custom.schema.external',
        'version': 1,
        'schema': {
            'type': 'object',
            'properties': {
                'value': {'$ref': 'https://attacker.example/schema.json'},
            },
        },
    })
    assert response.status_code == 422
    assert 'External JSON Schema references are not allowed' in response.text


def test_consumer_poll_checkpoint_and_monotonic_ack(client, headers):
    first = publish(client, headers, 'custom.consumer.one', {'value': 1})
    unrelated = publish(client, headers, 'custom.other.event', {'value': 2})
    second = publish(client, headers, 'custom.consumer.two', {'value': 3})

    created = client.post('/api/v1/event-consumers', headers=idem(headers), json={
        'name': 'integration-a',
        'event_patterns': ['custom.consumer.*'],
        'max_batch': 50,
        'start_from': 'earliest',
    })
    assert created.status_code == 201, created.text
    consumer_id = created.json()['id']
    assert created.json()['cursor_sequence'] == 0

    poll = client.get(
        f'/api/v1/event-consumers/{consumer_id}/events',
        headers=headers,
        params={'limit': 1},
    )
    assert poll.status_code == 200, poll.text
    assert [item['id'] for item in poll.json()['items']] == [first['id']]
    assert poll.json()['checkpoint_sequence'] == first['sequence']

    too_far = client.post(
        f'/api/v1/event-consumers/{consumer_id}/ack',
        headers=headers,
        json={'sequence': second['sequence']},
    )
    assert too_far.status_code == 409
    assert 'last polled checkpoint' in too_far.text

    ack = client.post(
        f'/api/v1/event-consumers/{consumer_id}/ack',
        headers=headers,
        json={'sequence': first['sequence']},
    )
    assert ack.status_code == 200
    assert ack.json()['cursor_sequence'] == first['sequence']

    poll = client.get(f'/api/v1/event-consumers/{consumer_id}/events', headers=headers)
    assert poll.status_code == 200
    assert [item['id'] for item in poll.json()['items']] == [second['id']]
    assert poll.json()['checkpoint_sequence'] >= second['sequence']
    assert unrelated['sequence'] < second['sequence']

    ack = client.post(
        f'/api/v1/event-consumers/{consumer_id}/ack',
        headers=headers,
        json={'sequence': second['sequence']},
    )
    assert ack.status_code == 200

    backwards = client.post(
        f'/api/v1/event-consumers/{consumer_id}/ack',
        headers=headers,
        json={'sequence': first['sequence']},
    )
    assert backwards.status_code == 409


def test_consumer_owner_isolation(client, headers):
    owner, owner_headers = new_user(
        client,
        headers,
        username='event-owner',
        permissions=['events.consume'],
    )
    _other, other_headers = new_user(
        client,
        headers,
        username='event-other',
        permissions=['events.consume'],
    )

    created = client.post('/api/v1/event-consumers', headers=idem(headers), json={
        'name': 'owned-consumer',
        'event_patterns': ['custom.*'],
        'owner_user_id': owner['id'],
        'start_from': 'latest',
    })
    assert created.status_code == 201, created.text
    consumer_id = created.json()['id']

    allowed = client.get(f'/api/v1/event-consumers/{consumer_id}', headers=owner_headers)
    assert allowed.status_code == 200

    denied = client.get(f'/api/v1/event-consumers/{consumer_id}', headers=other_headers)
    assert denied.status_code == 404

    listed = client.get('/api/v1/event-consumers', headers=owner_headers)
    assert listed.status_code == 200
    assert [item['id'] for item in listed.json()['items']] == [consumer_id]


def test_event_retention_respects_consumer_cursor_and_pending_delivery(client, headers, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(), 'retention_events_days', 1)

    with session() as db:
        first = publish_event(db, 'custom.retention.one', {'value': 1})
        second = publish_event(db, 'custom.retention.two', {'value': 2})
        db.flush()
        old = now() - timedelta(days=10)
        first.created_at = old
        second.created_at = old

        states = sync_extension_states(db)
        for state in states.values():
            state.last_event_sequence = second.sequence

        consumer = EventConsumer(
            name='retention-consumer',
            event_patterns=['custom.*'],
            cursor_sequence=first.sequence,
            max_batch=100,
            owner_user_id=1,
            created_by=1,
        )
        db.add(consumer)
        db.commit()
        first_sequence = first.sequence
        second_sequence = second.sequence
        consumer_id = consumer.id

    cleanup_retention_once(force=True)

    with session() as db:
        assert db.get(EventRecord, first_sequence) is None
        assert db.get(EventRecord, second_sequence) is not None
        consumer = db.get(EventConsumer, consumer_id)
        consumer.cursor_sequence = second_sequence
        delivery = ExtensionDelivery(
            extension_name='core.webhook-bridge',
            event_sequence=second_sequence,
            status='pending',
            is_replay=True,
        )
        db.add(delivery)
        db.commit()
        delivery_id = delivery.id

    cleanup_retention_once(force=True)

    with session() as db:
        assert db.get(EventRecord, second_sequence) is not None
        delivery = db.get(ExtensionDelivery, delivery_id)
        delivery.status = 'delivered'
        db.commit()

    cleanup_retention_once(force=True)

    with session() as db:
        assert db.get(EventRecord, second_sequence) is None


def test_event_metrics_include_schema_and_consumer_lag(client, headers):
    publish(client, headers, 'custom.metrics.test', {'value': 1})
    schema = client.post('/api/v1/event-schemas', headers=idem(headers), json={
        'event_type': 'custom.metrics.contract',
        'version': 1,
        'schema': {'type': 'object'},
    })
    assert schema.status_code == 201
    consumer = client.post('/api/v1/event-consumers', headers=idem(headers), json={
        'name': 'metrics-consumer',
        'event_patterns': ['custom.*'],
        'start_from': 'earliest',
    })
    assert consumer.status_code == 201

    response = client.get('/api/v1/metrics', headers=headers)
    assert response.status_code == 200
    body = response.text
    assert 'cloudportal_event_schemas_active ' in body
    assert 'cloudportal_event_consumers_active ' in body
    assert 'cloudportal_event_consumer_lag{consumer="metrics-consumer"}' in body


def test_consumer_subscription_change_invalidates_previous_checkpoint(client, headers):
    old_event = publish(client, headers, 'custom.old.match', {'value': 1})
    new_event = publish(client, headers, 'custom.new.match', {'value': 2})

    created = client.post('/api/v1/event-consumers', headers=idem(headers), json={
        'name': 'subscription-change',
        'event_patterns': ['custom.old.*'],
        'start_from': 'earliest',
    })
    assert created.status_code == 201, created.text
    consumer = created.json()

    poll = client.get(f"/api/v1/event-consumers/{consumer['id']}/events", headers=headers)
    assert poll.status_code == 200
    assert [item['id'] for item in poll.json()['items']] == [old_event['id']]
    stale_checkpoint = poll.json()['checkpoint_sequence']
    assert stale_checkpoint >= new_event['sequence']

    updated = client.put(f"/api/v1/event-consumers/{consumer['id']}", headers=headers, json={
        'name': consumer['name'],
        'event_patterns': ['custom.new.*'],
        'max_batch': consumer['max_batch'],
        'is_active': True,
        'owner_user_id': consumer['owner_user_id'],
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()['last_checkpoint_sequence'] is None

    stale_ack = client.post(
        f"/api/v1/event-consumers/{consumer['id']}/ack",
        headers=headers,
        json={'sequence': stale_checkpoint},
    )
    assert stale_ack.status_code == 409
    assert 'last polled checkpoint' in stale_ack.text

    poll = client.get(f"/api/v1/event-consumers/{consumer['id']}/events", headers=headers)
    assert poll.status_code == 200
    assert [item['id'] for item in poll.json()['items']] == [new_event['id']]


def test_consumer_detects_gap_after_retention_while_disabled(client, headers, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(), 'retention_events_days', 1)

    with session() as db:
        old_event = publish_event(db, 'custom.gap.old', {'value': 1})
        db.flush()
        old_event.created_at = now() - timedelta(days=10)
        states = sync_extension_states(db)
        for state in states.values():
            state.last_event_sequence = old_event.sequence
        consumer = EventConsumer(
            name='gap-consumer',
            event_patterns=['custom.*'],
            cursor_sequence=0,
            max_batch=100,
            is_active=False,
            owner_user_id=1,
            created_by=1,
        )
        db.add(consumer)
        db.commit()
        consumer_id = consumer.id

    cleanup_retention_once(force=True)

    newer = publish(client, headers, 'custom.gap.new', {'value': 2})
    assert newer['sequence'] > 1

    with session() as db:
        consumer = db.get(EventConsumer, consumer_id)
        consumer.is_active = True
        db.commit()

    poll = client.get(f'/api/v1/event-consumers/{consumer_id}/events', headers=headers)
    assert poll.status_code == 409
    assert 'retained event floor' in poll.text

    reset = client.post(
        f'/api/v1/event-consumers/{consumer_id}/reset',
        headers=headers,
        json={'start_from': 'earliest'},
    )
    assert reset.status_code == 200, reset.text

    poll = client.get(f'/api/v1/event-consumers/{consumer_id}/events', headers=headers)
    assert poll.status_code == 200
    assert any(item['id'] == newer['id'] for item in poll.json()['items'])
