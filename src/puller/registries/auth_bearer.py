from __future__ import annotations

import asyncio
import re
import time

import httpx

from puller.registries.base import AuthError

_CHALLENGE_PARAM_RE = re.compile(r'(\w+)="([^"]*)"')


def parse_bearer_challenge(www_authenticate: str) -> dict[str, str]:
    if not www_authenticate.lower().startswith("bearer"):
        raise AuthError(f"unsupported WWW-Authenticate scheme: {www_authenticate!r}")
    return dict(_CHALLENGE_PARAM_RE.findall(www_authenticate))


class BearerChallengeAuth:
    """Docker Registry V2 token-auth flow (Docker Hub, Harbor, GHCR, etc.).

    We don't know the token realm/service/scope until the registry challenges
    us with a 401 + WWW-Authenticate header, so the first request per unique
    scope is always anonymous; the caller (DockerV2Client) is responsible for
    calling handle_challenge() on a 401 and retrying once.
    """

    def __init__(self, username: str | None = None, password: str | None = None) -> None:
        self._username = username
        self._password = password
        self._tokens: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def get_authorization_header(
        self, http: httpx.AsyncClient, method: str, url: str
    ) -> str | None:
        for token, expiry in self._tokens.values():
            if time.monotonic() < expiry:
                return f"Bearer {token}"
        return None

    async def handle_challenge(self, http: httpx.AsyncClient, www_authenticate: str) -> str:
        params = parse_bearer_challenge(www_authenticate)
        realm = params.get("realm")
        if not realm:
            raise AuthError(f"WWW-Authenticate challenge missing realm: {www_authenticate!r}")

        query = {k: v for k, v in params.items() if k != "realm"}
        cache_key = f"{realm}|{query.get('service', '')}|{query.get('scope', '')}"

        async with self._lock:
            cached = self._tokens.get(cache_key)
            if cached and time.monotonic() < cached[1]:
                return f"Bearer {cached[0]}"

            auth = (self._username, self._password) if self._username else None
            try:
                resp = await http.get(realm, params=query, auth=auth)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise AuthError(f"token request to {realm} failed: {exc}") from exc

            data = resp.json()
            token = data.get("token") or data.get("access_token")
            if not token:
                raise AuthError(f"token response from {realm} did not contain a token")
            expires_in = data.get("expires_in", 300)
            expiry = time.monotonic() + max(int(expires_in) - 30, 10)
            self._tokens[cache_key] = (token, expiry)
            return f"Bearer {token}"

    async def on_unauthorized(self) -> None:
        async with self._lock:
            self._tokens.clear()
