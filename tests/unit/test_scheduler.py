from __future__ import annotations

import asyncio

import pytest

from puller.config.schema import CommandConfig, DockerV2RegistryConfig, TagRuleConfig, WatcherConfig
from puller.executor.command import CommandExecutor
from puller.registries.base import RegistryError
from puller.rules.base import ResolvedRef
from puller.scheduler import watcher_task as wt
from puller.state.store import JSONFileStateStore


class FlakyRule:
    """Fails once, then always succeeds — simulates a transient registry error."""

    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, client, repository) -> ResolvedRef:  # noqa: ANN001
        self.calls += 1
        if self.calls == 1:
            raise RegistryError("simulated transient failure")
        return ResolvedRef(tag="latest", digest="sha256:ok")


class FastBackoff:
    """A near-instant backoff so the test doesn't wait out the real 5s start delay."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def next(self) -> float:
        return 0.01

    def reset(self) -> None:
        pass


class DummyFactory:
    def get_client(self, config):  # noqa: ANN001
        return object()


@pytest.mark.asyncio
async def test_watcher_loop_recovers_from_transient_registry_error(monkeypatch, tmp_path) -> None:
    flaky = FlakyRule()
    monkeypatch.setattr(wt, "build_rule", lambda rule_config: flaky)
    monkeypatch.setattr(wt, "Backoff", FastBackoff)

    watcher = WatcherConfig(
        name="w1",
        registry="reg1",
        repository="org/app",
        rule=TagRuleConfig(type="tag", tag="latest"),
        command=CommandConfig(shell="true"),
    )
    registry_config = DockerV2RegistryConfig(type="docker_v2", name="reg1", base_url="https://example.com")
    state = JSONFileStateStore(str(tmp_path / "state.json"))
    executor = CommandExecutor()
    shutdown = asyncio.Event()

    task = asyncio.create_task(
        wt.watcher_loop(0, watcher, registry_config, DummyFactory(), state, executor, shutdown)
    )

    await asyncio.sleep(0.2)
    shutdown.set()
    await asyncio.wait_for(task, timeout=2)

    assert flaky.calls >= 2  # first call failed, a later call succeeded
    assert state.get("w1") is not None  # recovered and recorded a baseline
