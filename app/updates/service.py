from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


UPDATER_URL = os.environ.get('CP_UPDATER_URL', 'http://127.0.0.1:8766').rstrip('/')
CONTROL_TOKEN = Path(os.environ.get('CP_UPDATER_TOKEN_FILE', '/etc/cloudportal-backed/updater.token'))
STATUS_TOKEN = Path(os.environ.get('CP_UPDATER_STATUS_TOKEN_FILE', '/etc/cloudportal-backed/updater-status.token'))


class UpdaterError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _control_token() -> str:
    try:
        token = CONTROL_TOKEN.read_text(encoding='utf-8').strip()
    except OSError as exc:
        raise UpdaterError(503, 'Updater control token is unavailable') from exc
    if not token:
        raise UpdaterError(503, 'Updater control token is empty')
    return token


def status_access_token() -> str:
    try:
        token = STATUS_TOKEN.read_text(encoding='utf-8').strip()
    except OSError as exc:
        raise UpdaterError(503, 'Updater status token is unavailable') from exc
    if not token:
        raise UpdaterError(503, 'Updater status token is empty')
    return token


def updater_request(path: str, method: str = 'GET', payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode('utf-8')
    headers = {
        'Accept': 'application/json',
        'X-Updater-Token': _control_token(),
    }
    if body is not None:
        headers['Content-Type'] = 'application/json'
    request = Request(UPDATER_URL + path, data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=35) as response:
            raw = response.read().decode('utf-8')
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        try:
            data = json.loads(exc.read().decode('utf-8'))
            detail = data.get('detail') or str(exc)
        except Exception:
            detail = str(exc)
        raise UpdaterError(exc.code, detail) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise UpdaterError(503, 'Updater service is unavailable') from exc
