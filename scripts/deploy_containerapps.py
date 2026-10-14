"""Deploy the reviewed pilot on Consumption without a registry or API keys.

Requires the existing free/low-cost foundation, Linux bundle and runtime identity.
All CLI output and configuration stay in the private review directory. This is a
bounded pilot, not an approval for continuous high traffic or paid hosting SKUs.
"""

import argparse
import base64
import hashlib
import json
import os
import time
from pathlib import Path

from package_azure import revision_suffix
from provision_azure import GROUP, PREFIX, STORAGE, SUBSCRIPTION, run

NAME = PREFIX + "-service"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-output", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--image", required=True, help="Reviewed Python 3.12 Linux image digest")
    parser.add_argument("--stage", choices=["roles", "deploy"], required=True)
    parser.add_argument("--startup-diagnostic", action="store_true")
    parser.add_argument(
        "--activate-demo", action="store_true",
        help="Explicitly deploy the authorized finite demo policy",
    )
    args = parser.parse_args()
    if "@sha256:" not in args.image:
        raise ValueError("Pin the reviewed base image by digest")
    os.umask(0o077)
    directory = args.private_output
    identity = json.loads((directory / "containerapp-identity.private.json").read_text())
    environment = json.loads((directory / "containerapp-environment.private.json").read_text())
    resource_prefix = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/"
    cosmos_scope = resource_prefix + "Microsoft.DocumentDB/databaseAccounts/" + PREFIX + "-cosmos"
    if args.stage == "roles":
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
                    identity["principalId"],
                    "--assignee-principal-type",
                    "ServicePrincipal",
                    "--role",
                    role,
                    "--scope",
                    resource_prefix + resource,
                ],
                directory,
                "container-runtime-role-" + str(index),
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
                PREFIX + "-cosmos",
                "--role-definition-id",
                "00000000-0000-0000-0000-000000000002",
                "--principal-id",
                identity["principalId"],
                "--scope",
                cosmos_scope + "/dbs/deskmate/colls/state",
            ],
            directory,
            "container-runtime-cosmos-role",
        )
        return

    from azure.identity import AzureCliCredential
    from azure.storage.blob import BlobServiceClient, ContentSettings

    digest = hashlib.sha256(args.bundle.read_bytes()).hexdigest()
    blob_name = "deployments/" + digest + ".zip"
    storage_url = f"https://{STORAGE}.blob.core.windows.net"
    with (
        AzureCliCredential() as credential,
        BlobServiceClient(storage_url, credential=credential, retry_total=0) as storage,
    ):
        blob = storage.get_blob_client("documents", blob_name)
        if not blob.exists():
            with args.bundle.open("rb") as source:
                blob.upload_blob(
                    source,
                    overwrite=False,
                    content_settings=ContentSettings(content_type="application/zip"),
                    metadata={"sha256": digest},
                )
        elif blob.get_blob_properties().metadata.get("sha256") != digest:
            raise ValueError("Deployment bundle integrity mismatch")

    fqdn = NAME + "." + environment["properties"]["defaultDomain"]
    origin = "https://" + fqdn
    config = json.loads((directory / "configuration/host-settings.private.json").read_text())
    for key in [
        "SCM_DO_BUILD_DURING_DEPLOYMENT",
        "WEBSITES_CONTAINER_START_TIME_LIMIT",
        "VOICE_SESSION_SIGNING_KEY",
    ]:
        config.pop(key, None)
    config.update(
        {
            "ALLOWED_ORIGINS": origin,
            "AZURE_CLIENT_ID": identity["clientId"],
            "DESKMATE_BUNDLE_URL": storage_url + "/documents/" + blob_name,
            "DESKMATE_BUNDLE_SHA256": digest,
            "CASE_DB_PATH": "/opt/deskmate-inactive-local/cases.db",
            "CHROMA_DIR": "/opt/deskmate-inactive-local/chroma",
            "AZURE_USAGE_GUARD_ENABLED": "true",
            "AZURE_EMERGENCY_DISABLE": "true",
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "DESKMATE_STARTUP_DIAGNOSTIC": str(args.startup_diagnostic).lower(),
        }
    )
    if args.activate_demo:
        allowance = float(config.get("AZURE_ESTIMATED_MONTHLY_APP_LIMIT_CAD", "8"))
        if not 0 < allowance <= 250:
            raise ValueError("Demo deployment requires a finite application allowance")
        config["AZURE_EMERGENCY_DISABLE"] = "false"
        config["AZURE_LIVE_TEST_MODE"] = "false"
        config["AZURE_SETUP_VALIDATION_MODE"] = "false"
    bootstrap = base64.b64encode(
        (Path(__file__).parent / "container_bootstrap.py").read_bytes()
    ).decode()
    specification = {
        "location": "eastus2",
        "name": NAME,
        "type": "Microsoft.App/containerApps",
        "identity": {"type": "UserAssigned", "userAssignedIdentities": {identity["id"]: {}}},
        "properties": {
            "managedEnvironmentId": environment["id"],
            "configuration": {
                "activeRevisionsMode": "Single",
                "ingress": {
                    "external": True,
                    "targetPort": 8000,
                    "transport": "auto",
                    "allowInsecure": False,
                },
                "secrets": [
                    {
                        "name": "session-signing-key",
                        "identity": identity["id"],
                        "keyVaultUrl": f"https://{PREFIX}-kv.vault.azure.net/secrets/deskmate-session-signing-key",
                    }
                ],
            },
            "template": {
                "containers": [
                    {
                        "name": "deskmate",
                        "image": args.image,
                        "command": ["python", "-c"],
                        "args": [
                            "import base64;exec(compile(base64.b64decode('"
                            + bootstrap
                            + "'),'bootstrap','exec'))"
                        ],
                        "env": [{"name": key, "value": value} for key, value in config.items()]
                        + [
                            {
                                "name": "VOICE_SESSION_SIGNING_KEY",
                                "secretRef": "session-signing-key",
                            }
                        ],
                        "resources": {"cpu": 0.5, "memory": "1Gi"},
                        "probes": [
                            {
                                "type": "Startup",
                                "httpGet": {"path": "/health", "port": 8000},
                                "initialDelaySeconds": 5,
                                "periodSeconds": 5,
                                "failureThreshold": 60,
                            },
                            {
                                "type": "Readiness",
                                "httpGet": {"path": "/health", "port": 8000},
                                "periodSeconds": 10,
                                "failureThreshold": 3,
                            },
                        ],
                    }
                ],
                "scale": {
                    "minReplicas": 0,
                    "maxReplicas": 1,
                    "pollingInterval": 30,
                    "cooldownPeriod": 300,
                    "rules": [{"name": "http", "http": {"metadata": {"concurrentRequests": "10"}}}],
                },
            },
        },
    }
    specification["properties"]["template"]["revisionSuffix"] = revision_suffix(specification)
    file = directory / "containerapp-specification.private.json"
    file.write_text(json.dumps(specification))
    # The CLI's YAML conversion rewrites underscores inside identity map keys,
    # corrupting the existing resource group's ARM ID. Raw ARM JSON preserves it.
    resource_url = (
        f"https://management.azure.com/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}"
        f"/providers/Microsoft.App/containerApps/{NAME}?api-version=2025-07-01"
    )
    run(
        ["rest", "--method", "PUT", "--url", resource_url, "--body", "@" + str(file)],
        directory,
        "containerapp-create",
    )
    for _ in range(60):
        host = run(["rest", "--method", "GET", "--url", resource_url], directory, "containerapp")
        state = host["properties"].get("provisioningState")
        if state == "Succeeded":
            break
        if state in {"Failed", "Canceled"}:
            raise RuntimeError("Container host provisioning failed; inspect private diagnostics")
        time.sleep(5)
    else:
        raise RuntimeError("Container host provisioning timed out; inspect private diagnostics")
    app = json.loads((directory / "configuration/entra-application.private.json").read_text())
    current = run(
        ["ad", "app", "show", "--id", app["appId"], "--query", "spa"],
        directory,
        "container-entra-spa",
    )
    redirects = sorted({*current.get("redirectUris", []), origin + "/"})
    patch = directory / "container-entra-redirect.private.json"
    patch.write_text(json.dumps({"spa": {"redirectUris": redirects}}))
    run(
        [
            "rest",
            "--method",
            "PATCH",
            "--url",
            "https://graph.microsoft.com/v1.0/applications/" + app["id"],
            "--body",
            "@" + str(patch),
        ],
        directory,
        "container-entra-redirect",
    )
    print(
        json.dumps(
            {
                "endpoint": origin,
                "emergency_disabled": not args.activate_demo,
                "min_replicas": 0,
                "max_replicas": 1,
                "bundle_sha256": digest,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
