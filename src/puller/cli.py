from __future__ import annotations

import asyncio

import click

from puller.config.loader import ConfigError, load_config
from puller.config.schema import RootConfig
from puller.executor.command import CommandExecutor
from puller.health.server import ReadinessState, serve_health
from puller.logging_setup import configure_logging, get_logger
from puller.registries.base import RegistryError
from puller.registries.factory import RegistryClientFactory
from puller.rules import RuleError, build_rule
from puller.scheduler.supervisor import Supervisor
from puller.state.store import JSONFileStateStore
from puller.version import __version__

log = get_logger(component="cli")


@click.command()
@click.version_option(version=__version__, prog_name="puller")
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to the YAML config file.",
)
@click.option(
    "--validate-config", is_flag=True, help="Load and validate the config, then exit."
)
@click.option(
    "--once",
    is_flag=True,
    help="Resolve every watcher's current tag/digest once and print it, without polling or triggering commands.",
)
@click.option("--log-level", default=None, help="Override the config's global.log_level.")
def main(config_path: str, validate_config: bool, once: bool, log_level: str | None) -> None:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        click.echo(f"Configuration error: {exc}", err=True)
        raise SystemExit(1) from exc

    configure_logging(log_level or config.global_.log_level)

    if validate_config:
        click.echo(f"Configuration is valid: {len(config.watchers)} watcher(s) configured.")
        return

    if once:
        asyncio.run(_run_once(config))
        return

    asyncio.run(_run_daemon(config))


async def _run_once(config: RootConfig) -> None:
    async with RegistryClientFactory(config.global_.http) as factory:
        for watcher in config.watchers:
            registry_config = config.registry_by_name(watcher.registry)
            rule = build_rule(watcher.rule)
            client = factory.get_client(registry_config)
            try:
                ref = await rule.resolve(client, watcher.repository)
                click.echo(f"{watcher.name}: tag={ref.tag} digest={ref.digest}")
            except (RuleError, RegistryError) as exc:
                click.echo(f"{watcher.name}: ERROR {exc}", err=True)


async def _run_daemon(config: RootConfig) -> None:
    state = JSONFileStateStore(config.global_.state_file)
    executor = CommandExecutor()
    readiness = ReadinessState()

    async with RegistryClientFactory(config.global_.http) as factory:
        supervisor = Supervisor(config, factory, state, executor)
        readiness.set_ready()
        log.info("puller_starting", watcher_count=len(config.watchers))

        await asyncio.gather(
            supervisor.run(),
            serve_health(
                config.global_.health.bind_host,
                config.global_.health.bind_port,
                readiness,
                supervisor.shutdown_event,
            ),
        )


if __name__ == "__main__":
    main()
