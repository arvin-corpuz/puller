from __future__ import annotations

import re

import semver

from puller.logging_setup import get_logger
from puller.registries.docker_v2 import DockerV2Client
from puller.rules.base import ResolvedRef, RuleError

log = get_logger(component="semver_rule")


class SemverRule:
    """Picks the highest valid-semver tag, optionally restricted to tags
    with a given prefix (e.g. 'v') and/or matching a regex pattern. Tags
    that aren't valid semver (after stripping the prefix) are skipped
    rather than raising, since real repositories mix semver tags with
    things like 'latest', 'production', or 'sha-abc123'."""

    def __init__(
        self, prefix: str = "", pattern: str | None = None, include_prerelease: bool = False
    ) -> None:
        self._prefix = prefix
        self._pattern = re.compile(pattern) if pattern else None
        self._include_prerelease = include_prerelease

    def _candidate_version(self, tag: str) -> semver.Version | None:
        if self._pattern and not self._pattern.match(tag):
            return None
        if self._prefix:
            if not tag.startswith(self._prefix):
                return None
            tag = tag[len(self._prefix) :]
        try:
            version = semver.Version.parse(tag)
        except ValueError:
            return None
        if version.prerelease and not self._include_prerelease:
            return None
        return version

    async def resolve(self, client: DockerV2Client, repository: str) -> ResolvedRef:
        tags = await client.list_tags(repository)

        candidates: list[tuple[semver.Version, str]] = []
        for tag in tags:
            version = self._candidate_version(tag)
            if version is not None:
                candidates.append((version, tag))
            else:
                log.debug("skipping_non_semver_tag", repository=repository, tag=tag)

        if not candidates:
            raise RuleError(f"no valid semver tags found for repository '{repository}'")

        version, tag = max(candidates, key=lambda vt: vt[0])
        digest = await client.resolve_digest(repository, tag)
        return ResolvedRef(tag=tag, digest=digest, metadata={"version": str(version)})
