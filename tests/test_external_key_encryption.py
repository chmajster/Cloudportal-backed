import os

from app.config import settings
import app.security.core as core


def test_external_envelope_roundtrip_and_legacy_compatibility(system, monkeypatch):
    monkeypatch.setenv('CP_SECRET_BACKEND', 'local')
    settings.cache_clear()
    legacy = core.encrypt_blob(b'legacy-value', 'test:1')
    assert not legacy.startswith(core.ENVELOPE_MAGIC)

    wrapped = {}
    monkeypatch.setenv('CP_SECRET_BACKEND', 'aws-kms')
    settings.cache_clear()

    def fake_wrap(key, aad):
        wrapped['key'] = key
        wrapped['aad'] = aad
        return {'backend': 'test-kek', 'wrapped': 'opaque'}

    monkeypatch.setattr(core, '_wrap_data_key', fake_wrap)
    monkeypatch.setattr(core, '_unwrap_data_key', lambda header, aad: wrapped['key'])

    envelope = core.encrypt_blob(b'external-value', 'test:2')
    assert envelope.startswith(core.ENVELOPE_MAGIC)
    assert core.decrypt_blob(envelope, 'test:2') == b'external-value'

    # Existing local blobs remain readable after switching the configured backend.
    assert core.decrypt_blob(legacy, 'test:1') == b'legacy-value'

    monkeypatch.setenv('CP_SECRET_BACKEND', 'local')
    settings.cache_clear()
