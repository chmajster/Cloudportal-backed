"""Credential-domain public response models."""
from datetime import datetime
from typing import Literal

from app.api.outputs import Output


class CredentialOutput(Output):
    id: int
    name: str
    type: str
    endpoint: str
    username: str
    verify_ssl: bool
    expires_at: datetime | None
    rotation_due_at: datetime | None
    secret_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime
    configured: bool
    supports_cloud_init_ssh_key: bool = False
    supports_cloud_init_password: bool = False
    secret: Literal['********']


class SSHKeyBootstrapOutput(Output):
    credential: CredentialOutput
    public_key: str
    fingerprint: str
