from __future__ import annotations

import asyncio

import httpx

from puller.config.schema import (
    BasicAuthConfig,
    DockerV2RegistryConfig,
    EcrRegistryConfig,
    GhcrRegistryConfig,
    HttpConfig,
    RegistryConfig,
)
from puller.config.secrets import resolve_secret
from puller.registries.auth_bearer import BearerChallengeAuth
from puller.registries.auth_ecr import EcrAuthProvider
from puller.registries.docker_v2 import DockerV2Client


class RegistryClientFactory:
    """Builds and caches one DockerV2Client (+ shared auth/token cache) per
    registry name, so multiple watchers pointing at the same registry share
    a single token cache and rate-limit semaphore rather than duplicating
    auth calls and connections."""

    def __init__(self, http_config: HttpConfig) -> None:
        self._http_config = http_config
        self._http: httpx.AsyncClient | None = None
        self._insecure_http: httpx.AsyncClient | None = None
        self._clients: dict[str, DockerV2Client] = {}

    async def __aenter__(self) -> "RegistryClientFactory":
        self._http = httpx.AsyncClient(timeout=self._http_config.timeout_seconds)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._http is not None:
            await self._http.aclose()
        if self._insecure_http is not None:
            await self._insecure_http.aclose()

    def _get_http_client(self, verify: bool) -> httpx.AsyncClient:
        if verify:
            assert self._http is not None, "factory must be used as an async context manager"
            return self._http
        if self._insecure_http is None:
            self._insecure_http = httpx.AsyncClient(
                timeout=self._http_config.timeout_seconds, verify=False
            )
        return self._insecure_http

    def _build_bearer_auth(self, auth_cfg: BasicAuthConfig | None) -> BearerChallengeAuth:
        if auth_cfg is None:
            return BearerChallengeAuth()
        return BearerChallengeAuth(
            resolve_secret(auth_cfg.username), resolve_secret(auth_cfg.password)
        )

    def _build_client(self, config: RegistryConfig) -> DockerV2Client:
        semaphore = asyncio.Semaphore(self._http_config.max_concurrent_requests_per_registry)

        if isinstance(config, DockerV2RegistryConfig):
            http = self._get_http_client(config.tls_verify)
            auth = self._build_bearer_auth(config.auth)
            return DockerV2Client(config.base_url, auth, http, semaphore)

        if isinstance(config, GhcrRegistryConfig):
            http = self._get_http_client(True)
            auth = self._build_bearer_auth(config.auth)
            return DockerV2Client(config.base_url, auth, http, semaphore)

        if isinstance(config, EcrRegistryConfig):
            http = self._get_http_client(True)
            base_url = f"https://{config.account_id}.dkr.ecr.{config.region}.amazonaws.com"
            auth = EcrAuthProvider(
                region=config.region,
                access_key_id=resolve_secret(config.access_key_id)
                if config.access_key_id
                else None,
                secret_access_key=resolve_secret(config.secret_access_key)
                if config.secret_access_key
                else None,
                session_token=resolve_secret(config.session_token)
                if config.session_token
                else None,
            )
            return DockerV2Client(base_url, auth, http, semaphore)

        raise TypeError(f"unsupported registry config type: {type(config)!r}")

    def get_client(self, config: RegistryConfig) -> DockerV2Client:
        if config.name not in self._clients:
            self._clients[config.name] = self._build_client(config)
        return self._clients[config.name]
