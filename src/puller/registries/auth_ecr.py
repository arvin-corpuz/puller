from __future__ import annotations

import asyncio
import time

import boto3
import httpx

from puller.registries.base import AuthError

_REFRESH_MARGIN_SECONDS = 60


class EcrAuthProvider:
    """Amazon ECR auth: exchanges AWS credentials for a short-lived (~12h)
    HTTP Basic token via ecr:GetAuthorizationToken. ECR accepts this Basic
    token directly against its registry host, with no WWW-Authenticate
    challenge round-trip, so this plugs straight into DockerV2Client.

    Falls back to the default botocore credential chain (env vars, shared
    config, instance profile, or IRSA in EKS) when no static keys are given.
    """

    def __init__(
        self,
        region: str,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        session_token: str | None = None,
    ) -> None:
        self._region = region
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._session_token = session_token
        self._header: str | None = None
        self._expiry_monotonic: float = 0.0
        self._lock = asyncio.Lock()

    def _build_client(self):
        kwargs: dict[str, str] = {"region_name": self._region}
        if self._access_key_id and self._secret_access_key:
            kwargs["aws_access_key_id"] = self._access_key_id
            kwargs["aws_secret_access_key"] = self._secret_access_key
            if self._session_token:
                kwargs["aws_session_token"] = self._session_token
        return boto3.client("ecr", **kwargs)

    def _fetch_token_sync(self) -> tuple[str, float]:
        client = self._build_client()
        try:
            resp = client.get_authorization_token()
        except Exception as exc:  # noqa: BLE001 - surface any botocore error uniformly
            raise AuthError(f"ecr:GetAuthorizationToken failed: {exc}") from exc

        authorization_data = resp.get("authorizationData") or []
        if not authorization_data:
            raise AuthError("ECR returned no authorizationData")

        entry = authorization_data[0]
        token = entry["authorizationToken"]
        expires_at = entry["expiresAt"].timestamp()
        seconds_until_expiry = max(expires_at - time.time() - _REFRESH_MARGIN_SECONDS, 10)
        return token, time.monotonic() + seconds_until_expiry

    async def get_authorization_header(
        self, http: httpx.AsyncClient, method: str, url: str
    ) -> str | None:
        if self._header and time.monotonic() < self._expiry_monotonic:
            return self._header

        async with self._lock:
            if self._header and time.monotonic() < self._expiry_monotonic:
                return self._header
            token, expiry = await asyncio.to_thread(self._fetch_token_sync)
            self._header = f"Basic {token}"
            self._expiry_monotonic = expiry
            return self._header

    async def on_unauthorized(self) -> None:
        async with self._lock:
            self._header = None
            self._expiry_monotonic = 0.0
