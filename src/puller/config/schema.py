from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from puller.config.secrets import SecretRef


class BasicAuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: SecretRef
    password: SecretRef


class DockerV2RegistryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["docker_v2"]
    name: str
    base_url: str
    tls_verify: bool = True
    auth: Optional[BasicAuthConfig] = None


class GhcrRegistryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ghcr"]
    name: str
    base_url: str = "https://ghcr.io"
    auth: BasicAuthConfig


class EcrRegistryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ecr"]
    name: str
    region: str
    account_id: str
    access_key_id: Optional[SecretRef] = None
    secret_access_key: Optional[SecretRef] = None
    session_token: Optional[SecretRef] = None


RegistryConfig = Annotated[
    Union[DockerV2RegistryConfig, GhcrRegistryConfig, EcrRegistryConfig],
    Field(discriminator="type"),
]


class SemverRuleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["semver"]
    prefix: str = ""
    pattern: Optional[str] = None
    include_prerelease: bool = False


class TagRuleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["tag"]
    tag: str


class LatestRuleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["latest"]


RuleConfig = Annotated[
    Union[SemverRuleConfig, TagRuleConfig, LatestRuleConfig],
    Field(discriminator="type"),
]


class CommandConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exec: Optional[list[str]] = None
    shell: Optional[str] = None
    cwd: Optional[str] = None
    timeout_seconds: int = 120
    env: dict[str, str] = Field(default_factory=dict)
    env_passthrough: bool = False

    @model_validator(mode="after")
    def _check_exec_xor_shell(self) -> "CommandConfig":
        if bool(self.exec) == bool(self.shell):
            raise ValueError("command must specify exactly one of 'exec' or 'shell'")
        return self


class WatcherConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    registry: str
    repository: str
    poll_interval_seconds: Optional[int] = None
    rule: RuleConfig
    command: CommandConfig
    trigger_on_first_run: bool = False


class HealthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bind_host: str = "0.0.0.0"
    bind_port: int = 8080


class HttpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: float = 10.0
    max_concurrent_requests_per_registry: int = 4


class GlobalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_level: str = "info"
    state_file: str = "/var/lib/puller/state.json"
    default_poll_interval_seconds: int = 60
    health: HealthConfig = Field(default_factory=HealthConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)


class RootConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    global_: GlobalConfig = Field(default_factory=GlobalConfig, alias="global")
    registries: list[RegistryConfig]
    watchers: list[WatcherConfig]

    @model_validator(mode="after")
    def _check_references(self) -> "RootConfig":
        registry_names = [r.name for r in self.registries]
        if len(registry_names) != len(set(registry_names)):
            raise ValueError("duplicate registry names in 'registries'")

        watcher_names: set[str] = set()
        for watcher in self.watchers:
            if watcher.registry not in registry_names:
                raise ValueError(
                    f"watcher '{watcher.name}' references unknown registry '{watcher.registry}'"
                )
            if watcher.name in watcher_names:
                raise ValueError(f"duplicate watcher name '{watcher.name}'")
            watcher_names.add(watcher.name)
        return self

    def effective_poll_interval(self, watcher: WatcherConfig) -> int:
        return watcher.poll_interval_seconds or self.global_.default_poll_interval_seconds

    def registry_by_name(self, name: str) -> RegistryConfig:
        for registry in self.registries:
            if registry.name == name:
                return registry
        raise KeyError(name)
