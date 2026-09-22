from __future__ import annotations

from puller.registries.docker_v2 import DockerV2Client
from puller.rules.base import ResolvedRef


class TagRule:
    """Resolves the digest currently pointed to by a fixed/floating tag
    (e.g. 'production'). Also backs the 'latest' rule, which is just this
    rule pinned to tag='latest'."""

    def __init__(self, tag: str) -> None:
        self._tag = tag

    async def resolve(self, client: DockerV2Client, repository: str) -> ResolvedRef:
        digest = await client.resolve_digest(repository, self._tag)
        return ResolvedRef(tag=self._tag, digest=digest)
