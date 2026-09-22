from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from puller.registries.docker_v2 import DockerV2Client


class RuleError(Exception):
    pass


@dataclass(frozen=True)
class ResolvedRef:
    tag: str
    digest: str
    metadata: dict[str, str] = field(default_factory=dict)


class Rule(Protocol):
    async def resolve(self, client: DockerV2Client, repository: str) -> ResolvedRef: ...
