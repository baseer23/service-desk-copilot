"""Initialize reviewed data-plane schema and the persistent Key Vault signing key.

Never retrieve account/API keys. The signing key is read privately from the existing
local configuration and transferred in memory; it is never a CLI argument/artifact.
"""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--configuration", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads((args.configuration / "host-settings.private.json").read_text())
    from azure.core.exceptions import ResourceExistsError
    from azure.identity import AzureCliCredential
    from azure.keyvault.secrets import SecretClient
    from azure.search.documents.indexes import SearchIndexClient
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchableField,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SemanticConfiguration,
        SemanticField,
        SemanticPrioritizedFields,
        SemanticSearch,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )
    from azure.storage.blob import BlobServiceClient
    from backend.app.core.config import get_settings

    credential = AzureCliCredential(process_timeout=5)
    with BlobServiceClient(
        config["AZURE_STORAGE_ACCOUNT_URL"], credential=credential, retry_total=0
    ) as blob:
        try:
            blob.create_container(config["AZURE_STORAGE_CONTAINER"])
        except ResourceExistsError:
            pass
    print(json.dumps({"private_blob_container": True}), flush=True)
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="tenant_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="doc_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="chunk_id", type=SearchFieldDataType.String),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SearchableField(name="text", type=SearchFieldDataType.String),
        SimpleField(name="source_type", type=SearchFieldDataType.String),
        SimpleField(name="source", type=SearchFieldDataType.String),
        SimpleField(name="page", type=SearchFieldDataType.Int32),
        SimpleField(name="metadata_json", type=SearchFieldDataType.String),
        SearchField(
            name="vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=int(config["AZURE_EMBEDDING_DIMENSIONS"]),
            vector_search_profile_name="knowledge-vector",
        ),
    ]
    index = SearchIndex(
        name=config["AZURE_SEARCH_INDEX"],
        fields=fields,
        vector_search=VectorSearch(
            algorithms=[
                HnswAlgorithmConfiguration(name="hnsw-cosine", parameters={"metric": "cosine"})
            ],
            profiles=[
                VectorSearchProfile(
                    name="knowledge-vector", algorithm_configuration_name="hnsw-cosine"
                )
            ],
        ),
        semantic_search=SemanticSearch(
            configurations=[
                SemanticConfiguration(
                    name="grounded",
                    prioritized_fields=SemanticPrioritizedFields(
                        title_field=SemanticField(field_name="title"),
                        content_fields=[SemanticField(field_name="text")],
                    ),
                )
            ]
        ),
    )
    with SearchIndexClient(
        config["AZURE_SEARCH_ENDPOINT"], credential=credential, retry_total=0
    ) as search:
        search.create_or_update_index(index)
    print(
        json.dumps(
            {"hybrid_semantic_index": True, "dimensions": int(config["AZURE_EMBEDDING_DIMENSIONS"])}
        ),
        flush=True,
    )
    signing = get_settings().voice_session_signing_key
    if not signing:
        raise RuntimeError("Existing persistent signing key is required")
    with SecretClient(config["AZURE_KEY_VAULT_URL"], credential=credential, retry_total=0) as vault:
        vault.set_secret("deskmate-session-signing-key", signing.get_secret_value())
    credential.close()
    print(json.dumps({"persistent_signing_key_stored": True}), flush=True)


if __name__ == "__main__":
    main()
