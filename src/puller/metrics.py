from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

registry = CollectorRegistry()

POLL_TOTAL = Counter(
    "puller_poll_total",
    "Number of registry poll cycles per watcher",
    ["watcher", "result"],
    registry=registry,
)

LAST_POLL_TIMESTAMP = Gauge(
    "puller_last_poll_timestamp_seconds",
    "Unix timestamp of the last poll attempt per watcher",
    ["watcher"],
    registry=registry,
)

TRIGGER_TOTAL = Counter(
    "puller_trigger_total",
    "Number of times a watcher's command was triggered",
    ["watcher", "result"],
    registry=registry,
)

LAST_DIGEST_CHANGE_TIMESTAMP = Gauge(
    "puller_last_digest_change_timestamp_seconds",
    "Unix timestamp of the last detected digest change per watcher",
    ["watcher"],
    registry=registry,
)

COMMAND_DURATION = Histogram(
    "puller_command_duration_seconds",
    "Duration of triggered command executions",
    ["watcher"],
    registry=registry,
)
