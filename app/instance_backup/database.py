from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from app.config import settings
from app.database import engine, session

COUNT_TABLES = {
    "users": "users",
    "credentials": "credentials",
    "providers": "providers",
    "projects": "projects",
    "tenants": "tenants",
    "blueprints": "blueprints",
    "deployments": "deployments",
    "terraform_state": "terraform_states",
}


def current_alembic_revision() -> str:
    try:
        with engine().connect() as connection:
            return str(connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one())
    except Exception:
        return "unknown"


def database_counts() -> dict[str, int]:
    available = set(inspect(engine()).get_table_names())
    result: dict[str, int] = {}
    with session() as db:
        for label, table in COUNT_TABLES.items():
            if table not in available:
                result[label] = 0
                continue
            result[label] = int(db.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one())
    return result


def _postgres_command(binary: str, *, dump: Path | None = None) -> tuple[list[str], dict[str, str]]:
    url = make_url(settings().database_url)
    if not url.drivername.startswith("postgresql"):
        raise RuntimeError("Instance backup and restore require PostgreSQL")
    executable = shutil.which(binary)
    if not executable:
        raise RuntimeError(f"{binary} is not installed in the Cloudportal runtime")

    host = url.query.get("host") or url.host or ""
    command = [executable]
    if binary == "pg_dump":
        command += [
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--exclude-table-data=instance_backups",
        ]
    else:
        command += [
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            "--single-transaction",
        ]
    command += ["--username", url.username or "cloudportal", "--dbname", url.database or "cloudportal"]
    if host:
        command += ["--host", str(host)]
    command += ["--port", str(url.port or 5432)]
    if dump is not None:
        command.append(str(dump))

    process_env = {
        "PATH": os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
        "LANG": "C.UTF-8",
    }
    if url.password:
        process_env["PGPASSWORD"] = url.password
    return command, process_env


def dump_database(destination: Path) -> None:
    command, process_env = _postgres_command("pg_dump")
    with destination.open("xb") as stream:
        os.chmod(destination, 0o600)
        try:
            subprocess.run(command, env=process_env, stdout=stream, stderr=subprocess.PIPE, check=True, timeout=3600)
        except subprocess.CalledProcessError as exc:
            destination.unlink(missing_ok=True)
            raise RuntimeError("PostgreSQL backup failed") from exc
        except subprocess.TimeoutExpired as exc:
            destination.unlink(missing_ok=True)
            raise RuntimeError("PostgreSQL backup timed out") from exc


def restore_database(dump: Path) -> None:
    engine().dispose()
    command, process_env = _postgres_command("pg_restore", dump=dump)
    try:
        subprocess.run(command, env=process_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=3600)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("PostgreSQL restore failed") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("PostgreSQL restore timed out") from exc
    finally:
        engine().dispose()
