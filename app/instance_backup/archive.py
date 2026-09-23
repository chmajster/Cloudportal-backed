from __future__ import annotations

import hashlib
import json
import os
import tarfile
from pathlib import Path, PurePosixPath

from app.config import settings
from app.instance_backup.config import (
    FORMAT_NAME,
    FORMAT_VERSION,
    MAX_ARCHIVE_MEMBERS,
    MAX_CHECKSUM_BYTES,
    MAX_MANIFEST_BYTES,
)
from app.instance_backup.paths import chmod_file

REQUIRED_MEMBERS = {"manifest.json", "checksums.json", "database.dump", "configuration/runtime.json"}
ALLOWED_MEMBERS = REQUIRED_MEMBERS | {"secrets/master.key"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    chmod_file(path)


def build_archive(
    staging: Path,
    destination: Path,
    manifest: dict,
    member_names: list[str],
    *,
    on_stage=None,
) -> tuple[int, str]:
    member_names = sorted(set(member_names))
    _json_file(staging / "manifest.json", manifest)
    checksum_names = sorted(set(member_names + ["manifest.json"]))
    if on_stage:
        on_stage("checksums")
    checksums = {name: sha256_file(staging / name) for name in checksum_names}
    _json_file(staging / "checksums.json", {"algorithm": "sha256", "files": checksums})
    archive_names = sorted(set(checksum_names + ["checksums.json"]))
    if on_stage:
        on_stage("archive")

    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(destination.name + ".partial")
    temporary.unlink(missing_ok=True)
    with temporary.open("xb") as raw:
        os.chmod(temporary, 0o600)
        with tarfile.open(fileobj=raw, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            for name in archive_names:
                source = staging / name
                info = tarfile.TarInfo(name=name)
                info.size = source.stat().st_size
                info.mode = 0o600
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mtime = 0
                with source.open("rb") as stream:
                    archive.addfile(info, stream)
        raw.flush()
        os.fsync(raw.fileno())
    os.replace(temporary, destination)
    chmod_file(destination)
    return destination.stat().st_size, sha256_file(destination)


def _safe_member_name(name: str) -> bool:
    if not name or "\\" in name or name.startswith("/"):
        return False
    path = PurePosixPath(name)
    return all(part not in {"", ".", ".."} for part in path.parts) and str(path) == name


def _load_json_member(archive: tarfile.TarFile, member: tarfile.TarInfo, limit: int) -> dict:
    if member.size > limit:
        raise ValueError(f"{member.name} is too large")
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError(f"Cannot read {member.name}")
    try:
        return json.loads(stream.read(limit + 1).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{member.name} is invalid JSON") from exc


def inspect_archive(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size < 32:
        raise ValueError("Backup file is empty or missing")
    with path.open("rb") as stream:
        if stream.read(2) != b"\x1f\x8b":
            raise ValueError("Backup does not have the expected CPB/GZip type")

    max_uncompressed = max(settings().instance_backup_max_upload_bytes * 4, 1024 * 1024 * 1024)
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ValueError("Backup contains too many archive members")
        by_name: dict[str, tarfile.TarInfo] = {}
        total = 0
        for member in members:
            if not member.isfile() or member.issym() or member.islnk() or not _safe_member_name(member.name):
                raise ValueError("Backup contains an unsafe archive entry")
            if member.name not in ALLOWED_MEMBERS or member.name in by_name:
                raise ValueError("Backup contains an unexpected archive entry")
            total += member.size
            if total > max_uncompressed:
                raise ValueError("Backup expands beyond the configured safety limit")
            by_name[member.name] = member
        missing = REQUIRED_MEMBERS - by_name.keys()
        if missing:
            raise ValueError("Backup is incomplete: " + ", ".join(sorted(missing)))

        manifest = _load_json_member(archive, by_name["manifest.json"], MAX_MANIFEST_BYTES)
        if manifest.get("format") != FORMAT_NAME:
            raise ValueError("Unsupported backup format")
        version = manifest.get("format_version")
        if not isinstance(version, int) or version < 1 or version > FORMAT_VERSION:
            raise ValueError("Unsupported backup format version")
        if manifest.get("application", {}).get("name") != "Cloudportal-backed":
            raise ValueError("Backup belongs to another application")

        checksums = _load_json_member(archive, by_name["checksums.json"], MAX_CHECKSUM_BYTES)
        if checksums.get("algorithm") != "sha256" or not isinstance(checksums.get("files"), dict):
            raise ValueError("Backup checksum manifest is invalid")
        expected = checksums["files"]
        for name, expected_hash in expected.items():
            if name not in by_name or name == "checksums.json" or not isinstance(expected_hash, str):
                raise ValueError("Backup checksum manifest references an invalid file")
            member_stream = archive.extractfile(by_name[name])
            if member_stream is None:
                raise ValueError("Backup member cannot be read")
            digest = hashlib.sha256()
            for chunk in iter(lambda: member_stream.read(1024 * 1024), b""):
                digest.update(chunk)
            if digest.hexdigest() != expected_hash:
                raise ValueError(f"Checksum mismatch for {name}")

        expected_names = set(by_name) - {"checksums.json"}
        if set(expected) != expected_names:
            raise ValueError("Backup checksum manifest is incomplete")
        if (manifest.get("secret") or {}).get("backend") == "local" and "secrets/master.key" not in by_name:
            raise ValueError("Local-key backup is missing master.key")
        return manifest


def extract_archive(path: Path, destination: Path) -> dict:
    manifest = inspect_archive(path)
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(destination, 0o700)
    with tarfile.open(path, mode="r:gz") as archive:
        for member in archive.getmembers():
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(target.parent, 0o700)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("Backup member cannot be extracted")
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    stream.write(chunk)
            os.chmod(target, 0o600)
    return manifest
