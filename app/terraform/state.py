import hashlib
import json
import os
from contextlib import contextmanager

from sqlalchemy import text

from app.database import session
from app.models import TerraformState, now
from app.security.core import decrypt_blob, encrypt_blob


MAX_STATE_BYTES = 64 * 1024 * 1024


def _lock_key(deployment_id: str) -> int:
    digest = hashlib.sha256(('terraform:' + deployment_id).encode()).digest()[:8]
    return int.from_bytes(digest, byteorder='big', signed=True)


@contextmanager
def distributed_deployment_lock(deployment_id: str):
    """Cross-worker lock backed by PostgreSQL; SQLite tests retain the local flock."""
    db = session()
    try:
        with db.begin():
            if db.bind.dialect.name == 'postgresql':
                db.execute(
                    text('SELECT pg_advisory_xact_lock(:lock_key)'),
                    {'lock_key': _lock_key(deployment_id)},
                )
            yield
    finally:
        db.close()


def read_stored_state(db, deployment_id: str):
    row = db.get(TerraformState, deployment_id)
    if row is None:
        return None
    raw = decrypt_blob(row.encrypted_state, f'terraform-state:{deployment_id}')
    if len(raw) > MAX_STATE_BYTES:
        raise RuntimeError('Stored Terraform state exceeds the safety limit')
    if hashlib.sha256(raw).hexdigest() != row.state_sha256:
        raise RuntimeError('Stored Terraform state integrity check failed')
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        raise RuntimeError('Stored Terraform state is not valid JSON') from None
    if not isinstance(payload, dict):
        raise RuntimeError('Stored Terraform state has invalid structure')
    return payload


def restore_state(deployment_id: str, workspace):
    with session() as db:
        state = read_stored_state(db, deployment_id)
        if state is None:
            return False
        raw = json.dumps(state, separators=(',', ':'), sort_keys=True).encode()
        row = db.get(TerraformState, deployment_id)
        original = decrypt_blob(row.encrypted_state, f'terraform-state:{deployment_id}')
        if hashlib.sha256(original).hexdigest() != row.state_sha256:
            raise RuntimeError('Stored Terraform state integrity check failed')
        raw = original
    target = workspace / 'terraform.tfstate'
    temporary = workspace / '.terraform.tfstate.restore'
    temporary.write_bytes(raw)
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    return True


def persist_state(deployment_id: str, workspace):
    source = workspace / 'terraform.tfstate'
    if not source.exists():
        return False
    raw = source.read_bytes()
    if len(raw) > MAX_STATE_BYTES:
        raise RuntimeError('Terraform state exceeds the safety limit')
    digest = hashlib.sha256(raw).hexdigest()
    encrypted = encrypt_blob(raw, f'terraform-state:{deployment_id}')
    with session() as db:
        row = db.get(TerraformState, deployment_id)
        if row is None:
            row = TerraformState(
                deployment_id=deployment_id,
                encrypted_state=encrypted,
                state_sha256=digest,
                version=1,
            )
            db.add(row)
        else:
            row.encrypted_state = encrypted
            row.state_sha256 = digest
            row.version += 1
            row.updated_at = now()
        db.commit()
    return True
