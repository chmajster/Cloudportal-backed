from __future__ import annotations

import base64
import os
from pathlib import Path

from app.config import settings
from app.instance_backup.paths import chmod_file
from app.security.core import encryption_key


def _local_key_bytes(path: Path) -> bytes:
    # Reuse the application's ownership/mode validation for the active key.
    if path == settings().master_key_file:
        encryption_key()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        raw = os.read(descriptor, 1024).strip()
    finally:
        os.close(descriptor)
    try:
        decoded = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise RuntimeError("Master key has invalid encoding") from exc
    if len(decoded) != 32:
        raise RuntimeError("Master key has invalid length")
    return raw + b"\n"


def secret_descriptor() -> dict:
    cfg = settings()
    if cfg.secret_backend == "local":
        return {"backend": "local", "material": "secrets/master.key"}
    if cfg.secret_backend == "aws-kms":
        return {
            "backend": "aws-kms",
            "key_id": cfg.aws_kms_key_id,
            "region": cfg.aws_kms_region,
            "material": "external-reference",
        }
    if cfg.secret_backend == "vault-transit":
        return {
            "backend": "vault-transit",
            "addr": (cfg.vault_addr or "").rstrip("/"),
            "mount": cfg.vault_transit_mount,
            "key": cfg.vault_transit_key,
            "material": "external-reference",
        }
    raise RuntimeError("Unsupported secret backend")


def collect_secret_material(staging: Path) -> list[str]:
    descriptor = secret_descriptor()
    if descriptor["backend"] != "local":
        return []
    destination = staging / "secrets" / "master.key"
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination.parent, 0o700)
    destination.write_bytes(_local_key_bytes(settings().master_key_file))
    chmod_file(destination)
    return ["secrets/master.key"]


def validate_secret_compatibility(manifest: dict) -> None:
    source = manifest.get("secret") or {}
    target = secret_descriptor()
    if source.get("backend") != target.get("backend"):
        raise RuntimeError(
            f"Secret backend mismatch: backup={source.get('backend') or 'unknown'}, target={target.get('backend')}"
        )
    if target["backend"] == "aws-kms":
        if source.get("key_id") != target.get("key_id") or source.get("region") != target.get("region"):
            raise RuntimeError("AWS KMS key/region differs from the backup")
    if target["backend"] == "vault-transit":
        for key in ("addr", "mount", "key"):
            if source.get(key) != target.get(key):
                raise RuntimeError("Vault Transit configuration differs from the backup")


def install_local_key(extracted: Path) -> bytes | None:
    if settings().secret_backend != "local":
        return None
    source = extracted / "secrets" / "master.key"
    raw = _local_key_bytes(source)
    target = settings().master_key_file
    previous = target.read_bytes() if target.is_file() else None
    temporary = target.with_name(target.name + ".restore-new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return previous


def restore_previous_local_key(previous: bytes | None) -> None:
    if previous is None:
        return
    target = settings().master_key_file
    temporary = target.with_name(target.name + ".rollback-new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(previous)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    finally:
        temporary.unlink(missing_ok=True)
