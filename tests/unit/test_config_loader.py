from __future__ import annotations

import textwrap

import pytest

from puller.config.loader import ConfigError, load_config

VALID_CONFIG = """
registries:
  - name: dockerhub
    type: docker_v2
    base_url: https://registry-1.docker.io

watchers:
  - name: nginx-latest
    registry: dockerhub
    repository: library/nginx
    rule:
      type: latest
    command:
      shell: "true"
"""


def _write(tmp_path, content: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(content))
    return str(path)


def test_valid_config_loads(tmp_path) -> None:
    config = load_config(_write(tmp_path, VALID_CONFIG))

    assert len(config.watchers) == 1
    assert config.watchers[0].name == "nginx-latest"
    assert config.global_.default_poll_interval_seconds == 60


def test_missing_file_raises_config_error(tmp_path) -> None:
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "does-not-exist.yaml"))


def test_invalid_yaml_raises_config_error(tmp_path) -> None:
    path = _write(tmp_path, "not: valid: yaml: [")
    with pytest.raises(ConfigError):
        load_config(path)


def test_unknown_registry_reference_raises_config_error(tmp_path) -> None:
    content = VALID_CONFIG.replace("registry: dockerhub", "registry: nonexistent")
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, content))


def test_duplicate_watcher_names_raises_config_error(tmp_path) -> None:
    content = VALID_CONFIG.split("watchers:")[0] + textwrap.dedent(
        """
        watchers:
          - name: dup
            registry: dockerhub
            repository: library/nginx
            rule:
              type: latest
            command:
              shell: "true"
          - name: dup
            registry: dockerhub
            repository: library/nginx
            rule:
              type: tag
              tag: stable
            command:
              shell: "true"
        """
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, content))


def test_missing_env_secret_raises_config_error(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MISSING_VAR_FOR_TEST", raising=False)
    content = """
    registries:
      - name: dockerhub
        type: docker_v2
        base_url: https://registry-1.docker.io
        auth:
          username: {env: MISSING_VAR_FOR_TEST}
          password: literal-password

    watchers:
      - name: nginx-latest
        registry: dockerhub
        repository: library/nginx
        rule:
          type: latest
        command:
          shell: "true"
    """
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, content))


def test_command_requires_exec_xor_shell(tmp_path) -> None:
    content = VALID_CONFIG.replace('command:\n      shell: "true"', "command: {}")
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, content))
