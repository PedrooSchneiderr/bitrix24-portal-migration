"""Recreate only the custom fields that actually hold data in the records being migrated."""

ENTITIES = {"deal": "crm.deal", "company": "crm.company", "contact": "crm.contact"}

# Auxiliary fields that carry the original owner when the user doesn't exist in the destination.
OWNER_FIELDS = {"MIGR_OWNER_NAME": "Original owner (name)",
                "MIGR_OWNER_EMAIL": "Original owner (e-mail)"}


def used_field_names(src, entity):
    used = set()
    for record in src.list_all(f"{ENTITIES[entity]}.list"):
        used.update(k for k, v in record.items() if k.startswith("UF_CRM_") and v not in (None, "", [], False))
    return used


def migrate_fields(src, dst, xw, entity):
    prefix = ENTITIES[entity]
    used = used_field_names(src, entity)
    source_fields = src.call(f"{prefix}.userfield.list") or []
    dest_by_label = {_label(f): f for f in dst.call(f"{prefix}.userfield.list") or []}

    for field in source_fields:
        name = field["FIELD_NAME"]
        if name not in used:
            continue
        # UF_CRM_* names are auto-generated per portal; match by label instead.
        existing = dest_by_label.get(_label(field))
        if existing:
            xw.set(f"field:{entity}", name, existing["FIELD_NAME"])
            _map_list_options(xw, entity, field, existing)
            continue
        new = {k: field.get(k) for k in ("USER_TYPE_ID", "MULTIPLE", "MANDATORY", "SETTINGS",
                                         "EDIT_FORM_LABEL", "LIST_COLUMN_LABEL")}
        new["FIELD_NAME"] = name.replace("UF_CRM_", "", 1)[:13]
        if field.get("LIST"):
            new["LIST"] = [{"VALUE": o["VALUE"], "SORT": o.get("SORT")} for o in field["LIST"]]
        new_id = dst.call(f"{prefix}.userfield.add", {"fields": new}, write=True)
        if new_id:
            created = dst.call(f"{prefix}.userfield.get", {"id": new_id})
            xw.set(f"field:{entity}", name, created["FIELD_NAME"])
            _map_list_options(xw, entity, field, created)

    for code, label in OWNER_FIELDS.items():
        if not any(_label(f) == label for f in dest_by_label.values()):
            dst.call(f"{prefix}.userfield.add", {"fields": {
                "FIELD_NAME": code, "USER_TYPE_ID": "string", "EDIT_FORM_LABEL": label}}, write=True)


def file_fields(src, entity):
    """Names of source fields of type `file`; these bypass generic value translation."""
    return {f["FIELD_NAME"] for f in src.call(f"{ENTITIES[entity]}.userfield.list") or []
            if f.get("USER_TYPE_ID") == "file"}


def _map_list_options(xw, entity, src_field, dst_field):
    dst_opts = {o["VALUE"]: o["ID"] for o in dst_field.get("LIST") or []}
    for opt in src_field.get("LIST") or []:
        if opt["VALUE"] in dst_opts:
            xw.set(f"enum:{entity}", opt["ID"], dst_opts[opt["VALUE"]])


def _label(field):
    label = field.get("EDIT_FORM_LABEL") or field.get("FIELD_NAME")
    if isinstance(label, dict):
        label = next(iter(label.values()), "")
    return (label or "").strip().lower()
