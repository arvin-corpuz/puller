from __future__ import annotations

import asyncio
import hashlib
import re

import httpx

from puller.registries.base import (
    MANIFEST_ACCEPT_HEADER,
    AuthError,
    AuthProvider,
    NotFoundError,
    RateLimitedError,
    RegistryError,
)

_LINK_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


def _parse_next_link(link_header: str) -> str | None:
    match = _LINK_RE.search(link_header)
    return match.group(1) if match else None


class DockerV2Client:
    """HTTP client for the Docker Registry HTTP API V2 / OCI Distribution
    Spec, shared by ECR, Docker Hub, Harbor, and GHCR — they differ only in
    the AuthProvider plugged in (see registries/auth_*.py)."""

    def __init__(
        self,
        base_url: str,
        auth: AuthProvider,
        http: httpx.AsyncClient,
        semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._http = http
        self._semaphore = semaphore or asyncio.Semaphore(4)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        full_url: str | None = None,
    ) -> httpx.Response:
        url = full_url or f"{self._base_url}{path}"
        headers = dict(headers or {})

        async with self._semaphore:
            auth_header = await self._auth.get_authorization_header(self._http, method, url)
            if auth_header:
                headers["Authorization"] = auth_header

            resp = await self._http.request(method, url, headers=headers, params=params)

            if resp.status_code == 401:
                www_auth = resp.headers.get("WWW-Authenticate")
                handle_challenge = getattr(self._auth, "handle_challenge", None)
                if www_auth and handle_challenge is not None:
                    token = await handle_challenge(self._http, www_auth)
                    headers["Authorization"] = token
                    resp = await self._http.request(method, url, headers=headers, params=params)

                if resp.status_code == 401:
                    await self._auth.on_unauthorized()
                    raise AuthError(f"authentication failed for {method} {url}")

            if resp.status_code == 404:
                raise NotFoundError(f"not found: {method} {url}")

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                raise RateLimitedError(
                    f"rate limited: {method} {url}",
                    retry_after=float(retry_after) if retry_after else None,
                )

            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise RegistryError(f"{method} {url} failed: {exc}") from exc

            return resp

    async def list_tags(self, repository: str, max_tags: int = 10000) -> list[str]:
        tags: list[str] = []
        path: str | None = f"/v2/{repository}/tags/list"
        full_url: str | None = None
        params: dict[str, str] | None = {"n": "1000"}

        while path or full_url:
            resp = await self._request("GET", path or "", params=params, full_url=full_url)
            data = resp.json()
            tags.extend(data.get("tags") or [])
            params = None

            next_link = resp.headers.get("Link")
            if next_link and len(tags) < max_tags:
                next_path = _parse_next_link(next_link)
                if next_path is None:
                    break
                if next_path.startswith("http"):
                    full_url, path = next_path, None
                else:
                    path, full_url = next_path, None
            else:
                break

        return tags[:max_tags]

    async def resolve_digest(self, repository: str, reference: str) -> str:
        path = f"/v2/{repository}/manifests/{reference}"
        headers = {"Accept": MANIFEST_ACCEPT_HEADER}

        resp = await self._request("HEAD", path, headers=headers)
        digest = resp.headers.get("Docker-Content-Digest")
        if digest:
            return digest

        resp = await self._request("GET", path, headers=headers)
        digest = resp.headers.get("Docker-Content-Digest")
        if digest:
            return digest

        return "sha256:" + hashlib.sha256(resp.content).hexdigest()
