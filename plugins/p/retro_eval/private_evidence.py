"""External-only redacted evidence routed through source adapters."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from .adapters.registry import default_registry


def _default_redactor():
    path = Path(__file__).resolve().parents[1] / "bin" / "retro.py"
    spec = importlib.util.spec_from_file_location("retro_eval_redaction_bridge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.redact


def collect_private_tool_evidence(source_roots, id_salt: bytes, *, redactor=None,
                                  registry=None):
    """Collect redacted evidence keyed by normalized span id.

    The caller may persist the result only outside Git. Normalized trace storage
    never receives this mapping.
    """
    if not isinstance(id_salt, bytes) or len(id_salt) != 32:
        raise ValueError("private evidence requires the extraction id salt")
    redactor = redactor or _default_redactor()
    registry = registry or default_registry()
    roots = {str(name): Path(path) for name, path in source_roots.items()}
    evidence = {}
    for registration in registry:
        root = roots.get(registration.name)
        if root is None:
            continue
        adapter = registration.create(id_salt)
        extractor = getattr(adapter, "private_tool_evidence", None)
        if extractor is None:
            continue
        for path in sorted(registration.discover(root)):
            for span_id, details in extractor(path, root, redactor).items():
                if span_id in evidence and evidence[span_id] != details:
                    raise ValueError("private evidence span id collision")
                evidence[span_id] = details
    return evidence
