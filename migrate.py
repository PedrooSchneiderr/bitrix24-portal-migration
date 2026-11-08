"""Orchestrates a Bitrix24 -> Bitrix24 migration in dependency order.

Usage: python migrate.py [--dry-run] [--only STEP ...] [--crosswalk FILE]
"""
import argparse
import os

from bitrix_client import BitrixClient
from crosswalk import Crosswalk
from steps.fields import ENTITIES, OWNER_FIELDS, file_fields, migrate_fields
from steps.pipelines import migrate_pipelines
from steps.records import RecordMigrator
from steps.users import OwnerResolver
from steps.verify import verify

STEPS = ["fields", "pipelines", "companies", "contacts", "deals", "files", "verify"]


def load_env(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def owner_field_map(dst, entity):
    labels = {v.lower(): k for k, v in OWNER_FIELDS.items()}
    out = {}
    for f in dst.call(f"{ENTITIES[entity]}.userfield.list") or []:
        label = f.get("EDIT_FORM_LABEL")
        label = next(iter(label.values()), "") if isinstance(label, dict) else (label or "")
        if label.lower() in labels:
            out[labels[label.lower()]] = f["FIELD_NAME"]
    # In dry-run the fields may not exist yet; fall back to the intended code.
    return {k: out.get(k, f"UF_CRM_{k}") for k in OWNER_FIELDS}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="read everything, write nothing")
    parser.add_argument("--only", nargs="*", choices=STEPS, default=STEPS)
    parser.add_argument("--crosswalk", default="crosswalk.json")
    args = parser.parse_args()

    load_env()
    src = BitrixClient.from_env("SOURCE_WEBHOOK_URL")
    dst = BitrixClient.from_env("DEST_WEBHOOK_URL", dry_run=args.dry_run)
    xw = Crosswalk("crosswalk.dryrun.json" if args.dry_run else args.crosswalk)

    print("Reminder: disable destination automations/webhooks before loading data.")

    if "fields" in args.only:
        for entity in ENTITIES:
            migrate_fields(src, dst, xw, entity)
    if "pipelines" in args.only:
        migrate_pipelines(src, dst, xw)

    files = {e: file_fields(src, e) for e in ENTITIES}
    migrator = RecordMigrator(src, dst, xw, OwnerResolver(src, dst), files,
                              {e: owner_field_map(dst, e) for e in ENTITIES})

    if "companies" in args.only:
        migrator.migrate_companies()
    if "contacts" in args.only:
        migrator.migrate_contacts()
    if "deals" in args.only:
        migrator.migrate_deals()
    if "files" in args.only:
        for entity in ENTITIES:
            migrator.migrate_files(entity)
    if "verify" in args.only and not args.dry_run:
        for entity in ENTITIES:
            verify(src, dst, xw, entity, files[entity], out_path=f"verify_{entity}.csv")

    for kind, source_id, reason in migrator.flagged:
        print(f"REVIEW {kind} {source_id}: {reason}")


if __name__ == "__main__":
    main()
