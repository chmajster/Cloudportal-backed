from datetime import timedelta

from app.database import session
from app.instance_backup.models import InstanceBackup, utcnow
from app.instance_backup.paths import generated_dir
from app.instance_backup.service import cleanup_expired_backups, generated_filename, public_status


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
