from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

from puller.config.schema import CommandConfig
from puller.logging_setup import get_logger

log = get_logger(component="executor")

_MAX_RETAINED_OUTPUT_BYTES = 64 * 1024


@dataclass(frozen=True)
class TriggerContext:
    watcher_name: str
    registry_type: str
    registry_host: str
    repository: str
    image: str
    matched_tag: str
    new_digest: str
    previous_digest: str | None
    rule_type: str
    timestamp: str
    event_id: str


@dataclass
class CommandResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    duration_seconds: float


class CommandTimeout(Exception):
    def __init__(self, watcher_name: str, timeout_seconds: int) -> None:
        super().__init__(f"command for watcher '{watcher_name}' timed out after {timeout_seconds}s")
        self.watcher_name = watcher_name


def build_env(cfg: CommandConfig, ctx: TriggerContext) -> dict[str, str]:
    base: dict[str, str] = dict(os.environ) if cfg.env_passthrough else {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    }
    base.update(
        {
            "PULLER_WATCHER_NAME": ctx.watcher_name,
            "PULLER_REGISTRY_TYPE": ctx.registry_type,
            "PULLER_REGISTRY_HOST": ctx.registry_host,
            "PULLER_REPOSITORY": ctx.repository,
            "PULLER_IMAGE": ctx.image,
            "PULLER_MATCHED_TAG": ctx.matched_tag,
            "PULLER_NEW_DIGEST": ctx.new_digest,
            "PULLER_PREVIOUS_DIGEST": ctx.previous_digest or "",
            "PULLER_RULE_TYPE": ctx.rule_type,
            "PULLER_TIMESTAMP": ctx.timestamp,
            "PULLER_EVENT_ID": ctx.event_id,
        }
    )
    base.update(cfg.env)
    return base


async def _stream_and_log(stream: asyncio.StreamReader, watcher_name: str, event_id: str, stream_name: str) -> bytes:
    collected = bytearray()
    while True:
        line = await stream.readline()
        if not line:
            break
        if len(collected) < _MAX_RETAINED_OUTPUT_BYTES:
            collected.extend(line)
        text = line.decode("utf-8", errors="replace").rstrip("\n")
        log.info(f"command_{stream_name}", watcher=watcher_name, event_id=event_id, line=text)
    return bytes(collected[:_MAX_RETAINED_OUTPUT_BYTES])


async def run_command(cfg: CommandConfig, ctx: TriggerContext) -> CommandResult:
    env = build_env(cfg, ctx)
    start = time.monotonic()

    if cfg.shell:
        proc = await asyncio.create_subprocess_shell(
            cfg.shell,
            cwd=cfg.cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    else:
        assert cfg.exec is not None
        proc = await asyncio.create_subprocess_exec(
            *cfg.exec,
            cwd=cfg.cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    assert proc.stdout is not None and proc.stderr is not None

    try:
        stdout, stderr, _ = await asyncio.wait_for(
            asyncio.gather(
                _stream_and_log(proc.stdout, ctx.watcher_name, ctx.event_id, "stdout"),
                _stream_and_log(proc.stderr, ctx.watcher_name, ctx.event_id, "stderr"),
                proc.wait(),
            ),
            timeout=cfg.timeout_seconds,
        )
    except asyncio.TimeoutError:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        raise CommandTimeout(ctx.watcher_name, cfg.timeout_seconds)

    duration = time.monotonic() - start
    return CommandResult(exit_code=proc.returncode or 0, stdout=stdout, stderr=stderr, duration_seconds=duration)


class CommandExecutor:
    """Runs watcher commands, skipping (with a warning log) a new trigger if
    the previous invocation for that same watcher hasn't finished yet."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, watcher_name: str) -> asyncio.Lock:
        return self._locks.setdefault(watcher_name, asyncio.Lock())

    async def run(self, cfg: CommandConfig, ctx: TriggerContext) -> CommandResult | None:
        lock = self._lock_for(ctx.watcher_name)
        if lock.locked():
            log.warning(
                "command_skipped_already_running", watcher=ctx.watcher_name, event_id=ctx.event_id
            )
            return None
        async with lock:
            return await run_command(cfg, ctx)
