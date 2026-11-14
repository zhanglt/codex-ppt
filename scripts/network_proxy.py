"""Retry a failed connection through the configured HTTP proxy."""

from __future__ import annotations

import errno
import socket
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener, urlopen


_CONNECT_ERRNOS = {
    errno.ECONNREFUSED, errno.ENETUNREACH, errno.EHOSTUNREACH,
    getattr(errno, "WSAECONNREFUSED", 10061),
    getattr(errno, "WSAENETUNREACH", 10051),
    getattr(errno, "WSAEHOSTUNREACH", 10065),
}


def validate_proxy(proxy: str) -> str:
    if not proxy:
        return ""
    parsed = urlparse(proxy)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("PROXY 须为 HTTP(S) 代理地址，例如 http://127.0.0.1:81")
    return proxy


def is_connect_error(exc: BaseException) -> bool:
    """Only retry failures known to occur before an HTTP request is accepted."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, HTTPError):
            return False
        if isinstance(exc, (ConnectionRefusedError, socket.gaierror)):
            return True
        if isinstance(exc, OSError) and exc.errno in _CONNECT_ERRNOS:
            return True
        if exc.__class__.__name__ in {"ConnectError", "ConnectTimeout"}:
            return True
        if isinstance(exc, URLError) and isinstance(exc.reason, BaseException):
            exc = exc.reason
        else:
            exc = exc.__cause__ or exc.__context__
    return False


def open_with_proxy_fallback(
    request: Request, *, timeout: int, proxy: str = "",
    direct_open: Callable[..., Any] = urlopen,
) -> Any:
    if not proxy:
        return direct_open(request, timeout=timeout)
    # Ignore machine-wide proxy variables so the first attempt is truly direct.
    opener = build_opener(ProxyHandler({})).open if direct_open is urlopen else direct_open
    try:
        return opener(request, timeout=timeout)
    except Exception as exc:
        if not is_connect_error(exc):
            raise
    return build_opener(ProxyHandler({"http": proxy, "https": proxy})).open(request, timeout=timeout)
