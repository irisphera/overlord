from __future__ import annotations

import base64
import socket
import time
from collections.abc import Mapping
from http.client import HTTPException
from urllib.request import Request, urlopen

from overlord_py.web_types import OPENCODE_WEB_LOG_FILE, OPENCODE_WEB_WAIT_SECONDS, WebServerError


def resolve_network_host_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("8.8.8.8", 80))
        except OSError:
            return ""
        ip = probe.getsockname()[0]
    if ip.startswith("127."):
        return ""
    return ip


def wait_for_opencode_web(host_port: str, *, password: str = "", wait_seconds: int = OPENCODE_WEB_WAIT_SECONDS) -> None:
    url = f"http://127.0.0.1:{host_port}/global/health"
    if not wait_for_http(url, password=password, contains=None, timeout=5, wait_seconds=wait_seconds):
        raise WebServerError(f"Error: OpenCode web UI did not become healthy at {url}\nCheck {OPENCODE_WEB_LOG_FILE} inside container for startup errors.")


def wait_for_opencode_web_ui(host_port: str, *, password: str = "", wait_seconds: int = OPENCODE_WEB_WAIT_SECONDS) -> None:
    url = f"http://127.0.0.1:{host_port}/"
    if not wait_for_http(url, password=password, contains="<!doctype html>", timeout=10, wait_seconds=wait_seconds):
        raise WebServerError(f"Error: OpenCode web UI root did not become ready at {url}\nCheck {OPENCODE_WEB_LOG_FILE} inside container for startup errors.")


def wait_for_http(url: str, *, password: str, contains: str | None, timeout: int, wait_seconds: int) -> bool:
    for _attempt in range(max(wait_seconds, 1)):
        request = Request(url, headers=dict(http_headers(password)))
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except (HTTPException, OSError):
            body = ""
        if contains is None and body != "":
            return True
        if contains is not None and contains in body:
            return True
        if wait_seconds > 1:
            time.sleep(1)
    return False


def http_headers(password: str) -> Mapping[str, str]:
    if not password:
        return {}
    token = base64.b64encode(f"opencode:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}
