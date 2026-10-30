import socket

import pytest

from backend.app.testing.network_guard import OfflineNetworkError

pytestmark = [pytest.mark.security, pytest.mark.unit]


def test_offline_profile_blocks_external_provider_even_with_credentials(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "unused-test-credential")
    monkeypatch.setenv("GROQ_API_KEY", "unused-test-credential")
    with pytest.raises(OfflineNetworkError, match="Offline test profile blocked"):
        socket.create_connection(("api.openai.com", 443), timeout=0.01)


def test_offline_profile_allows_loopback():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    client = socket.create_connection(listener.getsockname(), timeout=1)
    server, _ = listener.accept()
    client.close()
    server.close()
    listener.close()
