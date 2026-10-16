"""Validated file-backed generic registries."""

from __future__ import annotations
import re
from pathlib import Path
from threading import RLock
from backend.app.resolution.matching import match_domain

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "registries" / "domains.txt"
DOMAIN_RE = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
SYNTHETIC = [f"tenant{i}.example.com" for i in range(1, 11)]


class RegistryStore:
    def __init__(self, path=DEFAULT_PATH, *, test_mode=False, threshold=90.0):
        self.path = Path(path)
        self.test_mode = test_mode
        self.threshold = threshold
        self._lock = RLock()
        self._values = {}
        self.reload("domains")

    def reload(self, registry_type):
        if registry_type != "domains":
            raise ValueError("Unknown registry type")
        values = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            value = line.split("#", 1)[0].strip().casefold()
            if not value:
                continue
            if not DOMAIN_RE.fullmatch(value):
                raise ValueError(f"Invalid domain registry entry: {value}")
            if value in values:
                raise ValueError(f"Duplicate domain registry entry: {value}")
            values.append(value)
        if self.test_mode:
            values.extend(SYNTHETIC)
        with self._lock:
            self._values[registry_type] = tuple(values)
        return values

    def list(self, registry_type):
        with self._lock:
            return list(self._values.get(registry_type, ()))

    def resolve(self, entity_type, candidate, current_value=None):
        if entity_type != "contact.email.domain":
            raise ValueError("Unsupported entity type")
        return match_domain(candidate, self.list("domains"), self.threshold).dict()


_stores = {}


def registry_store(test_mode=False):
    key = bool(test_mode)
    if key not in _stores:
        _stores[key] = RegistryStore(test_mode=key)
    return _stores[key]
