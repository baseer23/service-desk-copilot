"""Keyless Azure AI authentication without Azure persistence dependencies."""

import logging
from backend.app.core.config import Settings


def azure_credential(settings: Settings):
    from azure.identity import AzureCliCredential, DefaultAzureCredential

    # SDK HTTP INFO diagnostics include resource identifiers and request metadata.
    # Application boundaries already report sanitized, actionable failure codes.
    logger = logging.getLogger("azure")
    if not any(isinstance(handler, logging.NullHandler) for handler in logger.handlers):
        logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return (
        AzureCliCredential(process_timeout=5)
        if settings.deployment_mode == "local"
        else DefaultAzureCredential(
            exclude_interactive_browser_credential=True,
            process_timeout=5,
            connection_timeout=5,
            read_timeout=5,
            retry_total=0,
        )
    )
