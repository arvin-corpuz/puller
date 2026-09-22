"""End-to-end test against a real local `registry:2` container.

Exercises the full stack (DockerV2Client + BearerChallengeAuth + SemverRule +
TagRule) against a live Docker Registry HTTP API V2 server -- no mocking.
Requires a working local Docker daemon; skipped automatically otherwise.

Run explicitly with: pytest tests/integration -m integration
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
import uuid

import httpx
import pytest

from puller.registries.auth_bearer import BearerChallengeAuth
from puller.registries.docker_v2 import DockerV2Client
from puller.rules.semver_rule import SemverRule
from puller.rules.tag_rule import TagRule

pytestmark = pytest.mark.integration


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10, check=True
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run(*args: str, timeout: int = 60) -> None:
    subprocess.run(args, capture_output=True, timeout=timeout, check=True)


@pytest.fixture(scope="module")
def local_registry():
    if not _docker_available():
        pytest.skip("Docker is not available in this environment")

    container_name = f"puller-test-registry-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    base_url = f"http://localhost:{port}"

    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    _run("docker", "run", "-d", "--name", container_name, "-p", f"{port}:5000", "registry:2")

    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                httpx.get(f"{base_url}/v2/", timeout=2).raise_for_status()
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            pytest.fail("local registry:2 container never became ready")

        yield base_url
    finally:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)


@pytest.fixture(scope="module")
def pushed_repo(local_registry: str) -> str:
    repo = "testrepo"
    _run("docker", "pull", "-q", "alpine:3.19")
    _run("docker", "pull", "-q", "alpine:3.20")

    registry_host = local_registry.replace("http://", "")
    for tag in ("v1.0.0", "v1.1.0", "v2.0.0-rc1", "latest"):
        _run("docker", "tag", "alpine:3.19", f"{registry_host}/{repo}:{tag}")
        _run("docker", "push", "-q", f"{registry_host}/{repo}:{tag}")

    return repo


@pytest.mark.asyncio
async def test_semver_rule_picks_highest_stable_version(local_registry: str, pushed_repo: str) -> None:
    async with httpx.AsyncClient() as http:
        client = DockerV2Client(local_registry, BearerChallengeAuth(), http)
        rule = SemverRule(prefix="v")
        ref = await rule.resolve(client, pushed_repo)

    assert ref.tag == "v1.1.0"  # v2.0.0-rc1 excluded: prerelease, not included by default


@pytest.mark.asyncio
async def test_tag_rule_detects_digest_change_on_retag(local_registry: str, pushed_repo: str) -> None:
    registry_host = local_registry.replace("http://", "")

    async with httpx.AsyncClient() as http:
        client = DockerV2Client(local_registry, BearerChallengeAuth(), http)
        rule = TagRule("latest")

        before = await rule.resolve(client, pushed_repo)

        _run("docker", "tag", "alpine:3.20", f"{registry_host}/{pushed_repo}:latest")
        _run("docker", "push", "-q", f"{registry_host}/{pushed_repo}:latest")

        after = await rule.resolve(client, pushed_repo)

    assert before.digest != after.digest


@pytest.mark.asyncio
async def test_list_tags_returns_all_pushed_tags(local_registry: str, pushed_repo: str) -> None:
    async with httpx.AsyncClient() as http:
        client = DockerV2Client(local_registry, BearerChallengeAuth(), http)
        tags = await client.list_tags(pushed_repo)

    assert set(tags) >= {"v1.0.0", "v1.1.0", "v2.0.0-rc1", "latest"}
