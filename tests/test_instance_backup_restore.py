import os
import shutil
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.database import session
from app.instance_backup.archive import build_archive
from app.instance_backup.models import InstanceBackup, utcnow
from app.instance_backup.database import current_alembic_revision
from app.instance_backup.paths import generated_dir
from app.instance_backup.restore import (
    _quarantine_restored_execution,
    _restore_workspaces,
    execute_restore,
    read_restore_status,
    write_restore_status,
)
from app.config import settings
from app.models import Job, User


def make_restore_source(tmp_path):
    staging = tmp_path / "restore-source"
    staging.mkdir()
    (staging / "database.dump").write_bytes(b"postgres-dump")
    (staging / "configuration").mkdir()
    (staging / "configuration" / "runtime.json").write_text("{}\n")
    (staging / "secrets").mkdir()
    (staging / "secrets" / "master.key").write_bytes(settings().master_key_file.read_bytes())
    backup_uuid = "77777777-7777-7777-7777-777777777777"
    manifest = {
        "format": "cloudportal-instance-backup",
        "format_version": 1,
        "backup_uuid": backup_uuid,
        "created_at": "2026-09-23T22:45:00Z",
        "application": {"name": "Cloudportal-backed", "version": "source", "api_version": "v1", "commit": "abc", "alembic_revision": current_alembic_revision()},
        "source": {"hostname": "source01", "install_mode": "docker"},
        "secret": {"backend": "local", "material": "secrets/master.key"},
        "counts": {"users": 18, "credentials": 9, "providers": 4, "projects": 6, "tenants": 3, "blueprints": 12, "deployments": 37, "terraform_state": 24},
    }
    path = generated_dir() / (backup_uuid + ".cpb")
    size, digest = build_archive(staging, path, manifest, ["database.dump", "configuration/runtime.json", "secrets/master.key"])
    with session() as db:
        row = InstanceBackup(
            backup_uuid=backup_uuid,
            origin="uploaded", status="validated", stage="validated", progress=100,
            filename="migration.cpb", file_path=str(path), size_bytes=size, sha256=digest,
            application_version="source", manifest=manifest,
            expires_at=utcnow() + timedelta(hours=1),
        )
        db.add(row); db.commit(); return row.id, path, manifest


def test_restore_status_file_is_private(system):
    payload = write_restore_status(
        "88888888-8888-8888-8888-888888888888",
        backup_id=1, status="queued", stage="queued",
    )
    loaded = read_restore_status(payload["restore_uuid"])
    assert loaded["status"] == "queued"
    from app.instance_backup.paths import restore_status_dir
    mode = os.stat(restore_status_dir() / (payload["restore_uuid"] + ".json")).st_mode & 0o777
    assert mode == 0o600


def test_restore_endpoint_requires_typed_confirmation(system, tmp_path, monkeypatch):
    client, headers, _ = system
    backup_id, _, _ = make_restore_source(tmp_path)
    queued = {}
    monkeypatch.setattr(
        "app.instance_backup.api.enqueue_restore",
        lambda *args: queued.setdefault("args", args),
    )
    rejected = client.post(
        f"/api/v1/instance-backups/{backup_id}/restore", headers=headers,
        json={"confirmation": "YES", "safety_backup": True},
    )
    assert rejected.status_code == 422
    accepted = client.post(
        f"/api/v1/instance-backups/{backup_id}/restore", headers=headers,
        json={"confirmation": "RESTORE", "safety_backup": True},
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["safety_backup"] is True
    assert len(queued["args"]) == 4
    assert queued["args"][3] is not None


def test_failed_restore_uses_safety_backup_for_rollback(system, tmp_path, monkeypatch):
    backup_id, source_path, source_manifest = make_restore_source(tmp_path)
    restore_uuid = "99999999-9999-9999-9999-999999999999"

    def safety_archive(output, backup_uuid, **kwargs):
        shutil.copy2(source_path, output)
        manifest = dict(source_manifest)
        manifest["backup_uuid"] = backup_uuid
        return manifest, output.stat().st_size, "unused"

    calls = []
    def restore_database(dump):
        calls.append(dump)
        if len(calls) == 1:
            raise RuntimeError("simulated restore failure")

    monkeypatch.setattr("app.instance_backup.restore.create_snapshot_archive", safety_archive)
    monkeypatch.setattr("app.instance_backup.restore.restore_database", restore_database)
    monkeypatch.setattr("app.instance_backup.restore._upgrade_database", lambda: None)
    monkeypatch.setattr("app.instance_backup.restore._health_validation", lambda: None)

    execute_restore(backup_id, restore_uuid, True)
    status = read_restore_status(restore_uuid)
    assert status["status"] == "failed"
    assert status["rollback"] == "completed"
    assert len(calls) == 2
    with session() as db:
        rows = db.scalars(select(InstanceBackup)).all()
        assert any(row.id == backup_id for row in rows)
        assert any(row.origin == "safety" for row in rows)


def test_restore_quarantines_nonterminal_jobs(system):
    with session() as db:
        admin = db.scalar(select(User).where(User.username == "admin"))
        job = Job(
            id=str(uuid.uuid4()),
            operation="terraform.apply",
            deployment_id=None,
            payload={},
            status="queued",
            created_by=admin.id,
            token_id=None,
            request_id=str(uuid.uuid4()),
            ip="",
            source="API",
        )
        db.add(job)
        db.commit()
        job_id = job.id

    result = _quarantine_restored_execution()
    assert result["jobs"] == 1

    with session() as db:
        job = db.get(Job, job_id)
        assert job.status == "failed"
        assert "quarantined" in job.error.lower()


def test_workspace_restore_replaces_target_tree(system, tmp_path):
    target = settings().data_dir / "workspaces"
    target.mkdir(parents=True, exist_ok=True)
    (target / "old-state").write_text("old")

    extracted = tmp_path / "extracted-workspace"
    source = extracted / "workspaces" / "deployment-2"
    source.mkdir(parents=True)
    (source / "terraform.tfstate").write_text('{"version":4,"serial":2}')

    _restore_workspaces(extracted, "workspace-test")

    assert not (target / "old-state").exists()
    restored = target / "deployment-2" / "terraform.tfstate"
    assert restored.read_text() == '{"version":4,"serial":2}'
    assert (restored.stat().st_mode & 0o777) == 0o600
