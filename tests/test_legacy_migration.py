import base64
import os

from nacl.secret import SecretBox

from app.migration.legacy import decrypt_legacy_secret, proxmox_username


def test_legacy_secretbox_decryption_matches_php_crypto_format():
    key = os.urandom(SecretBox.KEY_SIZE)
    nonce = os.urandom(SecretBox.NONCE_SIZE)
    ciphertext = SecretBox(key).encrypt(b'legacy-token-secret', nonce).ciphertext
    encoded = base64.b64encode(nonce + ciphertext).decode()
    assert decrypt_legacy_secret(encoded, key) == 'legacy-token-secret'


def test_proxmox_username_is_derived_from_full_token_id():
    assert proxmox_username('root@pam!cloudportal') == 'root@pam'
