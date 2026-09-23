import base64
import hashlib
from datetime import timedelta

from app.database import session
from app.instance_backup.archive import build_archive
from app.instance_backup.models import InstanceBackup, utcnow
from app.instance_backup.database import current_alembic_revision
from app.instance_backup.paths import generated_dir
from app.config import settings
from conftest import new_user


def make_ready_backup(tmp_path, *, status="ready", expires_at=None):
    staging = tmp_path / ("staging-" + status)
    staging.mkdir()
    (staging / "database.dump").write_bytes(b"postgres-dump-for-api-test")
    (staging / "configuration").mkdir()
    (staging / "configuration" / "runtime.json").write_text("{}\n")
    (staging / "secrets").mkdir()
    (staging / "secrets" / "master.key").write_bytes(settings().master_key_file.read_bytes())
    backup_uuid = "44444444-4444-4444-4444-" + ("4" * 12 if status == "ready" else "5" * 12)
    manifest = {
        "format": "cloudportal-instance-backup",
        "format_version": 1,
        "backup_uuid": backup_uuid,
        "created_at": "2026-09-23T22:45:00Z",
        "application": {
            "name": "Cloudportal-backed", "version": "test", "api_version": "v1",
            "commit": "abcdef123456", "alembic_revision": current_alembic_revision(),
        },
        "source": {"hostname": "source01", "install_mode": "docker", "worker_count": 1},
        "secret": {"backend": "local", "material": "secrets/master.key"},
        "counts": {"users": 1, "credentials": 0, "providers": 0, "projects": 0, "tenants": 0, "blueprints": 0, "deployments": 0, "terraform_state": 0},
    }
    path = generated_dir() / (backup_uuid + ".cpb")
    size, digest = build_archive(staging, path, manifest, ["database.dump", "configuration/runtime.json", "secrets/master.key"])
    with session() as db:
        row = InstanceBackup(
            backup_uuid=backup_uuid,
            filename="cloudportal-backup-20260923-224500.cpb",
            file_path=str(path),
            status=status,
            stage=status,
            progress=100 if status == "ready" else 20,
            size_bytes=size,
            sha256=digest,
            application_version="test",
            git_commit="abcdef123456",
            manifest=manifest,
            expires_at=expires_at if expires_at is not None else utcnow() + timedelta(hours=1),
        )
        db.add(row)
        db.commit()
        return row.id, path, digest


def test_create_backup_returns_202_without_holding_request(client, headers, monkeypatch):
    monkeypatch.setattr("app.instance_backup.api.enqueue_backup", lambda backup_id: None)
    response = client.post("/api/v1/instance-backups", headers=headers)
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    assert response.json()["id"] > 0


def test_download_streams_cpb_with_secure_headers(system, tmp_path):
    client, headers, _ = system
    backup_id, path, digest = make_ready_backup(tmp_path)
    response = client.get(f"/api/v1/instance-backups/{backup_id}/download", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/octet-stream"
    assert "attachment;" in response.headers["content-disposition"]
    assert ".cpb" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content
    assert hashlib.sha256(response.content).hexdigest() == digest
    assert response.content == path.read_bytes()


def test_download_permission_missing_and_incomplete_states(system, tmp_path):
    client, headers, _ = system
    backup_id, _, _ = make_ready_backup(tmp_path)
    _, reader = new_user(client, headers, username="instance-reader", permissions=["instance_backups.read"])
    assert client.get(f"/api/v1/instance-backups/{backup_id}/download", headers=reader).status_code == 403
    assert client.get("/api/v1/instance-backups/999999/download", headers=headers).status_code == 404

    with session() as db:
        queued = InstanceBackup(
            backup_uuid="66666666-6666-6666-6666-666666666666",
            filename="queued.cpb", status="queued", stage="queued", progress=0,
        )
        db.add(queued); db.commit(); queued_id = queued.id
    assert client.get(f"/api/v1/instance-backups/{queued_id}/download", headers=headers).status_code == 409


def test_expired_backup_download_returns_410(system, tmp_path):
    client, headers, _ = system
    backup_id, _, _ = make_ready_backup(tmp_path, expires_at=utcnow() - timedelta(seconds=1))
    assert client.get(f"/api/v1/instance-backups/{backup_id}/download", headers=headers).status_code == 410


def test_upload_streams_valid_cpb_and_builds_migration_plan(system, tmp_path):
    client, headers, _ = system
    _, path, _ = make_ready_backup(tmp_path)
    response = client.post(
        "/api/v1/instance-backups/upload",
        headers=headers,
        files={"file": ("migration.cpb", path.read_bytes(), "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["valid"] is True
    assert data["backup"]["status"] == "validated"
    assert data["backup"]["filename"] == "migration.cpb"
    assert data["plan"]["source"]["hostname"] == "source01"
    assert data["plan"]["preserved_local_settings"]


def test_upload_rejects_wrong_extension_and_oversized_file(system, tmp_path):
    client, headers, _ = system
    _, path, _ = make_ready_backup(tmp_path)
    wrong = client.post(
        "/api/v1/instance-backups/upload", headers=headers,
        files={"file": ("migration.zip", path.read_bytes(), "application/octet-stream")},
    )
    assert wrong.status_code == 422

    cfg = settings()
    old_limit = cfg.instance_backup_max_upload_bytes
    cfg.instance_backup_max_upload_bytes = 128
    try:
        oversized = client.post(
            "/api/v1/instance-backups/upload", headers=headers,
            files={"file": ("migration.cpb", b"x" * 1024, "application/octet-stream")},
        )
    finally:
        cfg.instance_backup_max_upload_bytes = old_limit
    assert oversized.status_code == 413
