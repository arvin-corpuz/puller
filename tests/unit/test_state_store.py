from __future__ import annotations

import json
import os

import pytest

from puller.state.store import JSONFileStateStore, WatcherStateEntry


@pytest.mark.asyncio
async def test_set_and_get_round_trip(tmp_path) -> None:
    store = JSONFileStateStore(str(tmp_path / "state.json"))
    entry = WatcherStateEntry(digest="sha256:abc", tag="latest", last_checked="2026-01-01T00:00:00Z")

    assert store.get("watcher-a") is None
    await store.set("watcher-a", entry)

    assert store.get("watcher-a") == entry


@pytest.mark.asyncio
async def test_state_persists_across_instances(tmp_path) -> None:
    path = str(tmp_path / "state.json")
    store1 = JSONFileStateStore(path)
    entry = WatcherStateEntry(digest="sha256:abc", tag="v1.0.0", last_checked="2026-01-01T00:00:00Z")
    await store1.set("watcher-a", entry)

    store2 = JSONFileStateStore(path)
    assert store2.get("watcher-a") == entry


@pytest.mark.asyncio
async def test_write_is_atomic_no_leftover_tmp_files(tmp_path) -> None:
    path = str(tmp_path / "state.json")
    store = JSONFileStateStore(path)
    await store.set(
        "watcher-a",
        WatcherStateEntry(digest="sha256:abc", tag="latest", last_checked="2026-01-01T00:00:00Z"),
    )

    files = os.listdir(tmp_path)
    assert files == ["state.json"]
    with open(path) as fh:
        data = json.load(fh)
    assert data["watchers"]["watcher-a"]["digest"] == "sha256:abc"


@pytest.mark.asyncio
async def test_missing_state_file_starts_empty(tmp_path) -> None:
    store = JSONFileStateStore(str(tmp_path / "does-not-exist.json"))
    assert store.get("anything") is None
