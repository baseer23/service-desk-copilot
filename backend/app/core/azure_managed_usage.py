"""Guard semantics over a tenant-scoped transactional record ledger.

SQLite evaluates policy tables in memory. The record adapter provides persistence
and atomic locking: local SQLite on the Mac, or leased Cosmos in legacy deployments.
A failed transaction never commits a reservation or enables dispatch.
"""

import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime

from backend.app.core.azure_usage import AzureUsageBlocked, AzureUsageGuard
from backend.app.store.azure_records import ManagedStoreError

TABLES = {
    "azure_policy": "id INTEGER PRIMARY KEY,disabled INTEGER NOT NULL",
    "azure_demo_policy": (
        "id INTEGER PRIMARY KEY,limit_micros INTEGER NOT NULL,authorized_at REAL NOT NULL,"
        "authorization_reference TEXT NOT NULL"
    ),
    "azure_usage": (
        "id TEXT PRIMARY KEY,month TEXT NOT NULL,day TEXT NOT NULL,kind TEXT NOT NULL,"
        "estimate INTEGER NOT NULL,active INTEGER NOT NULL,usage TEXT NOT NULL DEFAULT '{}',"
        "seconds REAL NOT NULL DEFAULT 0,turns INTEGER NOT NULL DEFAULT 0,"
        "phase TEXT NOT NULL DEFAULT 'demo'"
    ),
    "azure_voice_usage": (
        "reservation TEXT NOT NULL,event TEXT NOT NULL,usage TEXT NOT NULL,"
        "PRIMARY KEY(reservation,event)"
    ),
    "azure_acceptance_fixture": "id INTEGER PRIMARY KEY CHECK(id=1),digest TEXT NOT NULL",
    "azure_acceptance_operator": "id INTEGER PRIMARY KEY CHECK(id=1),digest TEXT NOT NULL",
    "azure_dispatch_slots": "kind TEXT PRIMARY KEY,next_at REAL NOT NULL",
    "azure_validation": "capability TEXT PRIMARY KEY,passed INTEGER NOT NULL,code TEXT NOT NULL",
}

_clients = {}
_clients_lock = threading.Lock()


def shared_records(settings):
    key = (
        str(settings.case_db_path.resolve()),
        settings.local_persistence_enabled,
        settings.data_platform,
        settings.azure_cosmos_endpoint,
        settings.azure_cosmos_database,
        settings.azure_cosmos_container,
        settings.deployment_mode,
    )
    with _clients_lock:
        if key not in _clients:
            from backend.app.store.local_records import record_store

            _clients[key] = record_store(settings)
        return _clients[key]


def close_usage_clients():
    with _clients_lock:
        for records in _clients.values():
            records.close()
        _clients.clear()


