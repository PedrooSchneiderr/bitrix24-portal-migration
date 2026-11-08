"""Persistent source->destination ID map. Checked before every create, so reruns are safe."""
import json
import os


class Crosswalk:
    def __init__(self, path="crosswalk.json"):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)

    def get(self, kind, source_id):
        return self.data.get(kind, {}).get(str(source_id))

    def set(self, kind, source_id, dest_id):
        self.data.setdefault(kind, {})[str(source_id)] = dest_id
        self.save()  # save on each write so a crash loses at most one record

    def items(self, kind):
        return self.data.get(kind, {}).items()

    def get_or_create(self, kind, source_id, create_fn):
        existing = self.get(kind, source_id)
        if existing is not None:
            return existing
        new_id = create_fn()
        if new_id is not None:
            self.set(kind, source_id, new_id)
        return new_id

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
