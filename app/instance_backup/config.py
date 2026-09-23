from __future__ import annotations

FORMAT_NAME = "cloudportal-instance-backup"
FORMAT_VERSION = 1

STAGES = (
    "queued",
    "preparing",
    "database_dump",
    "configuration",
    "secret_material",
    "manifest",
    "checksums",
    "archive",
    "ready",
)

STAGE_PROGRESS = {
    "queued": 0,
    "preparing": 8,
    "database_dump": 28,
    "configuration": 42,
    "secret_material": 56,
    "manifest": 68,
    "checksums": 78,
    "archive": 92,
    "ready": 100,
    "failed": 100,
    "validated": 100,
    "expired": 100,
    "deleted": 100,
}

STAGE_LABELS = {
    "queued": "Oczekiwanie",
    "preparing": "Przygotowywanie",
    "database_dump": "Backup bazy PostgreSQL",
    "configuration": "Zbieranie konfiguracji",
    "secret_material": "Zabezpieczanie danych kryptograficznych",
    "manifest": "Generowanie manifestu",
    "checksums": "Weryfikacja integralności",
    "archive": "Tworzenie pliku backupu",
    "ready": "Gotowy do pobrania",
    "validated": "Backup poprawny",
    "failed": "Błąd",
    "expired": "Wygasł",
    "deleted": "Usunięty",
}

DOWNLOADABLE_STATUSES = {"ready", "validated"}
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_CHECKSUM_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 32