class CosmosUsageGuard(AzureUsageGuard):
    def __init__(self, settings, records=None):
        super().__init__(settings)
        self.records = records or shared_records(settings)

    def ledger_exists(self):
        return True

    @contextmanager
    def inspection(self):
        """Read committed diagnostics without competing with paid reservations."""
        db = sqlite3.connect(":memory:")
        try:
            row = self.records.read("usage_policy", "subscription", tenant="system:usage")
            tables = (
                row["payload"]["tables"]
                if row
                else {"azure_policy": [{"id": 1, "disabled": 1}]}
            )
            for name, schema in TABLES.items():
                db.execute(f"CREATE TABLE {name}({schema})")
                for entry in tables.get(name, []):
                    columns = [column[1] for column in db.execute(f"PRAGMA table_info({name})")]
                    if set(entry) != set(columns):
                        raise AzureUsageBlocked("Managed usage ledger schema is invalid")
                    db.execute(
                        f"INSERT INTO {name} VALUES({','.join('?' for _ in columns)})",
                        [entry[column] for column in columns],
                    )
            if not self.settings.test_mode:
                now = datetime.now(UTC)
                db.execute(
                    "INSERT OR IGNORE INTO azure_usage "
                    "(id,month,day,kind,estimate,active,phase) VALUES(?,?,?,?,?,0,'demo') "
                    "ON CONFLICT(id) DO UPDATE SET estimate=MAX(estimate,excluded.estimate)",
                    (
                        "infrastructure-" + now.strftime("%Y-%m") + "-azure-native-foundation",
                        now.strftime("%Y-%m"), now.strftime("%Y-%m-%d"),
                        "infrastructure",
                        int(self.settings.azure_estimated_infrastructure_cad * 1_000_000),
                    ),
                )
            yield db
        except (ManagedStoreError, sqlite3.Error, KeyError, TypeError):
            raise AzureUsageBlocked(
                "Managed usage ledger unavailable; paid operations blocked"
            ) from None
        finally:
            db.close()

    def require_enabled(self):
        # A speech heartbeat reads committed policy; it must not acquire a writer
        # lease for every 20 ms audio frame. Paid reservations still use the full
        # atomic transaction and recheck policy before they commit.
        if not self.settings.azure_usage_guard_enabled or self.settings.azure_emergency_disable:
            raise AzureUsageBlocked("Azure usage disabled; explicit local enablement required")
        try:
            row = self.records.read("usage_policy", "subscription", tenant="system:usage")
            if not row:
                raise AzureUsageBlocked("Managed usage policy is missing")
            tables = row["payload"]["tables"]
            if not any(
                policy.get("id") == 1 and policy.get("disabled") == 0
                for policy in tables["azure_policy"]
            ):
                raise AzureUsageBlocked("Azure usage disabled; explicit local enablement required")
            month = datetime.now(UTC).strftime("%Y-%m")
            charges = [entry for entry in tables["azure_usage"] if entry["month"] == month]
            used = sum(entry["estimate"] for entry in charges)
            if not self.settings.test_mode:
                reserved = sum(
                    entry["estimate"] for entry in charges if entry["kind"] == "infrastructure"
                )
                forecast = int(self.settings.azure_estimated_infrastructure_cad * 1_000_000)
                used += max(0, forecast - reserved)
            limits = [
                policy["limit_micros"]
                for policy in tables.get("azure_demo_policy", [])
                if policy.get("id") == 1
            ]
            limit = min(self.limit, *limits) if limits else self.limit
            if used >= limit:
                raise AzureUsageBlocked("Estimated monthly Azure application allowance exhausted")
            if (
                self.settings.azure_setup_validation_mode
                and sum(
                    entry["estimate"]
                    for entry in tables["azure_usage"]
                    if entry["phase"] == "setup"
                )
                >= self.settings.azure_estimated_setup_limit_cad * 1_000_000
            ):
                raise AzureUsageBlocked("Estimated Azure setup validation allowance exhausted")
        except (ManagedStoreError, KeyError, TypeError):
            raise AzureUsageBlocked(
                "Managed usage ledger unavailable; paid operations blocked"
            ) from None

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(":memory:")
        try:
            with self.records.locked(
                "usage_policy", "subscription", tenant="system:usage"
            ) as payload:
                tables = payload.get("tables", {"azure_policy": [{"id": 1, "disabled": 1}]})
                for name, schema in TABLES.items():
                    db.execute(f"CREATE TABLE {name}({schema})")
                    for row in tables.get(name, []):
                        columns = [column[1] for column in db.execute(f"PRAGMA table_info({name})")]
                        if set(row) != set(columns):
                            raise AzureUsageBlocked("Managed usage ledger schema is invalid")
                        db.execute(
                            f"INSERT INTO {name}({','.join(columns)}) "
                            f"VALUES({','.join('?' for _ in columns)})",
                            [row[column] for column in columns],
                        )
                db.commit()
                db.execute("BEGIN IMMEDIATE")
                if not self.settings.test_mode:
                    now = datetime.now(UTC)
                    month = now.strftime("%Y-%m")
                    reference = "infrastructure-" + month + "-azure-native-foundation"
                    db.execute(
                        "INSERT INTO azure_usage "
                        "(id,month,day,kind,estimate,active,phase) "
                        "VALUES(?,?,?,?,?,0,'demo') "
                        "ON CONFLICT(id) DO UPDATE SET estimate=MAX(estimate,excluded.estimate)",
                        (
                            reference, month, now.strftime("%Y-%m-%d"), "infrastructure",
                            int(self.settings.azure_estimated_infrastructure_cad * 1_000_000),
                        ),
                    )
                yield db
                db.commit()
                saved = {}
                for name in TABLES:
                    cursor = db.execute(f"SELECT * FROM {name}")  # noqa: S608 - Identifiers come from a fixed validated allowlist.
                    columns = [column[0] for column in cursor.description]
                    saved[name] = [
                        dict(zip(columns, row, strict=True)) for row in cursor.fetchall()
                    ]
                payload["tables"] = saved
        except (ManagedStoreError, sqlite3.Error):
            raise AzureUsageBlocked(
                "Managed usage ledger unavailable; paid operations blocked"
            ) from None
        finally:
            db.close()
