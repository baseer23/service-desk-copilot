"""Explicit free/low-cost data-plane probes. Never dispatch AI or print credentials."""

import argparse
import json
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--configuration", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads((args.configuration / "host-settings.private.json").read_text())
    from backend.app.core.azure_usage import AzureUsageGuard
    from backend.app.core.config import get_settings
    from backend.app.core.principal import Principal, principal_scope
    from backend.app.store.azure_knowledge import AzureBlobDocuments, AzureSearchStore
    from backend.app.store.azure_records import CosmosRecords

    settings = get_settings().model_copy(
        update={
            "data_platform": "azure",
            "azure_cosmos_endpoint": config["AZURE_COSMOS_ENDPOINT"],
            "azure_storage_account_url": config["AZURE_STORAGE_ACCOUNT_URL"],
            "azure_search_endpoint": config["AZURE_SEARCH_ENDPOINT"],
            "auth_allowed_tenants": [config["AUTH_ALLOWED_TENANTS"]],
        }
    )
    records = CosmosRecords(settings)
    result = {"actual_azure": True, "paid_ai_requests": 0}
    try:
        with principal_scope(
            Principal(config["AUTH_ALLOWED_TENANTS"], config["AUTH_OPERATOR_IDS"], True)
        ):
            identifier = "connectivity-" + uuid.uuid4().hex
            payload = {"fictional": True, "purpose": "persistence validation"}
            row = records.write("connectivity", identifier, payload, create_only=True)
            restored = CosmosRecords(settings)
            try:
                result["database_restart_read"] = (
                    restored.read("connectivity", identifier)["payload"] == payload
                )
            finally:
                restored.close()
            result["partition_query"] = any(
                item["key"] == identifier for item in records.query("connectivity")
            )
            from azure.core import MatchConditions

            records.container.delete_item(
                row["id"],
                partition_key=row["tenant_id"],
                etag=row["_etag"],
                match_condition=MatchConditions.IfNotModified,
            )
            search = AzureSearchStore(settings, records=records)
            try:
                hits = search.search_keywords("VPN")
                result["semantic_knowledge_search"] = bool(hits)
                result["citations"] = bool(hits and hits[0]["metadata"].get("doc_id"))
            finally:
                search.client.close()
                if search.credential:
                    search.credential.close()
            documents = records.query("document", limit=1)
            if documents:
                blob = AzureBlobDocuments(settings, records=records)
                result["private_document_download"] = bool(
                    blob.get(documents[0]["key"], "original")
                )
                blob.client.close()
                blob.credential.close()
            result["persistent_shutdown"] = not AzureUsageGuard(settings).snapshot()["enabled"]
            with principal_scope(Principal("untrusted-test-tenant", "user")):
                result["tenant_read_isolation"] = records.query("case") == []
        print(json.dumps(result), flush=True)
    finally:
        records.close()


if __name__ == "__main__":
    main()
