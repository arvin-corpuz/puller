from __future__ import annotations

import asyncio
import random
import time
import uuid
from datetime import datetime, timezone

from puller import metrics
from puller.config.schema import EcrRegistryConfig, RegistryConfig, WatcherConfig
from puller.executor.command import CommandExecutor, CommandTimeout, TriggerContext
from puller.logging_setup import get_logger
from puller.registries.base import RegistryError
from puller.registries.factory import RegistryClientFactory
from puller.rules import RuleError, build_rule
from puller.rules.base import ResolvedRef
from puller.state.store import StateStore, WatcherStateEntry

log = get_logger(component="watcher")


class Backoff:
    def __init__(self, start: float = 5, cap: float = 300) -> None:
        self._start = start
        self._cap = cap
        self._current = start

    def next(self) -> float:
        delay = self._current
        self._current = min(self._current * 2, self._cap)
        return delay + random.uniform(0, delay * 0.1)

    def reset(self) -> None:
        self._current = self._start


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _registry_host(config: RegistryConfig) -> str:
    if isinstance(config, EcrRegistryConfig):
        return f"{config.account_id}.dkr.ecr.{config.region}.amazonaws.com"
    return config.base_url


async def _sleep_or_shutdown(seconds: float, shutdown: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(shutdown.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def _trigger(
    watcher: WatcherConfig,
    registry_config: RegistryConfig,
    ref: ResolvedRef,
    prev_entry: WatcherStateEntry | None,
    state: StateStore,
    executor: CommandExecutor,
) -> None:
    event_id = str(uuid.uuid4())
    ctx = TriggerContext(
        watcher_name=watcher.name,
        registry_type=registry_config.type,
        registry_host=_registry_host(registry_config),
        repository=watcher.repository,
        image=f"{watcher.repository}@{ref.digest}",
        matched_tag=ref.tag,
        new_digest=ref.digest,
        previous_digest=prev_entry.digest if prev_entry else None,
        rule_type=watcher.rule.type,
        timestamp=_now_iso(),
        event_id=event_id,
    )

    log.info(
        "digest_change_detected",
        watcher=watcher.name,
        tag=ref.tag,
        new_digest=ref.digest,
        previous_digest=ctx.previous_digest,
        event_id=event_id,
    )
    metrics.LAST_DIGEST_CHANGE_TIMESTAMP.labels(watcher.name).set(time.time())

    start = time.monotonic()
    try:
        result = await executor.run(watcher.command, ctx)
    except CommandTimeout as exc:
        metrics.TRIGGER_TOTAL.labels(watcher.name, "timeout").inc()
        log.error("command_timed_out", watcher=watcher.name, event_id=event_id, error=str(exc))
        return
    finally:
        metrics.COMMAND_DURATION.labels(watcher.name).observe(time.monotonic() - start)

    if result is None:
        return

    new_entry = WatcherStateEntry(
        digest=ref.digest, tag=ref.tag, last_checked=_now_iso(), last_triggered=_now_iso()
    )
    if result.exit_code == 0:
        metrics.TRIGGER_TOTAL.labels(watcher.name, "success").inc()
        await state.set(watcher.name, new_entry)
        log.info(
            "command_succeeded",
            watcher=watcher.name,
            event_id=event_id,
            exit_code=result.exit_code,
            duration_seconds=result.duration_seconds,
        )
    else:
        metrics.TRIGGER_TOTAL.labels(watcher.name, "failure").inc()
        log.error(
            "command_failed",
            watcher=watcher.name,
            event_id=event_id,
            exit_code=result.exit_code,
            duration_seconds=result.duration_seconds,
        )


async def watcher_loop(
    interval_seconds: int,
    watcher: WatcherConfig,
    registry_config: RegistryConfig,
    client_factory: RegistryClientFactory,
    state: StateStore,
    executor: CommandExecutor,
    shutdown: asyncio.Event,
) -> None:
    rule = build_rule(watcher.rule)
    backoff = Backoff()

    startup_jitter = random.uniform(0, min(interval_seconds, 10))
    await _sleep_or_shutdown(startup_jitter, shutdown)

    while not shutdown.is_set():
        try:
            client = client_factory.get_client(registry_config)
            ref = await rule.resolve(client, watcher.repository)
            metrics.POLL_TOTAL.labels(watcher.name, "success").inc()
            metrics.LAST_POLL_TIMESTAMP.labels(watcher.name).set(time.time())

            prev_entry = state.get(watcher.name)
            is_first_run = prev_entry is None
            changed = is_first_run or prev_entry.digest != ref.digest

            if changed and not is_first_run:
                await _trigger(watcher, registry_config, ref, prev_entry, state, executor)
            elif is_first_run:
                if watcher.trigger_on_first_run:
                    await _trigger(watcher, registry_config, ref, None, state, executor)
                else:
                    await state.set(
                        watcher.name,
                        WatcherStateEntry(digest=ref.digest, tag=ref.tag, last_checked=_now_iso()),
                    )
                    log.info(
                        "baseline_recorded", watcher=watcher.name, tag=ref.tag, digest=ref.digest
                    )

            backoff.reset()
        except (RuleError, RegistryError) as exc:
            metrics.POLL_TOTAL.labels(watcher.name, "error").inc()
            log.warning("poll_failed", watcher=watcher.name, error=str(exc))
            await _sleep_or_shutdown(backoff.next(), shutdown)
            continue
        except Exception:
            metrics.POLL_TOTAL.labels(watcher.name, "error").inc()
            log.exception("unexpected_error_in_watcher_loop", watcher=watcher.name)
            await _sleep_or_shutdown(backoff.next(), shutdown)
            continue

        await _sleep_or_shutdown(interval_seconds, shutdown)
