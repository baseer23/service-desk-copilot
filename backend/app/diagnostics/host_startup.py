"""Operator-only, bounded startup stack diagnostics in the private deployment Blob.

Opt-in through DESKMATE_STARTUP_DIAGNOSTIC=true. Python stacks contain file/function
names, never frame locals, headers or environment values. Publish at most three
small artifacts; normal hosting invokes uvicorn directly and never uses this module.
"""

import faulthandler
import json
import os
import tempfile
import threading
import time
from email.utils import formatdate
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


def artifact_url(bundle_url: str) -> str:
    import re

    parsed = urlsplit(bundle_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".blob.core.windows.net")
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"/documents/deployments/[a-f0-9]{64}\.zip", parsed.path)
    ):
        raise ValueError("Invalid diagnostic artifact origin")
    return bundle_url.removesuffix(".zip") + ".startup.txt"


def publish(path: Path) -> None:
    for _ in range(3):
        time.sleep(60)
        content = path.read_bytes()[:100_000]
        if not content:
            continue
        try:
            endpoint = os.environ["IDENTITY_ENDPOINT"]
            parsed = urlsplit(endpoint)
            if parsed.scheme != "http" or parsed.hostname not in {
                "localhost",
                "127.0.0.1",
                "169.254.255.2",
            }:
                return
            query = urlencode(
                {
                    "resource": "https://storage.azure.com/",
                    "api-version": "2019-08-01",
                    "client_id": os.environ["AZURE_CLIENT_ID"],
                }
            )
            request = Request(  # noqa: S310 - Validated local identity endpoint.
                endpoint + ("&" if "?" in endpoint else "?") + query,
                headers={"X-IDENTITY-HEADER": os.environ["IDENTITY_HEADER"]},
            )
            with urlopen(request, timeout=10) as response:  # noqa: S310 - Validated local endpoint.
                token = json.loads(response.read(65536))["access_token"]
            request = Request(  # noqa: S310 - Validated private Microsoft Blob HTTPS origin.
                artifact_url(os.environ["DESKMATE_BUNDLE_URL"]),
                data=content,
                method="PUT",
                headers={
                    "Authorization": "Bearer " + token,
                    "x-ms-blob-type": "BlockBlob",
                    "x-ms-version": "2023-11-03",
                    "x-ms-date": formatdate(usegmt=True),
                    "Content-Type": "text/plain",
                },
            )
            with urlopen(request, timeout=20):  # noqa: S310 - Validated HTTPS origin.
                pass
        except Exception:  # noqa: BLE001 - No credential-bearing HTTP errors in logs.
            print("Protected startup artifact unavailable", flush=True)


def main():
    if os.environ.get("DESKMATE_STARTUP_DIAGNOSTIC") != "true":
        raise SystemExit("Protected startup diagnostics require operator opt-in")
    import uvicorn

    os.umask(0o077)
    descriptor, filename = tempfile.mkstemp(prefix="deskmate-startup-", suffix=".trace")
    with os.fdopen(descriptor, "w") as trace:
        faulthandler.dump_traceback_later(45, repeat=True, file=trace)
        thread = threading.Thread(target=publish, args=(Path(filename),), daemon=True)
        thread.start()
        try:
            uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, access_log=False)  # noqa: S104 - Managed ingress.
        finally:
            faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    main()
