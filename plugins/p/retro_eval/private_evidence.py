"""External-only redacted evidence routed through source adapters."""

from __future__ import annotations

from pathlib import Path

from .adapters.registry import default_registry
from .text_rules import redact


def _default_redactor():
    return redact


def _collect(method, source_roots, id_salt, redactor, registry):
    if not isinstance(id_salt, bytes) or len(id_salt) != 32:
        raise ValueError("private evidence requires the extraction id salt")
    redactor = redactor or _default_redactor()
    registry = registry or default_registry()
    roots = {str(name): Path(path) for name, path in source_roots.items()}
    unknown = sorted(set(roots) - {registration.name for registration in registry})
    if unknown:
        raise ValueError("unregistered evidence source: %s" % ", ".join(unknown))
    evidence = {}
    for registration in registry:
        root = roots.get(registration.name)
        if root is None:
            continue
        if not root.is_dir():
            raise ValueError("evidence source root is not a directory: %s"
                             % registration.name)
        adapter = registration.create(id_salt)
        extractor = getattr(adapter, method, None)
        if extractor is None:
            continue
        for path in sorted(registration.discover(root)):
            for span_id, details in extractor(path, root, redactor).items():
                if span_id in evidence and evidence[span_id] != details:
                    raise ValueError("private evidence span id collision")
                evidence[span_id] = details
    return evidence


def collect_private_tool_evidence(source_roots, id_salt: bytes, *, redactor=None,
                                  registry=None):
    """Collect redacted tool evidence keyed by normalized span id.

    The caller may persist the result only outside Git. Normalized trace storage
    never receives this mapping.
    """
    return _collect("private_tool_evidence", source_roots, id_salt, redactor,
                    registry)


def collect_private_prompt_evidence(source_roots, id_salt: bytes, *, redactor=None,
                                    registry=None):
    """Collect redacted direct-human prompt evidence keyed by PROMPT span id.

    Same boundary as tool evidence: the result feeds external annotation
    packets only and never enters normalized traces.
    """
    return _collect("private_prompt_evidence", source_roots, id_salt, redactor,
                    registry)
