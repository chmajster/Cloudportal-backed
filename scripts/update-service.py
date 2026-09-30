#!/usr/bin/env python3
"""Privileged Cloudportal update sidecar.

The sidecar is intentionally independent from the application virtualenv so it
keeps reporting progress while the main API, dispatcher and workers restart.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn, UnixStreamServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

CONFIG_DIR = Path(os.environ.get("CP_UPDATER_CONFIG_DIR", "/etc/cloudportal-backed"))
DATA_DIR = Path(os.environ.get("CP_UPDATER_DATA_DIR", "/var/lib/cloudportal-backed"))
APP_ROOT = Path(os.environ.get("CP_UPDATER_APP_ROOT", "/opt/cloudportal-backed"))
PORT = int(os.environ.get("CP_UPDATER_PORT", "8766"))
REPOSITORY = os.environ.get("CP_UPDATER_REPOSITORY", "chmajster/Cloudportal-backed")
INSTALL_MODE = os.environ.get("CP_UPDATER_INSTALL_MODE", "systemd").strip().lower()
BIND_HOST = os.environ.get(
    "CP_UPDATER_BIND",
    "127.0.0.1",
).strip() or "127.0.0.1"
SOCKET_PATH = os.environ.get("CP_UPDATER_SOCKET", "").strip()

CONTROL_TOKEN = CONFIG_DIR / "updater.token"
STATUS_TOKEN = CONFIG_DIR / "updater-status.token"
SETTINGS_FILE = CONFIG_DIR / "updater.json"
STATE_DIR = DATA_DIR / "update"
STATE_FILE = STATE_DIR / "state.json"
CURRENT_LINK = APP_ROOT / "current"

REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
PROGRESS_RE = re.compile(r"^::cloudportal-progress::(\d{1,3})::([^:]+)::(.*)$")
SECRET_RE = re.compile(
    r"(?i)(authorization|token|password|secret|master[_ -]?key)(\s*[:=]\s*)(\S+)"
)
MAX_BODY = 128 * 1024
MAX_OUTPUT = 160
MAX_EVENTS = 120
MAX_CANDIDATE_ARCHIVE = 128 * 1024 * 1024
MAX_CANDIDATE_FILES = 20_000
MAX_CANDIDATE_EXTRACTED = 768 * 1024 * 1024
CI_POLL_SECONDS = 15
HTTP_RETRY_ATTEMPTS = 3
HTTP_RETRY_BASE_SECONDS = 1.0
HTTP_RETRY_STATUS = {429, 500, 502, 503, 504}
AUTO_FAILURE_COOLDOWN_MINUTES = 180

lock = threading.RLock()
update_thread: threading.Thread | None = None


class ThreadingUnixHTTPServer(ThreadingMixIn, UnixStreamServer):
    daemon_threads = True


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def default_settings() -> dict:
    return {
        "enabled": False,
        "interval_hours": 24,
        "ref": "main",
        "github_token_file": "",
        "github_config": "",
        "require_ci": True,
        "ci_workflow": "Backend CI",
        "ci_wait_minutes": 45,
        "candidate_validation": True,
        "runtime_preflight": True,
    }


def load_settings() -> dict:
    data = default_settings()
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data.update(raw)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return data


def public_settings() -> dict:
    data = load_settings()
    return {
        "enabled": bool(data.get("enabled", False)),
        "interval_hours": int(data.get("interval_hours", 24)),
        "ref": str(data.get("ref", "main")),
    }


def validate_ref(value: str) -> str:
    value = str(value or "").strip()
    if not REF_RE.fullmatch(value) or ".." in value or value.startswith("/"):
        raise ValueError("Invalid update ref")
    return value


def update_settings(payload: dict) -> dict:
    current = load_settings()
    enabled = payload.get("enabled", current.get("enabled", False))
    interval = payload.get("interval_hours", current.get("interval_hours", 24))
    ref = payload.get("ref", current.get("ref", "main"))
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be boolean")
    if isinstance(interval, bool) or not isinstance(interval, int) or not 1 <= interval <= 168:
        raise ValueError("interval_hours must be between 1 and 168")
    ref = validate_ref(ref)
    current.update({"enabled": enabled, "interval_hours": interval, "ref": ref})
    atomic_json(SETTINGS_FILE, current)
    return public_settings()


def default_state() -> dict:
    return {
        "status": "idle",
        "phase": "idle",
        "progress": 0,
        "message": "Updater gotowy.",
        "started_at": None,
        "finished_at": None,
        "last_check_at": None,
        "last_attempt_at": None,
        "last_success_at": None,
        "last_failure_at": None,
        "last_result": None,
        "consecutive_failures": 0,
        "failed_target_sha": None,
        "retry_not_before": None,
        "current_version": None,
        "target_version": None,
        "current_commit_at": None,
        "target_commit_at": None,
        "commit_relation": None,
        "ahead_by": 0,
        "behind_by": 0,
        "version_strategy": "git_commit",
        "ref": public_settings()["ref"],
        "update_available": None,
        "automatic": False,
        "events": [],
        "output": [],
    }


def load_state() -> dict:
    state = default_state()
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            state.update(raw)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    state["events"] = list(state.get("events") or [])[-MAX_EVENTS:]
    state["output"] = list(state.get("output") or [])[-MAX_OUTPUT:]
    return state


def save_state(**changes) -> dict:
    with lock:
        state = load_state()
        state.update(changes)
        atomic_json(STATE_FILE, state)
        return state


def event(phase: str, progress: int, message: str, **changes) -> dict:
    with lock:
        state = load_state()
        progress = max(0, min(100, int(progress)))
        state.update(changes)
        state.update({"phase": phase, "progress": progress, "message": str(message)})
        events = list(state.get("events") or [])
        events.append({"at": utcnow(), "phase": phase, "progress": progress, "message": str(message)})
        state["events"] = events[-MAX_EVENTS:]
        atomic_json(STATE_FILE, state)
        return state


def append_output(line: str) -> None:
    line = SECRET_RE.sub(lambda match: match.group(1) + match.group(2) + "***", line.rstrip())
    if not line:
        return
    with lock:
        state = load_state()
        output = list(state.get("output") or [])
        output.append(line[:2000])
        state["output"] = output[-MAX_OUTPUT:]
        atomic_json(STATE_FILE, state)


def release_info() -> dict:
    marker = CURRENT_LINK / ".cloudportal-release.json"
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _github_headers(settings: dict) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "cloudportal-updater/1"}
    token_file = str(settings.get("github_token_file") or "")
    if token_file:
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
            if token:
                headers["Authorization"] = "Bearer " + token
        except OSError:
            pass
    return headers


def _read_url(url: str, settings: dict, accept: str) -> bytes:
    github_config = str(settings.get("github_config") or "")
    if github_config:
        command = [
            "curl", "-fsSL", "--proto", "=https", "--tlsv1.2",
            "--connect-timeout", "15", "--max-time", "180", "--retry", "3",
            "--retry-all-errors",
            "--config", github_config, "-H", "Accept: " + accept, url,
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or "GitHub download failed")
        return result.stdout

    request = Request(url, headers={**_github_headers(settings), "Accept": accept})
    for attempt in range(1, HTTP_RETRY_ATTEMPTS + 1):
        try:
            with urlopen(request, timeout=60) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code not in HTTP_RETRY_STATUS or attempt >= HTTP_RETRY_ATTEMPTS:
                raise
            retry_after = 0.0
            try:
                retry_after = float(exc.headers.get("Retry-After", "0") or 0)
            except (TypeError, ValueError, AttributeError):
                retry_after = 0.0
            delay = max(retry_after, HTTP_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
            append_output(
                f"GitHub API HTTP {exc.code}; ponawiam próbę "
                f"{attempt + 1}/{HTTP_RETRY_ATTEMPTS} za {delay:g}s."
            )
            time.sleep(delay)
        except (URLError, TimeoutError, OSError):
            if attempt >= HTTP_RETRY_ATTEMPTS:
                raise
            delay = HTTP_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            append_output(
                f"Połączenie z GitHub nie powiodło się; ponawiam próbę "
                f"{attempt + 1}/{HTTP_RETRY_ATTEMPTS} za {delay:g}s."
            )
            time.sleep(delay)
    raise RuntimeError("GitHub request retry loop exhausted")


def _http_json(url: str, settings: dict) -> dict:
    return json.loads(_read_url(url, settings, "application/vnd.github+json").decode("utf-8"))


def _http_json_ci(url: str, settings: dict) -> dict:
    """Read GitHub Actions state, retrying anonymously for public repositories.

    A fine-grained Contents-only token can legitimately be unable to read the
    Actions API. For a public repository the anonymous retry still lets the
    safety gate work; for a private repository we fail closed and require
    Actions: read instead of silently deploying an unverified commit.
    """
    try:
        return _http_json(url, settings)
    except HTTPError as authenticated_error:
        if authenticated_error.code not in {401, 403, 404}:
            raise
        request = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "cloudportal-updater/1",
            },
        )
        try:
            with urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as anonymous_error:
            raise RuntimeError(
                "Nie można odczytać statusu GitHub Actions. "
                "Dla prywatnego repozytorium token updatera wymaga uprawnienia Actions: read."
            ) from anonymous_error


def required_ci_status(target_sha: str, settings: dict) -> dict:
    workflow = str(settings.get("ci_workflow") or "Backend CI").strip()
    url = (
        "https://api.github.com/repos/" + REPOSITORY
        + "/actions/runs?head_sha=" + quote(target_sha, safe="")
        + "&per_page=100"
    )
    data = _http_json_ci(url, settings)
    runs = [
        run for run in (data.get("workflow_runs") or [])
        if str(run.get("head_sha") or "") == target_sha
        and str(run.get("name") or "") == workflow
    ]
    if not runs:
        return {
            "state": "pending",
            "workflow": workflow,
            "message": f"Oczekiwanie na workflow {workflow} dla {target_sha[:12]}.",
            "url": None,
        }

    runs.sort(
        key=lambda run: (
            str(run.get("updated_at") or run.get("created_at") or ""),
            int(run.get("run_attempt") or 0),
        ),
        reverse=True,
    )
    run = runs[0]
    status = str(run.get("status") or "")
    conclusion = str(run.get("conclusion") or "")
    run_url = str(run.get("html_url") or "") or None
    if status != "completed":
        return {
            "state": "pending",
            "workflow": workflow,
            "message": f"Workflow {workflow} ma status {status or 'oczekuje'}.",
            "url": run_url,
        }
    if conclusion == "success":
        return {
            "state": "success",
            "workflow": workflow,
            "message": f"Workflow {workflow} zakończył się powodzeniem.",
            "url": run_url,
        }
    return {
        "state": "failed",
        "workflow": workflow,
        "message": f"Workflow {workflow} zakończył się wynikiem {conclusion or 'unknown'}.",
        "url": run_url,
    }


def wait_for_required_ci(target_sha: str, settings: dict) -> None:
    if not bool(settings.get("require_ci", True)):
        append_output("CI gate disabled in updater settings.")
        return

    try:
        timeout_minutes = int(settings.get("ci_wait_minutes", 45))
    except (TypeError, ValueError):
        timeout_minutes = 45
    timeout_minutes = max(5, min(180, timeout_minutes))
    deadline = time.monotonic() + timeout_minutes * 60
    last_message = None

    while True:
        result = required_ci_status(target_sha, settings)
        if result["state"] == "success":
            event(
                "ci_gate",
                10,
                result["message"],
                ci_status="success",
                ci_workflow=result["workflow"],
                ci_url=result.get("url"),
            )
            return
        if result["state"] == "failed":
            raise RuntimeError("CI gate zablokował aktualizację: " + result["message"])
        if result["message"] != last_message:
            event(
                "ci_wait",
                9,
                result["message"],
                ci_status="pending",
                ci_workflow=result["workflow"],
                ci_url=result.get("url"),
            )
            last_message = result["message"]
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"CI gate nie uzyskał zielonego wyniku w ciągu {timeout_minutes} min; aktualizacja nie została uruchomiona."
            )
        time.sleep(CI_POLL_SECONDS)


def download_candidate_archive(target_sha: str, target: Path, settings: dict) -> None:
    url = "https://api.github.com/repos/" + REPOSITORY + "/tarball/" + quote(target_sha, safe="")
    content = _read_url(url, settings, "application/vnd.github+json")
    if not content.startswith(b"\x1f\x8b"):
        raise RuntimeError("GitHub returned an invalid candidate archive")
    if len(content) > MAX_CANDIDATE_ARCHIVE:
        raise RuntimeError("Candidate archive is unexpectedly large")
    target.write_bytes(content)
    os.chmod(target, 0o600)


def extract_candidate_archive(archive_path: Path, destination: Path) -> None:
    file_count = 0
    extracted_size = 0
    with tarfile.open(archive_path, mode="r:gz") as archive:
        members = archive.getmembers()
        if not members:
            raise RuntimeError("Candidate archive is empty")
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError("Candidate archive contains an unsafe path")
            # GitHub tarballs have one generated top-level directory.
            relative_parts = path.parts[1:]
            if not relative_parts:
                continue
            relative = Path(*relative_parts)
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise RuntimeError("Candidate archive contains an unsupported special entry")
            file_count += 1
            extracted_size += int(member.size or 0)
            if file_count > MAX_CANDIDATE_FILES or extracted_size > MAX_CANDIDATE_EXTRACTED:
                raise RuntimeError("Candidate archive exceeds extraction safety limits")
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            if stream is None:
                raise RuntimeError("Candidate archive contains an unreadable file")
            with stream, target.open("wb") as output:
                shutil.copyfileobj(stream, output)
            os.chmod(target, 0o700 if member.mode & 0o111 else 0o600)


def _normalize_candidate_build_context(source: Path) -> None:
    """Make the extracted scratch tree readable by the non-root image user.

    extract_candidate_archive() deliberately writes restrictive 0600/0700 modes.
    Docker COPY preserves those modes and root ownership, so the candidate image
    would otherwise contain Python/Alembic files unreadable by UID 10001.
    """
    os.chmod(source, 0o755)
    for path in source.rglob("*"):
        if path.is_dir():
            os.chmod(path, 0o755)
        elif path.is_file():
            executable = bool(path.stat().st_mode & 0o111)
            os.chmod(path, 0o755 if executable else 0o644)


def _run_candidate_command(command: list[str], cwd: Path, label: str, timeout: int = 300) -> None:
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Walidacja kandydata przekroczyła limit czasu: {label}") from exc
    if result.returncode == 0:
        return
    lines = (result.stdout or "").splitlines()
    for line in lines[-40:]:
        append_output("candidate: " + line)
    raise RuntimeError(f"Walidacja kandydata nie przeszła: {label} (kod {result.returncode})")


def validate_candidate(target_sha: str, settings: dict) -> None:
    if not bool(settings.get("candidate_validation", True)):
        append_output("Local candidate validation disabled in updater settings.")
        return

    event("candidate_validation", 11, "Pobieranie i walidacja kandydata bez zmiany aktywnej aplikacji.")
    with tempfile.TemporaryDirectory(prefix="cloudportal-candidate-") as temp:
        root = Path(temp)
        archive = root / "candidate.tar.gz"
        source = root / "source"
        source.mkdir()
        download_candidate_archive(target_sha, archive, settings)
        extract_candidate_archive(archive, source)

        required = [
            source / "install.sh",
            source / "requirements.txt",
            source / "app" / "main.py",
            source / "alembic.ini",
            source / "scripts" / "update-service.py",
        ]
        missing = [str(path.relative_to(source)) for path in required if not path.is_file()]
        if missing:
            raise RuntimeError("Kandydat nie zawiera wymaganych plików: " + ", ".join(missing))

        bash = shutil.which("bash") or "/bin/bash"
        shell_scripts = [source / "install.sh"]
        secondary_installer = source / "scripts" / "install.sh"
        if secondary_installer.is_file():
            shell_scripts.append(secondary_installer)
        _run_candidate_command(
            [bash, "-n", *[str(path) for path in shell_scripts]],
            source,
            "bash -n instalatora",
            timeout=120,
        )
        _run_candidate_command(
            [sys.executable, "-m", "compileall", "-q", "app", "migrations", "scripts"],
            source,
            "kompilacja składni Python",
            timeout=300,
        )

    event(
        "candidate_validated",
        13,
        "Kandydat przeszedł lokalną walidację i nie zmienił aktywnej instalacji.",
        candidate_validation="success",
    )


def _database_clone_details(database_url: str) -> dict:
    parsed = urlsplit(database_url)
    if not parsed.scheme.startswith("postgresql"):
        raise RuntimeError("Runtime preflight obsługuje tylko PostgreSQL")
    query = parse_qs(parsed.query)
    host = (query.get("host") or [parsed.hostname or ""])[0]
    if host not in {"", "/var/run/postgresql", "/run/postgresql"}:
        raise RuntimeError(
            "Runtime preflight wymaga lokalnego PostgreSQL na socket; "
            "dla zewnętrznej bazy auto-update jest blokowany zamiast ryzykować wdrożenie bez testu."
        )
    username = parsed.username or "cloudportal"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,62}", username):
        raise RuntimeError("Nieprawidłowa lokalna rola PostgreSQL w CP_DATABASE_URL")
    port = parsed.port
    database = parsed.path.lstrip("/")
    if not database:
        raise RuntimeError("CP_DATABASE_URL nie zawiera nazwy bazy")
    return {
        "parsed": parsed,
        "host": host,
        "port": port,
        "username": username,
        "database": database,
    }


def _url_with_database(database_url: str, database: str) -> str:
    parsed = urlsplit(database_url)
    # urlunsplit() collapses postgresql+psycopg:///db to
    # postgresql+psycopg:/db when netloc is empty. SQLAlchemy requires the
    # triple-slash form for a local database URL.
    value = f"{parsed.scheme}://{parsed.netloc}/{database}"
    if parsed.query:
        value += "?" + parsed.query
    if parsed.fragment:
        value += "#" + parsed.fragment
    return value


def _scratch_redis_url(redis_url: str) -> str:
    parsed = urlsplit(redis_url)
    current = parsed.path.lstrip("/")
    try:
        current_db = int(current or "0")
    except ValueError:
        current_db = 0
    scratch_db = 15 if current_db != 15 else 14
    return urlunsplit((parsed.scheme, parsed.netloc, "/" + str(scratch_db), parsed.query, parsed.fragment))


def _postgres_connection_args(details: dict) -> list[str]:
    args = []
    if details.get("host"):
        args.extend(["--host", str(details["host"])])
    if details.get("port"):
        args.extend(["--port", str(details["port"])])
    return args


def _run_runtime_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict | None = None,
    label: str,
    timeout: int = 300,
) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Runtime preflight przekroczył limit czasu: {label}") from exc
    if result.returncode == 0:
        return result.stdout or ""
    for line in (result.stdout or "").splitlines()[-60:]:
        append_output("runtime-preflight: " + line)
    raise RuntimeError(f"Runtime preflight nie przeszedł: {label} (kod {result.returncode})")


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _candidate_health_payload(port: int) -> dict | None:
    request = Request(
        f"http://127.0.0.1:{port}/api/v1/health",
        headers={"Accept": "application/json", "User-Agent": "cloudportal-updater-preflight/1"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            raw = response.read()
    except HTTPError as exc:
        if exc.code != 503:
            return None
        raw = exc.read()
    except (URLError, TimeoutError, OSError):
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _candidate_core_healthy(payload: dict) -> bool:
    checks = payload.get("checks")
    if not isinstance(checks, dict):
        return False
    # Dispatcher/workers are intentionally not started in preflight because the
    # cloned production database can contain queued jobs with real provider
    # credentials. Starting executors would risk touching infrastructure.
    required = ("api", "database", "queue", "terraform", "ansible", "disk", "encryption")
    return all(bool(checks.get(name)) for name in required)


def _docker_wait_ready(command: list[str], *, label: str, timeout: int = 90) -> None:
    deadline = time.monotonic() + timeout
    last_output = ""
    while time.monotonic() < deadline:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        last_output = result.stdout or ""
        if result.returncode == 0:
            return
        time.sleep(1)
    for line in last_output.splitlines()[-40:]:
        append_output("runtime-preflight: " + line)
    raise RuntimeError(f"Runtime preflight nie przeszedł: {label}")


def _docker_validate_candidate_runtime(target_sha: str, backup_dir: Path, settings: dict) -> None:
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("Docker Engine jest wymagany do izolowanego runtime preflight")

    info = subprocess.run(
        [docker, "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if info.returncode != 0:
        raise RuntimeError("Docker Engine nie odpowiada; runtime preflight zablokowany")

    dump = backup_dir / "database.dump"
    if not dump.is_file():
        raise RuntimeError("Backup przed aktualizacją nie zawiera database.dump")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    event(
        "runtime_preflight",
        16,
        "Docker: testowanie obrazu, migracji i API kandydata na izolowanej kopii bazy.",
    )

    suffix = f"{os.getpid()}-{int(time.time())}-{target_sha[:8]}".lower()
    image = f"cloudportal-preflight:{suffix}"
    network = f"cloudportal-preflight-{suffix}"
    postgres = f"cloudportal-preflight-postgres-{suffix}"
    redis = f"cloudportal-preflight-redis-{suffix}"
    api = f"cloudportal-preflight-api-{suffix}"
    created_image = False
    created_network = False
    containers: list[str] = []

    with tempfile.TemporaryDirectory(prefix="runtime-preflight-", dir=str(DATA_DIR)) as temp:
        root = Path(temp)
        os.chmod(root, 0o755)
        archive = root / "candidate.tar.gz"
        source = root / "source"
        config_dir = root / "config"
        data_dir = root / "data"
        source.mkdir()
        config_dir.mkdir(mode=0o700)
        data_dir.mkdir(mode=0o700)

        # The candidate must not receive the production encryption key. Health only
        # validates key availability, while restored encrypted secrets remain inert.
        master_key = config_dir / "master.key"
        master_key.write_bytes(base64.b64encode(os.urandom(32)) + b"\n")
        os.chmod(master_key, 0o600)
        os.chown(config_dir, 10001, 10001)
        os.chown(data_dir, 10001, 10001)
        os.chown(master_key, 10001, 10001)

        download_candidate_archive(target_sha, archive, settings)
        extract_candidate_archive(archive, source)
        _normalize_candidate_build_context(source)

        password = os.urandom(24).hex()
        database_url = f"postgresql+psycopg://cloudportal:{password}@postgres/cloudportal"
        redis_url = "redis://redis:6379/0"
        runtime_env = [
            "-e", f"CP_DATABASE_URL={database_url}",
            "-e", f"CP_REDIS_URL={redis_url}",
            "-e", "CP_MASTER_KEY_FILE=/etc/cloudportal-backed/master.key",
            "-e", "CP_DATA_DIR=/var/lib/cloudportal-backed",
            "-e", "CP_WORKER_COUNT=1",
            "-e", "CP_ALLOW_HTTP=true",
            "-e", "CP_INSTALL_MODE=docker",
        ]
        runtime_mounts = [
            "-v", f"{config_dir}:/etc/cloudportal-backed:ro",
            "-v", f"{data_dir}:/var/lib/cloudportal-backed:rw",
        ]

        try:
            _run_runtime_command(
                [
                    docker, "build", "--pull",
                    "--build-arg", f"BUILD_COMMIT={target_sha}",
                    "-t", image, ".",
                ],
                cwd=source,
                label="build obrazu Docker kandydata",
                timeout=1800,
            )
            created_image = True

            _run_runtime_command(
                [docker, "network", "create", network],
                label="utworzenie izolowanej sieci Docker",
                timeout=60,
            )
            created_network = True

            _run_runtime_command(
                [
                    docker, "run", "-d", "--name", postgres, "--network", network,
                    "--network-alias", "postgres",
                    "-e", "POSTGRES_DB=cloudportal",
                    "-e", "POSTGRES_USER=cloudportal",
                    "-e", f"POSTGRES_PASSWORD={password}",
                    "postgres:16-alpine",
                ],
                label="uruchomienie izolowanego PostgreSQL",
                timeout=120,
            )
            containers.append(postgres)
            _run_runtime_command(
                [
                    docker, "run", "-d", "--name", redis, "--network", network,
                    "--network-alias", "redis",
                    "redis:7-alpine", "redis-server", "--save", "", "--appendonly", "no",
                ],
                label="uruchomienie izolowanego Redis",
                timeout=120,
            )
            containers.append(redis)

            _docker_wait_ready(
                [docker, "exec", postgres, "pg_isready", "-U", "cloudportal", "-d", "cloudportal"],
                label="gotowość izolowanego PostgreSQL",
                timeout=120,
            )
            _docker_wait_ready(
                [docker, "exec", redis, "redis-cli", "ping"],
                label="gotowość izolowanego Redis",
                timeout=60,
            )

            _run_runtime_command(
                [docker, "cp", str(dump), f"{postgres}:/tmp/database.dump"],
                label="kopiowanie backupu do izolowanego PostgreSQL",
                timeout=300,
            )
            _run_runtime_command(
                [
                    docker, "exec", postgres,
                    "pg_restore", "-U", "cloudportal", "-d", "cloudportal",
                    "--no-owner", "--no-privileges", "--exit-on-error",
                    "/tmp/database.dump",
                ],
                label="odtworzenie produkcyjnego dumpa w izolowanej bazie",
                timeout=1800,
            )

            candidate_base = [
                docker, "run", "--rm", "--network", network,
                "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true",
                "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777",
                *runtime_env, *runtime_mounts, image,
            ]
            _run_runtime_command(
                [*candidate_base, "alembic", "upgrade", "head"],
                label="migracje kandydata na izolowanej kopii bazy",
                timeout=900,
            )

            port = _free_loopback_port()
            start = [
                docker, "run", "-d", "--name", api, "--network", network,
                "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true",
                "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777",
                "-p", f"127.0.0.1:{port}:8765",
                *runtime_env, *runtime_mounts, image,
                "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765",
            ]
            _run_runtime_command(
                start,
                label="uruchomienie izolowanego API kandydata",
                timeout=120,
            )
            containers.append(api)

            payload = None
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if subprocess.run(
                    [docker, "inspect", "-f", "{{.State.Running}}", api],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    check=False,
                ).stdout.strip() != "true":
                    break
                payload = _candidate_health_payload(port)
                if payload is not None and _candidate_core_healthy(payload):
                    break
                time.sleep(1)

            if payload is None or not _candidate_core_healthy(payload):
                logs = subprocess.run(
                    [docker, "logs", "--tail", "100", api],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                ).stdout or ""
                for line in logs.splitlines()[-80:]:
                    append_output("runtime-preflight-api: " + line)
                checks = payload.get("checks") if isinstance(payload, dict) else None
                raise RuntimeError(
                    "Docker kandydat nie przeszedł izolowanego healthchecku"
                    + (f": {checks}" if checks else "")
                )
        finally:
            for container in reversed(containers):
                subprocess.run(
                    [docker, "rm", "-f", container],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            if created_network:
                subprocess.run(
                    [docker, "network", "rm", network],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            if created_image:
                subprocess.run(
                    [docker, "image", "rm", "-f", image],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )

    event(
        "runtime_preflight_ok",
        18,
        "Docker: migracje i API kandydata działają na izolowanej kopii produkcyjnej bazy.",
        runtime_preflight="success",
    )


def validate_candidate_runtime(target_sha: str, backup_dir: Path, settings: dict) -> None:
    if not bool(settings.get("runtime_preflight", True)):
        append_output("Runtime candidate preflight disabled in updater settings.")
        return
    if INSTALL_MODE == "docker":
        _docker_validate_candidate_runtime(target_sha, backup_dir, settings)
        return

    backend = parse_kv(CONFIG_DIR / "backend.env")
    database_url = str(backend.get("CP_DATABASE_URL") or "")
    redis_url = str(backend.get("CP_REDIS_URL") or "")
    if not database_url or not redis_url:
        raise RuntimeError("Brak CP_DATABASE_URL lub CP_REDIS_URL; runtime preflight zablokowany")

    details = _database_clone_details(database_url)
    runuser = shutil.which("runuser")
    createdb = shutil.which("createdb")
    dropdb = shutil.which("dropdb")
    pg_restore = shutil.which("pg_restore")
    if not all((runuser, createdb, dropdb, pg_restore)):
        raise RuntimeError("Brak runuser/createdb/dropdb/pg_restore wymaganych do bezpiecznego runtime preflight")

    dump = backup_dir / "database.dump"
    if not dump.is_file():
        raise RuntimeError("Backup przed aktualizacją nie zawiera database.dump")

    scratch_db = f"cloudportal_preflight_{os.getpid()}_{int(time.time())}"
    scratch_url = _url_with_database(database_url, scratch_db)
    scratch_redis = _scratch_redis_url(redis_url)
    pg_conn = _postgres_connection_args(details)
    database_created = False
    process = None

    event(
        "runtime_preflight",
        16,
        "Testowanie migracji i API kandydata na kopii aktualnej bazy; produkcja pozostaje bez zmian.",
    )

    with tempfile.TemporaryDirectory(prefix="cloudportal-runtime-preflight-") as temp:
        root = Path(temp)
        os.chmod(root, 0o755)
        archive = root / "candidate.tar.gz"
        source = root / "source"
        venv = root / "venv"
        data_dir = root / "data"
        source.mkdir()
        data_dir.mkdir()
        download_candidate_archive(target_sha, archive, settings)
        extract_candidate_archive(archive, source)

        _run_runtime_command(
            [sys.executable, "-m", "venv", str(venv)],
            label="utworzenie izolowanego venv",
            timeout=300,
        )
        _run_runtime_command(
            ["chown", "-R", "cloudportal:cloudportal", str(source), str(venv), str(data_dir)],
            label="uprawnienia izolowanego środowiska",
            timeout=120,
        )
        _run_runtime_command(
            [
                runuser, "-u", "cloudportal", "--",
                str(venv / "bin" / "pip"), "install", "--disable-pip-version-check",
                "-r", str(source / "requirements.txt"),
            ],
            cwd=source,
            label="instalacja zależności kandydata",
            timeout=1200,
        )

        scratch_dump = root / "database.dump"
        shutil.copy2(dump, scratch_dump)
        os.chmod(scratch_dump, 0o600)
        _run_runtime_command(
            ["chown", "cloudportal:cloudportal", str(scratch_dump)],
            label="uprawnienia tymczasowej kopii backupu",
            timeout=60,
        )

        admin_create = [
            runuser, "-u", "postgres", "--", createdb,
            "--owner", details["username"], *pg_conn, scratch_db,
        ]
        _run_runtime_command(admin_create, label="utworzenie tymczasowej bazy", timeout=120)
        database_created = True

        try:
            restore_command = [
                runuser, "-u", details["username"], "--", pg_restore,
                "--no-owner", "--no-privileges", "--exit-on-error",
                *pg_conn, "--dbname", scratch_db, str(scratch_dump),
            ]
            _run_runtime_command(
                restore_command,
                label="odtworzenie backupu do tymczasowej bazy",
                timeout=1800,
            )

            candidate_env = os.environ.copy()
            candidate_env.update(backend)
            candidate_env.update({
                "CP_DATABASE_URL": scratch_url,
                "CP_REDIS_URL": scratch_redis,
                "CP_DATA_DIR": str(data_dir),
                "CP_WORKER_COUNT": "1",
                "CP_ALLOW_HTTP": "true",
            })

            flush_code = (
                "import os; from redis import Redis; "
                "Redis.from_url(os.environ['CP_REDIS_URL']).flushdb()"
            )
            _run_runtime_command(
                [runuser, "-u", "cloudportal", "--", str(venv / "bin" / "python"), "-c", flush_code],
                cwd=source,
                env=candidate_env,
                label="wyczyszczenie izolowanej kolejki Redis",
                timeout=60,
            )
            _run_runtime_command(
                [runuser, "-u", "cloudportal", "--", str(venv / "bin" / "alembic"), "upgrade", "head"],
                cwd=source,
                env=candidate_env,
                label="migracje kandydata na kopii produkcyjnej bazy",
                timeout=900,
            )

            port = _free_loopback_port()
            log_path = root / "candidate-api.log"
            with log_path.open("w+", encoding="utf-8") as log:
                process = subprocess.Popen(
                    [
                        runuser, "-u", "cloudportal", "--",
                        str(venv / "bin" / "uvicorn"), "app.main:app",
                        "--host", "127.0.0.1", "--port", str(port),
                    ],
                    cwd=str(source),
                    env=candidate_env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                payload = None
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    payload = _candidate_health_payload(port)
                    if payload is not None and _candidate_core_healthy(payload):
                        break
                    time.sleep(1)

                if payload is None or not _candidate_core_healthy(payload):
                    log.flush()
                    log.seek(0)
                    for line in log.read().splitlines()[-80:]:
                        append_output("runtime-preflight-api: " + line)
                    checks = payload.get("checks") if isinstance(payload, dict) else None
                    raise RuntimeError(
                        "Kandydat nie przeszedł izolowanego healthchecku"
                        + (f": {checks}" if checks else "")
                    )
        finally:
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=10)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            try:
                candidate_env
            except UnboundLocalError:
                candidate_env = None
            if candidate_env is not None:
                try:
                    _run_runtime_command(
                        [runuser, "-u", "cloudportal", "--", str(venv / "bin" / "python"), "-c", flush_code],
                        cwd=source,
                        env=candidate_env,
                        label="sprzątanie izolowanej kolejki Redis",
                        timeout=60,
                    )
                except Exception as exc:
                    append_output("runtime-preflight cleanup warning: " + str(exc))
            if database_created:
                try:
                    _run_runtime_command(
                        [runuser, "-u", "postgres", "--", dropdb, "--if-exists", *pg_conn, scratch_db],
                        label="usunięcie tymczasowej bazy",
                        timeout=120,
                    )
                except Exception as exc:
                    append_output("runtime-preflight cleanup warning: " + str(exc))

    event(
        "runtime_preflight_ok",
        18,
        "Migracje i API kandydata działają na kopii aktualnej bazy. Można rozpocząć właściwą aktualizację.",
        runtime_preflight="success",
    )


def _service_active(unit: str) -> bool:
    return subprocess.run(
        ["systemctl", "is-active", "--quiet", unit],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def ensure_current_installation_healthy() -> None:
    if INSTALL_MODE == "docker":
        installer = CURRENT_LINK / "install.sh"
        if not installer.is_file():
            raise RuntimeError("Brak aktywnego instalatora Docker do walidacji stanu")
        result = subprocess.run(
            ["/bin/bash", str(installer), "--docker", "--status", "--no-auto-repair"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=180,
            check=False,
        )
        if result.returncode != 0:
            for line in (result.stdout or "").splitlines()[-80:]:
                append_output("docker-health: " + line)
            raise RuntimeError("Bieżący stack Docker nie przeszedł walidacji; auto-update wstrzymany")
        return

    backend = parse_kv(CONFIG_DIR / "backend.env")
    try:
        workers = max(1, min(64, int(backend.get("CP_WORKER_COUNT", "1"))))
    except ValueError:
        workers = 1
    units = [
        "cloudportal-api.service",
        "cloudportal-dispatcher.service",
        "cloudportal-redis.service",
        "nginx.service",
        *[f"cloudportal-worker@{index}.service" for index in range(1, workers + 1)],
    ]
    inactive = [unit for unit in units if not _service_active(unit)]
    if inactive:
        raise RuntimeError(
            "Bieżąca instalacja nie jest zdrowa; auto-update wstrzymany. Nieaktywne usługi: "
            + ", ".join(inactive)
        )

    request = Request(
        "http://127.0.0.1:8765/api/v1/health",
        headers={"Accept": "application/json", "User-Agent": "cloudportal-updater/1"},
    )
    try:
        with urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Bieżący healthcheck API nie przeszedł; auto-update wstrzymany") from exc
    if response.status != 200 or str(payload.get("status") or "").lower() != "ok":
        raise RuntimeError("Bieżący healthcheck API zwrócił nieprawidłowy stan; auto-update wstrzymany")


def verify_installed_release(target_sha: str) -> None:
    release = release_info()
    installed_sha = str(release.get("commit_sha") or "")
    if installed_sha != target_sha:
        raise RuntimeError(
            "Instalator zakończył się bez potwierdzenia oczekiwanego commita "
            f"{target_sha[:12]} (aktywny: {installed_sha[:12] or 'unknown'})."
        )
    ensure_current_installation_healthy()


def remote_commit(ref: str, settings: dict) -> dict:
    ref = validate_ref(ref)
    url = "https://api.github.com/repos/" + REPOSITORY + "/commits/" + quote(ref, safe="")
    data = _http_json(url, settings)
    sha = str(data.get("sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("GitHub returned an invalid commit identifier")
    commit = data.get("commit") or {}
    committer = commit.get("committer") or {}
    author = commit.get("author") or {}
    committed_at = committer.get("date") or author.get("date")
    return {
        "sha": sha,
        "committed_at": committed_at,
    }


def _unknown_current_order() -> dict:
    return {
        "relation": "unknown_current",
        "update_available": True,
        "ahead_by": 0,
        "behind_by": 0,
        "current_commit_at": None,
    }


def commit_order(current_sha: str, target: dict, settings: dict) -> dict:
    target_sha = target["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", current_sha or ""):
        # Older installers could persist an archive SHA-256 or another
        # non-Git release marker in commit_sha. Such values cannot be used
        # with GitHub's compare endpoint, but they must not block a repair
        # update. Treat the local version as unknown and pin the next
        # installation to the verified channel HEAD.
        return _unknown_current_order()
    if current_sha == target_sha:
        return {
            "relation": "identical",
            "update_available": False,
            "ahead_by": 0,
            "behind_by": 0,
            "current_commit_at": target.get("committed_at"),
        }

    compare_url = (
        "https://api.github.com/repos/" + REPOSITORY + "/compare/"
        + quote(current_sha, safe="") + "..." + quote(target_sha, safe="")
    )
    try:
        comparison = _http_json(compare_url, settings)
    except HTTPError as exc:
        if exc.code in {404, 422}:
            # The marker is syntactically a SHA but is no longer resolvable in
            # the repository (for example after a legacy archive-based
            # install). Recover exactly like an unknown local commit.
            return _unknown_current_order()
        raise
    status = str(comparison.get("status") or "")
    ahead_by = int(comparison.get("ahead_by") or 0)
    behind_by = int(comparison.get("behind_by") or 0)

    current = remote_commit(current_sha, settings)
    if status == "ahead":
        available = ahead_by > 0
        relation = "target_newer" if available else "identical"
    elif status == "behind":
        available = False
        relation = "current_newer"
    elif status == "identical":
        available = False
        relation = "identical"
    elif status == "diverged":
        current_at = current.get("committed_at")
        target_at = target.get("committed_at")
        available = bool(target_at and current_at and target_at > current_at)
        relation = "target_newer_diverged" if available else "current_newer_diverged"
    else:
        raise RuntimeError("GitHub returned an unknown commit relation")

    return {
        "relation": relation,
        "update_available": available,
        "ahead_by": ahead_by,
        "behind_by": behind_by,
        "current_commit_at": current.get("committed_at"),
    }


def check_remote(ref: str | None = None, *, update_context: bool = False) -> dict:
    settings = load_settings()
    ref = validate_ref(ref or settings.get("ref", "main"))
    if update_context:
        event(
            "preflight",
            3,
            "Weryfikowanie nowszego commita przed aktualizacją.",
            status="running",
            ref=ref,
            finished_at=None,
        )
    else:
        event("checking", 3, "Porównywanie commitów wybranego kanału.", status="checking", ref=ref, finished_at=None)
    current = str(release_info().get("commit_sha") or "")
    try:
        target = remote_commit(ref, settings)
        ordering = commit_order(current, target, settings)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        attempted_at = utcnow()
        if not update_context:
            save_state(status="failed", finished_at=attempted_at, last_check_at=attempted_at)
            event("check_failed", 0, "Nie udało się sprawdzić aktualizacji: " + str(exc))
        raise

    available = ordering["update_available"]
    relation = ordering["relation"]
    status = "update_available" if available else (
        "local_ahead" if relation.startswith("current_newer") else "up_to_date"
    )
    now = utcnow()
    state_changes = {
        "current_version": current[:12] or "nieznana",
        "target_version": target["sha"][:12],
        "current_commit_at": ordering.get("current_commit_at"),
        "target_commit_at": target.get("committed_at"),
        "commit_relation": relation,
        "ahead_by": ordering.get("ahead_by", 0),
        "behind_by": ordering.get("behind_by", 0),
        "version_strategy": "git_commit",
        "update_available": available,
        "last_check_at": now,
        "ref": ref,
    }

    if available and update_context:
        save_state(status="running", finished_at=None, **state_changes)
        event(
            "preflight",
            8,
            "Nowszy commit potwierdzony. Przygotowanie do instalacji.",
            status="running",
        )
    else:
        save_state(status=status, finished_at=now, **state_changes)
        if available:
            message = "Dostępny jest nowszy commit."
            phase = "available"
            progress = 8
        elif status == "local_ahead":
            message = "Zainstalowany commit jest nowszy niż commit kanału."
            phase = "local_ahead"
            progress = 100
        else:
            message = "Zainstalowany commit jest aktualny."
            phase = "up_to_date"
            progress = 100
        event(phase, progress, message, status=status)
    return {
        "ref": ref,
        "current_version": current[:12] or "nieznana",
        "target_version": target["sha"][:12],
        "target_sha": target["sha"],
        "current_commit_at": ordering.get("current_commit_at"),
        "target_commit_at": target.get("committed_at"),
        "commit_relation": relation,
        "ahead_by": ordering.get("ahead_by", 0),
        "behind_by": ordering.get("behind_by", 0),
        "version_strategy": "git_commit",
        "update_available": available,
    }


def parse_kv(path: Path) -> dict:
    result = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                result[key] = value
    except OSError:
        pass
    return result



LETSENCRYPT_LIVE_DIR = Path(os.environ.get("CP_LETSENCRYPT_LIVE_DIR", "/etc/letsencrypt/live"))
CERTBOT_DEPLOY_HOOK = Path(
    os.environ.get(
        "CP_CERTBOT_DEPLOY_HOOK",
        "/etc/letsencrypt/renewal-hooks/deploy/cloudportal-backed",
    )
)
NGINX_SYSTEMD_CONF = Path(
    os.environ.get("CP_NGINX_CONFIG_FILE", "/etc/nginx/conf.d/cloudportal-backed.conf")
)


def _tls_directory() -> Path:
    return CONFIG_DIR / "tls"


def _tls_source_marker() -> Path:
    name = "source" if INSTALL_MODE == "docker" else "certificate-source"
    return _tls_directory() / name


def _tls_metadata_path() -> Path:
    return _tls_directory() / "cloudportal-source.json"


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _public_endpoint() -> tuple[str, str]:
    if INSTALL_MODE == "docker":
        values = parse_kv(CONFIG_DIR / "docker.env")
        host = str(values.get("CP_PUBLIC_HOST") or "").strip()
        port = str(values.get("CP_HTTPS_PORT") or "").strip()
    else:
        values = parse_kv(CONFIG_DIR / "public.conf")
        host = str(values.get("host") or "").strip()
        port = str(values.get("port") or "").strip()
    if not host or not port:
        raise RuntimeError("Brak zapisanej konfiguracji publicznego hosta/portu")
    return host, port


def _valid_tls_hostname(value: str) -> str:
    value = str(value or "").strip().rstrip(".")
    if (
        not value
        or len(value) > 253
        or "/" in value
        or "\\" in value
        or ".." in value
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,251}[A-Za-z0-9]|[A-Za-z0-9]", value)
    ):
        raise ValueError("Nieprawidłowy publiczny hostname TLS")
    return value


def _run_tls_command(
    command: list[str],
    *,
    input_data: bytes | None = None,
    timeout: int = 20,
) -> bytes:
    try:
        result = subprocess.run(
            command,
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Walidacja TLS przekroczyła limit czasu") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise ValueError(detail or "Walidacja TLS nie powiodła się")
    return result.stdout


def _certificate_matches_hostname(cert: Path, hostname: str) -> bool:
    openssl = shutil.which("openssl") or "openssl"
    option = "-checkip" if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", hostname) else "-checkhost"
    result = subprocess.run(
        [openssl, "x509", "-in", str(cert), "-noout", option, hostname],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
        check=False,
    )
    return result.returncode == 0


def _certificate_key_matches(cert: Path, key: Path) -> bool:
    openssl = shutil.which("openssl") or "openssl"
    try:
        cert_pem = _run_tls_command(
            [openssl, "x509", "-in", str(cert), "-pubkey", "-noout"],
        )
        cert_der = _run_tls_command(
            [openssl, "pkey", "-pubin", "-outform", "DER"],
            input_data=cert_pem,
        )
        key_der = _run_tls_command(
            [openssl, "pkey", "-in", str(key), "-pubout", "-outform", "DER"],
        )
    except (ValueError, RuntimeError):
        return False
    return bool(cert_der and cert_der == key_der)


def _certificate_details(cert: Path, hostname: str | None = None) -> dict:
    if not cert.is_file():
        return {"present": False}
    openssl = shutil.which("openssl") or "openssl"
    try:
        raw = _run_tls_command(
            [
                openssl,
                "x509",
                "-in",
                str(cert),
                "-noout",
                "-subject",
                "-issuer",
                "-serial",
                "-startdate",
                "-enddate",
                "-ext",
                "subjectAltName",
            ]
        ).decode("utf-8", "replace")
    except (ValueError, RuntimeError) as exc:
        return {"present": True, "valid": False, "error": str(exc)}

    values = {}
    for line in raw.splitlines():
        for key, prefix in (
            ("subject", "subject="),
            ("issuer", "issuer="),
            ("serial", "serial="),
            ("not_before", "notBefore="),
            ("not_after", "notAfter="),
        ):
            if line.startswith(prefix):
                values[key] = line[len(prefix):].strip()
    dns_names = re.findall(r"DNS:([^,\s]+)", raw)
    ip_addresses = re.findall(r"IP Address:([^,\s]+)", raw)
    valid_now = subprocess.run(
        [openssl, "x509", "-in", str(cert), "-noout", "-checkend", "0"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0
    expires_soon = subprocess.run(
        [openssl, "x509", "-in", str(cert), "-noout", "-checkend", str(30 * 86400)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode != 0
    return {
        "present": True,
        "valid": valid_now,
        "expires_within_30_days": expires_soon,
        "matches_hostname": _certificate_matches_hostname(cert, hostname) if hostname else None,
        "dns_names": dns_names,
        "ip_addresses": ip_addresses,
        **values,
    }


def _validate_tls_pair(cert: Path, key: Path, hostname: str) -> dict:
    openssl = shutil.which("openssl") or "openssl"
    _run_tls_command([openssl, "x509", "-in", str(cert), "-noout"])
    _run_tls_command([openssl, "pkey", "-in", str(key), "-noout"])
    if not _certificate_key_matches(cert, key):
        raise ValueError("Certyfikat TLS i klucz prywatny nie pasują do siebie")
    if subprocess.run(
        [openssl, "x509", "-in", str(cert), "-noout", "-checkend", "300"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode != 0:
        raise ValueError("Certyfikat TLS jest nieważny albo wygasa w ciągu 5 minut")
    if not _certificate_matches_hostname(cert, hostname):
        raise ValueError(f"Certyfikat TLS nie obejmuje hosta {hostname}")
    return _certificate_details(cert, hostname)


def _atomic_bytes(path: Path, value: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    try:
        tmp.write_bytes(value)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _atomic_text(path: Path, value: str, mode: int = 0o600) -> None:
    _atomic_bytes(path, value.encode("utf-8"), mode)


def _snapshot_paths(paths: list[Path]) -> dict[Path, tuple[bytes | None, int]]:
    snapshot = {}
    for path in paths:
        try:
            snapshot[path] = (path.read_bytes(), path.stat().st_mode & 0o777)
        except FileNotFoundError:
            snapshot[path] = (None, 0o600)
    return snapshot


def _restore_paths(snapshot: dict[Path, tuple[bytes | None, int]]) -> None:
    for path, (content, mode) in snapshot.items():
        if content is None:
            path.unlink(missing_ok=True)
        else:
            _atomic_bytes(path, content, mode)


def _set_kv_value(path: Path, key: str, value: str) -> None:
    original = path.read_text(encoding="utf-8")
    mode = path.stat().st_mode & 0o777
    prefix = key + "="
    lines = []
    replaced = False
    for line in original.splitlines():
        if line.startswith(prefix):
            if not replaced:
                lines.append(prefix + value)
                replaced = True
            continue
        lines.append(line)
    if not replaced:
        lines.append(prefix + value)
    _atomic_text(path, "\n".join(lines) + "\n", mode)


def _set_public_hostname(hostname: str) -> None:
    hostname = _valid_tls_hostname(hostname)
    if INSTALL_MODE == "docker":
        path = CONFIG_DIR / "docker.env"
        if not path.is_file():
            raise RuntimeError("Brak docker.env")
        _set_kv_value(path, "CP_PUBLIC_HOST", hostname)
        return

    public = CONFIG_DIR / "public.conf"
    backend = CONFIG_DIR / "backend.env"
    if not public.is_file() or not backend.is_file():
        raise RuntimeError("Brak konfiguracji public.conf/backend.env")
    _set_kv_value(public, "host", hostname)
    _set_kv_value(backend, "CP_PUBLIC_HOST", hostname)
    if NGINX_SYSTEMD_CONF.is_file():
        original = NGINX_SYSTEMD_CONF.read_text(encoding="utf-8")
        updated, count = re.subn(
            r"(?m)^(\s*server_name\s+)[^;]+;",
            lambda match: match.group(1) + hostname + ";",
            original,
            count=1,
        )
        if count:
            _atomic_text(
                NGINX_SYSTEMD_CONF,
                updated,
                NGINX_SYSTEMD_CONF.stat().st_mode & 0o777,
            )


def _validate_proxy_tls() -> None:
    if INSTALL_MODE == "docker":
        _run_worker_command(
            _docker_compose_base() + ["exec", "-T", "proxy", "nginx", "-t"],
            "walidacja konfiguracji Nginx Docker",
            timeout=60,
        )
        return
    nginx = shutil.which("nginx") or "nginx"
    _run_worker_command([nginx, "-t"], "walidacja konfiguracji Nginx", timeout=60)


def _reload_proxy_tls() -> None:
    if INSTALL_MODE == "docker":
        _run_worker_command(
            _docker_compose_base() + ["kill", "-s", "HUP", "proxy"],
            "przeładowanie TLS Nginx Docker",
            timeout=60,
        )
        return
    systemctl = shutil.which("systemctl") or "systemctl"
    _run_worker_command(
        [systemctl, "reload", "nginx"],
        "przeładowanie TLS Nginx",
        timeout=60,
    )


def _write_certbot_hook(enabled: bool) -> None:
    if not enabled:
        CERTBOT_DEPLOY_HOOK.unlink(missing_ok=True)
        return
    token_file = CONFIG_DIR / "updater.token"
    metadata = _read_json(_tls_metadata_path())
    active_lineage = str(metadata.get("path") or "")
    if not active_lineage:
        raise RuntimeError("Brak źródła Let's Encrypt dla hooka odnowienia")
    if SOCKET_PATH:
        curl_target = (
            f'curl --fail --silent --show-error --unix-socket "{SOCKET_PATH}" '
            '-H "X-Updater-Token: $TOKEN" -H "Content-Type: application/json" '
            "-d '{}' http://localhost/tls/sync >/dev/null"
        )
    else:
        curl_target = (
            f'curl --fail --silent --show-error '
            '-H "X-Updater-Token: $TOKEN" -H "Content-Type: application/json" '
            f"-d '{{}}' http://127.0.0.1:{PORT}/tls/sync >/dev/null"
        )
    script = (
        "#!/bin/sh\n"
        "set -eu\n"
        f'TOKEN_FILE="{token_file}"\n'
        f'ACTIVE_LINEAGE="{active_lineage}"\n'
        'if [ -n "${RENEWED_LINEAGE:-}" ] && [ "$RENEWED_LINEAGE" != "$ACTIVE_LINEAGE" ]; then exit 0; fi\n'
        'TOKEN=$(cat "$TOKEN_FILE")\n'
        + curl_target
        + "\n"
    )
    CERTBOT_DEPLOY_HOOK.parent.mkdir(parents=True, exist_ok=True)
    _atomic_text(CERTBOT_DEPLOY_HOOK, script, 0o700)


def _tls_status_payload() -> dict:
    host, port = _public_endpoint()
    tls_dir = _tls_directory()
    cert = tls_dir / "server.crt"
    key = tls_dir / "server.key"
    metadata = _read_json(_tls_metadata_path())
    source = ""
    try:
        source = _tls_source_marker().read_text(encoding="utf-8").strip()
    except OSError:
        pass
    details = _certificate_details(cert, host)
    return {
        "install_mode": INSTALL_MODE,
        "hostname": host,
        "port": int(port),
        "url": f"https://{host}:{port}/ui/",
        "source": metadata.get("type") or source or "unknown",
        "source_path": metadata.get("path"),
        "certificate": details,
        "key_present": key.is_file(),
        "renewal_hook_installed": CERTBOT_DEPLOY_HOOK.is_file(),
        "letsencrypt_live_dir": str(LETSENCRYPT_LIVE_DIR),
    }


def _letsencrypt_lineage_path(lineage: str) -> Path:
    lineage = str(lineage or "").strip()
    if (
        not lineage
        or len(lineage) > 253
        or lineage in {".", ".."}
        or "/" in lineage
        or "\\" in lineage
        or not re.fullmatch(r"[A-Za-z0-9*_.-]+", lineage)
    ):
        raise ValueError("Nieprawidłowa nazwa certyfikatu Let's Encrypt")
    return LETSENCRYPT_LIVE_DIR / lineage


def letsencrypt_candidates() -> dict:
    host, _ = _public_endpoint()
    items = []
    try:
        entries = sorted(LETSENCRYPT_LIVE_DIR.iterdir(), key=lambda path: path.name.lower())
    except OSError:
        entries = []
    for entry in entries:
        if not entry.is_dir():
            continue
        cert = entry / "fullchain.pem"
        key = entry / "privkey.pem"
        if not cert.is_file() or not key.is_file():
            continue
        details = _certificate_details(cert, host)
        items.append({
            "lineage": entry.name,
            "path": str(entry),
            "certificate_path": str(cert),
            "private_key_path": str(key),
            "certificate": details,
            "usable": bool(details.get("valid")),
        })
    return {
        "live_dir": str(LETSENCRYPT_LIVE_DIR),
        "hostname": host,
        "items": items,
    }


def _activate_tls_material(
    cert_bytes: bytes,
    key_bytes: bytes,
    *,
    hostname: str,
    source_type: str,
    source_path: str | None = None,
    configure_renewal_hook: bool = False,
) -> dict:
    hostname = _valid_tls_hostname(hostname)
    tls_dir = _tls_directory()
    tls_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(tls_dir, 0o700)
    except OSError:
        pass

    stage = Path(tempfile.mkdtemp(prefix=".tls-stage-", dir=str(tls_dir)))
    stage_cert = stage / "server.crt"
    stage_key = stage / "server.key"
    try:
        stage_cert.write_bytes(cert_bytes)
        stage_key.write_bytes(key_bytes)
        os.chmod(stage_cert, 0o600)
        os.chmod(stage_key, 0o600)
        _validate_tls_pair(stage_cert, stage_key, hostname)

        with lock:
            config_paths = [
                tls_dir / "server.crt",
                tls_dir / "server.key",
                _tls_source_marker(),
                tls_dir / "host",
                _tls_metadata_path(),
            ]
            if INSTALL_MODE == "docker":
                config_paths.append(CONFIG_DIR / "docker.env")
            else:
                config_paths.extend(
                    [CONFIG_DIR / "public.conf", CONFIG_DIR / "backend.env", NGINX_SYSTEMD_CONF]
                )
            snapshot = _snapshot_paths(config_paths)

            try:
                _atomic_bytes(tls_dir / "server.crt", stage_cert.read_bytes(), 0o600)
                _atomic_bytes(tls_dir / "server.key", stage_key.read_bytes(), 0o600)
                marker_value = "managed-self-signed" if source_type == "managed-self-signed" else "custom"
                _atomic_text(_tls_source_marker(), marker_value + "\n", 0o600)
                _atomic_text(tls_dir / "host", hostname + "\n", 0o600)
                atomic_json(
                    _tls_metadata_path(),
                    {
                        "type": source_type,
                        "path": source_path,
                        "hostname": hostname,
                        "updated_at": utcnow(),
                    },
                )
                _set_public_hostname(hostname)
                _validate_proxy_tls()
                _reload_proxy_tls()
            except Exception:
                _restore_paths(snapshot)
                try:
                    _validate_proxy_tls()
                    _reload_proxy_tls()
                except Exception:
                    pass
                raise

            _write_certbot_hook(configure_renewal_hook)
            result = _tls_status_payload()
            result["changed"] = True
            return result
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def apply_custom_tls(payload: dict) -> dict:
    certificate = payload.get("certificate_pem")
    private_key = payload.get("private_key_pem")
    if not isinstance(certificate, str) or "BEGIN CERTIFICATE" not in certificate:
        raise ValueError("certificate_pem musi zawierać certyfikat PEM")
    if not isinstance(private_key, str) or "PRIVATE KEY" not in private_key:
        raise ValueError("private_key_pem musi zawierać klucz prywatny PEM")
    current_host, _ = _public_endpoint()
    hostname = _valid_tls_hostname(payload.get("hostname") or current_host)
    return _activate_tls_material(
        certificate.encode("utf-8"),
        private_key.encode("utf-8"),
        hostname=hostname,
        source_type="custom",
    )


def apply_letsencrypt_tls(payload: dict) -> dict:
    lineage = str(payload.get("lineage") or "").strip()
    source = _letsencrypt_lineage_path(lineage)
    cert = source / "fullchain.pem"
    key = source / "privkey.pem"
    if not cert.is_file() or not key.is_file():
        raise ValueError(f"Brak fullchain.pem/privkey.pem w {source}")

    current_host, _ = _public_endpoint()
    details = _certificate_details(cert, current_host)
    requested_hostname = payload.get("hostname")
    if requested_hostname:
        hostname = _valid_tls_hostname(requested_hostname)
    elif details.get("matches_hostname"):
        hostname = current_host
    else:
        dns_names = [
            value for value in (details.get("dns_names") or [])
            if value and not value.startswith("*.")
        ]
        if not dns_names:
            raise ValueError(
                "Certyfikat nie obejmuje bieżącego hosta; podaj hostname objęty certyfikatem"
            )
        hostname = _valid_tls_hostname(dns_names[0])

    return _activate_tls_material(
        cert.read_bytes(),
        key.read_bytes(),
        hostname=hostname,
        source_type="letsencrypt",
        source_path=str(source),
        configure_renewal_hook=True,
    )


def apply_self_signed_tls(payload: dict) -> dict:
    current_host, _ = _public_endpoint()
    hostname = _valid_tls_hostname(payload.get("hostname") or current_host)
    tls_dir = _tls_directory()
    tls_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".self-signed-", dir=str(tls_dir)))
    cert = stage / "server.crt"
    key = stage / "server.key"
    openssl = shutil.which("openssl") or "openssl"
    san = f"IP:{hostname}" if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", hostname) else f"DNS:{hostname}"
    try:
        _run_tls_command(
            [
                openssl,
                "req",
                "-x509",
                "-newkey",
                "rsa:3072",
                "-sha256",
                "-nodes",
                "-days",
                "365",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-subj",
                f"/CN={hostname}",
                "-addext",
                f"subjectAltName={san}",
            ],
            timeout=30,
        )
        return _activate_tls_material(
            cert.read_bytes(),
            key.read_bytes(),
            hostname=hostname,
            source_type="managed-self-signed",
        )
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def sync_letsencrypt_tls() -> dict:
    metadata = _read_json(_tls_metadata_path())
    if metadata.get("type") != "letsencrypt":
        raise ValueError("Aktywny TLS nie korzysta z Let's Encrypt")
    source_value = str(metadata.get("path") or "")
    source = Path(source_value)
    try:
        relative = source.relative_to(LETSENCRYPT_LIVE_DIR)
    except ValueError as exc:
        raise ValueError("Zapisane źródło Let's Encrypt jest poza katalogiem live") from exc
    if len(relative.parts) != 1:
        raise ValueError("Nieprawidłowe zapisane źródło Let's Encrypt")
    cert = source / "fullchain.pem"
    key = source / "privkey.pem"
    if not cert.is_file() or not key.is_file():
        raise ValueError("Źródło Let's Encrypt nie zawiera fullchain.pem/privkey.pem")
    hostname = _valid_tls_hostname(str(metadata.get("hostname") or _public_endpoint()[0]))
    return _activate_tls_material(
        cert.read_bytes(),
        key.read_bytes(),
        hostname=hostname,
        source_type="letsencrypt",
        source_path=str(source),
        configure_renewal_hook=True,
    )


def installer_args(ref: str) -> list[str]:
    settings = load_settings()
    if INSTALL_MODE == "docker":
        backend = parse_kv(CONFIG_DIR / "docker.env")
        host = backend.get("CP_PUBLIC_HOST")
        port = backend.get("CP_HTTPS_PORT")
        workers = backend.get("CP_WORKER_COUNT", "1")
        if not host or not port:
            raise RuntimeError("Missing installed Docker host/port configuration")
        args = [
            "/bin/bash", "{installer}", "--docker", "--non-interactive",
            "--host", host, "--port", port, "--workers", workers,
            "--ref", ref,
        ]
    else:
        public = parse_kv(CONFIG_DIR / "public.conf")
        backend = parse_kv(CONFIG_DIR / "backend.env")
        host = public.get("host")
        port = public.get("port")
        workers = backend.get("CP_WORKER_COUNT", "1")
        retention = backend.get("CP_BACKUP_RETENTION_DAYS", "14")
        backup = backend.get("CP_BACKUP_SCHEDULE_ENABLED", "false").lower() == "true"
        if not host or not port:
            raise RuntimeError("Missing installed host/port configuration")
        args = [
            "/bin/bash", "{installer}", "--non-interactive",
            "--host", host, "--port", port, "--workers", workers,
            "--backup-retention-days", retention,
            "--ref", ref,
            "--enable-backups" if backup else "--disable-backups",
        ]
    token_file = str(settings.get("github_token_file") or "")
    github_config = str(settings.get("github_config") or "")
    if token_file:
        args.extend(["--github-token-file", token_file])
    elif github_config:
        args.extend(["--github-config", github_config])
    return args


def download_installer(ref: str, target: Path) -> None:
    settings = load_settings()
    url = "https://api.github.com/repos/" + REPOSITORY + "/contents/install.sh?ref=" + quote(ref, safe="")
    content = _read_url(url, settings, "application/vnd.github.raw+json")
    if not content.startswith(b"#!/usr/bin/env bash"):
        raise RuntimeError("Downloaded installer is invalid")
    target.write_bytes(content)
    os.chmod(target, 0o700)


def _docker_compose_base() -> list[str]:
    env_file = CONFIG_DIR / "docker.env"
    compose_file = CURRENT_LINK / "docker-compose.yml"
    if not env_file.is_file() or not compose_file.is_file():
        raise RuntimeError("Brak aktywnej konfiguracji Docker wymaganej do backupu")

    docker = shutil.which("docker")
    if docker:
        probe = subprocess.run(
            [docker, "compose", "version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if probe.returncode == 0:
            return [
                docker, "compose", "-p", "cloudportal-backed",
                "--env-file", str(env_file), "-f", str(compose_file),
            ]

    docker_compose = shutil.which("docker-compose")
    if docker_compose:
        return [
            docker_compose, "-p", "cloudportal-backed",
            "--env-file", str(env_file), "-f", str(compose_file),
        ]
    raise RuntimeError("Docker Compose nie jest dostępny dla updatera")


def _worker_config_path() -> Path:
    if INSTALL_MODE == "docker":
        return CONFIG_DIR / "docker.env"
    if INSTALL_MODE == "systemd":
        return CONFIG_DIR / "backend.env"
    raise RuntimeError(f"Dynamiczna zmiana workerów nie jest obsługiwana dla trybu {INSTALL_MODE!r}")


def _configured_worker_count() -> tuple[Path, int]:
    path = _worker_config_path()
    if not path.is_file():
        raise RuntimeError(f"Brak konfiguracji workerów: {path}")
    raw = str(parse_kv(path).get("CP_WORKER_COUNT", "1")).strip()
    try:
        count = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Nieprawidłowy CP_WORKER_COUNT w {path}: {raw!r}") from exc
    if not 1 <= count <= 64:
        raise RuntimeError(f"CP_WORKER_COUNT poza zakresem 1-64 w {path}: {count}")
    return path, count


def _atomic_config_text(path: Path, value: str, source_stat=None) -> None:
    source_stat = source_stat or path.stat()
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(value, encoding="utf-8")
        os.chmod(tmp, source_stat.st_mode & 0o777)
        try:
            os.chown(tmp, source_stat.st_uid, source_stat.st_gid)
        except PermissionError:
            pass
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _set_worker_count(path: Path, count: int) -> tuple[str, object]:
    original = path.read_text(encoding="utf-8")
    source_stat = path.stat()
    lines = original.splitlines()
    key = "CP_WORKER_COUNT="
    replacement = key + str(count)
    replaced = False
    updated = []
    for line in lines:
        if line.startswith(key):
            if not replaced:
                updated.append(replacement)
                replaced = True
            continue
        updated.append(line)
    if not replaced:
        updated.append(replacement)
    _atomic_config_text(path, "\n".join(updated) + "\n", source_stat)
    return original, source_stat


def _run_worker_command(command: list[str], label: str, timeout: int = 300) -> str:
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{label}: przekroczono limit czasu") from exc
    if result.returncode == 0:
        return result.stdout or ""
    detail = "\n".join((result.stdout or "").splitlines()[-30:]).strip()
    raise RuntimeError(
        f"{label}: kod {result.returncode}" + (f"\n{detail}" if detail else "")
    )


def _refresh_api_runtime_after_worker_change() -> None:
    time.sleep(2)
    try:
        if INSTALL_MODE == "docker":
            _run_worker_command(
                _docker_compose_base() + ["up", "-d", "--no-deps", "--force-recreate", "api"],
                "odświeżenie API po zmianie workerów",
                timeout=300,
            )
        elif INSTALL_MODE == "systemd":
            systemctl = shutil.which("systemctl") or "systemctl"
            _run_worker_command(
                [systemctl, "restart", "cloudportal-api.service"],
                "restart API po zmianie workerów",
                timeout=120,
            )
    except Exception as exc:
        append_output("worker-capacity refresh warning: " + str(exc))


def _schedule_api_refresh_after_worker_change() -> None:
    threading.Thread(
        target=_refresh_api_runtime_after_worker_change,
        daemon=True,
        name="cloudportal-worker-capacity-refresh",
    ).start()


def ensure_worker_capacity(minimum: int) -> dict:
    try:
        minimum = int(minimum)
    except (TypeError, ValueError) as exc:
        raise ValueError("minimum must be an integer from 1 to 64") from exc
    if not 1 <= minimum <= 64:
        raise ValueError("minimum must be an integer from 1 to 64")

    with lock:
        if update_operation_active():
            raise RuntimeError("Nie można zmieniać puli workerów podczas aktywnej aktualizacji")

        path, current = _configured_worker_count()
        target = max(current, minimum)
        config_changed = target != current
        original = path.read_text(encoding="utf-8")
        source_stat = path.stat()
        if config_changed:
            _set_worker_count(path, target)

        systemd_state = {}
        try:
            if INSTALL_MODE == "docker":
                _run_worker_command(
                    _docker_compose_base()
                    + ["up", "-d", "--no-deps", "--no-recreate", "--scale", f"worker={target}", "worker"],
                    "rekonsyliacja workerów Docker",
                    timeout=300,
                )
            elif INSTALL_MODE == "systemd":
                systemctl = shutil.which("systemctl") or "systemctl"
                units_to_ensure = []
                for index in range(1, target + 1):
                    unit = f"cloudportal-worker@{index}.service"
                    active_before = _service_active(unit)
                    enabled_before = subprocess.run(
                        [systemctl, "is-enabled", "--quiet", unit],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    ).returncode == 0
                    systemd_state[unit] = (active_before, enabled_before)
                    if not (active_before and enabled_before):
                        units_to_ensure.append(unit)
                if units_to_ensure:
                    _run_worker_command(
                        [systemctl, "enable", "--now", *units_to_ensure],
                        "rekonsyliacja workerów systemd",
                        timeout=300,
                    )
            else:
                raise RuntimeError(
                    f"Dynamiczna zmiana workerów nie jest obsługiwana dla trybu {INSTALL_MODE!r}"
                )
        except Exception:
            if config_changed:
                _atomic_config_text(path, original, source_stat)
            if INSTALL_MODE == "docker":
                try:
                    _run_worker_command(
                        _docker_compose_base()
                        + ["up", "-d", "--no-deps", "--no-recreate", "--scale", f"worker={current}", "worker"],
                        "rollback skali workerów Docker",
                        timeout=300,
                    )
                except Exception as rollback_exc:
                    append_output("worker-capacity rollback warning: " + str(rollback_exc))
            elif INSTALL_MODE == "systemd":
                systemctl = shutil.which("systemctl") or "systemctl"
                for unit, (active_before, enabled_before) in reversed(list(systemd_state.items())):
                    try:
                        if not active_before:
                            subprocess.run(
                                [systemctl, "stop", unit],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                check=False,
                            )
                        if not enabled_before:
                            subprocess.run(
                                [systemctl, "disable", unit],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                check=False,
                            )
                    except Exception:
                        pass
            raise

        if config_changed:
            _schedule_api_refresh_after_worker_change()
        return {
            "changed": config_changed,
            "reconciled": True,
            "previous_worker_count": current,
            "worker_count": target,
            "install_mode": INSTALL_MODE,
        }


def _docker_pre_update_backup() -> Path:
    root = DATA_DIR / "backups"
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    backup_dir = root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = 0
    while backup_dir.exists():
        suffix += 1
        backup_dir = root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{suffix}")
    backup_dir.mkdir(mode=0o700)
    dump = backup_dir / "database.dump"

    command = _docker_compose_base() + [
        "exec", "-T", "postgres",
        "pg_dump", "-U", "cloudportal", "-d", "cloudportal", "-Fc",
    ]
    with dump.open("xb") as stream:
        result = subprocess.run(
            command,
            stdout=stream,
            stderr=subprocess.PIPE,
            timeout=1800,
            check=False,
        )
    if result.returncode != 0:
        dump.unlink(missing_ok=True)
        shutil.rmtree(backup_dir, ignore_errors=True)
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError("Pre-update Docker PostgreSQL backup failed" + (": " + detail if detail else ""))

    metadata = {
        "created_at": utcnow(),
        "install_mode": "docker",
        "current_release": str(CURRENT_LINK.resolve()),
        "current_commit": str(release_info().get("commit_sha") or ""),
    }
    atomic_json(backup_dir / "metadata.json", metadata)
    append_output("Docker backup: " + str(backup_dir))
    return backup_dir


def pre_update_backup() -> Path:
    if INSTALL_MODE == "docker":
        return _docker_pre_update_backup()
    backup = Path("/usr/local/sbin/cloudportal-backup")
    if not backup.exists():
        raise RuntimeError("Pre-update backup command is not installed; refusing unsafe update")
    result = subprocess.run(
        [str(backup)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=1800,
        check=False,
    )
    lines = result.stdout.splitlines()
    for line in lines:
        append_output(line)
    if result.returncode != 0:
        raise RuntimeError("Pre-update PostgreSQL backup failed")
    for line in reversed(lines):
        candidate = Path(line.strip())
        if candidate.is_absolute() and (candidate / "database.dump").is_file() and (candidate / "metadata.json").is_file():
            return candidate
    raise RuntimeError("Pre-update backup finished without a verifiable backup directory")


def automatic_retry_deferred(target_sha: str) -> bool:
    state = load_state()
    if str(state.get("failed_target_sha") or "") != target_sha:
        return False
    raw_deadline = str(state.get("retry_not_before") or "")
    if not raw_deadline:
        return False
    try:
        deadline = datetime.fromisoformat(raw_deadline)
    except ValueError:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= deadline:
        return False
    event(
        "cooldown",
        8,
        "Automatyczne ponowienie tego samego commita jest czasowo wstrzymane po poprzednim błędzie.",
        status="deferred",
        finished_at=utcnow(),
        update_available=True,
        automatic=True,
    )
    return True


def run_update(ref: str | None = None, automatic: bool = False) -> None:
    global update_thread
    settings = load_settings()
    target_sha = ""
    try:
        ref = validate_ref(ref or settings.get("ref", "main"))
        previous = load_state()
        started_at = utcnow()
        durable_fields = {
            key: previous.get(key)
            for key in (
                "last_success_at",
                "last_failure_at",
                "last_result",
                "consecutive_failures",
                "failed_target_sha",
                "retry_not_before",
            )
        }
        with lock:
            # In-memory thread ownership is the source of truth for concurrency.
            # A persisted "running" state can survive an updater restart and must
            # never block a new, legitimate update attempt. Keep retry metadata
            # across attempts so automation can quarantine a repeatedly failing SHA.
            atomic_json(STATE_FILE, {
                **default_state(),
                **durable_fields,
                "status": "running",
                "phase": "preflight",
                "progress": 1,
                "message": "Rozpoczynanie bezpiecznej aktualizacji.",
                "started_at": started_at,
                "last_attempt_at": started_at,
                "ref": ref,
                "automatic": automatic,
                "events": [],
                "output": [],
            })

        checked = check_remote(ref, update_context=True)
        if not checked["update_available"]:
            save_state(last_result="up_to_date")
            return

        target_sha = checked["target_sha"]
        save_state(status="running", finished_at=None, automatic=automatic)

        if automatic and automatic_retry_deferred(target_sha):
            return

        if automatic:
            event("current_health", 8, "Sprawdzanie stanu bieżącej instalacji przed auto-update.")
            ensure_current_installation_healthy()
            event("current_health", 9, "Bieżąca instalacja jest zdrowa; można ocenić kandydata.")

        wait_for_required_ci(target_sha, settings)
        validate_candidate(target_sha, settings)

        event("backup", 14, "Tworzenie backupu PostgreSQL przed izolowanym testem migracji.")
        backup_dir = pre_update_backup()
        validate_candidate_runtime(target_sha, backup_dir, settings)

        event("download", 19, "Pobieranie instalatora dokładnie dla zweryfikowanego commita.")
        with tempfile.TemporaryDirectory(prefix="cloudportal-update-") as temp:
            installer = Path(temp) / "install.sh"
            # Pin both installer and source download to the immutable SHA that
            # passed CI. The tracked channel (for example main) is preserved
            # separately so a moving branch cannot race this update.
            download_installer(target_sha, installer)
            args = [part.format(installer=str(installer)) for part in installer_args(target_sha)]
            env = os.environ.copy()
            env["CLOUDPORTAL_UPDATE_IN_PROGRESS"] = "1"
            env["CLOUDPORTAL_UPDATE_CHANNEL_REF"] = ref
            env["CLOUDPORTAL_RELEASE_SHA"] = target_sha
            event("install", 20, "Uruchamianie instalatora zweryfikowanego commita.")
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
            assert process.stdout is not None
            for raw_line in process.stdout:
                line = raw_line.rstrip()
                marker = PROGRESS_RE.match(line)
                if marker:
                    progress, phase, message = marker.groups()
                    mapped_progress = min(95, 20 + (int(progress) * 75 // 100))
                    event(phase, mapped_progress, message, status="running")
                else:
                    append_output(line)
            code = process.wait()
            if code != 0:
                raise RuntimeError("Installer exited with code " + str(code))

        event("postcheck", 97, "Weryfikowanie aktywnego commita, usług i healthchecku po instalacji.")
        verify_installed_release(target_sha)
        finished_at = utcnow()
        save_state(
            status="success",
            update_available=False,
            current_version=checked["target_version"],
            target_version=checked["target_version"],
            current_commit_at=checked.get("target_commit_at"),
            target_commit_at=checked.get("target_commit_at"),
            commit_relation="identical",
            ahead_by=0,
            behind_by=0,
            version_strategy="git_commit",
            finished_at=finished_at,
            last_success_at=finished_at,
            last_result="success",
            consecutive_failures=0,
            failed_target_sha=None,
            retry_not_before=None,
            ci_status="success" if bool(settings.get("require_ci", True)) else "disabled",
            candidate_validation="success" if bool(settings.get("candidate_validation", True)) else "disabled",
            runtime_preflight="success" if bool(settings.get("runtime_preflight", True)) else "disabled",
        )
        event("complete", 100, "Aktualizacja zakończona pomyślnie po wszystkich bramkach bezpieczeństwa.", status="success")
    except Exception as exc:
        failed_at = utcnow()
        failure_changes = {
            "status": "failed",
            "finished_at": failed_at,
            "last_result": "failed",
        }
        if automatic:
            state = load_state()
            failures = max(0, int(state.get("consecutive_failures") or 0)) + 1
            failure_changes.update({
                "last_failure_at": failed_at,
                "consecutive_failures": failures,
            })
            if target_sha:
                failure_changes.update({
                    "failed_target_sha": target_sha,
                    "retry_not_before": (
                        datetime.now(timezone.utc) + timedelta(minutes=AUTO_FAILURE_COOLDOWN_MINUTES)
                    ).isoformat(),
                })
        save_state(**failure_changes)
        event(
            "failed",
            load_state().get("progress", 0),
            "Aktualizacja została zablokowana lub nie powiodła się: " + str(exc),
            status="failed",
        )
        append_output("ERROR: " + str(exc))
    finally:
        with lock:
            update_thread = None

def update_operation_active() -> bool:
    global update_thread
    with lock:
        if update_thread is not None and not update_thread.is_alive():
            update_thread = None
        return bool(update_thread)


def start_update(ref: str | None = None, automatic: bool = False) -> bool:
    global update_thread
    with lock:
        if update_operation_active():
            return False
        update_thread = threading.Thread(target=run_update, args=(ref, automatic), daemon=True, name="cloudportal-update")
        update_thread.start()
        return True


def runtime_state() -> dict:
    state = load_state()
    active = update_operation_active()

    # Self-heal an orphaned persisted state. This can happen when the updater
    # service is restarted or killed while state.json still says "running".
    # The live thread, not the file, is authoritative.
    if not active and state.get("status") == "running":
        if state.get("phase") == "complete" and int(state.get("progress") or 0) >= 100:
            finished = state.get("finished_at") or utcnow()
            event(
                "complete",
                100,
                "Aktualizacja zakończona pomyślnie.",
                status="success",
                update_available=False,
                finished_at=finished,
            )
        else:
            event(
                "interrupted",
                state.get("progress", 0),
                "Poprzedni proces aktualizacji nie jest już aktywny. Stan został automatycznie odblokowany.",
                status="failed",
                finished_at=utcnow(),
            )
        state = load_state()

    state["operation_active"] = active
    if active and state.get("status") not in {"running", "failed", "success"}:
        state["status"] = "running"
        if state.get("phase") in {"checking", "available", "up_to_date", "local_ahead", "idle"}:
            state["phase"] = "preflight"
            state["message"] = "Aktualizacja jest już uruchomiona."
            state["finished_at"] = None
    return state


def status_payload(*, compact: bool = False) -> dict:
    state = runtime_state()
    if compact:
        return {key: value for key, value in state.items() if key not in {"events", "output"}}
    return state


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def scheduler() -> None:
    while True:
        time.sleep(60)
        settings = load_settings()
        if not settings.get("enabled"):
            continue
        if update_operation_active():
            continue
        state = load_state()
        last = parse_time(state.get("last_check_at"))
        interval = max(1, min(168, int(settings.get("interval_hours", 24)))) * 3600
        if last is not None and (datetime.now(timezone.utc) - last).total_seconds() < interval:
            continue
        try:
            checked = check_remote(settings.get("ref", "main"))
            if checked["update_available"]:
                start_update(settings.get("ref", "main"), automatic=True)
        except Exception:
            continue


def read_token(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def control_authorized(headers) -> bool:
    expected = read_token(CONTROL_TOKEN)
    supplied = headers.get("X-Updater-Token", "")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def status_authorized(handler: BaseHTTPRequestHandler) -> bool:
    if control_authorized(handler.headers):
        return True
    expected = read_token(STATUS_TOKEN)
    supplied = handler.headers.get("X-Update-Status-Token", "")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


class Handler(BaseHTTPRequestHandler):
    server_version = "CloudportalUpdater/1"

    def log_message(self, fmt, *args):
        return

    def send_json(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body(self) -> dict:
        size = int(self.headers.get("Content-Length", "0") or "0")
        if size < 0 or size > MAX_BODY:
            raise ValueError("Request body is too large")
        if not size:
            return {}
        value = json.loads(self.rfile.read(size).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self.send_json(HTTPStatus.OK, {"ok": True})
            return
        if path == "/status":
            if not status_authorized(self):
                self.send_json(HTTPStatus.UNAUTHORIZED, {"detail": "Unauthorized"})
                return
            compact = parse_qs(parsed.query).get("compact", []) == ["1"]
            self.send_json(HTTPStatus.OK, status_payload(compact=compact))
            return
        if path == "/settings":
            if not control_authorized(self.headers):
                self.send_json(HTTPStatus.UNAUTHORIZED, {"detail": "Unauthorized"})
                return
            self.send_json(HTTPStatus.OK, public_settings())
            return
        if path == "/tls/status":
            if not control_authorized(self.headers):
                self.send_json(HTTPStatus.UNAUTHORIZED, {"detail": "Unauthorized"})
                return
            self.send_json(HTTPStatus.OK, _tls_status_payload())
            return
        if path == "/tls/letsencrypt":
            if not control_authorized(self.headers):
                self.send_json(HTTPStatus.UNAUTHORIZED, {"detail": "Unauthorized"})
                return
            self.send_json(HTTPStatus.OK, letsencrypt_candidates())
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"detail": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if not control_authorized(self.headers):
            self.send_json(HTTPStatus.UNAUTHORIZED, {"detail": "Unauthorized"})
            return
        try:
            payload = self.body()
            if path == "/check":
                if update_operation_active():
                    state = runtime_state()
                    state["already_running"] = True
                    self.send_json(HTTPStatus.OK, state)
                    return
                self.send_json(HTTPStatus.OK, check_remote(payload.get("ref")))
                return
            if path == "/run":
                ref = payload.get("ref")
                if ref is not None:
                    validate_ref(ref)
                automatic = payload.get("automatic", False)
                if not isinstance(automatic, bool):
                    raise ValueError("automatic must be boolean")
                if not start_update(ref, automatic=automatic):
                    self.send_json(HTTPStatus.ACCEPTED, {
                        "accepted": False,
                        "already_running": True,
                        "status": runtime_state(),
                    })
                    return
                self.send_json(HTTPStatus.ACCEPTED, {"accepted": True, "already_running": False})
                return
            if path == "/workers/ensure":
                if update_operation_active():
                    self.send_json(
                        HTTPStatus.CONFLICT,
                        {"detail": "Update already in progress; worker capacity cannot be changed"},
                    )
                    return
                self.send_json(
                    HTTPStatus.OK,
                    ensure_worker_capacity(payload.get("minimum")),
                )
                return
            if path == "/settings":
                self.send_json(HTTPStatus.OK, update_settings(payload))
                return
            if path == "/tls/custom":
                self.send_json(HTTPStatus.OK, apply_custom_tls(payload))
                return
            if path == "/tls/letsencrypt":
                self.send_json(HTTPStatus.OK, apply_letsencrypt_tls(payload))
                return
            if path == "/tls/self-signed":
                self.send_json(HTTPStatus.OK, apply_self_signed_tls(payload))
                return
            if path == "/tls/sync":
                self.send_json(HTTPStatus.OK, sync_letsencrypt_tls())
                return
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"detail": str(exc)})
            return
        except Exception as exc:
            self.send_json(HTTPStatus.BAD_GATEWAY, {"detail": str(exc)})
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"detail": "Not found"})


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        atomic_json(STATE_FILE, default_state())
    state = load_state()
    release = release_info()
    if not state.get("current_version") and release.get("commit_sha"):
        save_state(current_version=str(release["commit_sha"])[:12], ref=release.get("ref") or state.get("ref"))
        state = load_state()
    if state.get("status") in {"running", "checking"}:
        if state.get("phase") == "complete" and int(state.get("progress") or 0) >= 100:
            save_state(
                status="success",
                progress=100,
                update_available=False,
                finished_at=state.get("finished_at") or utcnow(),
            )
            event(
                "complete",
                100,
                "Aktualizacja zakończona pomyślnie.",
                status="success",
                update_available=False,
                finished_at=state.get("finished_at") or utcnow(),
            )
        else:
            event(
                "interrupted",
                state.get("progress", 0),
                "Poprzedni proces aktualizacji został przerwany przed zakończeniem.",
                status="failed",
                finished_at=utcnow(),
            )
    threading.Thread(target=scheduler, daemon=True, name="cloudportal-update-scheduler").start()
    if SOCKET_PATH:
        socket_path = Path(SOCKET_PATH)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(socket_path.parent, 0o755)
        socket_path.unlink(missing_ok=True)
        server = ThreadingUnixHTTPServer(str(socket_path), Handler)
        os.chmod(socket_path, 0o666)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            socket_path.unlink(missing_ok=True)
    else:
        server = ThreadingHTTPServer((BIND_HOST, PORT), Handler)
        server.serve_forever()


if __name__ == "__main__":
    main()
