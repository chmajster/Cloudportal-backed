from datetime import timedelta

from app.config import settings
from app.database import session
from app.instance_backup.models import InstanceBackup, utcnow
from app.instance_backup.paths import generated_dir
from app.instance_backup.service import (
    cleanup_expired_backups,
    collect_workspace_material,
    generated_filename,
    public_status,
)


def test_generated_filename_is_sanitized_cpb_name():
    name = generated_filename()
    assert name.startswith("cloudportal-backup-")
    assert name.endswith(".cpb")
    assert "/" not in name and "\\" not in name


def test_cleanup_removes_only_expired_managed_file(system):
    path = generated_dir() / "expired.cpb"
    path.write_bytes(b"backup")
    path.chmod(0o600)
    with session() as db:
        row = InstanceBackup(
            backup_uuid="22222222-2222-2222-2222-222222222222",
            filename="cloudportal-backup-expired.cpb",
            file_path=str(path),
            status="ready",
            stage="ready",
            progress=100,
            sha256="0" * 64,
            expires_at=utcnow() - timedelta(minutes=1),
        )
        db.add(row)
        db.commit()
        row_id = row.id

    assert cleanup_expired_backups() == 1
    assert not path.exists()
    with session() as db:
        row = db.get(InstanceBackup, row_id)
        assert row.status == "expired"
        assert row.file_path is None
        assert public_status(row)["download_ready"] is False


def test_nonexpired_backup_remains_available(system):
    path = generated_dir() / "active.cpb"
    path.write_bytes(b"backup")
    with session() as db:
        row = InstanceBackup(
            backup_uuid="33333333-3333-3333-3333-333333333333",
            filename="cloudportal-backup-active.cpb",
            file_path=str(path),
            status="ready",
            stage="ready",
            progress=100,
            sha256="0" * 64,
            expires_at=utcnow() + timedelta(hours=1),
        )
        db.add(row)
        db.commit()
    assert cleanup_expired_backups() == 0
    assert path.exists()


def test_workspace_snapshot_copies_durable_files_and_skips_runtime_secrets(system, tmp_path):
    workspace = settings().data_dir / "workspaces" / "deployment-1"
    workspace.mkdir(parents=True)
    (workspace / "terraform.tfstate").write_text('{"version":4}')
    (workspace / ".cloudportal-qemu-bootstrap-key").write_text("private-key")
    (workspace / "terraform.tfvars.json").write_text('{"password":"plaintext"}')
    (workspace / ".execution.lock").write_text("")
    cache = workspace / ".terraform" / "providers"
    cache.mkdir(parents=True)
    (cache / "provider").write_bytes(b"binary")

    staging = tmp_path / "workspace-staging"
    staging.mkdir()
    members = collect_workspace_material(staging)

    assert "workspaces/deployment-1/terraform.tfstate" in members
    assert "workspaces/deployment-1/.cloudportal-qemu-bootstrap-key" in members
    assert not any("terraform.tfvars.json" in item for item in members)
    assert not any(".terraform/" in item for item in members)
    assert not any(".execution.lock" in item for item in members)
    assert (staging / "workspaces/deployment-1/terraform.tfstate").read_text() == '{"version":4}'
