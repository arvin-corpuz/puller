from __future__ import annotations

import pytest

from puller.rules.base import RuleError
from puller.rules.semver_rule import SemverRule


class FakeClient:
    def __init__(self, tags: list[str]) -> None:
        self._tags = tags

    async def list_tags(self, repository: str) -> list[str]:
        return self._tags

    async def resolve_digest(self, repository: str, reference: str) -> str:
        return f"sha256:{reference}"


@pytest.mark.asyncio
async def test_picks_highest_semver_ignoring_junk_tags() -> None:
    client = FakeClient(["1.0.0", "1.2.0", "1.10.0", "latest", "production", "sha-abc123"])
    rule = SemverRule()

    ref = await rule.resolve(client, "myorg/myapp")

    assert ref.tag == "1.10.0"
    assert ref.digest == "sha256:1.10.0"


@pytest.mark.asyncio
async def test_strips_v_prefix() -> None:
    client = FakeClient(["v1.0.0", "v2.3.1", "not-a-version"])
    rule = SemverRule(prefix="v")

    ref = await rule.resolve(client, "myorg/myapp")

    assert ref.tag == "v2.3.1"
    assert ref.metadata["version"] == "2.3.1"


@pytest.mark.asyncio
async def test_prefix_excludes_non_prefixed_tags() -> None:
    client = FakeClient(["v1.0.0", "2.0.0"])
    rule = SemverRule(prefix="v")

    ref = await rule.resolve(client, "myorg/myapp")

    assert ref.tag == "v1.0.0"


@pytest.mark.asyncio
async def test_excludes_prerelease_by_default() -> None:
    client = FakeClient(["1.0.0", "2.0.0-rc1"])
    rule = SemverRule()

    ref = await rule.resolve(client, "myorg/myapp")

    assert ref.tag == "1.0.0"


@pytest.mark.asyncio
async def test_includes_prerelease_when_enabled() -> None:
    client = FakeClient(["1.0.0", "2.0.0-rc1"])
    rule = SemverRule(include_prerelease=True)

    ref = await rule.resolve(client, "myorg/myapp")

    assert ref.tag == "2.0.0-rc1"


@pytest.mark.asyncio
async def test_pattern_filters_candidates() -> None:
    client = FakeClient(["app-1.0.0", "1.5.0", "app-2.0.0"])
    rule = SemverRule(prefix="app-", pattern=r"^app-")

    ref = await rule.resolve(client, "myorg/myapp")

    assert ref.tag == "app-2.0.0"


@pytest.mark.asyncio
async def test_raises_when_no_valid_semver_tags() -> None:
    client = FakeClient(["latest", "production", "sha-abc123"])
    rule = SemverRule()

    with pytest.raises(RuleError):
        await rule.resolve(client, "myorg/myapp")
