"""Import operator-supplied public campus places; never invent real places."""
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from plusone.models import CampusLocation


def read_locations(path):
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 1024 * 1024:
            raise CommandError("Location JSON must be smaller than 1 MiB.")
        rows = json.loads(raw)
    except (OSError, ValueError, UnicodeDecodeError) as error:
        raise CommandError("Could not read a valid location JSON file.") from error
    if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
        raise CommandError("Location JSON must be an array of 1 to 500 public places.")
    cleaned = []
    names = set()
    allowed = {"name", "location_type", "area", "latitude", "longitude"}
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict) or set(row) - allowed:
            raise CommandError(f"Location {number} has unsupported fields.")
        item = {}
        for field, maximum in (("name", 120), ("area", 80)):
            value = row.get(field)
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
                raise CommandError(f"Location {number} requires a nonempty {field} of at most {maximum} characters.")
            item[field] = value.strip()
        if item["name"] in names:
            raise CommandError(f"Location {number} repeats a name.")
        names.add(item["name"])
        if row.get("location_type") not in CampusLocation.LocationType.values:
            raise CommandError(f"Location {number} needs a supported location_type.")
        item["location_type"] = row["location_type"]
        if (row.get("latitude") is None) != (row.get("longitude") is None):
            raise CommandError(f"Location {number} must supply both coordinates or neither.")
        for field, bound in (("latitude", 90), ("longitude", 180)):
            if field not in row:
                continue
            value = row[field]
            if value is None:
                item[field] = None
                continue
            try:
                coordinate = Decimal(str(value))
                if isinstance(value, bool) or not coordinate.is_finite() or abs(coordinate) > bound or coordinate.as_tuple().exponent < -6:
                    raise ValueError
            except (InvalidOperation, ValueError):
                raise CommandError(f"Location {number} has an invalid {field}; use valid degrees with at most six decimals.")
            item[field] = coordinate
        cleaned.append(item)
    return cleaned


class Command(BaseCommand):
    help = "Preview or import real public campus places from supplied JSON; preserve referenced historical places."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="JSON array of public campus places.")
        parser.add_argument("--commit", action="store_true", help="Apply the reviewed import.")
        parser.add_argument("--remove-unused-unlisted", action="store_true",
                            help="Also remove unlisted places only when no activity has ever referenced them.")

    def handle(self, *args, **options):
        rows = read_locations(options["file"])
        names = {row["name"] for row in rows}
        commit = options["commit"]
        with transaction.atomic():
            existing = CampusLocation.objects.all()
            if commit:
                existing = existing.select_for_update()
            existing = {place.name: place for place in existing.order_by("pk")}
            created, changed, unchanged = [], [], []
            for row in rows:
                place = existing.get(row["name"])
                fields = {key: value for key, value in row.items() if key != "name"}
                if place is None:
                    created.append(row["name"])
                    if commit:
                        CampusLocation.objects.create(**row)
                elif any(getattr(place, key) != value for key, value in fields.items()):
                    changed.append(row["name"])
                    if commit:
                        for key, value in fields.items():
                            setattr(place, key, value)
                        place.save(update_fields=list(fields))
                else:
                    unchanged.append(row["name"])
            unlisted = CampusLocation.objects.exclude(name__in=names)
            removable = list(unlisted.filter(activity_posts__isnull=True).values_list("name", flat=True))
            preserved = list(unlisted.filter(activity_posts__isnull=False).order_by().values_list("name", flat=True).distinct())
            if commit and options["remove_unused_unlisted"]:
                # PROTECT also enforces this boundary if a publish races us.
                unlisted.filter(activity_posts__isnull=True).delete()
        self.stdout.write(json.dumps({
            "mode": "commit" if commit else "dry_run", "create": created, "update": changed,
            "unchanged": unchanged,
            "remove_unused_unlisted": removable if options["remove_unused_unlisted"] else [],
            "preserve_referenced_unlisted": preserved,
            "note": "Imported supplied names only. Existing referenced locations and activity history are preserved.",
        }, indent=2))
