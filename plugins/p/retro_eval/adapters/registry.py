"""Versioned adapter discovery and construction registry."""

from __future__ import annotations

import importlib
import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


def _load_callable(specification: str):
    """Resolve a profile's ``module:callable`` discovery function."""
    module_name, separator, attribute = specification.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("source discovery must use module:callable syntax")
    try:
        value = getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError) as exc:
        raise ValueError("source discovery cannot be loaded") from exc
    if not callable(value):
        raise ValueError("source discovery is not callable")
    return value


@dataclass(frozen=True)
class AdapterRegistration:
    name: str
    discover: Callable[[Path], Iterable[Path]]
    factory: Callable[[bytes, dict[str, object]], object] | None = None
    module: str = ""
    class_name: str = ""
    options: dict[str, object] | None = None

    def accepts_option(self, name: str) -> bool:
        """Whether the profile-built adapter class takes this constructor option."""
        if self.factory is not None or not self.module:
            return False
        adapter_class = getattr(importlib.import_module(self.module), self.class_name)
        return name in inspect.signature(adapter_class.__init__).parameters

    def create(self, salt: bytes, runtime_options=None):
        options = dict(self.options or {})
        options.update(runtime_options or {})
        if self.factory is not None:
            return self.factory(salt, options)
        adapter_class = getattr(importlib.import_module(self.module), self.class_name)
        accepted = set(inspect.signature(adapter_class.__init__).parameters) - {
            "self", "id_salt"
        }
        unknown = set(options) - accepted
        if unknown:
            raise ValueError("unsupported adapter options: %s" % ", ".join(sorted(unknown)))
        return adapter_class(salt, **options)


class AdapterRegistry:
    def __init__(self, registrations=()):
        registrations = tuple(registrations)
        self._items = {item.name: item for item in registrations}
        if len(self._items) != len(registrations):
            raise ValueError("duplicate adapter registration")

    def __iter__(self):
        return iter(self._items.values())

    def get(self, name: str) -> AdapterRegistration:
        return self._items[name]

    @classmethod
    def from_profile(cls, path: Path) -> "AdapterRegistry":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("invalid source profile") from exc
        if payload.get("schema_version") != 1:
            raise ValueError("unsupported source profile schema")
        registrations = []
        for raw in payload.get("sources") or ():
            if raw.get("discover"):
                discover = _load_callable(str(raw["discover"]))
            else:
                pattern = str(raw["glob"])
                discover = lambda root, pattern=pattern: root.rglob(pattern)  # noqa: E731
            registrations.append(AdapterRegistration(
                name=str(raw["name"]), module=str(raw["module"]),
                class_name=str(raw["class"]), options=dict(raw.get("options") or {}),
                discover=discover,
            ))
        return cls(tuple(registrations))


def default_registry() -> AdapterRegistry:
    path = Path(__file__).resolve().parents[2] / "profiles" / "sources.json"
    return AdapterRegistry.from_profile(path)


def default_options_for(name: str) -> dict[str, object]:
    return dict(default_registry().get(name).options or {})
