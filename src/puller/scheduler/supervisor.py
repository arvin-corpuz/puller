from __future__ import annotations

import asyncio
import signal

from puller.config.schema import RootConfig
from puller.executor.command import CommandExecutor
from puller.logging_setup import get_logger
from puller.registries.factory import RegistryClientFactory
from puller.scheduler.watcher_task import watcher_loop
from puller.state.store import StateStore

log = get_logger(component="supervisor")

_TASK_CRASH_RESTART_DELAY_SECONDS = 5


class Supervisor:
    """Owns one asyncio.Task per watcher. A watcher's own poll loop already
    catches and backs off on registry/rule errors; this restart wrapper only
    guards against a bug causing an unhandled exception to escape the loop
    entirely, so one broken watcher can never take down the whole process."""

    def __init__(
        self,
        config: RootConfig,
        client_factory: RegistryClientFactory,
        state: StateStore,
        executor: CommandExecutor,
    ) -> None:
        self._config = config
        self._client_factory = client_factory
        self._state = state
        self._executor = executor
        self._shutdown = asyncio.Event()
        self._tasks: dict[str, asyncio.Task] = {}

    def request_shutdown(self) -> None:
        self._shutdown.set()

    @property
    def shutdown_event(self) -> asyncio.Event:
        return self._shutdown

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._shutdown.set)
            except NotImplementedError:
                pass

        for watcher in self._config.watchers:
            self._spawn(watcher)

        await self._shutdown.wait()
        log.info("shutdown_requested")
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        log.info("shutdown_complete")

    def _spawn(self, watcher) -> None:  # noqa: ANN001 - WatcherConfig, avoid import cycle noise
        registry_config = self._config.registry_by_name(watcher.registry)
        interval = self._config.effective_poll_interval(watcher)
        task = asyncio.create_task(
            self._run_with_restart(watcher, registry_config, interval),
            name=f"watcher:{watcher.name}",
        )
        self._tasks[watcher.name] = task

    async def _run_with_restart(self, watcher, registry_config, interval: int) -> None:  # noqa: ANN001
        while not self._shutdown.is_set():
            try:
                await watcher_loop(
                    interval,
                    watcher,
                    registry_config,
                    self._client_factory,
                    self._state,
                    self._executor,
                    self._shutdown,
                )
                return
            except Exception:
                log.exception("watcher_task_crashed_restarting", watcher=watcher.name)
                await asyncio.sleep(_TASK_CRASH_RESTART_DELAY_SECONDS)
