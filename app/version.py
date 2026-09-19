import json
import os
import re
import subprocess
from functools import lru_cache

from app.config import settings


COMMIT_RE = re.compile(r'^[0-9a-fA-F]{7,64}$')


def _normalize_commit(value):
    value = str(value or '').strip()
    return value.lower() if COMMIT_RE.fullmatch(value) else ''


@lru_cache(maxsize=1)
def build_commit():
    configured = _normalize_commit(os.environ.get('CP_BUILD_COMMIT'))
    if configured:
        return configured

    source = settings().source_dir
    marker = source / '.cloudportal-release.json'
    try:
        data = json.loads(marker.read_text(encoding='utf-8'))
        marked = _normalize_commit(data.get('commit_sha'))
        if marked:
            return marked
    except (FileNotFoundError, OSError, json.JSONDecodeError, AttributeError):
        pass

    try:
        result = subprocess.run(
            ['git', '-C', str(source), 'rev-parse', 'HEAD'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
            check=False,
        )
        discovered = _normalize_commit(result.stdout)
        if result.returncode == 0 and discovered:
            return discovered
    except (OSError, subprocess.SubprocessError):
        pass

    return 'unknown'


def build_version():
    commit = build_commit()
    return commit[:12] if commit != 'unknown' else 'unknown'
