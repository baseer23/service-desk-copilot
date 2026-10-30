"""Process-start test safety hook; inert outside the explicit offline profile."""

import os

if os.getenv("DESKMATE_TEST_PROFILE") == "offline":
    from backend.app.testing.network_guard import install_offline_network_guard

    install_offline_network_guard()
