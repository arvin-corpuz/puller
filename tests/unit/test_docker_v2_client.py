from __future__ import annotations

import httpx
import pytest
import respx

from puller.registries.auth_bearer import BearerChallengeAuth
from puller.registries.auth_static import StaticBasicAuth
from puller.registries.base import NotFoundError, RateLimitedError
from puller.registries.docker_v2 import DockerV2Client

BASE_URL = "https://registry.example.com"


@pytest.mark.asyncio
@respx.mock
async def test_bearer_challenge_then_retry_succeeds() -> None:
    manifest_url = f"{BASE_URL}/v2/myorg/myapp/manifests/latest"
    challenge = (
        'Bearer realm="https://auth.example.com/token",'
        'service="registry.example.com",'
        'scope="repository:myorg/myapp:pull"'
    )

    manifest_route = respx.head(manifest_url)
    manifest_route.side_effect = [
        httpx.Response(401, headers={"WWW-Authenticate": challenge}),
        httpx.Response(200, headers={"Docker-Content-Digest": "sha256:abc123"}),
    ]
    token_route = respx.get(url__regex=r"https://auth\.example\.com/token.*").mock(
        return_value=httpx.Response(200, json={"token": "tok123", "expires_in": 300})
    )

    async with httpx.AsyncClient() as http:
        client = DockerV2Client(BASE_URL, BearerChallengeAuth(), http)
        digest = await client.resolve_digest("myorg/myapp", "latest")

    assert digest == "sha256:abc123"
    assert token_route.called
    assert manifest_route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_static_auth_sends_header_without_challenge() -> None:
    manifest_url = f"{BASE_URL}/v2/myorg/myapp/manifests/production"
    route = respx.head(manifest_url).mock(
        return_value=httpx.Response(200, headers={"Docker-Content-Digest": "sha256:def456"})
    )

    async with httpx.AsyncClient() as http:
        client = DockerV2Client(BASE_URL, StaticBasicAuth("robot", "token"), http)
        digest = await client.resolve_digest("myorg/myapp", "production")

    assert digest == "sha256:def456"
    sent_request = route.calls.last.request
    assert sent_request.headers["Authorization"].startswith("Basic ")


@pytest.mark.asyncio
@respx.mock
async def test_list_tags_follows_pagination_link() -> None:
    first_url = f"{BASE_URL}/v2/myorg/myapp/tags/list"
    second_url = f"{first_url}?n=1000&last=b"

    # Registered before the generic route: respx matches routes in order, so
    # the query-specific route must be checked first, falling through to the
    # generic (no-query) route only for the initial request.
    respx.get(first_url, params={"last": "b"}).mock(
        return_value=httpx.Response(200, json={"tags": ["c"]})
    )
    respx.get(first_url).mock(
        return_value=httpx.Response(
            200,
            json={"tags": ["a", "b"]},
            headers={"Link": f'<{second_url}>; rel="next"'},
        )
    )

    async with httpx.AsyncClient() as http:
        client = DockerV2Client(BASE_URL, StaticBasicAuth("u", "p"), http)
        tags = await client.list_tags("myorg/myapp")

    assert tags == ["a", "b", "c"]


@pytest.mark.asyncio
@respx.mock
async def test_404_raises_not_found_error() -> None:
    respx.head(f"{BASE_URL}/v2/myorg/missing/manifests/latest").mock(
        return_value=httpx.Response(404)
    )

    async with httpx.AsyncClient() as http:
        client = DockerV2Client(BASE_URL, StaticBasicAuth("u", "p"), http)
        with pytest.raises(NotFoundError):
            await client.resolve_digest("myorg/missing", "latest")


@pytest.mark.asyncio
@respx.mock
async def test_429_raises_rate_limited_error_with_retry_after() -> None:
    respx.get(f"{BASE_URL}/v2/myorg/myapp/tags/list").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "30"})
    )

    async with httpx.AsyncClient() as http:
        client = DockerV2Client(BASE_URL, StaticBasicAuth("u", "p"), http)
        with pytest.raises(RateLimitedError) as exc_info:
            await client.list_tags("myorg/myapp")

    assert exc_info.value.retry_after == 30.0
