"""Storage-neutral identities and sanitized transactional storage errors."""

import hashlib


class ManagedStoreError(RuntimeError):
    pass


class ManagedStateConflict(ManagedStoreError):
    pass


def record_id(kind: str, key: str) -> str:
    # Preserve imported checkpoint/case identities exactly.
    return kind + "-" + hashlib.sha256(key.encode()).hexdigest()
