"""Companies, contacts and deals, with links, product rows and file fields."""
from bitrix_client import file_payload

SKIP = {"ID", "DATE_CREATE", "DATE_MODIFY", "CREATED_BY_ID", "MODIFY_BY_ID", "ASSIGNED_BY_ID",
        "COMPANY_ID", "CONTACT_ID", "CONTACT_IDS", "LEAD_ID", "CATEGORY_ID", "STAGE_ID"}
ENTITY_TYPE_ID = {"deal": 2, "contact": 3, "company": 4}


class RecordMigrator:
    def __init__(self, src, dst, xw, owners, file_fields, owner_field_map):
        self.src, self.dst, self.xw = src, dst, xw
        self.owners = owners
        self.file_fields = file_fields          # {entity: {source field names}}
        self.owner_field_map = owner_field_map  # {entity: {MIGR_OWNER_NAME: UF_..., ...}}
        self.flagged = []

    # ---- generic field translation -----------------------------------------
    def translate(self, entity, record):
        out = {}
        for key, value in record.items():
            if key in SKIP or key in self.file_fields.get(entity, ()):
                continue  # file fields go through migrate_files, never through here
            if key.startswith("UF_CRM_"):
                dest_key = self.xw.get(f"field:{entity}", key)
                if not dest_key:
                    continue
                out[dest_key] = self._translate_enum(entity, value)
            elif key in ("PHONE", "EMAIL", "WEB", "IM"):
                out[key] = [{"VALUE": v["VALUE"], "VALUE_TYPE": v.get("VALUE_TYPE")} for v in value or []]
            else:
                out[key] = value
        out.update(self.owners.owner_fields(record.get("ASSIGNED_BY_ID"), self.owner_field_map[entity]))
        return out

    def _translate_enum(self, entity, value):
        lookup = lambda v: self.xw.get(f"enum:{entity}", v) or v
        return [lookup(v) for v in value] if isinstance(value, list) else lookup(value)

    # ---- dedup ---------------------------------------------------------------
    def find_duplicate(self, entity, record):
        # filter[PHONE]/filter[EMAIL] on *.list can return wrong matches silently.
        for comm in ("PHONE", "EMAIL"):
            values = [v["VALUE"] for v in record.get(comm) or []]
            if not values:
                continue
            res = self.dst.call("crm.duplicate.findbycomm",
                                {"type": comm, "values": values, "entity_type": entity.upper()})
            # Match -> dict {"CONTACT": [ids]}; no match -> empty list.
            if isinstance(res, dict) and res.get(entity.upper()):
                return res[entity.upper()][0]
        return None

    def _create(self, entity, record, extra=None):
        def create():
            dup = self.find_duplicate(entity, record) if entity != "deal" else None
            if dup:
                return dup
            fields = {**self.translate(entity, record), **(extra or {})}
            return self.dst.call(f"crm.{entity}.add",
                                 {"fields": fields, "params": {"REGISTER_SONET_EVENT": "N"}}, write=True)
        return self.xw.get_or_create(entity, record["ID"], create)

    # ---- entities --------------------------------------------------------------
    def migrate_companies(self):
        for rec in self.src.list_all("crm.company.list"):
            self._create("company", rec)

    def migrate_contacts(self):
        for rec in self.src.list_all("crm.contact.list"):
            dest_id = self._create("contact", rec)
            for company_id in self.src.call("crm.contact.company.items.get", {"id": rec["ID"]}) or []:
                dest_company = self.xw.get("company", company_id["COMPANY_ID"])
                if dest_id and dest_company:
                    self.dst.call("crm.company.contact.add",
                                  {"id": dest_company, "fields": {"CONTACT_ID": dest_id}}, write=True)

    def migrate_deals(self):
        for rec in self.src.list_all("crm.deal.list"):
            extra = {"CATEGORY_ID": self.xw.get("category", rec.get("CATEGORY_ID") or 0)}
            stage = self.xw.get("stage", rec.get("STAGE_ID"))
            if stage:
                extra["STAGE_ID"] = stage
            else:
                self.flagged.append(("deal", rec["ID"], f"unmapped stage {rec.get('STAGE_ID')}"))
            if rec.get("COMPANY_ID") and rec["COMPANY_ID"] != "0":
                company = self.xw.get("company", rec["COMPANY_ID"])
                if company:
                    extra["COMPANY_ID"] = company
                else:
                    # Source already deleted the company: report it, don't invent a link.
                    self.flagged.append(("deal", rec["ID"], "orphan COMPANY_ID in source"))
            dest_id = self._create("deal", rec, extra)
            if dest_id:
                self._link_contacts(rec["ID"], dest_id)
                self._product_rows(rec["ID"], dest_id)

    def _link_contacts(self, src_id, dest_id):
        items = self.src.call("crm.deal.contact.items.get", {"id": src_id}) or []
        mapped = [{"CONTACT_ID": self.xw.get("contact", i["CONTACT_ID"]),
                   "IS_PRIMARY": i.get("IS_PRIMARY")} for i in items
                  if self.xw.get("contact", i["CONTACT_ID"])]
        if mapped:
            # .set REPLACES the whole list, so always send the complete set at once.
            self.dst.call("crm.deal.contact.items.set", {"id": dest_id, "items": mapped}, write=True)

    def _product_rows(self, src_id, dest_id):
        rows = self.src.call("crm.deal.productrows.get", {"id": src_id}) or []
        out = []
        for r in rows:
            product = self.xw.get("product", r["PRODUCT_ID"]) if int(r["PRODUCT_ID"]) else None
            # PRODUCT_ID 0 = free-text line; unmapped products are kept frozen the same way.
            out.append({"PRODUCT_ID": product or 0, "PRODUCT_NAME": r["PRODUCT_NAME"],
                        "PRICE": r["PRICE"], "QUANTITY": r["QUANTITY"],
                        "DISCOUNT_RATE": r.get("DISCOUNT_RATE"), "TAX_RATE": r.get("TAX_RATE")})
        if out:
            self.dst.call("crm.deal.productrows.set", {"id": dest_id, "rows": out}, write=True)

    # ---- files ------------------------------------------------------------------
    def migrate_files(self, entity):
        fields = self.file_fields.get(entity) or set()
        if not fields:
            return
        for rec in self.src.list_all(f"crm.{entity}.list", select=["ID", *fields]):
            dest_id = self.xw.get(entity, rec["ID"])
            if not dest_id or self.xw.get(f"files:{entity}", rec["ID"]):
                continue
            update = {}
            for field in fields:
                value = rec.get(field)
                files = value if isinstance(value, list) else [value] if value else []
                payload = []
                for f in files:
                    content = self.src.download_file(ENTITY_TYPE_ID[entity], rec["ID"], field, f["id"])
                    payload.append(file_payload(f.get("name") or f"file_{f['id']}", content))
                if payload:
                    update[self.xw.get(f"field:{entity}", field)] = payload
            if update:
                self.dst.call(f"crm.{entity}.update", {"id": dest_id, "fields": update}, write=True)
                self.xw.set(f"files:{entity}", rec["ID"], True)
