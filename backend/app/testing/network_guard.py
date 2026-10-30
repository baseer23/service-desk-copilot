"""Fail-closed outbound network guard for deterministic offline tests."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any

_installed = False
_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_original_create_connection = socket.create_connection


class OfflineNetworkError(RuntimeError):
    pass


def _allowed(address: Any) -> bool:
    if isinstance(address, str):  # AF_UNIX
        return True
    if not isinstance(address, tuple) or not address:
        return False
    host = str(address[0]).strip("[]").casefold()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _reject(address: Any) -> None:
    if not _allowed(address):
        host = address[0] if isinstance(address, tuple) and address else "unknown"
        raise OfflineNetworkError(
            f"Offline test profile blocked outbound connection to {host!s}. "
            "Use the explicit live profile only with authorization."
        )


def install_offline_network_guard() -> None:
    global _installed
    if _installed:
        return

    def connect(sock, address):
        _reject(address)
        return _original_connect(sock, address)

    def connect_ex(sock, address):
        _reject(address)
        return _original_connect_ex(sock, address)

    def create_connection(address, *args, **kwargs):
        _reject(address)
        return _original_create_connection(address, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.create_connection = create_connection
    _installed = True
