#!/usr/bin/env python3
"""Privileged Cloudportal update sidecar.

The sidecar is intentionally independent from the application virtualenv so it
keeps reporting progress while the main API, dispatcher and workers restart.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

CONFIG_DIR = Path(os.environ.get("CP_UPDATER_CONFIG_DIR", "/etc/cloudportal-backed"))
DATA_DIR = Path(os.environ.get("CP_UPDATER_DATA_DIR", "/var/lib/cloudportal-backed"))
APP_ROOT = Path(os.environ.get("CP_UPDATER_APP_ROOT", "/opt/cloudportal-backed"))
PORT = int(os.environ.get("CP_UPDATER_PORT", "8766"))
REPOSITORY = os.environ.get("CP_UPDATER_REPOSITORY", "chmajster/Cloudportal-backed")

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
MAX_BODY = 16 * 1024
MAX_OUTPUT = 160
MAX_EVENTS = 120

lock = threading.RLock()
update_thread: threading.Thread | None = None


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
        "current_version": None,
        "target_version": None,
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
            "--config", github_config, "-H", "Accept: " + accept, url,
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or "GitHub download failed")
        return result.stdout
    request = Request(url, headers={**_github_headers(settings), "Accept": accept})
    with urlopen(request, timeout=60) as response:
        return response.read()


def _http_json(url: str, settings: dict) -> dict:
    return json.loads(_read_url(url, settings, "application/vnd.github+json").decode("utf-8"))


def remote_sha(ref: str, settings: dict) -> str:
    ref = validate_ref(ref)
    url = "https://api.github.com/repos/" + REPOSITORY + "/commits/" + quote(ref, safe="")
    data = _http_json(url, settings)
    sha = str(data.get("sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("GitHub returned an invalid commit identifier")
    return sha


def check_remote(ref: str | None = None) -> dict:
    settings = load_settings()
    ref = validate_ref(ref or settings.get("ref", "main"))
    event("checking", 3, "Sprawdzanie dostępnej wersji.", status="checking", ref=ref, finished_at=None)
    try:
        target = remote_sha(ref, settings)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        save_state(status="failed", finished_at=utcnow())
        event("check_failed", 0, "Nie udało się sprawdzić aktualizacji: " + str(exc))
        raise
    current = str(release_info().get("commit_sha") or "")
    available = current != target
    now = utcnow()
    save_state(
        status="update_available" if available else "up_to_date",
        current_version=current[:12] or "nieznana",
        target_version=target[:12],
        update_available=available,
        last_check_at=now,
        finished_at=now,
        ref=ref,
    )
    event(
        "available" if available else "up_to_date",
        8 if available else 100,
        "Dostępna jest nowa wersja." if available else "Zainstalowana wersja jest aktualna.",
    )
    return {
        "ref": ref,
        "current_version": current[:12] or "nieznana",
        "target_version": target[:12],
        "target_sha": target,
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


def installer_args(ref: str) -> list[str]:
    public = parse_kv(CONFIG_DIR / "public.conf")
    backend = parse_kv(CONFIG_DIR / "backend.env")
    settings = load_settings()
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


def pre_update_backup() -> None:
    backup = Path("/usr/local/sbin/cloudportal-backup")
    if not backup.exists():
        append_output("Pre-update backup command is not installed; continuing without automatic backup.")
        return
    result = subprocess.run(
        [str(backup)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=1800,
        check=False,
    )
    for line in result.stdout.splitlines():
        append_output(line)
    if result.returncode != 0:
        raise RuntimeError("Pre-update PostgreSQL backup failed")


def run_update(ref: str | None = None, automatic: bool = False) -> None:
    global update_thread
    try:
        settings = load_settings()
        ref = validate_ref(ref or settings.get("ref", "main"))
        with lock:
            current = load_state()
            if current.get("status") == "running":
                return
            atomic_json(STATE_FILE, {
                **default_state(),
                "status": "running",
                "phase": "preflight",
                "progress": 1,
                "message": "Rozpoczynanie aktualizacji.",
                "started_at": utcnow(),
                "ref": ref,
                "automatic": automatic,
                "events": [],
                "output": [],
            })
        checked = check_remote(ref)
        if not checked["update_available"]:
            return
        save_state(status="running", started_at=utcnow(), finished_at=None, automatic=automatic)
        event("backup", 10, "Tworzenie backupu PostgreSQL przed aktualizacją.")
        pre_update_backup()
        event("download", 15, "Pobieranie aktualnego instalatora.")
        with tempfile.TemporaryDirectory(prefix="cloudportal-update-") as temp:
            installer = Path(temp) / "install.sh"
            download_installer(ref, installer)
            args = [part.format(installer=str(installer)) for part in installer_args(ref)]
            env = os.environ.copy()
            env["CLOUDPORTAL_UPDATE_IN_PROGRESS"] = "1"
            env["CLOUDPORTAL_RELEASE_SHA"] = checked["target_sha"]
            event("install", 20, "Uruchamianie instalatora nowej wersji.")
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
                    event(phase, int(progress), message, status="running")
                else:
                    append_output(line)
            code = process.wait()
            if code != 0:
                raise RuntimeError("Installer exited with code " + str(code))
        save_state(
            status="success",
            update_available=False,
            current_version=checked["target_version"],
            target_version=checked["target_version"],
            finished_at=utcnow(),
        )
        event("complete", 100, "Aktualizacja zakończona pomyślnie.", status="success")
    except Exception as exc:
        save_state(status="failed", finished_at=utcnow())
        event("failed", load_state().get("progress", 0), "Aktualizacja nie powiodła się: " + str(exc), status="failed")
        append_output("ERROR: " + str(exc))
    finally:
        with lock:
            update_thread = None


def start_update(ref: str | None = None, automatic: bool = False) -> bool:
    global update_thread
    with lock:
        if update_thread and update_thread.is_alive():
            return False
        update_thread = threading.Thread(target=run_update, args=(ref, automatic), daemon=True, name="cloudportal-update")
        update_thread.start()
        return True


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
        with lock:
            if update_thread and update_thread.is_alive():
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
    query = parse_qs(urlparse(handler.path).query)
    supplied = (query.get("key") or [""])[0]
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
        path = urlparse(self.path).path
        if path == "/health":
            self.send_json(HTTPStatus.OK, {"ok": True})
            return
        if path == "/status":
            if not status_authorized(self):
                self.send_json(HTTPStatus.UNAUTHORIZED, {"detail": "Unauthorized"})
                return
            self.send_json(HTTPStatus.OK, load_state())
            return
        if path == "/settings":
            if not control_authorized(self.headers):
                self.send_json(HTTPStatus.UNAUTHORIZED, {"detail": "Unauthorized"})
                return
            self.send_json(HTTPStatus.OK, public_settings())
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
                self.send_json(HTTPStatus.OK, check_remote(payload.get("ref")))
                return
            if path == "/run":
                ref = payload.get("ref")
                if ref is not None:
                    validate_ref(ref)
                if not start_update(ref, automatic=False):
                    self.send_json(HTTPStatus.CONFLICT, {"detail": "Update already in progress"})
                    return
                self.send_json(HTTPStatus.ACCEPTED, {"accepted": True})
                return
            if path == "/settings":
                self.send_json(HTTPStatus.OK, update_settings(payload))
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
    threading.Thread(target=scheduler, daemon=True, name="cloudportal-update-scheduler").start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
