import base64
import io
import json
import tarfile

import pytest

from app.instance_backup.archive import build_archive, inspect_archive, sha256_file


def manifest():
    return {
        "format": "cloudportal-instance-backup",
        "format_version": 1,
        "backup_uuid": "11111111-1111-1111-1111-111111111111",
        "created_at": "2026-09-23T22:45:00Z",
        "application": {"name": "Cloudportal-backed", "version": "test", "api_version": "v1", "commit": "abc", "alembic_revision": "head"},
        "source": {"hostname": "source", "install_mode": "docker"},
        "secret": {"backend": "local", "material": "secrets/master.key"},
        "counts": {},
    }


def make_archive(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "database.dump").write_bytes(b"postgres-custom-dump")
    (staging / "configuration").mkdir()
    (staging / "configuration" / "runtime.json").write_text("{}\n")
    (staging / "secrets").mkdir()
    (staging / "secrets" / "master.key").write_bytes(base64.b64encode(b"k" * 32) + b"\n")
    destination = tmp_path / "backup.cpb"
    size, digest = build_archive(
        staging,
        destination,
        manifest(),
        ["database.dump", "configuration/runtime.json", "secrets/master.key"],
    )
    return destination, size, digest


def test_archive_roundtrip_validates_manifest_and_sha256(tmp_path, system):
    path, size, digest = make_archive(tmp_path)
    parsed = inspect_archive(path)
    assert parsed["format_version"] == 1
    assert parsed["source"]["hostname"] == "source"
    assert size == path.stat().st_size
    assert digest == sha256_file(path)


def test_archive_rejects_path_traversal(tmp_path, system):
    path = tmp_path / "traversal.cpb"
    with tarfile.open(path, "w:gz") as archive:
        data = b"owned"
        entry = tarfile.TarInfo("../escape")
        entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))
    with pytest.raises(ValueError, match="unsafe archive entry"):
        inspect_archive(path)


def test_archive_rejects_invalid_master_key(tmp_path, system):
    staging = tmp_path / "bad-key"
    staging.mkdir()
    (staging / "database.dump").write_bytes(b"dump")
    (staging / "configuration").mkdir()
    (staging / "configuration" / "runtime.json").write_text("{}")
    (staging / "secrets").mkdir()
    (staging / "secrets" / "master.key").write_bytes(b"not-a-master-key\n")
    path = tmp_path / "bad.cpb"
    build_archive(staging, path, manifest(), ["database.dump", "configuration/runtime.json", "secrets/master.key"])
    with pytest.raises(ValueError, match="master.key"):
        inspect_archive(path)


def test_archive_rejects_checksum_mismatch(tmp_path, system):
    path = tmp_path / "checksum.cpb"
    payloads = {
        "database.dump": b"dump",
        "configuration/runtime.json": b"{}",
        "secrets/master.key": base64.b64encode(b"k" * 32) + b"\n",
    }
    checksum_doc = {"algorithm": "sha256", "files": {name: "0" * 64 for name in [*payloads, "manifest.json"]}}
    entries = {**payloads, "manifest.json": json.dumps(manifest()).encode(), "checksums.json": json.dumps(checksum_doc).encode()}
    with tarfile.open(path, "w:gz") as archive:
        for name, data in entries.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    with pytest.raises(ValueError, match="Checksum mismatch"):
        inspect_archive(path)
