"""Configure the reviewed foundation, managed identity and secret-free host settings.

This creates no API keys or client secrets. CLI responses stay in a private directory.
Run only after the free-tier foundation has been verified.
"""

import argparse
import json
import os
from pathlib import Path

from provision_azure import GROUP, PREFIX, STORAGE, SUBSCRIPTION, run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-output", type=Path, required=True)
    parser.add_argument(
        "--stage", choices=["database", "hosting", "identity", "data"], required=True
    )
    args = parser.parse_args()
    os.umask(0o077)
    directory = args.private_output
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    account = PREFIX + "-cosmos"
    webapp = PREFIX + "-app"
    account_id = (
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/"
        f"Microsoft.DocumentDB/databaseAccounts/{account}"
    )
    if args.stage == "database":
        run(
            [
                "cosmosdb",
                "sql",
                "database",
                "create",
                "-g",
                GROUP,
                "-a",
                account,
                "-n",
                "deskmate",
                "--throughput",
                "1000",
            ],
            directory,
            "database",
        )
        indexing = {
            "indexingMode": "consistent",
            "automatic": True,
            "includedPaths": [{"path": "/*"}],
            "excludedPaths": [{"path": "/payload/*"}],
        }
        policy = directory / "indexing.private.json"
        policy.write_text(json.dumps(indexing))
        run(
            [
                "cosmosdb",
                "sql",
                "container",
                "create",
                "-g",
                GROUP,
                "-a",
                account,
                "--database-name",
                "deskmate",
                "-n",
                "state",
                "--partition-key-path",
                "/tenant_id",
                "--idx",
                "@" + str(policy),
            ],
            directory,
            "container",
        )
        user = run(["ad", "signed-in-user", "show", "--query", "id"], directory, "operator-id")
        run(
            [
                "cosmosdb",
                "sql",
                "role",
                "assignment",
                "create",
                "-g",
                GROUP,
                "-a",
                account,
                "--role-definition-id",
                "00000000-0000-0000-0000-000000000002",
                "--principal-id",
                user,
                "--scope",
                account_id + "/dbs/deskmate/colls/state",
            ],
            directory,
            "operator-cosmos-role",
        )
    elif args.stage == "hosting":
        run(
            [
                "webapp",
                "create",
                "-g",
                GROUP,
                "-n",
                webapp,
                "--plan",
                PREFIX + "-free-plan",
                "--runtime",
                "PYTHON:3.12",
                "--assign-identity",
                "[system]",
                "--https-only",
                "true",
                "--basic-auth",
                "Disabled",
            ],
            directory,
            "webapp",
        )
        run(
            [
                "webapp",
                "config",
                "set",
                "-g",
                GROUP,
                "-n",
                webapp,
                "--min-tls-version",
                "1.2",
                "--ftps-state",
                "Disabled",
                "--http20-enabled",
                "true",
                "--web-sockets-enabled",
                "true",
                "--startup-file",
                "python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --no-access-log",
            ],
            directory,
            "webapp-config",
        )
    elif args.stage == "identity":
        host = json.loads((directory / "webapp.private.json").read_text())
        host_url = "https://" + host["defaultHostName"]
        operator = json.loads((directory / "operator-id.private.json").read_text())
        application_file = directory / "entra-application.private.json"
        app = (
            json.loads(application_file.read_text())
            if application_file.exists()
            else run(
                [
                    "ad",
                    "app",
                    "create",
                    "--display-name",
                    "DeskMate private service desk pilot",
                    "--sign-in-audience",
                    "AzureADMyOrg",
                ],
                directory,
                "entra-application",
            )
        )
        import uuid

        current = run(
            ["ad", "app", "show", "--id", app["appId"], "--query", "api"],
            directory,
            "entra-current-api",
        )
        scopes = current.get("oauth2PermissionScopes", [])
        scope_id = next(
            (scope["id"] for scope in scopes if scope["value"] == "DeskMate.Access"),
            str(uuid.uuid4()),
        )
        patch = {
            "identifierUris": ["api://" + app["appId"]],
            "spa": {"redirectUris": [host_url + "/"]},
            "api": {
                "requestedAccessTokenVersion": 2,
                "oauth2PermissionScopes": [
                    {
                        "id": scope_id,
                        "value": "DeskMate.Access",
                        "type": "User",
                        "isEnabled": True,
                        "adminConsentDisplayName": "Use DeskMate",
                        "adminConsentDescription": (
                            "Read and update investigations and knowledge "
                            "within the authorized tenant."
                        ),
                        "userConsentDisplayName": "Use DeskMate",
                        "userConsentDescription": "Access your tenant's service desk workspace.",
                    }
                ],
            },
        }
        patch["api"]["preAuthorizedApplications"] = [
            {"appId": "04b07795-8ddb-461a-bbee-02f9e1bf7b46", "delegatedPermissionIds": [scope_id]}
        ]
        file = directory / "entra-configuration.private.json"
        file.write_text(json.dumps(patch))
        run(
            [
                "rest",
                "--method",
                "PATCH",
                "--url",
                "https://graph.microsoft.com/v1.0/applications/" + app["id"],
                "--body",
                "@" + str(file),
            ],
            directory,
            "entra-configuration",
        )
        if not (directory / "entra-service-principal.private.json").exists():
            run(["ad", "sp", "create", "--id", app["appId"]], directory, "entra-service-principal")
        tenant = run(["account", "show", "--query", "tenantId"], directory, "tenant-id")
        operator = json.loads((directory / "operator-id.private.json").read_text())
        foundation = directory.parent / "foundation"
        cosmos = json.loads((foundation / "foundation-05.private.json").read_text())
        documents = json.loads((foundation / "foundation-09.private.json").read_text())
        from backend.app.core.config import get_settings

        existing = get_settings()
        config = {
            "DEPLOYMENT_MODE": "production",
            "DATA_PLATFORM": "azure",
            "TEST_MODE": "false",
            "ENABLE_LOCAL_FIXTURES": "false",
            "AUTH_CLIENT_ID": app["appId"],
            "AUTH_ALLOWED_TENANTS": tenant,
            "AUTH_OPERATOR_IDS": operator,
            "ALLOWED_ORIGINS": host_url,
            "AZURE_COSMOS_ENDPOINT": cosmos["documentEndpoint"],
            "AZURE_COSMOS_DATABASE": "deskmate",
            "AZURE_COSMOS_CONTAINER": "state",
            "AZURE_STORAGE_ACCOUNT_URL": f"https://{STORAGE}.blob.core.windows.net",
            "AZURE_STORAGE_CONTAINER": "documents",
            "AZURE_SEARCH_ENDPOINT": f"https://{PREFIX}-search.search.windows.net",
            "AZURE_SEARCH_INDEX": "knowledge",
            "AZURE_SEARCH_SEMANTIC_ENABLED": "true",
            "AZURE_DOCUMENT_ENDPOINT": documents["properties"]["endpoint"],
            "AZURE_DOCUMENT_FREE_TIER": "true",
            "AZURE_KEY_VAULT_URL": f"https://{PREFIX}-kv.vault.azure.net",
            "VOICE_SESSION_SIGNING_KEY": f"@Microsoft.KeyVault(SecretUri=https://{PREFIX}-kv.vault.azure.net/secrets/deskmate-session-signing-key/)",
            "GENERATION_PROVIDER": "azure_openai",
            "EMBED_PROVIDER": "azure_openai",
            "AZURE_USE_ENTRA_ID": "true",
            "AZURE_FOUNDRY_ENDPOINT": existing.azure_foundry_endpoint,
            "AZURE_MODEL_DEPLOYMENT": existing.azure_model_deployment,
            "AZURE_EMBEDDING_DEPLOYMENT": existing.azure_embedding_deployment,
            "AZURE_EMBEDDING_DIMENSIONS": str(existing.azure_embedding_dimensions),
            "AZURE_USAGE_GUARD_ENABLED": "true",
            "AZURE_EMERGENCY_DISABLE": "true",
            "AZURE_ESTIMATED_MONTHLY_APP_LIMIT_CAD": "8",
            "AZURE_VOICELIVE_ENDPOINT": existing.azure_voicelive_endpoint,
            "AZURE_VOICELIVE_MODEL": existing.azure_voicelive_model,
            "AZURE_VOICELIVE_API_VERSION": existing.azure_voicelive_api_version,
            "VOICE_PROVIDER": "azure_voicelive",
            "VOICE_ENABLED": "true",
            "VOICE_AUTH_MODE": "entra",
            "VOICE_AUTH_TENANT_ID": tenant,
            "VOICE_AUTH_AUDIENCE": app["appId"],
            "VOICE_TENANT_ID": tenant,
            "AZURE_REASONING_MIN_INTERVAL_SECONDS": "12",
            "AZURE_EMBEDDING_MIN_INTERVAL_SECONDS": "10",
            "ALLOW_URL_INGEST": "false",
            "SCM_DO_BUILD_DURING_DEPLOYMENT": "true",
            "WEBSITES_CONTAINER_START_TIME_LIMIT": "600",
            "CASE_DB_PATH": "/home/site/deskmate-inactive-local/cases.db",
            "CHROMA_DIR": "/home/site/deskmate-inactive-local/chroma",
        }
        (directory / "host-settings.private.json").write_text(json.dumps(config))
        (directory / "host-summary.json").write_text(
            json.dumps(
                {
                    "endpoint": host_url,
                    "client_id": app["appId"],
                    "tenant": tenant,
                    "scope_id": scope_id,
                    "managed_identity": host["identity"]["principalId"],
                }
            )
        )
    elif args.stage == "data":
        host = json.loads((directory / "webapp.private.json").read_text())
        operator = json.loads((directory / "operator-id.private.json").read_text())
        identity = host["identity"]["principalId"]
        resource_prefix = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/"
        roles = [
            ("Microsoft.Storage/storageAccounts/" + STORAGE, "Storage Blob Data Contributor"),
            (
                "Microsoft.Search/searchServices/" + PREFIX + "-search",
                "Search Index Data Contributor",
            ),
            ("Microsoft.KeyVault/vaults/" + PREFIX + "-kv", "Key Vault Secrets User"),
            ("Microsoft.CognitiveServices/accounts/" + PREFIX + "-docs", "Cognitive Services User"),
            (
                "Microsoft.CognitiveServices/accounts/k-abdulbaseer-newlife-resource",
                "Cognitive Services User",
            ),
        ]
        for index, (resource, role) in enumerate(roles):
            run(
                [
                    "role",
                    "assignment",
                    "create",
                    "--assignee-object-id",
                    identity,
                    "--assignee-principal-type",
                    "ServicePrincipal",
                    "--role",
                    role,
                    "--scope",
                    resource_prefix + resource,
                ],
                directory,
                "runtime-role-" + str(index),
            )
        for index, (resource, role) in enumerate(
            [
                (roles[0][0], roles[0][1]),
                (roles[1][0], "Search Service Contributor"),
                (roles[1][0], "Search Index Data Contributor"),
                (roles[2][0], "Key Vault Secrets Officer"),
            ]
        ):
            run(
                [
                    "role",
                    "assignment",
                    "create",
                    "--assignee-object-id",
                    operator,
                    "--assignee-principal-type",
                    "User",
                    "--role",
                    role,
                    "--scope",
                    resource_prefix + resource,
                ],
                directory,
                "operator-role-" + str(index),
            )
        run(
            [
                "cosmosdb",
                "sql",
                "role",
                "assignment",
                "create",
                "-g",
                GROUP,
                "-a",
                account,
                "--role-definition-id",
                "00000000-0000-0000-0000-000000000002",
                "--principal-id",
                identity,
                "--scope",
                account_id + "/dbs/deskmate/colls/state",
            ],
            directory,
            "runtime-cosmos-role",
        )
        run(
            [
                "resource",
                "update",
                "-g",
                GROUP,
                "-n",
                PREFIX + "-docs",
                "--resource-type",
                "Microsoft.CognitiveServices/accounts",
                "--api-version",
                "2023-05-01",
                "--set",
                "properties.disableLocalAuth=true",
            ],
            directory,
            "document-entra-only",
        )


if __name__ == "__main__":
    main()
