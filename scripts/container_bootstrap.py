"""Start a pinned private source bundle using the host's managed identity.

Only Python's standard library is needed in the public Python base image. The
bundle contains reviewed Linux wheels, so startup performs no pip installation.
Never log identity responses, HTTP errors, environment values or document data.
"""

import hashlib
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from zipfile import ZipFile

MAX_COMPRESSED = 150 * 1024 * 1024
MAX_EXPANDED = 450 * 1024 * 1024


def unpack(content: bytes, destination: Path, expected_hash: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
        raise ValueError("Invalid bundle digest")
    if len(content) > MAX_COMPRESSED or hashlib.sha256(content).hexdigest() != expected_hash:
        raise ValueError("Bundle integrity check failed")
    with ZipFile(io.BytesIO(content)) as archive:
        members = archive.infolist()
        if len(members) > 15000 or sum(item.file_size for item in members) > MAX_EXPANDED:
            raise ValueError("Bundle exceeds extraction limits")
        for item in members:
            path = PurePosixPath(item.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in item.filename:
                raise ValueError("Unsafe bundle path")
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Bundle links are prohibited")
            if not item.is_dir():
                target = destination.joinpath(*path.parts)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                target.write_bytes(archive.read(item))
                target.chmod(0o600)


def download_bundle() -> bytes:
    url = os.environ["DESKMATE_BUNDLE_URL"]
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".blob.core.windows.net")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
        or not re.fullmatch(r"/documents/deployments/[a-f0-9]{64}\.zip", parsed.path)
    ):
        raise ValueError("Invalid private bundle location")
    endpoint = os.environ["IDENTITY_ENDPOINT"]
    identity_url = urlsplit(endpoint)
    if identity_url.scheme != "http" or identity_url.hostname not in {
        "127.0.0.1",
        "localhost",
        "169.254.255.2",
    }:
        raise ValueError("Invalid managed identity endpoint")
    parameters = urlencode(
        {
            "resource": "https://storage.azure.com/",
            "api-version": "2019-08-01",
            "client_id": os.environ["AZURE_CLIENT_ID"],
        }
    )
    request = Request(  # noqa: S310 - Host-supplied, validated local managed identity URL.
        endpoint + ("&" if "?" in endpoint else "?") + parameters,
        headers={"X-IDENTITY-HEADER": os.environ["IDENTITY_HEADER"]},
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - Validated local endpoint.
        token = json.loads(response.read(65536))["access_token"]
    request = Request(  # noqa: S310 - Validated Microsoft Storage HTTPS origin, no SAS.
        url,
        headers={"Authorization": "Bearer " + token, "x-ms-version": "2023-11-03"},
    )
    with urlopen(request, timeout=90) as response:  # noqa: S310 - Validated HTTPS origin.
        return response.read(MAX_COMPRESSED + 1)


def main() -> None:
    os.umask(0o077)
    try:
        directory = Path(tempfile.mkdtemp(prefix="deskmate-runtime-"))
        unpack(download_bundle(), directory, os.environ["DESKMATE_BUNDLE_SHA256"])
        libraries = directory / ".python_packages" / "lib" / "site-packages"
        if not (directory / "backend/app/main.py").is_file() or not libraries.is_dir():
            raise ValueError("Incomplete bundle")
        os.environ["PYTHONPATH"] = os.pathsep.join([str(directory), str(libraries)])
        os.chdir(directory)
        if os.environ.get("DESKMATE_STARTUP_DIAGNOSTIC") == "true":
            os.execv(sys.executable, [sys.executable, "-m", "backend.app.diagnostics.host_startup"])  # noqa: S606 - Fixed diagnostic module, no shell.
        os.execv(  # noqa: S606 - Fixed module invocation, no shell or user arguments.
            sys.executable,
            [
                sys.executable,
                "-m",
                "uvicorn",
                "backend.app.main:app",
                "--host",
                "0.0.0.0",  # noqa: S104 - Container ingress.
                "--port",
                "8000",
                "--no-access-log",
            ],
        )
    except Exception:  # noqa: BLE001 - Suppress credential-bearing SDK/HTTP failure details.
        print("DeskMate deployment startup failed; consult protected diagnostics.", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
