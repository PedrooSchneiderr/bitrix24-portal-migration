"""Deal pipelines (categories) and their stages.

STATUS_ID is "C<category>:<CODE>"; the category half always differs between portals,
so every stage goes through the crosswalk.
"""

WON_PARKING_SORT = 9000
SEMANTIC_PROVISIONAL_SORT = 5000


def migrate_pipelines(src, dst, xw):
    dest_by_name = {c["NAME"]: c["ID"] for c in dst.call("crm.dealcategory.list") or []}
    for cat in src.call("crm.dealcategory.list") or []:
        dest_id = dest_by_name.get(cat["NAME"]) or xw.get("category", cat["ID"])
        if dest_id is None:
            dest_id = dst.call("crm.dealcategory.add",
                               {"fields": {"NAME": cat["NAME"], "SORT": cat.get("SORT")}}, write=True)
        if dest_id is None:  # dry-run
            continue
        xw.set("category", cat["ID"], dest_id)
        _migrate_stages(src, dst, xw, cat["ID"], dest_id)
    # The default pipeline (id 0) is never returned by crm.dealcategory.list.
    xw.set("category", 0, 0)
    _migrate_stages(src, dst, xw, 0, 0)


def _entity_id(category_id):
    return "DEAL_STAGE" if int(category_id) == 0 else f"DEAL_STAGE_{category_id}"


def _stages(client, category_id):
    return client.call("crm.status.list", {"filter": {"ENTITY_ID": _entity_id(category_id)},
                                           "order": {"SORT": "ASC"}}) or []


def _migrate_stages(src, dst, xw, src_cat, dst_cat):
    src_stages = _stages(src, src_cat)
    dst_stages = _stages(dst, dst_cat)
    # Match by name: an independently built destination pipeline shares labels, not CODEs.
    dst_by_name = {s["NAME"].strip().lower(): s for s in dst_stages}
    won = next((s for s in dst_stages if s.get("SEMANTICS") == "S"), None)

    missing = []
    for stage in src_stages:
        match = dst_by_name.get(stage["NAME"].strip().lower())
        if match:
            xw.set("stage", stage["STATUS_ID"], match["STATUS_ID"])
        else:
            missing.append(stage)
    if not missing:
        return

    # Plain stages must sort BEFORE the final WON stage: park WON far away first.
    if won:
        dst.call("crm.status.update", {"id": won["ID"], "fields": {"SORT": WON_PARKING_SORT}}, write=True)

    final_sorts = []
    for i, stage in enumerate(missing):
        fields = {"ENTITY_ID": _entity_id(dst_cat), "NAME": stage["NAME"],
                  "STATUS_ID": stage["STATUS_ID"].split(":")[-1], "COLOR": stage.get("COLOR")}
        if stage.get("SEMANTICS"):
            # Semantic (S/F) stages are refused unless sorted AFTER existing S/F stages:
            # create high, fix in the cleanup pass below.
            fields["SEMANTICS"] = stage["SEMANTICS"]
            fields["SORT"] = SEMANTIC_PROVISIONAL_SORT + i
        else:
            fields["SORT"] = stage["SORT"]
        new_id = dst.call("crm.status.add", {"fields": fields}, write=True)
        if new_id:
            final_sorts.append((new_id, stage["SORT"]))

    for stage in _stages(dst, dst_cat):
        key = stage["NAME"].strip().lower()
        src_match = next((s for s in src_stages if s["NAME"].strip().lower() == key), None)
        if src_match:
            xw.set("stage", src_match["STATUS_ID"], stage["STATUS_ID"])
    for status_id, sort in final_sorts:
        dst.call("crm.status.update", {"id": status_id, "fields": {"SORT": sort}}, write=True)
    if won:
        dst.call("crm.status.update", {"id": won["ID"], "fields": {"SORT": won["SORT"]}}, write=True)
