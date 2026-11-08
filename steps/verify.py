"""Re-read migrated records from the destination; a success response is not proof."""
import csv

CHECK = ("TITLE", "NAME", "LAST_NAME", "OPPORTUNITY", "STAGE_ID")


def verify(src, dst, xw, entity, file_fields=(), out_path=None):
    rows, problems = [], 0
    for src_id, dest_id in list(xw.items(entity)):
        a = src.call(f"crm.{entity}.get", {"id": src_id}) or {}
        b = dst.call(f"crm.{entity}.get", {"id": dest_id}) or {}
        issues = []
        for key in CHECK:
            if key in a and key != "STAGE_ID" and str(a.get(key)) != str(b.get(key)):
                issues.append(key)
        # File fields that "succeeded" but are empty are the classic silent failure.
        for field in file_fields:
            dest_field = xw.get(f"field:{entity}", field)
            if a.get(field) and not b.get(dest_field):
                issues.append(f"empty file field {dest_field}")
        problems += bool(issues)
        rows.append({"source_id": src_id, "dest_id": dest_id, "issues": "; ".join(issues)})
    if out_path:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["source_id", "dest_id", "issues"])
            writer.writeheader()
            writer.writerows(rows)
    print(f"verify {entity}: {len(rows)} checked, {problems} with issues")
    return problems
