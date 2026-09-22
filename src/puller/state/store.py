from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass
class WatcherStateEntry:
    digest: str
    tag: str
    last_checked: str
    last_triggered: str | None = None


class StateStore(Protocol):
    def get(self, watcher_name: str) -> WatcherStateEntry | None: ...

    async def set(self, watcher_name: str, entry: WatcherStateEntry) -> None: ...


class JSONFileStateStore:
    """Local JSON file, one entry per watcher. Writes are atomic (write to a
    tmp file in the same directory, then os.replace) so a crash mid-write
    never leaves a corrupt state file. Loaded once at startup; every set()
    persists the full file immediately so state survives a restart."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._entries: dict[str, WatcherStateEntry] = self._load()

    def _load(self) -> dict[str, WatcherStateEntry]:
        if not os.path.exists(self._path):
            return {}
        with open(self._path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        watchers = raw.get("watchers", {})
        return {name: WatcherStateEntry(**fields) for name, fields in watchers.items()}

    def _write(self) -> None:
        directory = os.path.dirname(self._path) or "."
        os.makedirs(directory, exist_ok=True)
        payload = {"watchers": {name: asdict(entry) for name, entry in self._entries.items()}}

        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
            os.replace(tmp_path, self._path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def get(self, watcher_name: str) -> WatcherStateEntry | None:
        return self._entries.get(watcher_name)

    async def set(self, watcher_name: str, entry: WatcherStateEntry) -> None:
        async with self._lock:
            self._entries[watcher_name] = entry
            await asyncio.to_thread(self._write)
