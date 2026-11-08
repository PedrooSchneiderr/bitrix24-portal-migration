"""Responsible-user resolution. Users cannot be created via REST, only mapped.

Every create in every entity step must go through `owner_fields`; forgetting it on a single
entity type leaves those records assigned to the webhook owner.
"""
import os


class OwnerResolver:
    def __init__(self, src, dst, exceptions=None):
        self.src, self.dst = src, dst
        self.fallback = os.environ.get("FALLBACK_USER_ID")
        self.exceptions = {k.lower(): v.lower() for k, v in (exceptions or {}).items()}
        self._src_cache = {}
        self._dst_by_email = None

    def _source_user(self, user_id):
        if user_id not in self._src_cache:
            users = self.src.call("user.get", {"ID": user_id}) or [{}]
            u = users[0]
            name = " ".join(p for p in (u.get("NAME"), u.get("LAST_NAME")) if p)
            self._src_cache[user_id] = (name, (u.get("EMAIL") or "").lower())
        return self._src_cache[user_id]

    def _dest_id(self, email):
        if self._dst_by_email is None:
            self._dst_by_email = {(u.get("EMAIL") or "").lower(): u["ID"]
                                  for u in self.dst.list_all("user.get", select=None)}
        email = self.exceptions.get(email, email)
        return self._dst_by_email.get(email) or self.fallback

    def owner_fields(self, source_user_id, field_map):
        """Return ASSIGNED_BY_ID plus the auxiliary name/e-mail marker fields."""
        if not source_user_id:
            return {}
        name, email = self._source_user(source_user_id)
        out = {field_map["MIGR_OWNER_NAME"]: name, field_map["MIGR_OWNER_EMAIL"]: email}
        dest = self._dest_id(email)
        if dest:
            out["ASSIGNED_BY_ID"] = dest
        return out
