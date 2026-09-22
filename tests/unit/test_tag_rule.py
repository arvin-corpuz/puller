from __future__ import annotations

import pytest

from puller.config.schema import LatestRuleConfig, TagRuleConfig
from puller.rules import build_rule
from puller.rules.tag_rule import TagRule


class FakeClient:
    def __init__(self, digests: dict[str, str]) -> None:
        self._digests = digests

    async def resolve_digest(self, repository: str, reference: str) -> str:
        return self._digests[reference]


@pytest.mark.asyncio
async def test_tag_rule_resolves_fixed_tag() -> None:
    client = FakeClient({"production": "sha256:abc"})
    rule = TagRule(tag="production")

    ref = await rule.resolve(client, "myorg/myapp")

    assert ref.tag == "production"
    assert ref.digest == "sha256:abc"


@pytest.mark.asyncio
async def test_latest_rule_is_sugar_for_tag_rule_latest() -> None:
    client = FakeClient({"latest": "sha256:def"})
    rule = build_rule(LatestRuleConfig(type="latest"))

    assert isinstance(rule, TagRule)
    ref = await rule.resolve(client, "myorg/myapp")

    assert ref.tag == "latest"
    assert ref.digest == "sha256:def"


@pytest.mark.asyncio
async def test_build_rule_tag_config() -> None:
    client = FakeClient({"staging": "sha256:xyz"})
    rule = build_rule(TagRuleConfig(type="tag", tag="staging"))

    ref = await rule.resolve(client, "myorg/myapp")

    assert ref.tag == "staging"
    assert ref.digest == "sha256:xyz"
