"""Type definitions for dotcfg."""

from __future__ import annotations

from typing import Any, Callable, Dict, TypeVar, Union

T = TypeVar("T")

EnvDict = Dict[str, str]
CastFn = Callable[[str], Any]
