import pytest
from backend.app.diagnostics.host_startup import artifact_url


def test_startup_artifacts_remain_private_and_reject_untrusted_origins():
    path = "/documents/deployments/" + "a" * 64 + ".zip"
    origin = "https://fictional.blob.core.windows.net"
    assert artifact_url(origin + path) == origin + path.removesuffix(".zip") + ".startup.txt"
    for bad in [
        "https://attacker.example" + path,
        origin + path + "?sig=secret",
        "https://user:secret@fictional.blob.core.windows.net" + path,
        origin + "/documents/customer.pdf",
        "http://fictional.blob.core.windows.net" + path,
    ]:
        with pytest.raises(ValueError, match="origin"):
            artifact_url(bad)
