from __future__ import annotations

import os
from typing import Union

from pydantic import BaseModel, ConfigDict


class EnvSecretRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    env: str


class FileSecretRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str


SecretRef = Union[str, EnvSecretRef, FileSecretRef]


class SecretResolutionError(Exception):
    pass


def resolve_secret(ref: SecretRef) -> str:
    if isinstance(ref, str):
        return ref
    if isinstance(ref, EnvSecretRef):
        value = os.environ.get(ref.env)
        if value is None:
            raise SecretResolutionError(f"environment variable '{ref.env}' is not set")
        return value
    if isinstance(ref, FileSecretRef):
        try:
            with open(ref.file, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError as exc:
            raise SecretResolutionError(f"could not read secret file '{ref.file}': {exc}") from exc
    raise TypeError(f"unsupported secret reference type: {type(ref)!r}")
