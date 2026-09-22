from __future__ import annotations

import yaml
from pydantic import ValidationError

from puller.config.schema import BasicAuthConfig, EcrRegistryConfig, RootConfig
from puller.config.secrets import SecretResolutionError, resolve_secret


class ConfigError(Exception):
    pass


def _validate_secrets(config: RootConfig) -> None:
    errors: list[str] = []
    for registry in config.registries:
        auth: BasicAuthConfig | None = getattr(registry, "auth", None)
        if auth is not None:
            for field_name, ref in (("username", auth.username), ("password", auth.password)):
                try:
                    resolve_secret(ref)
                except SecretResolutionError as exc:
                    errors.append(f"registry '{registry.name}' auth.{field_name}: {exc}")
        if isinstance(registry, EcrRegistryConfig):
            for field_name in ("access_key_id", "secret_access_key", "session_token"):
                ref = getattr(registry, field_name)
                if ref is not None:
                    try:
                        resolve_secret(ref)
                    except SecretResolutionError as exc:
                        errors.append(f"registry '{registry.name}' {field_name}: {exc}")
    if errors:
        raise ConfigError("secret resolution failed:\n  " + "\n  ".join(errors))


def load_config(path: str) -> RootConfig:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except OSError as exc:
        raise ConfigError(f"could not read config file '{path}': {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in '{path}': {exc}") from exc

    if raw is None:
        raise ConfigError(f"config file '{path}' is empty")

    try:
        config = RootConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration in '{path}':\n{exc}") from exc

    _validate_secrets(config)
    return config
