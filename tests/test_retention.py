from datetime import timedelta
import uuid

from app.database import session
from app.models import Audit, Idempotency, JobLog, WebhookDelivery, WebhookEndpoint, now
from app.operations.service import cleanup_retention_once
from app.security.core import encrypt_blob


def test_retention_cleanup_removes_only_old_technical_rows(client, headers):
    old = now() - timedelta(days=800)
    recent = now()

    with session() as db:
        endpoint = WebhookEndpoint(
            name='retention-hook',
            url='https://hooks.example.test/retention',
            events=['job.failed'],
            encrypted_secret=encrypt_blob(b'retention-secret', 'webhook:retention-id'),
            is_active=False,
            created_by=1,
        )
        endpoint.id = 'retention-id'
        db.add(endpoint)
        db.flush()

        old_log = JobLog(job_id=None, timestamp=old, message='old')
        # JobLog requires a valid job FK; use direct technical rows that are independent.
        old_audit = Audit(
            timestamp=old, user_id=1, token_id=None, ip='', source='test',
            action='old', resource='test', resource_id='old',
            result='success', request_id=str(uuid.uuid4()),
        )
        new_audit = Audit(
            timestamp=recent, user_id=1, token_id=None, ip='', source='test',
            action='new', resource='test', resource_id='new',
            result='success', request_id=str(uuid.uuid4()),
        )
        old_idem = Idempotency(
            user_id=1, path='/test', key=str(uuid.uuid4()), fingerprint='a' * 64,
            response={}, created_at=old,
        )
        new_idem = Idempotency(
            user_id=1, path='/test', key=str(uuid.uuid4()), fingerprint='b' * 64,
            response={}, created_at=recent,
        )
        old_delivery = WebhookDelivery(
            endpoint_id=endpoint.id, event='job.failed', resource_id='old',
            payload={}, status='delivered', attempts=1,
            next_attempt_at=old, delivered_at=old, created_at=old, updated_at=old,
        )
        new_delivery = WebhookDelivery(
            endpoint_id=endpoint.id, event='job.failed', resource_id='new',
            payload={}, status='delivered', attempts=1,
            next_attempt_at=recent, delivered_at=recent,
        )
        db.add_all([old_audit, new_audit, old_idem, new_idem, old_delivery, new_delivery])
        db.commit()
        old_audit_id = old_audit.id
        new_audit_id = new_audit.id
        old_idem_id = old_idem.id
        new_idem_id = new_idem.id
        old_delivery_id = old_delivery.id
        new_delivery_id = new_delivery.id

    result = cleanup_retention_once(force=True)
    assert result['skipped'] is False

    with session() as db:
        assert db.get(Audit, old_audit_id) is None
        assert db.get(Audit, new_audit_id) is not None
        assert db.get(Idempotency, old_idem_id) is None
        assert db.get(Idempotency, new_idem_id) is not None
        assert db.get(WebhookDelivery, old_delivery_id) is None
        assert db.get(WebhookDelivery, new_delivery_id) is not None
