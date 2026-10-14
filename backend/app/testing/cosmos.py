"""Explicit in-process Cosmos test double. Never selected by runtime configuration."""

import copy
import re
import threading


class CosmosTestError(Exception):
    def __init__(self, status):
        self.status_code = status


class FakeCosmosContainer:
    def __init__(self):
        self.items = {}
        self.version = 0
        self.lock = threading.RLock()
        self.partitions = []

    def read_item(self, identifier, partition_key):
        with self.lock:
            self.partitions.append(partition_key)
            if (partition_key, identifier) not in self.items:
                raise CosmosTestError(404)
            return copy.deepcopy(self.items[partition_key, identifier])

    def upsert_item(self, body):
        with self.lock:
            self.version += 1
            saved = {**copy.deepcopy(body), "_etag": str(self.version)}
            self.items[body["tenant_id"], body["id"]] = saved
            return copy.deepcopy(saved)

    def create_item(self, body):
        with self.lock:
            if (body["tenant_id"], body["id"]) in self.items:
                raise CosmosTestError(409)
            return self.upsert_item(body)

    def replace_item(self, identifier, body, etag=None, match_condition=None):
        with self.lock:
            previous = self.read_item(identifier, body["tenant_id"])
            if previous["_etag"] != etag:
                raise CosmosTestError(412)
            return self.upsert_item(body)

    def query_items(self, query, parameters, partition_key, max_item_count=100):
        fields = {name: parameter for name, parameter in re.findall(r"c\.(\w+)=(@\w+)", query)}
        values = {parameter["name"]: parameter["value"] for parameter in parameters}
        with self.lock:
            return [
                copy.deepcopy(row)
                for (tenant, _), row in self.items.items()
                if tenant == partition_key
                and all(row.get(name) == values[parameter] for name, parameter in fields.items())
            ]

    def delete_item(self, identifier, partition_key, etag=None, match_condition=None):
        with self.lock:
            row = self.read_item(identifier, partition_key)
            if row["_etag"] != etag:
                raise CosmosTestError(412)
            del self.items[partition_key, identifier]

    def execute_item_batch(self, operations, partition_key):
        with self.lock:
            for kind, arguments, options in operations:
                if kind == "replace":
                    previous = self.read_item(arguments[0], partition_key)
                    if previous["_etag"] != options["if_match_etag"]:
                        raise CosmosTestError(412)
                elif kind == "create" and (partition_key, arguments[0]["id"]) in self.items:
                    raise CosmosTestError(409)
            for kind, arguments, _ in operations:
                self.upsert_item(arguments[-1])
            return [{"statusCode": 200} for _ in operations]
