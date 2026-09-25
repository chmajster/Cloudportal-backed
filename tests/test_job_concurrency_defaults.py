from pathlib import Path

from app.config import Settings
from app.jobs.settings import DEFAULT_MAX_PARALLEL_JOBS


def test_runtime_defaults_to_ten_workers_and_ten_parallel_jobs(monkeypatch):
    monkeypatch.delenv('CP_WORKER_COUNT', raising=False)
    assert Settings(_env_file=None).worker_count == 10
    assert DEFAULT_MAX_PARALLEL_JOBS == 10


def test_docker_compose_defaults_worker_count_to_ten():
    compose = Path('docker-compose.yml').read_text()
    assert 'CP_WORKER_COUNT: ${CP_WORKER_COUNT:-10}' in compose
