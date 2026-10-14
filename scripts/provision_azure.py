"""Reviewed free-tier Azure foundation. No keys, connection strings or paid fallback."""

import argparse
import json
import subprocess
from pathlib import Path

SUBSCRIPTION = "f4d123f6-dc20-4e1f-9d46-09ae395415f8"
GROUP = "rg-k_abdulbaseer-0925"
REGION = "eastus2"
PREFIX = "deskmate-f4d123f6"
STORAGE = "dskf4d123f6store"


def commands():
    return [
        ["provider", "register", "--namespace", namespace]
        for namespace in (
            "Microsoft.DocumentDB",
            "Microsoft.Search",
            "Microsoft.Storage",
            "Microsoft.KeyVault",
            "Microsoft.Web",
        )
    ] + [
        [
            "cosmosdb",
            "create",
            "--name",
            PREFIX + "-cosmos",
            "--resource-group",
            GROUP,
            "--locations",
            "regionName=" + REGION,
            "failoverPriority=0",
            "isZoneRedundant=False",
            "--enable-free-tier",
            "true",
            "--disable-local-auth",
            "true",
            "--default-consistency-level",
            "Session",
        ],
        [
            "search",
            "service",
            "create",
            "--name",
            PREFIX + "-search",
            "--resource-group",
            GROUP,
            "--location",
            "centralus",
            "--sku",
            "free",
            "--disable-local-auth",
            "true",
            "--semantic-search",
            "free",
        ],
        [
            "storage",
            "account",
            "create",
            "--name",
            STORAGE,
            "--resource-group",
            GROUP,
            "--location",
            REGION,
            "--sku",
            "Standard_LRS",
            "--kind",
            "StorageV2",
            "--access-tier",
            "Hot",
            "--allow-blob-public-access",
            "false",
            "--allow-shared-key-access",
            "false",
            "--min-tls-version",
            "TLS1_2",
        ],
        [
            "keyvault",
            "create",
            "--name",
            PREFIX + "-kv",
            "--resource-group",
            GROUP,
            "--location",
            REGION,
            "--sku",
            "standard",
            "--enable-rbac-authorization",
            "true",
        ],
        [
            "cognitiveservices",
            "account",
            "create",
            "--name",
            PREFIX + "-docs",
            "--resource-group",
            GROUP,
            "--location",
            REGION,
            "--kind",
            "FormRecognizer",
            "--sku",
            "F0",
            "--custom-domain",
            PREFIX + "-docs",
            "--yes",
        ],
        [
            "appservice",
            "plan",
            "create",
            "--name",
            PREFIX + "-free-plan",
            "--resource-group",
            GROUP,
            "--location",
            REGION,
            "--is-linux",
            "--sku",
            "F1",
        ],
    ]


def run(command, directory, name):
    complete = [
        "az",
        *command,
        *([] if command[0] == "ad" else ["--subscription", SUBSCRIPTION]),
        "--only-show-errors",
        "--output",
        "json",
    ]
    result = subprocess.run(complete, capture_output=True, text=True, timeout=600)  # noqa: S603 - Fixed reviewed Azure command arguments; no shell.
    (directory / (name + ".private.error")).write_text(result.stderr)
    if result.returncode:
        raise RuntimeError("Azure command failed: " + name + "; no paid fallback executed")
    value = json.loads(result.stdout) if result.stdout.strip() else None
    (directory / (name + ".private.json")).write_text(json.dumps(value))
    print(json.dumps({"step": name, "succeeded": True}), flush=True)
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--apply-low-cost", action="store_true")
    parser.add_argument("--private-output", type=Path)
    parser.add_argument("--start-at", type=int, default=0)
    arguments = parser.parse_args()
    if arguments.plan or not arguments.apply_low_cost:
        print(
            json.dumps(
                {
                    "subscription": SUBSCRIPTION,
                    "group": GROUP,
                    "region": REGION,
                    "infrastructure_forecast_cad": 0.25,
                    "commands": [
                        ["az", *command, "--subscription", SUBSCRIPTION, "--output", "none"]
                        for command in commands()
                    ],
                },
                indent=2,
            )
        )
        return
    if not arguments.private_output:
        parser.error("A private output directory is required")
    directory = arguments.private_output
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    from backend.app.core.azure_usage import AzureUsageGuard
    from backend.app.core.config import get_settings

    guard = AzureUsageGuard(get_settings())
    guard.earmark_infrastructure("azure-native-foundation", 0.25)
    for index, command in enumerate(commands()):
        if index < arguments.start_at:
            continue
        run(command, directory, f"foundation-{index:02d}")


if __name__ == "__main__":
    main()
