from __future__ import annotations

from typing import Protocol

import httpx


class RegistryError(Exception):
    """Base error for registry interactions."""


class AuthError(RegistryError):
    """Authentication/authorization with the registry failed."""


class NotFoundError(RegistryError):
    """The requested repository, tag, or manifest does not exist."""


class RateLimitedError(RegistryError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AuthProvider(Protocol):
    async def get_authorization_header(
        self, http: httpx.AsyncClient, method: str, url: str
    ) -> str | None:
        """Return a value for the Authorization header, or None for anonymous access."""
        ...

    async def on_unauthorized(self) -> None:
        """Called when a request unexpectedly received a 401; invalidate any cached token."""
        ...


MANIFEST_ACCEPT_HEADER = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
