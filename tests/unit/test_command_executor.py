from __future__ import annotations

import asyncio

import pytest

from puller.config.schema import CommandConfig
from puller.executor.command import CommandExecutor, CommandTimeout, TriggerContext, run_command


def make_ctx(watcher_name: str = "watcher-a") -> TriggerContext:
    return TriggerContext(
        watcher_name=watcher_name,
        registry_type="docker_v2",
        registry_host="registry.example.com",
        repository="myorg/myapp",
        image="myorg/myapp@sha256:abc",
        matched_tag="latest",
        new_digest="sha256:abc",
        previous_digest=None,
        rule_type="latest",
        timestamp="2026-01-01T00:00:00Z",
        event_id="event-1",
    )


@pytest.mark.asyncio
async def test_env_vars_are_passed_to_command() -> None:
    cfg = CommandConfig(exec=["python3", "-c", "import os; print(os.environ['PULLER_NEW_DIGEST'])"])
    result = await run_command(cfg, make_ctx())

    assert result.exit_code == 0
    assert result.stdout.strip() == b"sha256:abc"


@pytest.mark.asyncio
async def test_nonzero_exit_code_is_reported() -> None:
    cfg = CommandConfig(exec=["python3", "-c", "import sys; sys.exit(3)"])
    result = await run_command(cfg, make_ctx())

    assert result.exit_code == 3


@pytest.mark.asyncio
async def test_timeout_raises_and_kills_process() -> None:
    cfg = CommandConfig(shell="sleep 5", timeout_seconds=1)

    with pytest.raises(CommandTimeout):
        await run_command(cfg, make_ctx())


@pytest.mark.asyncio
async def test_static_env_merged_in() -> None:
    cfg = CommandConfig(exec=["python3", "-c", "import os; print(os.environ['MY_VAR'])"], env={"MY_VAR": "hello"})
    result = await run_command(cfg, make_ctx())

    assert result.stdout.strip() == b"hello"


@pytest.mark.asyncio
async def test_executor_skips_overlapping_run_for_same_watcher() -> None:
    executor = CommandExecutor()
    slow_cfg = CommandConfig(shell="sleep 0.3", timeout_seconds=5)
    fast_cfg = CommandConfig(shell="true", timeout_seconds=5)

    task1 = asyncio.create_task(executor.run(slow_cfg, make_ctx("watcher-a")))
    await asyncio.sleep(0.05)  # let task1 acquire the lock and start its subprocess

    result2 = await executor.run(fast_cfg, make_ctx("watcher-a"))
    assert result2 is None  # skipped: watcher-a already has a command running

    result1 = await task1
    assert result1 is not None
    assert result1.exit_code == 0


@pytest.mark.asyncio
async def test_executor_does_not_block_different_watchers() -> None:
    executor = CommandExecutor()
    slow_cfg = CommandConfig(shell="sleep 0.3", timeout_seconds=5)
    fast_cfg = CommandConfig(shell="true", timeout_seconds=5)

    task1 = asyncio.create_task(executor.run(slow_cfg, make_ctx("watcher-a")))
    await asyncio.sleep(0.05)

    result2 = await executor.run(fast_cfg, make_ctx("watcher-b"))
    assert result2 is not None
    assert result2.exit_code == 0

    await task1
