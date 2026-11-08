"""Minimal Bitrix24 REST client over an inbound webhook."""
import base64
import os
import time

import requests

THROTTLE_SECONDS = 0.5
MAX_RETRIES = 5
BATCH_LIMIT = 50


class BitrixError(Exception):
    pass


class BitrixClient:
    def __init__(self, webhook_url, dry_run=False, name="portal"):
        if not webhook_url:
            raise ValueError(f"webhook URL for {name} is not set")
        self.base = webhook_url.rstrip("/") + "/"
        self.dry_run = dry_run
        self.name = name
        self.session = requests.Session()
        self._last_call = 0.0

    @classmethod
    def from_env(cls, var, **kwargs):
        return cls(os.environ.get(var, ""), name=var, **kwargs)

    def _throttle(self):
        wait = THROTTLE_SECONDS - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def call(self, method, params=None, write=False):
        """POST a JSON body. Write calls are skipped (and logged) in dry-run mode."""
        if write and self.dry_run:
            print(f"[dry-run] {self.name} {method} {params}")
            return None
        for attempt in range(MAX_RETRIES):
            self._throttle()
            resp = self.session.post(self.base + method + ".json", json=params or {}, timeout=60)
            data = resp.json() if resp.content else {}
            error = data.get("error")
            if error == "QUERY_LIMIT_EXCEEDED" or resp.status_code == 503:
                time.sleep(2 ** attempt)
                continue
            if error:
                raise BitrixError(f"{method}: {error} - {data.get('error_description')}")
            return data.get("result")
        raise BitrixError(f"{method}: retries exhausted")

    def list_all(self, method, filter=None, select=None):
        """Paginate by filter[>ID] ordered by ID; `start=` offsets truncate on big sets."""
        last_id = 0
        while True:
            params = {
                "order": {"ID": "ASC"},
                "filter": {**(filter or {}), ">ID": last_id},
                "select": select or ["*", "UF_*"],
                "start": -1,  # disables the COUNT query, faster on large tables
            }
            page = self.call(method, params) or []
            if not page:
                return
            yield from page
            last_id = int(page[-1]["ID"])

    def batch(self, commands, write=True):
        """Run {key: (method, params)} in chunks of 50; raise if ANY sub-request failed.

        A batch returning HTTP 200 says nothing about its individual commands.
        """
        results = {}
        items = list(commands.items())
        for i in range(0, len(items), BATCH_LIMIT):
            chunk = items[i:i + BATCH_LIMIT]
            cmd = {key: _encode_cmd(method, params) for key, (method, params) in chunk}
            res = self.call("batch", {"halt": 0, "cmd": cmd}, write=write)
            if res is None:
                continue
            errors = res.get("result_error") or {}
            if errors:
                raise BitrixError(f"batch sub-request errors: {errors}")
            results.update(res.get("result") or {})
        return results

    def download_file(self, entity_type_id, entity_id, field_name, file_id):
        """The show_file.php URL returned by list calls redirects to login on webhooks."""
        self._throttle()
        resp = self.session.get(
            self.base + "crm.controller.item.getFile.json",
            params={"entityTypeId": entity_type_id, "id": entity_id,
                    "fieldName": field_name, "fileId": file_id},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.content


def file_payload(filename, content):
    """The only write shape Bitrix24 accepts for file fields; others are ignored silently."""
    return {"fileData": [filename, base64.b64encode(content).decode()]}


def _encode_cmd(method, params):
    from urllib.parse import urlencode
    return f"{method}?{urlencode(_flatten(params or {}))}"


def _flatten(obj, prefix=""):
    """Turn nested dicts/lists into PHP-style query keys: fields[TITLE]=x."""
    pairs = []
    items = obj.items() if isinstance(obj, dict) else enumerate(obj)
    for k, v in items:
        key = f"{prefix}[{k}]" if prefix else str(k)
        if isinstance(v, (dict, list)):
            pairs.extend(_flatten(v, key))
        else:
            pairs.append((key, "" if v is None else v))
    return pairs
