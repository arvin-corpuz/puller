from __future__ import annotations

import base64

import httpx


class StaticBasicAuth:
    """Always sends a fixed Authorization header, no 401 challenge round-trip.

    Suitable for registries (e.g. some Harbor robot-account setups, or ECR
    once we already have the short-lived basic-auth token) that accept Basic
    auth directly without a WWW-Authenticate/token dance.
    """

    def __init__(self, username: str, password: str) -> None:
        credentials = f"{username}:{password}".encode("utf-8")
        self._header = f"Basic {base64.b64encode(credentials).decode('ascii')}"

    async def get_authorization_header(
        self, http: httpx.AsyncClient, method: str, url: str
    ) -> str | None:
        return self._header

    async def on_unauthorized(self) -> None:
        # Static credentials can't be refreshed from here; the caller must
        # reconstruct this provider with new credentials (see EcrAuthProvider
        # for the refreshable case).
        pass
