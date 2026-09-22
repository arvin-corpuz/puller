from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from puller.registries.auth_ecr import EcrAuthProvider
from puller.registries.base import AuthError


def _fake_boto_client(token: str = "QVdTOnRlc3Q=", expires_in_seconds: int = 3600) -> MagicMock:
    client = MagicMock()
    client.get_authorization_token.return_value = {
        "authorizationData": [
            {
                "authorizationToken": token,
                "expiresAt": datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds),
            }
        ]
    }
    return client


@pytest.mark.asyncio
async def test_get_authorization_header_fetches_and_caches_token(monkeypatch) -> None:
    fake_client = _fake_boto_client()
    monkeypatch.setattr(
        "puller.registries.auth_ecr.boto3.client", lambda service, **kwargs: fake_client
    )

    provider = EcrAuthProvider(region="us-east-1")
    header1 = await provider.get_authorization_header(http=None, method="GET", url="https://x")
    header2 = await provider.get_authorization_header(http=None, method="GET", url="https://x")

    assert header1 == "Basic QVdTOnRlc3Q="
    assert header2 == header1
    assert fake_client.get_authorization_token.call_count == 1  # cached, not refetched


@pytest.mark.asyncio
async def test_refreshes_when_token_near_expiry(monkeypatch) -> None:
    fake_client = _fake_boto_client(expires_in_seconds=30)  # inside the refresh margin, floors to 10s
    monkeypatch.setattr(
        "puller.registries.auth_ecr.boto3.client", lambda service, **kwargs: fake_client
    )
    clock = {"t": 1000.0}
    monkeypatch.setattr("puller.registries.auth_ecr.time.monotonic", lambda: clock["t"])

    provider = EcrAuthProvider(region="us-east-1")
    await provider.get_authorization_header(http=None, method="GET", url="https://x")

    clock["t"] += 11  # advance past the cached token's (floored) 10s expiry
    await provider.get_authorization_header(http=None, method="GET", url="https://x")

    assert fake_client.get_authorization_token.call_count == 2


@pytest.mark.asyncio
async def test_on_unauthorized_clears_cache_forcing_refetch(monkeypatch) -> None:
    fake_client = _fake_boto_client()
    monkeypatch.setattr(
        "puller.registries.auth_ecr.boto3.client", lambda service, **kwargs: fake_client
    )

    provider = EcrAuthProvider(region="us-east-1")
    await provider.get_authorization_header(http=None, method="GET", url="https://x")
    await provider.on_unauthorized()
    await provider.get_authorization_header(http=None, method="GET", url="https://x")

    assert fake_client.get_authorization_token.call_count == 2


@pytest.mark.asyncio
async def test_boto_error_raises_auth_error(monkeypatch) -> None:
    fake_client = MagicMock()
    fake_client.get_authorization_token.side_effect = RuntimeError("no credentials")
    monkeypatch.setattr(
        "puller.registries.auth_ecr.boto3.client", lambda service, **kwargs: fake_client
    )

    provider = EcrAuthProvider(region="us-east-1")
    with pytest.raises(AuthError):
        await provider.get_authorization_header(http=None, method="GET", url="https://x")
