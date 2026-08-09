import hashlib
from pathlib import Path

from django.core.management import BaseCommand, CommandError, call_command


class Command(BaseCommand):
    help = "Export POSitive business data and users to a verifiable JSON backup."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True, help="Destination ending in .json or .json.gz.")
        parser.add_argument("--replace", action="store_true", help="Replace an existing backup file.")

    def handle(self, *args, **options):
        destination = Path(options["output"]).expanduser().resolve()
        if not (destination.name.endswith(".json") or destination.name.endswith(".json.gz")):
            raise CommandError("The backup destination must end in .json or .json.gz.")
        if destination.exists() and not options["replace"]:
            raise CommandError("The backup already exists. Choose another path or pass --replace.")
        if not destination.parent.is_dir():
            raise CommandError(f"The destination directory does not exist: {destination.parent}")

        call_command(
            "dumpdata",
            "auth.group",
            "auth.user",
            "pos",
            natural_foreign=True,
            natural_primary=True,
            indent=2,
            output=str(destination),
            verbosity=0,
        )
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        checksum_path = destination.with_name(f"{destination.name}.sha256")
        checksum_path.write_text(f"{digest}  {destination.name}\n", encoding="ascii")
        self.stdout.write(
            self.style.SUCCESS(
                f"Backup created: {destination} ({destination.stat().st_size:,} bytes)\n"
                f"SHA-256: {digest}\nChecksum: {checksum_path}"
            )
        )
