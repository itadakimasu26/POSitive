import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Restore an OXPOS JSON backup into an isolated temporary database and validate it."

    def add_arguments(self, parser):
        parser.add_argument("--input", required=True, help="Backup ending in .json or .json.gz.")

    def handle(self, *args, **options):
        source = Path(options["input"]).expanduser().resolve()
        if not source.is_file():
            raise CommandError(f"Backup not found: {source}")

        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        checksum_path = source.with_name(f"{source.name}.sha256")
        if checksum_path.exists():
            expected = checksum_path.read_text(encoding="ascii").split()[0].lower()
            if digest != expected:
                raise CommandError("Backup checksum verification failed.")

        opener = gzip.open if source.name.endswith(".gz") else open
        try:
            with opener(source, "rt", encoding="utf-8") as backup_file:
                records = json.load(backup_file)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CommandError(f"The backup is not valid JSON: {exc}") from exc
        if not isinstance(records, list):
            raise CommandError("The backup must contain a JSON fixture list.")

        with tempfile.TemporaryDirectory(prefix="oxpos-restore-check-") as temp_directory:
            database_path = Path(temp_directory) / "restore-check.sqlite3"
            environment = os.environ.copy()
            environment.pop("DATABASE_URL", None)
            environment["OXPOS_SQLITE_PATH"] = str(database_path)
            manage_py = Path(settings.BASE_DIR) / "manage.py"
            commands = [
                [sys.executable, str(manage_py), "migrate", "--noinput", "--verbosity", "0"],
                [sys.executable, str(manage_py), "loaddata", str(source), "--verbosity", "0"],
                [sys.executable, str(manage_py), "check", "--database", "default"],
            ]
            for command in commands:
                completed = subprocess.run(
                    command,
                    cwd=settings.BASE_DIR,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if completed.returncode:
                    detail = completed.stderr.strip() or completed.stdout.strip()
                    raise CommandError(f"Isolated restore verification failed: {detail}")

        checksum_note = "checksum matched" if checksum_path.exists() else "no checksum file supplied"
        self.stdout.write(
            self.style.SUCCESS(
                f"Restore verified in an isolated temporary database: {len(records):,} records; {checksum_note}; "
                f"SHA-256 {digest}."
            )
        )
